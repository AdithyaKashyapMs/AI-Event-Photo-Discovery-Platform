"""Fixed-window rate limits stored in Redis.

Each rule counts requests per key (client IP, user ID, or email) in a window
of `window_seconds`. Over the limit the request gets 429 with Retry-After.

If Redis is unreachable the limit is skipped (fail open) and a warning is
logged: losing rate limiting briefly is better than locking every user out.
Set RATE_LIMIT_ENABLED=false to switch limits off (for example in load tests).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import redis
from fastapi import HTTPException, Request

from app.config import REDIS_URL

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ratelimit:"
_redis_client: redis.Redis | None = None


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_seconds: int


# Login: slows password guessing for one account and from one address.
LOGIN_PER_EMAIL = Rule("login:email", 10, 15 * 60)
LOGIN_PER_IP = Rule("login:ip", 30, 15 * 60)
REGISTER_PER_IP = Rule("register:ip", 10, 60 * 60)
# Invite lookups: tokens are random, but there is no reason to allow probing.
INVITE_PER_IP = Rule("invite:ip", 30, 60)
# Selfie search runs face recognition, the most expensive request in the API.
LIVENESS_CHALLENGE_PER_USER = Rule("liveness:user", 10, 60)
SELFIE_MATCH_PER_USER = Rule("selfie:user", 6, 60)


def rate_limits_enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
    return _redis_client


def set_redis_client(client) -> None:
    """Replace the Redis client (used by tests)."""
    global _redis_client
    _redis_client = client


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers and
    # --forwarded-allow-ips so request.client is the real client address.
    return request.client.host if request.client else "unknown"


def hit(rule: Rule, key: str) -> None:
    """Count one request for `key`; raise 429 once the rule's limit is passed."""
    if not rate_limits_enabled():
        return
    redis_key = f"{_KEY_PREFIX}{rule.name}:{key}"
    try:
        pipe = _redis().pipeline()
        # Start the window on the first request only, then count.
        pipe.set(redis_key, 0, ex=rule.window_seconds, nx=True)
        pipe.incr(redis_key)
        pipe.ttl(redis_key)
        _, count, ttl = pipe.execute()
    except redis.RedisError:
        logger.warning("Rate limit %s skipped: Redis is unavailable", rule.name)
        return
    if int(count) > rule.limit:
        retry_after = max(int(ttl), 1)
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Please try again in {_human(retry_after)}.",
            headers={"Retry-After": str(retry_after)},
        )


def _human(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = -(-seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
