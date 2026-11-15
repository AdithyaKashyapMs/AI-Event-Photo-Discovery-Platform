"""Runtime settings, read once from environment variables.

Every default targets the local development setup described in the README.
"""

import os
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Photo storage. Resolved to an absolute path so it does not depend on the
# directory the API or the Celery worker was started from.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BACKEND_DIR / "uploads")).resolve()
THUMBNAIL_DIR = UPLOAD_DIR / ".thumbnails"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

# Public URL of the Next.js frontend, used in QR-code invite links.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """One shared Qdrant client per process. QDRANT_URL=:memory: runs an
    in-process store with no server (used by the test suite)."""
    if QDRANT_URL == ":memory:":
        return QdrantClient(location=":memory:")
    return QdrantClient(url=QDRANT_URL)


def face_collection_name(event_id: int) -> str:
    return f"event_{event_id}_faces"
