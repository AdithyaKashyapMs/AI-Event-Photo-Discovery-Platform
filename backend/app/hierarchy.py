"""Adaptive hierarchical face index for an event.

Build (after each upload batch, in the Celery worker):
  Faces are clustered top-down with k-means. A group is split again only while
  it holds more than MAX_LEAF_SIZE faces, so dense regions of the face space
  get deeper trees and sparse regions stay shallow; the depth adapts to the
  data instead of being fixed in advance. Each face stores its leaf path
  ("identity_code", for example "2-0-3") in its Qdrant payload.

Query:
  1. The query is compared with every leaf centroid (one matrix product over
     centroids cached in memory).
  2. Branch selection adapts to the query: the closest leaves are searched,
     plus every leaf whose centroid is within BRANCH_SIMILARITY_MARGIN of the
     best one, so an ambiguous query searches more of the index and a clear
     one searches less (bounded by MAX_SEARCHED_FRACTION).
  3. One filtered Qdrant search runs over the selected leaves only.
  4. Optional recall pass: a full-index search merged into the results, so a
     face placed in an unselected leaf cannot be missed.

backend/scripts/benchmark_search.py measures the speed and recall of each
stage against exact search.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny

# Lowest similarity returned as a candidate. Hosts can lower the gallery
# filter down to this floor to inspect borderline results.
CANDIDATE_SIMILARITY_FLOOR = 0.30
# Default similarity a candidate needs before it is shown as a match.
MATCH_SIMILARITY_THRESHOLD = 0.55

# --- Build parameters
BRANCHING_FACTOR = 4
# A group with more faces than this is split again.
MAX_LEAF_SIZE = 32
MAX_DEPTH = 6

# --- Query parameters
MIN_BRANCHES = 2
# Also search leaves whose centroid similarity is within this margin of the best leaf.
BRANCH_SIMILARITY_MARGIN = 0.15
# Never search more than this fraction of the faces in the hierarchy pass.
MAX_SEARCHED_FRACTION = 0.5

RECALL_LIMIT = 500
SCROLL_PAGE_SIZE = 1000
# The cache also expires so a hierarchy rebuilt by the worker process (same
# face count, new codes) is picked up by the API process.
CACHE_TTL_SECONDS = 120


@dataclass
class _CachedHierarchy:
    points_count: int
    loaded_at: float
    codes: list[str]
    centroids: np.ndarray  # one normalized centroid per leaf, shape (leaves, dim)
    sizes: np.ndarray  # faces per leaf
    max_depth: int


_hierarchy_cache: dict[str, _CachedHierarchy] = {}


def forget_cached_hierarchy(collection_name: str) -> None:
    """Drop this process's cached hierarchy (for example after deleting it)."""
    _hierarchy_cache.pop(collection_name, None)


def scroll_all_points(
    client: QdrantClient,
    collection_name: str,
    with_vectors: bool = True,
) -> list[Any]:
    """Read every point in a collection, following Qdrant's pagination."""
    all_points: list[Any] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=SCROLL_PAGE_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )
        all_points.extend(points)
        if offset is None:
            return all_points


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def _kmeans(vectors: np.ndarray, k: int) -> np.ndarray:
    """Spherical k-means with deterministic initialisation."""
    centroids = vectors[np.linspace(0, len(vectors) - 1, k, dtype=int)].copy()
    assignments = np.zeros(len(vectors), dtype=int)
    for _ in range(20):
        assignments = np.argmax(vectors @ centroids.T, axis=1)
        updated = centroids.copy()
        for index in range(k):
            members = vectors[assignments == index]
            if len(members):
                updated[index] = members.mean(axis=0)
        updated = _normalize(updated)
        if np.allclose(updated, centroids, atol=1e-5):
            break
        centroids = updated
    return assignments


def _build_codes(
    vectors: np.ndarray,
    indices: np.ndarray,
    prefix: tuple[int, ...] = (),
) -> dict[int, str]:
    """Assign each face its leaf path, splitting only groups that are too large."""
    if len(indices) <= MAX_LEAF_SIZE or len(prefix) >= MAX_DEPTH:
        code = "-".join(map(str, prefix)) or "0"
        return {int(index): code for index in indices}

    assignments = _kmeans(vectors[indices], min(BRANCHING_FACTOR, len(indices)))
    if len(np.unique(assignments)) < 2:
        # k-means could not separate this group (near-identical faces): keep it as a leaf.
        code = "-".join(map(str, prefix)) or "0"
        return {int(index): code for index in indices}

    codes: dict[int, str] = {}
    for cluster_index in np.unique(assignments):
        members = indices[assignments == cluster_index]
        codes.update(_build_codes(vectors, members, (*prefix, int(cluster_index))))
    return codes


def rebuild_hierarchy(client: QdrantClient, collection_name: str) -> dict[str, int]:
    """Recluster every face in the collection and store each face's leaf path."""
    _hierarchy_cache.pop(collection_name, None)
    if not client.collection_exists(collection_name):
        return {"indexed_faces": 0, "branches": 0}

    points = scroll_all_points(client, collection_name)
    if not points:
        return {"indexed_faces": 0, "branches": 0}

    vectors = _normalize(np.asarray([point.vector for point in points], dtype=float))
    codes = _build_codes(vectors, np.arange(len(points)))
    points_by_code: dict[str, list[Any]] = defaultdict(list)
    for index, code in codes.items():
        points_by_code[code].append(points[index].id)

    for code, point_ids in points_by_code.items():
        client.set_payload(
            collection_name=collection_name,
            payload={"identity_code": code, "hierarchy_depth": code.count("-") + 1},
            points=point_ids,
            wait=True,
        )

    sizes = [len(ids) for ids in points_by_code.values()]
    return {
        "indexed_faces": len(points),
        "branches": len(points_by_code),
        "branching_factor": BRANCHING_FACTOR,
        "max_depth": max(code.count("-") + 1 for code in points_by_code),
        "largest_leaf": max(sizes),
        "average_leaf": round(len(points) / len(points_by_code), 1),
    }


