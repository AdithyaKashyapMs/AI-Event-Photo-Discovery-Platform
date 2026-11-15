"""How the adaptive hierarchy scales: synthetic events from 10k to 1M faces.

Run from the backend directory:

    python -m scripts.benchmark_scale                       # 10k, 100k, 300k
    python -m scripts.benchmark_scale --sizes 10000 1000000 # needs ~8 GB free RAM for 1M
    python -m scripts.benchmark_scale --qdrant-max 100000   # also test Qdrant up to this size

Why synthetic: the real events have a few hundred faces, too few to show how
search cost grows. The generator is calibrated against the real event-2
embeddings (512-d ArcFace from InsightFace buffalo_l):

    real:      unrelated pairs mean 0.049, 90th pct 0.149, 99th pct 0.391
    synthetic: unrelated pairs mean ~0.05,  90th pct ~0.16,  99th pct ~0.33
Identity centroids share a global direction and one of 8 "look-alike" groups
(so unrelated faces are not all orthogonal). Each face is its identity
centroid plus noise; a per-face quality (0.3-0.95: profiles, blur and tiny
background faces up to sharp portraits) leaves about a third of same-person
pairs below the match threshold, the hard cases that decide recall.
Faces per person follow a geometric distribution (mean 10), like guests who
appear in a handful of photos and a few who appear in many.

Each query is a fresh, good-quality sample of a random guest (a selfie), so
queries are never points already in the index.

Strategies (all return faces with similarity >= MATCH_SIMILARITY_THRESHOLD):
  exact            brute-force cosine over every face (numpy) - ground truth
  hierarchy        the app's adaptive hierarchy: app.hierarchy builds the tree
                   (_build_codes) and picks leaves (select_branches); only the
                   faces in those leaves are compared (numpy)
  qdrant_exact     Qdrant exact search (exact=True)
  qdrant_hnsw      Qdrant default approximate search (HNSW graph)
  qdrant_hierarchy the hierarchy pass alone in Qdrant: one search filtered to
                   the selected leaves (hierarchical_query, recall_pass=False)
  app_served       exactly what the API runs: hierarchy pass plus the HNSW
                   recall pass, merged (hierarchical_query, recall_pass=True)

Metric definitions:
  match recall   share of the exact search's matches (>= threshold) a strategy
                 returns. The app's recall pass makes the served result 100%.
  compared       share of indexed faces the strategy scores for each query.
  latency        per query, after warm-up; p50 and p95 over all queries.

Qdrant runs use a temporary collection ("bench_scale_<n>") that is deleted at
the end; real event collections are never touched. Results go to
benchmarks/results/scale-benchmark.{md,csv}.
"""

from __future__ import annotations

import argparse
import csv
import gc
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app import hierarchy as hier
from app.config import BACKEND_DIR, get_qdrant_client
from app.hierarchy import MATCH_SIMILARITY_THRESHOLD

RESULTS_DIR = BACKEND_DIR / "benchmarks" / "results"
DIM = 512
LOOKALIKE_GROUPS = 8
GROUP_WEIGHT = 0.35
GLOBAL_WEIGHT = 0.05
FACE_QUALITY = (0.3, 0.95)
MEAN_FACES_PER_PERSON = 10
QUERY_QUALITY = 0.9  # a fused, liveness-checked selfie is a clean view


# --- Synthetic data -----------------------------------------------------------


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


