"""Server-side liveness verification for selfie search.

The browser guides the user through a random head-turn challenge and captures
one frame per step, but every decision is made here:

1. Challenge: random, bound to the user and event, single use, and expiring.
   It is stored in Redis and consumed atomically, so it cannot be replayed.
2. Pose: each frame must show exactly one face in the requested pose. The
   head turn is measured from the detector's five landmarks and cross-checked
   against the 3D pose estimate from insightface's landmark model.
3. Identity: every frame must show the same person as the first frame.
4. Anti-spoofing: insightface's passive liveness model must score the
   straight-on frames as a live face (not a printed photo or a screen).

Limitations: this is a challenge-response check plus a passive model, not a
certified presentation-attack-detection system. It stops static photos and
screenshots taken from social media. It cannot reliably stop a real-time
deepfake, a high-quality mask, or frames injected by a modified client.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import redis

from app.config import REDIS_URL

STEP_CENTER = "center"
STEP_LEFT = "turn_left"  # the user's own left
STEP_RIGHT = "turn_right"  # the user's own right
TURN_STEPS = (STEP_LEFT, STEP_RIGHT)

CHALLENGE_TTL_SECONDS = 120
# Each step must be held in the browser, so a real person cannot finish the
# challenge faster than this.
MIN_CHALLENGE_SECONDS = 2.0

# Head-turn ratio: how far the nose sits from the eye midpoint, measured along
# the eye line and divided by the eye distance. 0 is straight on; it grows by
# roughly 0.01 per degree of yaw. Positive means the user turned to their own
# left (the nose moves toward the right side of the unmirrored camera image).
CENTER_MAX_RATIO = 0.12
TURN_MIN_RATIO = 0.12
TURN_MIN_CHANGE_FROM_CENTER = 0.10
# Cross-check against insightface's 3D pose estimate (degrees).
CENTER_MAX_YAW_DEGREES = 15.0
TURN_MIN_YAW_DEGREES = 15.0

MIN_DETECTION_SCORE = 0.6
MIN_FACE_WIDTH_PX = 80
# A second face at least this large relative to the main face fails the check.
SECOND_FACE_AREA_RATIO = 0.25
# Same-person similarity between each frame and the first straight-on frame.
MIN_IDENTITY_SIMILARITY = 0.40
PASSIVE_LIVENESS_THRESHOLD = float(os.getenv("LIVENESS_THRESHOLD", "0.8"))

_REDIS_KEY_PREFIX = "liveness:challenge:"
_redis_client: redis.Redis | None = None


class LivenessError(Exception):
    """The selfie frames failed verification; the message is shown to the user."""


def liveness_required() -> bool:
    return os.getenv("LIVENESS_REQUIRED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL)
    return _redis_client


def _random_steps() -> list[str]:
    # Look straight, turn, look straight, turn. Returning to center between
    # turns means two turns in the same direction still require real movement.
    choice = secrets.SystemRandom().choice
    return [STEP_CENTER, choice(TURN_STEPS), STEP_CENTER, choice(TURN_STEPS)]


def create_challenge(user_id: int, event_id: int) -> dict[str, Any]:
    challenge_id = secrets.token_urlsafe(24)
    steps = _random_steps()
    record = {
        "user_id": user_id,
        "event_id": event_id,
        "steps": steps,
        "issued_at": time.time(),
    }
    _redis().set(
        _REDIS_KEY_PREFIX + challenge_id,
        json.dumps(record),
        ex=CHALLENGE_TTL_SECONDS,
    )
    return {
        "challenge_id": challenge_id,
        "steps": steps,
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


def consume_challenge(challenge_id: str, user_id: int, event_id: int) -> list[str]:
    """Atomically fetch and delete a challenge so it can be used only once."""
    raw = _redis().getdel(_REDIS_KEY_PREFIX + challenge_id)
    if raw is None:
        raise LivenessError(
            "The liveness check expired or was already used. Please try again."
        )
    record = json.loads(raw)
    if record["user_id"] != user_id or record["event_id"] != event_id:
        raise LivenessError("This liveness check belongs to a different session.")
    if time.time() - record["issued_at"] < MIN_CHALLENGE_SECONDS:
        raise LivenessError("The liveness check was completed too quickly.")
    return list(record["steps"])


def head_turn_ratio(kps: np.ndarray) -> float:
    """Signed head-turn ratio from the five detector landmarks.

    Measured along the eye line, so tilting the head sideways (roll) does not
    change it. Positive means the user turned to their own left.
    """
    points = np.asarray(kps, dtype=float)
    first, second = points[0], points[1]
    left_eye, right_eye = (first, second) if first[0] <= second[0] else (second, first)
    eye_vector = right_eye - left_eye
    eye_distance = float(np.linalg.norm(eye_vector))
    if eye_distance < 1e-6:
        return 0.0
    unit = eye_vector / eye_distance
    midpoint = (left_eye + right_eye) / 2
    return float((points[2] - midpoint) @ unit / eye_distance)


@dataclass
class FrameCheck:
    step: str
    ratio: float
    yaw: float | None
    live_score: float | None
    live_status: str
    embedding: np.ndarray
    det_score: float


def _main_face(faces: list[Any], step_number: int) -> Any:
    usable = [face for face in faces if float(face.det_score) >= MIN_DETECTION_SCORE]
    if not usable:
        raise LivenessError(f"No clear face was found in step {step_number}.")

    def area(face: Any) -> float:
        x1, y1, x2, y2 = face.bbox[:4]
        return max(0.0, float(x2 - x1)) * max(0.0, float(y2 - y1))

    usable.sort(key=area, reverse=True)
    main = usable[0]
    if len(usable) > 1 and area(usable[1]) >= SECOND_FACE_AREA_RATIO * area(main):
        raise LivenessError(
            f"More than one face was visible in step {step_number}. "
            "Only one person should be in front of the camera."
        )
    if float(main.bbox[2] - main.bbox[0]) < MIN_FACE_WIDTH_PX:
        raise LivenessError(
            f"Your face was too small in step {step_number}. Move closer to the camera."
        )
    if getattr(main, "embedding", None) is None:
        raise LivenessError(f"Your face could not be read in step {step_number}.")
    return main


def check_frame(faces: list[Any], step: str, step_number: int) -> FrameCheck:
    face = _main_face(faces, step_number)
    pose = getattr(face, "pose", None)
    yaw = float(pose[1]) if pose is not None else None
    liveness = getattr(face, "liveness", None) or {}
    return FrameCheck(
        step=step,
        ratio=head_turn_ratio(face.kps),
        yaw=yaw,
        live_score=liveness.get("live_score"),
        live_status=str(liveness.get("status", "unavailable")),
        embedding=np.asarray(face.embedding, dtype=float),
        det_score=float(face.det_score),
    )


def _step_label(step: str) -> str:
    return {
        STEP_CENTER: "look straight at the camera",
        STEP_LEFT: "turn your head to your left",
        STEP_RIGHT: "turn your head to your right",
    }[step]


def verify_frames(frames: list[FrameCheck]) -> dict[str, Any]:
    """Check pose, identity and passive liveness. Raises LivenessError."""
    centers = [frame for frame in frames if frame.step == STEP_CENTER]
    if not centers:
        raise LivenessError("The liveness check needs a straight-on frame.")

    # 1. Pose for each step.
    for number, frame in enumerate(frames, start=1):
        if frame.step == STEP_CENTER:
            yaw_ok = frame.yaw is None or abs(frame.yaw) <= CENTER_MAX_YAW_DEGREES
            if abs(frame.ratio) > CENTER_MAX_RATIO or not yaw_ok:
                raise LivenessError(
                    f"Step {number}: please {_step_label(frame.step)}."
                )
    center_ratio = float(np.mean([frame.ratio for frame in centers]))
    for number, frame in enumerate(frames, start=1):
        if frame.step == STEP_CENTER:
            continue
        direction = 1.0 if frame.step == STEP_LEFT else -1.0
        # The 3D pose yaw has the same sign as the ratio (checked on sample
        # images and their mirror images).
        yaw_ok = frame.yaw is None or direction * frame.yaw >= TURN_MIN_YAW_DEGREES
        if (
            direction * frame.ratio < TURN_MIN_RATIO
            or direction * (frame.ratio - center_ratio) < TURN_MIN_CHANGE_FROM_CENTER
            or not yaw_ok
        ):
            raise LivenessError(
                f"Step {number}: the head turn was not detected. "
                f"Please {_step_label(frame.step)} further."
            )

    # 2. The same person in every frame.
    def unit(vector: np.ndarray) -> np.ndarray:
        return vector / max(float(np.linalg.norm(vector)), 1e-12)

    reference = unit(centers[0].embedding)
    similarities = [float(unit(frame.embedding) @ reference) for frame in frames]
    if min(similarities) < MIN_IDENTITY_SIMILARITY:
        raise LivenessError(
            "The frames did not all show the same person. Please try again."
        )

    # 3. Passive anti-spoofing on the straight-on frames, where the model is
    # most reliable. Turned frames are reported but not scored.
    center_scores = []
    for frame in centers:
        if frame.live_status != "ok" or frame.live_score is None:
            raise LivenessError(
                "Keep your whole face inside the frame with some space around it."
            )
        center_scores.append(float(frame.live_score))
    live_score = float(np.mean(center_scores))
    if min(center_scores) < PASSIVE_LIVENESS_THRESHOLD:
        raise LivenessError(
            "Liveness check failed: this looks like a photo or a screen, "
            "not a live face. Use your camera directly with good lighting."
        )

    return {
        "passed": True,
        "method": "random head-turn challenge + passive anti-spoofing model",
        "steps": [frame.step for frame in frames],
        "live_score": round(live_score, 4),
        "live_threshold": PASSIVE_LIVENESS_THRESHOLD,
        "min_identity_similarity": round(min(similarities), 4),
        "head_turn_ratios": [round(frame.ratio, 3) for frame in frames],
    }
