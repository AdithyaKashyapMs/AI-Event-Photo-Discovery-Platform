from collections import defaultdict
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue


MAX_CANDIDATE_BRANCHES = 3
MIN_FACE_SIMILARITY = 0.55
_hierarchy_cache: dict[str, tuple[int, list[Any], dict[str, list[Any]], int, int]] = {}


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def _kmeans(vectors: np.ndarray, k: int) -> np.ndarray:
    centroids = vectors[np.linspace(0, len(vectors) - 1, k, dtype=int)].copy()
    for _ in range(20):
        similarities = vectors @ centroids.T
        assignments = np.argmax(similarities, axis=1)
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


def _hierarchy_config(vector_count: int) -> tuple[int, int]:
    if vector_count < 32:
        return 2, 2
    if vector_count < 256:
        return 4, 3
    return 8, 4


def _build_codes(
    vectors: np.ndarray,
    indices: np.ndarray,
    branching_factor: int,
    max_depth: int,
    prefix: tuple[int, ...] = (),
    depth: int = 0,
) -> dict[int, str]:
    if len(indices) <= 1 or depth >= max_depth:
        code = "-".join(map(str, prefix)) or "0"
        return {int(index): code for index in indices}

    k = min(branching_factor, len(indices))
    assignments = _kmeans(vectors[indices], k)
    codes: dict[int, str] = {}
    for cluster_index in range(k):
        members = indices[assignments == cluster_index]
        if len(members):
            codes.update(
                _build_codes(
                    vectors,
                    members,
                    branching_factor,
                    max_depth,
                    (*prefix, cluster_index),
                    depth + 1,
                )
            )
    return codes


def rebuild_event_hierarchy(client: QdrantClient, event_id: int) -> dict[str, int]:
    collection_name = f"event_{event_id}_faces"
    _hierarchy_cache.pop(collection_name, None)
    if not client.collection_exists(collection_name):
        return {"indexed_faces": 0, "branches": 0}

    points, _ = client.scroll(
        collection_name=collection_name,
        limit=10000,
        with_payload=True,
        with_vectors=True,
    )
    if not points:
        return {"indexed_faces": 0, "branches": 0}

    vectors = _normalize(np.asarray([point.vector for point in points], dtype=float))
    branching_factor, max_depth = _hierarchy_config(len(points))
    codes = _build_codes(
        vectors,
        np.arange(len(points)),
        branching_factor,
        max_depth,
    )
    points_by_code: dict[str, list[str]] = defaultdict(list)
    for index, code in codes.items():
        points_by_code[code].append(str(points[index].id))

    for code, point_ids in points_by_code.items():
        client.set_payload(
            collection_name=collection_name,
            payload={"identity_code": code, "hierarchy_depth": code.count("-") + 1},
            points=point_ids,
            wait=True,
        )

    return {
        "indexed_faces": len(points),
        "branches": len(points_by_code),
        "branching_factor": branching_factor,
        "max_depth": max_depth,
    }


def hierarchical_query(
    client: QdrantClient,
    collection_name: str,
    query_embedding: list[float],
    limit: int = 100,
    use_cache: bool = False,
) -> tuple[list[Any], dict[str, int]]:
    cached = _hierarchy_cache.get(collection_name) if use_cache else None
    if cached is not None:
        if client.get_collection(collection_name).points_count != cached[0]:
            cached = None
    if cached is None:
        points, _ = client.scroll(
            collection_name=collection_name,
            limit=10000,
            with_payload=True,
            with_vectors=True,
        )
        branching_factor, max_depth = _hierarchy_config(len(points))
        branches: dict[str, list[Any]] = defaultdict(list)
        for point in points:
            code = point.payload.get("identity_code") if point.payload else None
            if code:
                branches[code].append(point)
        if use_cache:
            _hierarchy_cache[collection_name] = (
                len(points),
                points,
                dict(branches),
                branching_factor,
                max_depth,
            )
    else:
        _, points, branches, branching_factor, max_depth = cached

    if not branches:
        results = client.query_points(
            collection_name=collection_name,
            query=query_embedding,
            score_threshold=MIN_FACE_SIMILARITY,
            limit=limit,
            with_payload=True,
        ).points
        return results, {
            "branches_considered": 0,
            "faces_considered": len(points),
            "indexed_faces": len(points),
            "branching_factor": branching_factor,
            "max_depth": max_depth,
        }

    query = _normalize(np.asarray([query_embedding], dtype=float))[0]
    branch_scores = []
    for code, branch_points in branches.items():
        centroid = _normalize(
            np.asarray([point.vector for point in branch_points], dtype=float).mean(
                axis=0, keepdims=True
            )
        )[0]
        branch_scores.append((float(query @ centroid), code))

    selected_codes = [
        code
        for _, code in sorted(branch_scores, reverse=True)[
            : min(MAX_CANDIDATE_BRANCHES, len(branch_scores))
        ]
    ]
    results = []
    for code in selected_codes:
        results.extend(
            client.query_points(
                collection_name=collection_name,
                query=query_embedding,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="identity_code",
                            match=MatchValue(value=code),
                        )
                    ]
                ),
                score_threshold=MIN_FACE_SIMILARITY,
                limit=limit,
                with_payload=True,
            ).points
        )
    results.sort(key=lambda result: result.score, reverse=True)
    return results[:limit], {
        "branches_considered": len(selected_codes),
        "faces_considered": sum(len(branches[code]) for code in selected_codes),
        "indexed_faces": len(points),
        "branching_factor": branching_factor,
        "max_depth": max_depth,
    }
