
import os
import json
import uuid
from celery import Celery
import insightface
from insightface.app import FaceAnalysis
import imagehash
from PIL import Image
import numpy as np
import cv2
import warnings
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams
from sqlmodel import Session, select

from app.database import engine
from app.models import Photo, FaceRecord
from app.hierarchy import rebuild_event_hierarchy

# Initialize Celery app with Redis broker
celery_app = Celery('tasks', broker='redis://localhost:6379/0')

# Lazy-loaded face analysis pipeline to prevent workers from locking up on load
_face_app = None

def get_face_app():
    global _face_app
    if _face_app is None:
        # Use CPU execution provider for standard local setups
        _face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _face_app

@celery_app.task
def process_photo(photo_id: int):
    with Session(engine) as session:
        # 1. Load photo metadata from database
        photo = session.get(Photo, photo_id)
        if not photo:
            return f"Error: Photo ID {photo_id} not found."

        # Mark photo status as processing
        photo.status = 'processing'
        session.add(photo)
        session.commit()
        session.refresh(photo)

        file_path = f"uploads/{photo.s3_key}"
        if not os.path.exists(file_path):
            photo.status = 'failed'
            session.add(photo)
            session.commit()
            return f"Error: Physical file {file_path} not found on disk."

        try:
            # Load images for OpenCV and Pillow
            img = cv2.imread(file_path)
            if img is None:
                raise ValueError("Could not decode image with OpenCV.")
            
            pil_img = Image.open(file_path)

            # 2. Detect faces with InsightFace buffalo_l
            face_analyzer = get_face_app()
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    'ignore',
                    message='`estimate` is deprecated since version 0.26.*',
                    category=FutureWarning,
                    module=r'insightface\.utils\.face_align',
                )
                faces = face_analyzer.get(img)

            # 5. Check duplicate via perceptual hash
            # Calculates an image signature to prevent redundant processing
            img_hash = str(imagehash.average_hash(pil_img))

            # Initialize Qdrant client to store vector embeddings
            qdrant_client = QdrantClient(host='localhost', port=6333)
            collection_name = f"event_{photo.event_id}_faces"

            # 6. Ensure the Qdrant collection exists for this event (512-dim vectors)
            if not qdrant_client.collection_exists(collection_name):
                qdrant_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(size=512, distance=Distance.COSINE)
                )

            # Loop through all detected faces
            for idx, face in enumerate(faces):
                # 3. Extract the 512-dimensional face embedding
                embedding = face['embedding'].tolist()

                # Get bounding box coordinates [x1, y1, x2, y2]
                bbox = face['bbox'].astype(int)
                x1, y1, x2, y2 = max(0, bbox[0]), max(0, bbox[1]), min(img.shape[1], bbox[2]), min(img.shape[0], bbox[3])

                # 4. Compute quality score using Laplacian variance (blurriness check)
                face_crop = img[y1:y2, x1:x2]
                if face_crop.size > 0:
                    gray_face = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
                    # Higher value = sharper focus, lower value = blurrier
                    laplacian_var = cv2.Laplacian(gray_face, cv2.CV_64F).var()
                    # Normalize quality score between 0.0 and 1.0 (arbitrary baseline scaling)
                    quality_score = min(1.0, laplacian_var / 500.0)
                else:
                    quality_score = 0.0

                # 6. Upsert the face embedding into Qdrant
                embedding_id = str(uuid.uuid4())
                qdrant_client.upsert(
                    collection_name=collection_name,
                    points=[
                        PointStruct(
                            id=embedding_id,
                            vector=embedding,
                            payload={
                                "photo_id": photo_id,
                                "event_id": photo.event_id,
                                "face_index": idx
                            }
                        )
                    ]
                )

                # 7. Create relational FaceRecord row in PostgreSQL
                face_record = FaceRecord(
                    photo_id=photo_id,
                    event_id=photo.event_id,
                    embedding_id=embedding_id,
                    quality_score=float(quality_score),
                    bbox=json.dumps(bbox.tolist())
                )
                session.add(face_record)

            # 8. Update Photo status to 'done'
            photo.status = 'done'
            session.add(photo)
            session.commit()
            rebuild_event_hierarchy(qdrant_client, photo.event_id)

            return f"Processed photo {photo_id} successfully. Found {len(faces)} faces."

        except Exception as e:
            session.rollback()
            photo.status = 'failed'
            session.add(photo)
            session.commit()
            return f"Failed processing photo {photo_id}: {str(e)}"
