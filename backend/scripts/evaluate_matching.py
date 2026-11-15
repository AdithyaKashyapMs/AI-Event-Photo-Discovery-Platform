"""Measure real matching accuracy against the labels from the labelling tool.

Run from the backend directory (read-only):

    python -m scripts.evaluate_matching --event-id 2
    python -m scripts.evaluate_matching --event-id 2 --selfies benchmarks/selfies/event2

Two ways to make the query for each labelled person:

  selfies (best)   --selfies DIR with one folder per person, named exactly as
                   in the labels, holding 1-6 selfie images taken with their
                   permission (DIR/Rahul/front.jpg, DIR/Rahul/left.jpg, ...).
                   Embeddings are fused exactly as the app fuses liveness
                   frames. The liveness check itself is not part of this test.
  leave-one-out    without --selfies: each person's sharpest labelled face is
                   the query and their other photos are the answer. Easier
                   than a real selfie (same camera, lighting and outfit), so
                   treat it as an upper bound.

The search is the one the app serves (adaptive hierarchy + recall pass).

Metrics (a "photo of X" is a photo with at least one face labelled X):
  recall      photos of X the app returned / photos of X
  precision   returned photos whose best-matching face is labelled X /
              returned photos whose best-matching face is labelled at all.
              Matches on unlabelled faces are counted as "unverified", not
              as wrong, so finish labelling for accurate precision.
Re-uploaded copies of the same photo (every face identical) are counted as
one photo, so finding a copy of the query's own photo does not count as a hit.
Results go to benchmarks/results/eval-event<ID>-<mode>.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlmodel import Session, select

from app.config import BACKEND_DIR, face_collection_name, get_qdrant_client
from app.database import engine
from app.hierarchy import MATCH_SIMILARITY_THRESHOLD, hierarchical_query, scroll_all_points
from app.models import NOT_A_FACE, FaceLabel, FaceRecord

RESULTS_DIR = BACKEND_DIR / "benchmarks" / "results"
THRESHOLDS = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def load_ground_truth(event_id: int):
    with Session(engine) as session:
        faces = {f.id: f for f in session.exec(select(FaceRecord).where(FaceRecord.event_id == event_id)).all()}
        labels = {
            row.face_id: row.person
            for row in session.exec(select(FaceLabel).where(FaceLabel.event_id == event_id)).all()
        }
    person_faces: dict[str, list[FaceRecord]] = defaultdict(list)
    for face_id, person in labels.items():
        if person != NOT_A_FACE and face_id in faces:
            person_faces[person].append(faces[face_id])
    return faces, labels, person_faces


def selfie_queries(selfie_dir: Path, people: set[str]) -> dict[str, np.ndarray]:
    import cv2

    from app.routers.events import fuse_selfie_embeddings
    from app.tasks import get_face_app

    face_app = get_face_app()
    queries = {}
    for folder in sorted(p for p in selfie_dir.iterdir() if p.is_dir()):
        if folder.name not in people:
            print(f"  skipping {folder.name}: no faces are labelled with this name")
            continue
        embeddings, scores = [], []
        for image_path in sorted(folder.iterdir()):
            if image_path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            image = cv2.imread(str(image_path))
            detected = face_app.get(image) if image is not None else []
            if not detected:
                print(f"  no face in {image_path}")
                continue
            face = max(detected, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            embeddings.append(np.asarray(face.embedding, dtype=float))
            scores.append(float(face.det_score))
        if embeddings:
            queries[folder.name], _ = fuse_selfie_embeddings(embeddings, scores)
    return queries


def search(client, collection: str, query: np.ndarray, faces_by_embedding: dict[str, FaceRecord]):
    """Best (similarity, face) per photo, as the app ranks them."""
    results, _ = hierarchical_query(client, collection, query.tolist(), limit=500, use_cache=False, recall_pass=True)
    best: dict[int, tuple[float, FaceRecord]] = {}
    for result in results:
        face = faces_by_embedding.get(str(result.id))
        if face and (face.photo_id not in best or result.score > best[face.photo_id][0]):
            best[face.photo_id] = (float(result.score), face)
    return best


def duplicate_groups(faces: dict[int, FaceRecord], vectors: dict[str, np.ndarray]) -> dict[int, int]:
    """Map each photo to the lowest ID of the photos that are copies of it."""
    by_photo: dict[int, list[np.ndarray]] = defaultdict(list)
    for face in faces.values():
        if face.embedding_id in vectors:
            by_photo[face.photo_id].append(vectors[face.embedding_id])
    canonical = {photo_id: photo_id for photo_id in by_photo}
    ids = sorted(by_photo)
    for i, a in enumerate(ids):
        if canonical[a] != a:
            continue
        A = np.asarray(by_photo[a])
        for b in ids[i + 1 :]:
            B = np.asarray(by_photo[b])
            if canonical[b] == b and len(A) == len(B) and (A @ B.T).max(axis=1).min() > 0.99:
                canonical[b] = a
    return canonical


def bucket(value: float | None, edges: list[float], names: list[str]) -> str:
    if value is None:
        return "unknown"
    for edge, name in zip(edges, names):
        if value < edge:
            return name
    return names[-1]


QUALITY_BUCKETS = ([0.3, 0.6], ["blurry (<0.3)", "medium (0.3-0.6)", "sharp (>=0.6)"])
WIDTH_BUCKETS = ([60, 120], ["small (<60 px)", "medium (60-120 px)", "large (>=120 px)"])


def face_width(face: FaceRecord) -> float | None:
    box = json.loads(face.bbox)
    return float(box[2] - box[0]) if len(box) == 4 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--selfies", type=Path, help="folder with one sub-folder of selfies per labelled person")
    args = parser.parse_args()

    faces, labels, person_faces = load_ground_truth(args.event_id)
    if not person_faces:
        raise SystemExit("No labels yet. Label faces at /dashboard/events/<id>/label first.")
    client = get_qdrant_client()
    collection = face_collection_name(args.event_id)
    vectors = {
        str(p.id): np.asarray(p.vector, dtype=float) / np.linalg.norm(p.vector)
        for p in scroll_all_points(client, collection)
    }
    faces_by_embedding = {f.embedding_id: f for f in faces.values()}
    canonical = duplicate_groups(faces, vectors)
    copies = sum(1 for photo, root in canonical.items() if photo != root)
    if copies:
        print(f"  {copies} photos are re-uploaded copies of other photos; each copy is counted once.")
    # From here on a "photo" is a group of identical copies.
    for face in faces.values():
        face.photo_id = canonical.get(face.photo_id, face.photo_id)

    mode = "selfies" if args.selfies else "leave-one-out"
    query_photo: dict[str, int | None] = {}
    if args.selfies:
        queries = selfie_queries(args.selfies, set(person_faces))
        query_photo = {person: None for person in queries}
    else:
        queries = {}
        for person, labelled in person_faces.items():
            if len({f.photo_id for f in labelled}) < 2:
                continue  # needs one photo to query with and at least one to find
            best = max((f for f in labelled if f.embedding_id in vectors), key=lambda f: f.quality_score, default=None)
            if best:
                queries[person] = vectors[best.embedding_id]
                query_photo[person] = best.photo_id
    if not queries:
        raise SystemExit("No person has enough labelled photos (or matching selfie folders) to evaluate.")

    per_person, sweep = [], {t: {"tp": 0, "truth": 0, "correct": 0, "wrong": 0, "unverified": 0} for t in THRESHOLDS}
    hard = {"quality": defaultdict(lambda: [0, 0]), "width": defaultdict(lambda: [0, 0])}
    for person, query in sorted(queries.items()):
        truth_faces = [f for f in person_faces[person] if f.photo_id != query_photo[person]]
        truth_photos = {f.photo_id for f in truth_faces}
        if not truth_photos:
            continue
        best = search(client, collection, query, faces_by_embedding)
        for t in THRESHOLDS:
            returned = {pid: v for pid, v in best.items() if v[0] >= t and pid != query_photo[person]}
            s = sweep[t]
            s["tp"] += len(truth_photos & set(returned))
            s["truth"] += len(truth_photos)
            for _, face in returned.values():
                label = labels.get(face.id)
                if label is None:
                    s["unverified"] += 1
                elif label == person:
                    s["correct"] += 1
                else:
                    s["wrong"] += 1
            if t == MATCH_SIMILARITY_THRESHOLD:
                found = truth_photos & set(returned)
                per_person.append(
                    {"person": person, "photos": len(truth_photos), "found": len(found), "returned": len(returned)}
                )
        # Hard cases: which labelled faces does the matcher score below the threshold?
        for face in truth_faces:
            if face.embedding_id not in vectors:
                continue
            hit = float(vectors[face.embedding_id] @ (query / np.linalg.norm(query))) >= MATCH_SIMILARITY_THRESHOLD
            for kind, value, edges in (
                ("quality", face.quality_score, QUALITY_BUCKETS),
                ("width", face_width(face), WIDTH_BUCKETS),
            ):
                cell = hard[kind][bucket(value, *edges)]
                cell[0] += hit
                cell[1] += 1

    path = write_report(args.event_id, mode, per_person, sweep, hard, len(faces), len(labels))
    print(path.read_text())
    print(f"Wrote {path}")


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "–"


def write_report(event_id, mode, per_person, sweep, hard, total_faces, labelled_faces) -> Path:
    t0 = MATCH_SIMILARITY_THRESHOLD
    s0 = sweep[t0]
    lines = [
        f"# Matching accuracy, event {event_id} ({mode})",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. "
        f"{labelled_faces} of {total_faces} faces labelled; {len(per_person)} people evaluated.",
        "",
        f"**At the app's threshold ({t0}): recall {_pct(s0['tp'], s0['truth'])}, "
        f"precision {_pct(s0['correct'], s0['correct'] + s0['wrong'])}** "
        f"({s0['wrong']} wrong photos, {s0['unverified']} unverified).",
        "",
        "## Per person",
        "",
        "| Person | Photos of them | Found | Recall | Photos returned |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {r['person']} | {r['photos']} | {r['found']} | {_pct(r['found'], r['photos'])} | {r['returned']} |"
        for r in per_person
    ]
    recalls = [r["found"] / r["photos"] for r in per_person if r["photos"]]
    if recalls:
        lines += ["", f"Average per person (macro) recall: {100 * statistics.mean(recalls):.1f}%."]
    lines += [
        "",
        "## Threshold sweep",
        "",
        "| Threshold | Recall | Precision | Wrong photos | Unverified |",
        "|---:|---:|---:|---:|---:|",
    ]
    for t, s in sweep.items():
        marker = " (app)" if t == t0 else ""
        lines.append(
            f"| {t:.2f}{marker} | {_pct(s['tp'], s['truth'])} | {_pct(s['correct'], s['correct'] + s['wrong'])} "
            f"| {s['wrong']} | {s['unverified']} |"
        )
    lines += ["", "## Hard cases (face-level recall at the app's threshold)", ""]
    for kind, title in (("quality", "Face sharpness"), ("width", "Face size in the photo")):
        lines += [f"| {title} | Faces | Found | Recall |", "|---|---:|---:|---:|"]
        for name, (hits, count) in sorted(hard[kind].items()):
            lines.append(f"| {name} | {count} | {hits} | {_pct(hits, count)} |")
        lines.append("")
    if mode == "leave-one-out":
        lines += [
            "Leave-one-out uses one of each person's own event faces as the query, which is easier than a real",
            "selfie. Run with --selfies for the numbers to report.",
        ]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"eval-event{event_id}-{mode}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    main()
