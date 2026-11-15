"""Find re-uploaded photos in events processed before duplicate detection existed.

Photos uploaded before the pHash column was added have no hash, so later
copies of them were indexed as separate photos. This script:

1. computes the missing perceptual hashes (same function as the worker),
2. keeps the oldest photo of each identical-hash group and marks the others
   'duplicate' (the gallery only shows 'done' photos, as for new uploads),
3. moves anything attached to a copy onto the kept photo: attendees' matches,
   and face labels when the kept photo's matching face is still unlabelled,
4. deletes the copies' face embeddings (Qdrant) and face records, then
   rebuilds the event's hierarchy.

Photo files are kept, as for duplicates detected at upload time.

    python -m scripts.backfill_duplicates --event-id 2           # dry run
    python -m scripts.backfill_duplicates --event-id 2 --apply
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import imagehash
import numpy as np
from PIL import Image
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue
from sqlmodel import Session, delete, select

from app.config import UPLOAD_DIR, face_collection_name, get_qdrant_client
from app.database import engine
from app.hierarchy import rebuild_hierarchy, scroll_all_points
from app.models import FaceLabel, FaceRecord, Photo, PhotoMatch

SAME_FACE_SIMILARITY = 0.99


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    args = parser.parse_args()

    client = get_qdrant_client()
    collection = face_collection_name(args.event_id)
    vectors = {}
    if client.collection_exists(collection):
        vectors = {
            str(p.id): np.asarray(p.vector) / np.linalg.norm(p.vector) for p in scroll_all_points(client, collection)
        }

    with Session(engine) as session:
        photos = session.exec(
            select(Photo).where(Photo.event_id == args.event_id, Photo.status == "done").order_by(Photo.id)
        ).all()
        hashed = 0
        for photo in photos:
            if not photo.phash:
                with Image.open(UPLOAD_DIR / photo.s3_key) as image:
                    photo.phash = str(imagehash.phash(image))
                session.add(photo)
                hashed += 1
        groups: dict[str, list[Photo]] = defaultdict(list)
        for photo in photos:
            groups[photo.phash].append(photo)
        copies = [(group[0], copy) for group in groups.values() for copy in group[1:]]

        print(f"Event {args.event_id}: {len(photos)} indexed photos, {hashed} hashes computed, {len(copies)} copies")
        moved_labels = moved_matches = removed_faces = 0
        for kept, copy in copies:
            kept_faces = session.exec(select(FaceRecord).where(FaceRecord.photo_id == kept.id)).all()
            copy_faces = session.exec(select(FaceRecord).where(FaceRecord.photo_id == copy.id)).all()
            labels = {
                row.face_id: row
                for row in session.exec(
                    select(FaceLabel).where(FaceLabel.face_id.in_([f.id for f in kept_faces + copy_faces]))
                ).all()
            }
            notes = []
            for face in copy_faces:
                label = labels.get(face.id)
                twin = _same_face(face, kept_faces, vectors)
                if label and twin is not None and twin.id not in labels:
                    notes.append(f"label {label.person!r} -> face {twin.id}")
                    if args.apply:
                        session.add(FaceLabel(event_id=args.event_id, face_id=twin.id, person=label.person))
                    labels[twin.id] = label
                    moved_labels += 1
            matches = session.exec(select(PhotoMatch).where(PhotoMatch.photo_id == copy.id)).all()
            have = {
                m.user_id for m in session.exec(select(PhotoMatch).where(PhotoMatch.photo_id == kept.id)).all()
            }
            for match in matches:
                if match.user_id not in have:
                    moved_matches += 1
                    if args.apply:
                        session.add(
                            PhotoMatch(user_id=match.user_id, event_id=match.event_id, photo_id=kept.id,
                                       similarity=match.similarity)
                        )
            removed_faces += len(copy_faces)
            print(f"  photo {copy.id} is a copy of {kept.id}: {len(copy_faces)} faces removed"
                  + (f"; {', '.join(notes)}" if notes else ""))

            if args.apply:
                session.execute(delete(PhotoMatch).where(PhotoMatch.photo_id == copy.id))
                session.execute(delete(FaceLabel).where(FaceLabel.face_id.in_([f.id for f in copy_faces])))
                session.execute(delete(FaceRecord).where(FaceRecord.photo_id == copy.id))
                copy.status = "duplicate"
                session.add(copy)
                if client.collection_exists(collection):
                    client.delete(
                        collection,
                        points_selector=FilterSelector(
                            filter=Filter(must=[FieldCondition(key="photo_id", match=MatchValue(value=copy.id))])
                        ),
                        wait=True,
                    )

        print(f"Totals: {len(copies)} copies, {removed_faces} faces removed, "
              f"{moved_labels} labels and {moved_matches} matches moved to the kept photo")
        if not args.apply:
            session.rollback()
            print("Dry run: nothing changed. Re-run with --apply.")
            return
        session.commit()
    if copies:
        print("Hierarchy rebuilt:", rebuild_hierarchy(client, collection))


def _same_face(face: FaceRecord, candidates: list[FaceRecord], vectors: dict) -> FaceRecord | None:
    vector = vectors.get(face.embedding_id)
    best, best_score = None, SAME_FACE_SIMILARITY
    for candidate in candidates:
        other = vectors.get(candidate.embedding_id)
        if vector is not None and other is not None and float(vector @ other) >= best_score:
            best, best_score = candidate, float(vector @ other)
    return best


if __name__ == "__main__":
    main()
