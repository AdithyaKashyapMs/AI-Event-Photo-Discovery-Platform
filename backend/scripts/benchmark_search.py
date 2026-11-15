"""Benchmark face-search strategies on one event, offline.

Run from the backend directory (read-only: nothing in Qdrant is modified):

    python -m scripts.benchmark_search --event-id 1
    python -m scripts.benchmark_search --event-id 1 --queries 100 --k 20

Queries are faces already indexed in the event (each query's own point is
excluded from its results), so no labelled selfies are needed. "Ground truth"
is an exact brute-force search over every face: it measures how many of the
true nearest faces each strategy returns, not whether those faces are the same
person. Identity accuracy needs a labelled dataset.

Strategies compared:
- exact:            brute-force cosine search over every face (the baseline)
- qdrant_hnsw:      Qdrant's default approximate (HNSW) search
- hierarchy_only:   the adaptive hierarchy pass alone (selected leaves only)
- hierarchy+recall: what the app runs: hierarchy pass plus full-index recall
                    pass, with the hierarchy cached in memory

Results are printed and written to benchmarks/results/ as Markdown and CSV.
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from qdrant_client.models import FieldCondition, Filter, HasIdCondition, SearchParams

from app.config import BACKEND_DIR, face_collection_name, get_qdrant_client
from app.hierarchy import (
    CANDIDATE_SIMILARITY_FLOOR,
    MATCH_SIMILARITY_THRESHOLD,
    hierarchical_query,
    scroll_all_points,
)

RESULTS_DIR = BACKEND_DIR / "benchmarks" / "results"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--collection", help="benchmark this Qdrant collection instead of the event's")
    parser.add_argument("--queries", type=int, default=50, help="number of query faces")
    parser.add_argument("--k", type=int, default=20, help="neighbours compared per query")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    client = get_qdrant_client()
    collection = args.collection or face_collection_name(args.event_id)
    if not client.collection_exists(collection):
        raise SystemExit(f"No face index for event {args.event_id} ({collection}).")

    points = scroll_all_points(client, collection)
    coded = sum(1 for p in points if p.payload and p.payload.get("identity_code"))
    if len(points) < 2:
        raise SystemExit("Need at least two indexed faces.")
    if coded < len(points):
        print(f"Warning: only {coded}/{len(points)} faces have hierarchy codes; "
              "the hierarchy may not be rebuilt yet.")

    rng = random.Random(args.seed)
    queries = rng.sample(points, min(args.queries, len(points)))
    k = args.k

    def without_self(query_id):
        return Filter(must_not=[HasIdCondition(has_id=[query_id])])

    def exact(query):
        return client.query_points(
            collection_name=collection, query=query.vector, query_filter=without_self(query.id),
            limit=k, score_threshold=CANDIDATE_SIMILARITY_FLOOR,
            search_params=SearchParams(exact=True),
        ).points

    def hnsw(query):
        return client.query_points(
            collection_name=collection, query=query.vector, query_filter=without_self(query.id),
            limit=k, score_threshold=CANDIDATE_SIMILARITY_FLOOR,
        ).points

    def hierarchy(recall_pass: bool):
        def run(query):
            results, stats = hierarchical_query(
                client, collection, query.vector, limit=k + 1,
                use_cache=True, recall_pass=recall_pass,
            )
            return [r for r in results if str(r.id) != str(query.id)][:k], stats["faces_considered"]
        return run

    total_faces = len(points)
    strategies = {
        "exact": lambda q: (exact(q), total_faces),
        "qdrant_hnsw": lambda q: (hnsw(q), total_faces),
        "hierarchy_only": hierarchy(recall_pass=False),
        "hierarchy+recall": hierarchy(recall_pass=True),
    }

    # Warm up connections and the hierarchy cache so no strategy pays setup
    # costs inside the timed loop.
    for run in strategies.values():
        run(queries[0])

    truth, truth_matches = {}, {}
    for q in queries:
        nearest = exact(q)
        truth[q.id] = {str(r.id) for r in nearest}
        # Faces similar enough to be shown to an attendee as a match.
        truth_matches[q.id] = {str(r.id) for r in nearest if r.score >= MATCH_SIMILARITY_THRESHOLD}

    rows = []
    for name, run in strategies.items():
        latencies, recalls, match_recalls, searched = [], [], [], []
        for query in queries:
            started = perf_counter()
            found, faces_searched = run(query)
            latencies.append((perf_counter() - started) * 1000)
            found_ids = {str(r.id) for r in found}
            searched.append(faces_searched / total_faces)
            if truth[query.id]:
                recalls.append(len(truth[query.id] & found_ids) / len(truth[query.id]))
            if truth_matches[query.id]:
                match_recalls.append(len(truth_matches[query.id] & found_ids) / len(truth_matches[query.id]))
        rows.append({
            "strategy": name,
            "median_ms": round(statistics.median(latencies), 2),
            "p95_ms": round(percentile(latencies, 0.95), 2),
            f"recall@{k}": round(statistics.mean(recalls), 4) if recalls else None,
            f"match_recall(>={MATCH_SIMILARITY_THRESHOLD})": round(statistics.mean(match_recalls), 4) if match_recalls else None,
            "faces_searched_%": round(100 * statistics.mean(searched), 1),
        })

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base = RESULTS_DIR / f"event{args.event_id}-{stamp}"
    header = list(rows[0])

    with base.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        f"# Face search benchmark: event {args.event_id}",
        "",
        f"- Indexed faces: {len(points)} ({coded} with hierarchy codes)",
        f"- Query faces: {len(queries)} (sampled with seed {args.seed}; each excluded from its own results)",
        f"- Neighbours compared: k = {k}, similarity floor {CANDIDATE_SIMILARITY_FLOOR}",
        "- Recall is measured against exact brute-force search over every face.",
        f"- match_recall: share of the true neighbours with similarity >= {MATCH_SIMILARITY_THRESHOLD} (what an attendee would be shown) that were found.",
        "- faces_searched_%: share of the index scanned by that strategy (the hierarchy pass scans only its selected leaves).",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(str(row[h]) for h in header) + " |" for row in rows),
        "",
    ]
    base.with_suffix(".md").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"Saved {base.with_suffix('.md')} and {base.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