class SyntheticEvent:
    """Faces sorted by hierarchy leaf (so each leaf is one contiguous slice)."""

    def __init__(self, n_faces: int, seed: int):
        rng = np.random.default_rng(seed)
        self.rng = rng
        counts = []
        while sum(counts) < n_faces:
            counts.append(int(min(rng.geometric(1 / MEAN_FACES_PER_PERSON), 200)))
        counts[-1] -= sum(counts) - n_faces
        counts = [c for c in counts if c > 0]
        self.n_people = len(counts)

        global_dir = _unit(rng.standard_normal(DIM)).astype(np.float32)
        groups = _unit(rng.standard_normal((LOOKALIKE_GROUPS, DIM))).astype(np.float32)
        group_of = rng.integers(0, LOOKALIKE_GROUPS, self.n_people)
        self.centroids = _unit(
            np.sqrt(GLOBAL_WEIGHT) * global_dir
            + np.sqrt(GROUP_WEIGHT) * groups[group_of]
            + np.sqrt(1 - GLOBAL_WEIGHT - GROUP_WEIGHT) * _unit(rng.standard_normal((self.n_people, DIM)).astype(np.float32))
        ).astype(np.float32)

        self.person = np.repeat(np.arange(self.n_people), counts)
        faces = np.empty((n_faces, DIM), dtype=np.float32)
        for start in range(0, n_faces, 50_000):  # chunked to bound memory
            stop = min(start + 50_000, n_faces)
            quality = rng.uniform(*FACE_QUALITY, (stop - start, 1)).astype(np.float32)
            noise = _unit(rng.standard_normal((stop - start, DIM)).astype(np.float32))
            faces[start:stop] = _unit(
                np.sqrt(quality) * self.centroids[self.person[start:stop]] + np.sqrt(1 - quality) * noise
            )
        self.faces = faces

    def queries(self, count: int) -> np.ndarray:
        people = self.rng.integers(0, self.n_people, count)
        noise = _unit(self.rng.standard_normal((count, DIM)).astype(np.float32))
        return _unit(np.sqrt(QUERY_QUALITY) * self.centroids[people] + np.sqrt(1 - QUERY_QUALITY) * noise)

    def build_hierarchy(self) -> dict:
        started = time.perf_counter()
        codes = hier._build_codes(self.faces, np.arange(len(self.faces)))
        build_seconds = time.perf_counter() - started
        code_per_face = np.array([codes[i] for i in range(len(self.faces))])
        order = np.argsort(code_per_face, kind="stable")
        self.faces = np.ascontiguousarray(self.faces[order])
        self.person = self.person[order]
        self.codes_sorted = code_per_face[order]
        leaf_codes, starts, sizes = np.unique(self.codes_sorted, return_index=True, return_counts=True)
        self.leaf_starts, self.leaf_sizes = starts, sizes
        centroids = np.stack([_unit(self.faces[s : s + n].mean(axis=0)) for s, n in zip(starts, sizes)])
        self.tree = hier._CachedHierarchy(
            points_count=len(self.faces),
            loaded_at=time.monotonic(),
            codes=list(leaf_codes),
            centroids=centroids.astype(np.float32),
            sizes=sizes,
            max_depth=max(code.count("-") + 1 for code in leaf_codes),
        )
        return {
            "build_seconds": round(build_seconds, 2),
            "leaves": len(leaf_codes),
            "max_depth": self.tree.max_depth,
            "average_leaf": round(len(self.faces) / len(leaf_codes), 1),
            "largest_leaf": int(sizes.max()),
        }


# --- Strategies ---------------------------------------------------------------


def exact_matches(faces: np.ndarray, query: np.ndarray) -> set[int]:
    return set(np.flatnonzero(faces @ query >= MATCH_SIMILARITY_THRESHOLD).tolist())


def hierarchy_matches(event: SyntheticEvent, query: np.ndarray) -> tuple[set[int], int]:
    selected = hier.select_branches(event.tree, query)
    found: list[np.ndarray] = []
    compared = 0
    for leaf in selected:
        start, size = event.leaf_starts[leaf], event.leaf_sizes[leaf]
        scores = event.faces[start : start + size] @ query
        found.append(start + np.flatnonzero(scores >= MATCH_SIMILARITY_THRESHOLD))
        compared += size
    return set(np.concatenate(found).tolist()) if found else set(), compared


# --- Qdrant -------------------------------------------------------------------


def load_into_qdrant(event: SyntheticEvent, name: str) -> None:
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = get_qdrant_client()
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(name, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))
    client.create_payload_index(name, "identity_code", field_schema="keyword")
    batch = 1000
    for start in range(0, len(event.faces), batch):
        stop = min(start + batch, len(event.faces))
        client.upsert(
            name,
            points=[
                PointStruct(id=i, vector=event.faces[i].tolist(), payload={"identity_code": str(event.codes_sorted[i])})
                for i in range(start, stop)
            ],
            wait=False,
        )
    # Wait for indexing (HNSW build) to finish before timing searches.
    while True:
        info = client.get_collection(name)
        if info.status.value == "green" and (info.points_count or 0) >= len(event.faces):
            break
        time.sleep(1)


def qdrant_runs(event: SyntheticEvent, name: str, queries: np.ndarray, truth: list[set[int]]) -> list[dict]:
    from qdrant_client.models import SearchParams

    client = get_qdrant_client()
    hier._hierarchy_cache[name] = event.tree  # the cached tree the API would hold
    runs = {"qdrant_exact": [], "qdrant_hnsw": [], "qdrant_hierarchy": [], "app_served": []}
    found = {key: [] for key in runs}

    def plain(query, exact):
        return client.query_points(
            name,
            query=query.tolist(),
            score_threshold=MATCH_SIMILARITY_THRESHOLD,
            limit=500,
            search_params=SearchParams(exact=exact),
        ).points

    def tree(query, recall_pass=False):
        results, _ = hier.hierarchical_query(
            client, name, query.tolist(), limit=500, use_cache=True, recall_pass=recall_pass
        )
        return [r for r in results if r.score >= MATCH_SIMILARITY_THRESHOLD]

    strategies = {
        "qdrant_exact": lambda q: plain(q, True),
        "qdrant_hnsw": lambda q: plain(q, False),
        "qdrant_hierarchy": tree,
        "app_served": lambda q: tree(q, recall_pass=True),
    }
    for key, run in strategies.items():
        for query in queries[:5]:
            run(query)  # warm-up
        for query in queries:
            started = time.perf_counter()
            points = run(query)
            runs[key].append((time.perf_counter() - started) * 1000)
            found[key].append({int(p.id) for p in points})

    rows = []
    for key in runs:
        rows.append(
            summarize(key, len(event.faces), runs[key], found[key], truth, [None] * len(queries))
        )
    hier.forget_cached_hierarchy(name)
    return rows