def rebuild_event_hierarchy(client: QdrantClient, event_id: int) -> dict[str, int]:
    return rebuild_hierarchy(client, f"event_{event_id}_faces")


def _load_hierarchy(client: QdrantClient, collection_name: str) -> _CachedHierarchy | None:
    """Leaf centroids for the collection, or None while some faces have no leaf yet."""
    points = scroll_all_points(client, collection_name)
    groups: dict[str, list[np.ndarray]] = defaultdict(list)
    for point in points:
        code = point.payload.get("identity_code") if point.payload else None
        if not code:
            # New faces wait for the rebuild at the end of the upload batch.
            return None
        groups[code].append(np.asarray(point.vector, dtype=float))
    if not groups:
        return None
    codes = list(groups)
    centroids = _normalize(
        np.asarray([_normalize(np.asarray(groups[c])).mean(axis=0) for c in codes])
    )
    return _CachedHierarchy(
        points_count=len(points),
        loaded_at=time.monotonic(),
        codes=codes,
        centroids=centroids,
        sizes=np.asarray([len(groups[c]) for c in codes]),
        max_depth=max(code.count("-") + 1 for code in codes),
    )


def _get_hierarchy(
    client: QdrantClient, collection_name: str, use_cache: bool
) -> tuple[_CachedHierarchy | None, int]:
    points_count = client.get_collection(collection_name).points_count or 0
    cached = _hierarchy_cache.get(collection_name) if use_cache else None
    if (
        cached is not None
        and cached.points_count == points_count
        and time.monotonic() - cached.loaded_at < CACHE_TTL_SECONDS
    ):
        return cached, points_count
    hierarchy = _load_hierarchy(client, collection_name)
    if use_cache:
        if hierarchy is not None:
            _hierarchy_cache[collection_name] = hierarchy
        else:
            _hierarchy_cache.pop(collection_name, None)
    return hierarchy, points_count


def select_branches(hierarchy: _CachedHierarchy, query: np.ndarray) -> np.ndarray:
    """Indices of the leaves to search for this query (adaptive)."""
    similarities = hierarchy.centroids @ query
    order = np.argsort(-similarities)
    best = similarities[order[0]]
    budget = MAX_SEARCHED_FRACTION * hierarchy.sizes.sum()
    selected, covered = [], 0
    for leaf in order:
        if len(selected) >= MIN_BRANCHES and (
            similarities[leaf] < best - BRANCH_SIMILARITY_MARGIN or covered >= budget
        ):
            break
        selected.append(leaf)
        covered += hierarchy.sizes[leaf]
    return np.asarray(selected)


def hierarchical_query(
    client: QdrantClient,
    collection_name: str,
    query_embedding: list[float],
    limit: int = 100,
    use_cache: bool = False,
    recall_pass: bool = True,
) -> tuple[list[Any], dict[str, Any]]:
    """Search the adaptively selected leaves, then (by default) the full index.

    recall_pass=False returns the hierarchy pass alone, which is what the
    benchmark script measures as the hierarchy's own speed and recall.
    """
    hierarchy, points_count = _get_hierarchy(client, collection_name, use_cache)

    def full_search(search_limit: int) -> list[Any]:
        return client.query_points(
            collection_name=collection_name,
            query=query_embedding,
            score_threshold=CANDIDATE_SIMILARITY_FLOOR,
            limit=search_limit,
            with_payload=True,
        ).points

    if hierarchy is None:
        # Hierarchy not built yet (new uploads still processing): search everything.
        return full_search(limit), {
            "hierarchy_ready": False,
            "branches_considered": 0,
            "hierarchy_candidates": points_count,
            "faces_considered": points_count,
            "indexed_faces": points_count,
            "leaves": 0,
            "max_depth": 0,
        }

    query = _normalize(np.asarray([query_embedding], dtype=float))[0]
    selected = select_branches(hierarchy, query)
    hierarchy_candidates = int(hierarchy.sizes[selected].sum())
    results = client.query_points(
        collection_name=collection_name,
        query=query_embedding,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="identity_code",
                    match=MatchAny(any=[hierarchy.codes[leaf] for leaf in selected]),
                )
            ]
        ),
        score_threshold=CANDIDATE_SIMILARITY_FLOOR,
        limit=limit,
        with_payload=True,
    ).points

    if recall_pass:
        merged = {str(result.id): result for result in results}
        for result in full_search(max(limit, RECALL_LIMIT)):
            merged.setdefault(str(result.id), result)
        results = sorted(merged.values(), key=lambda result: result.score, reverse=True)[:limit]

    return results, {
        "hierarchy_ready": True,
        "branches_considered": len(selected),
        # Faces inside the selected leaves (the hierarchy pass).
        "hierarchy_candidates": hierarchy_candidates,
        # With the recall pass the full index is searched as well.
        "faces_considered": points_count if recall_pass else hierarchy_candidates,
        "indexed_faces": points_count,
        "leaves": len(hierarchy.codes),
        "max_depth": hierarchy.max_depth,
    }
