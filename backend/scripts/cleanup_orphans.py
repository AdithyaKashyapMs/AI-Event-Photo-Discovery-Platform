"""Find and remove data that no longer belongs to anything.

The database, Qdrant and the uploads folder are separate stores, so a crash
in the middle of a delete (or data from older versions of the app) can leave
pieces behind. This checks all three against the database:

  collections    Qdrant collections of events that no longer exist
  points         face embeddings whose photo is gone or is not indexed
                 ('done'), and embeddings with no face record
  face records   database face rows whose embedding is missing from Qdrant
  files          files in UPLOAD_DIR that no photo row points to; moved to
                 UPLOAD_DIR/.orphaned/<time>/ (recoverable) unless --purge-files
  thumbnails     previews of photos that no longer exist (regenerated on demand)

    python -m scripts.cleanup_orphans            # dry run: report only
    python -m scripts.cleanup_orphans --apply
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from collections import defaultdict
from datetime import datetime

from sqlmodel import Session, delete, select

from app.config import THUMBNAIL_DIR, UPLOAD_DIR, face_collection_name, get_qdrant_client
from app.database import engine
from app.hierarchy import forget_cached_hierarchy, rebuild_hierarchy, scroll_all_points
from app.models import Event, FaceLabel, FaceRecord, Photo

COLLECTION_PATTERN = re.compile(r"^event_(\d+)_faces$")
ORPHAN_DIR_NAME = ".orphaned"
# Uploads are written to a temporary file first; leave recent ones alone.
IN_PROGRESS_SECONDS = 3600


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    parser.add_argument("--purge-files", action="store_true", help="delete orphaned files instead of moving them")
    args = parser.parse_args()
    apply = args.apply
    client = get_qdrant_client()

    with Session(engine) as session:
        event_ids = set(session.exec(select(Event.id)).all())
        photos = session.exec(select(Photo)).all()
        indexed = {p.id: p.event_id for p in photos if p.status == "done"}
        photo_ids = {p.id for p in photos}
        keys = {p.s3_key for p in photos}
        faces = session.exec(select(FaceRecord)).all()

        # --- Qdrant collections and points ---
        rebuild = []
        faces_by_embedding = {f.embedding_id: f for f in faces}
        embeddings_seen = set()
        for collection in client.get_collections().collections:
            match = COLLECTION_PATTERN.match(collection.name)
            if not match:
                continue
            event_id = int(match.group(1))
            if event_id not in event_ids:
                count = client.get_collection(collection.name).points_count or 0
                print(f"collection {collection.name}: event {event_id} does not exist ({count} faces) -> delete")
                if apply:
                    client.delete_collection(collection.name)
                    forget_cached_hierarchy(collection.name)
                continue
            stale = []
            for point in scroll_all_points(client, collection.name, with_vectors=False):
                embeddings_seen.add(str(point.id))
                photo_id = (point.payload or {}).get("photo_id")
                if indexed.get(photo_id) != event_id or str(point.id) not in faces_by_embedding:
                    stale.append(point.id)
            if stale:
                print(f"collection {collection.name}: {len(stale)} faces of missing or unindexed photos -> delete")
                if apply:
                    for start in range(0, len(stale), 1000):
                        client.delete(collection.name, points_selector=stale[start : start + 1000], wait=True)
                    rebuild.append(collection.name)

        # --- Face records without an embedding ---
        missing = [f for f in faces if f.embedding_id not in embeddings_seen]
        if missing:
            by_event = defaultdict(int)
            for face in missing:
                by_event[face.event_id] += 1
            print(f"face records without an embedding: {dict(by_event)} (by event) -> delete")
            if apply:
                ids = [f.id for f in missing]
                session.execute(delete(FaceLabel).where(FaceLabel.face_id.in_(ids)))
                session.execute(delete(FaceRecord).where(FaceRecord.id.in_(ids)))
                session.commit()

    # --- Files ---
    orphan_root = UPLOAD_DIR / ORPHAN_DIR_NAME
    destination = orphan_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    now = time.time()
    orphan_files = []
    for path in UPLOAD_DIR.rglob("*"):
        relative = path.relative_to(UPLOAD_DIR)
        if not path.is_file() or relative.parts[0] in {ORPHAN_DIR_NAME, THUMBNAIL_DIR.name}:
            continue
        if str(relative) in keys:
            continue
        if path.name.startswith(".upload-") and now - path.stat().st_mtime < IN_PROGRESS_SECONDS:
            continue
        orphan_files.append(path)
    if orphan_files:
        by_folder = defaultdict(int)
        for path in orphan_files:
            by_folder[str(path.relative_to(UPLOAD_DIR).parent)] += 1
        action = "delete" if args.purge_files else f"move to {destination.relative_to(UPLOAD_DIR)}"
        print(f"files no photo points to: {dict(by_folder)} (by folder) -> {action}")
        if apply:
            for path in orphan_files:
                if args.purge_files:
                    path.unlink()
                else:
                    target = destination / path.relative_to(UPLOAD_DIR)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), target)
            for folder in sorted(UPLOAD_DIR.iterdir()):
                if folder.is_dir() and folder.name not in {ORPHAN_DIR_NAME, THUMBNAIL_DIR.name} and not any(folder.iterdir()):
                    folder.rmdir()

    thumbnails = [
        t for t in (THUMBNAIL_DIR.glob("*.jpg") if THUMBNAIL_DIR.exists() else [])
        if not t.stem.isdigit() or int(t.stem) not in photo_ids
    ]
    if thumbnails:
        print(f"thumbnails of missing photos: {len(thumbnails)} -> delete")
        if apply:
            for thumbnail in thumbnails:
                thumbnail.unlink()

    for name in rebuild:
        if client.collection_exists(name):
            print(f"hierarchy {name} rebuilt: {rebuild_hierarchy(client, name)}")
    if not apply:
        print("Dry run: nothing changed. Re-run with --apply.")


if __name__ == "__main__":
    main()