# --- Reporting ----------------------------------------------------------------


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def summarize(strategy, n_faces, latencies, found, truth, compared) -> dict:
    recalls = [len(f & t) / len(t) for f, t in zip(found, truth) if t]
    known = [c for c in compared if c is not None]
    return {
        "faces": n_faces,
        "strategy": strategy,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "match_recall": round(statistics.mean(recalls), 4) if recalls else None,
        "min_recall": round(min(recalls), 4) if recalls else None,
        "compared_pct": round(float(100 * statistics.mean(known) / n_faces), 2) if known else None,
    }


def benchmark_size(n_faces: int, n_queries: int, seed: int, with_qdrant: bool) -> tuple[list[dict], dict]:
    print(f"\n== {n_faces:,} faces ==", flush=True)
    event = SyntheticEvent(n_faces, seed)
    build = event.build_hierarchy()
    build.update({"faces": n_faces, "people": event.n_people})
    print(f"  hierarchy: {build}", flush=True)
    queries = event.queries(n_queries)

    truth, exact_ms = [], []
    for query in queries:
        started = time.perf_counter()
        truth.append(exact_matches(event.faces, query))
        exact_ms.append((time.perf_counter() - started) * 1000)
    tree_ms, tree_found, tree_compared = [], [], []
    for query in queries:
        started = time.perf_counter()
        matches, compared = hierarchy_matches(event, query)
        tree_ms.append((time.perf_counter() - started) * 1000)
        tree_found.append(matches)
        tree_compared.append(compared)

    rows = [
        summarize("exact", n_faces, exact_ms, truth, truth, [n_faces] * n_queries),
        summarize("hierarchy", n_faces, tree_ms, tree_found, truth, tree_compared),
    ]
    build["matches_per_query"] = round(statistics.mean(len(t) for t in truth), 1)
    if with_qdrant:
        name = f"bench_scale_{n_faces}"
        print("  loading into Qdrant…", flush=True)
        try:
            load_into_qdrant(event, name)
            rows += qdrant_runs(event, name, queries, truth)
        finally:
            get_qdrant_client().delete_collection(name)
    for row in rows:
        print("  ", row, flush=True)
    del event
    gc.collect()
    return rows, build


def write_report(rows: list[dict], builds: list[dict], args) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULTS_DIR / "scale-benchmark.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Adaptive hierarchy at scale (synthetic events)",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by `python -m scripts.benchmark_scale "
        f"--sizes {' '.join(map(str, args.sizes))} --queries {args.queries} --qdrant-max {args.qdrant_max}`.",
        f"Match threshold {MATCH_SIMILARITY_THRESHOLD}; hierarchy: branching {hier.BRANCHING_FACTOR}, "
        f"leaf size {hier.MAX_LEAF_SIZE}, max depth {hier.MAX_DEPTH}, margin {hier.BRANCH_SIMILARITY_MARGIN}, "
        f"search cap {hier.MAX_SEARCHED_FRACTION:.0%}.",
        "See the script's docstring for how the synthetic faces are generated and calibrated.",
        "",
        "## Hierarchy shape",
        "",
        "| Faces | People | Leaves | Max depth | Avg leaf | Largest leaf | Build (s) | Matches per query |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in builds:
        lines.append(
            f"| {b['faces']:,} | {b['people']:,} | {b['leaves']:,} | {b['max_depth']} | {b['average_leaf']} "
            f"| {b['largest_leaf']} | {b['build_seconds']} | {b['matches_per_query']} |"
        )
    lines += [
        "",
        "## Search",
        "",
        "| Faces | Strategy | p50 ms | p95 ms | Match recall | Worst query | Faces compared |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["compared_pct"] is not None:
            compared = f"{r['compared_pct']}%"
        else:
            compared = {"qdrant_exact": "100%", "qdrant_hnsw": "graph walk"}.get(r["strategy"], "leaves + graph walk" if r["strategy"] == "app_served" else "selected leaves")
        lines.append(
            f"| {r['faces']:,} | {r['strategy']} | {r['p50_ms']} | {r['p95_ms']} | "
            f"{_pct(r['match_recall'])} | {_pct(r['min_recall'])} | {compared} |"
        )
    path = RESULTS_DIR / "scale-benchmark.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _pct(value):
    return "–" if value is None else f"{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 100_000, 300_000])
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--qdrant-max", type=int, default=100_000, help="largest size also tested in Qdrant (0 = none)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    all_rows, builds = [], []
    for n_faces in args.sizes:
        rows, build = benchmark_size(n_faces, args.queries, args.seed, n_faces <= args.qdrant_max)
        all_rows += rows
        builds.append(build)
    print(f"\nWrote {write_report(all_rows, builds, args)}")


if __name__ == "__main__":
    main()
