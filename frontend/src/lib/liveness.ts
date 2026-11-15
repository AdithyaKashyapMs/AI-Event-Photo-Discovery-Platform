// Browser-side guidance for the selfie liveness check.
//
// This file only guides the user and decides when to capture each frame. The
// pass/fail decision is made by the backend (backend/app/liveness.py), which
// re-checks every frame. The thresholds here are stricter than the backend's
// so a frame accepted here comfortably passes there.

export type LivenessStep = 'center' | 'turn_left' | 'turn_right'

export type Point = { x: number; y: number }

export const STEP_LABELS: Record<LivenessStep, { title: string; instruction: string; arrow: string }> = {
  center: { title: 'Look straight', instruction: 'Look straight at the camera', arrow: '●' },
  // The preview is mirrored, so the user's own left is on the left of the screen.
  turn_left: { title: 'Turn left', instruction: 'Slowly turn your head to your left', arrow: '←' },
  turn_right: { title: 'Turn right', instruction: 'Slowly turn your head to your right', arrow: '→' },
}

// Head-turn ratio (see headTurnRatio) needed for each pose. Measured on the
// same faces, MediaPipe's ratio is 1.0–1.7x the backend detector's and always
// has the same sign, so 0.30 here is about 0.18+ on the backend (minimum 0.12)
// and 0.06 here stays well inside the backend's 0.12 straight-on limit.
export const CENTER_MAX_RATIO = 0.06
export const TURN_TARGET_RATIO = 0.3
// Ratio at which a straight-on pose starts to earn progress.
const CENTER_PROGRESS_START = 0.3
// How long a pose must be held before its frame is captured.
export const HOLD_MS = 600

// Eye distance relative to the shorter side of the video. Too small and the
// face is too far away; too large and the backend's anti-spoofing crop runs
// outside the image.
const MIN_EYE_DISTANCE = 0.09
const MAX_EYE_DISTANCE = 0.19

// MediaPipe Face Landmarker indices.
const NOSE_TIP = 1
const EYE_A = [33, 133] // outer and inner corner of one eye
const EYE_B = [362, 263] // inner and outer corner of the other eye

export type FaceAssessment =
  | { ok: true; ratio: number }
  | { ok: false; message: string; ratio?: number }

function toPixels(point: { x: number; y: number }, width: number, height: number): Point {
  // Landmarks are normalized separately on each axis; convert to pixels so
  // distances are correct for non-square video.
  return { x: point.x * width, y: point.y * height }
}

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
}

/**
 * Signed head turn: how far the nose sits from the eye midpoint, measured
 * along the eye line and divided by the eye distance. 0 is straight on, and it
 * grows by roughly 0.01 per degree. Positive means the user turned to their
 * own left. Landmarks come from the unmirrored camera frame, where the user's
 * left side is on the right of the image. Measuring along the eye line means
 * tilting the head sideways does not change the value.
 */
export function headTurnRatio(eyeOne: Point, eyeTwo: Point, nose: Point): number {
  const [left, right] = eyeOne.x <= eyeTwo.x ? [eyeOne, eyeTwo] : [eyeTwo, eyeOne]
  const ex = right.x - left.x
  const ey = right.y - left.y
  const distance = Math.hypot(ex, ey)
  if (distance < 1e-6) return 0
  const mid = midpoint(left, right)
  return ((nose.x - mid.x) * ex + (nose.y - mid.y) * ey) / (distance * distance)
}

/** Check that exactly one usable face is in view and measure its head turn. */
export function assessFace(
  faces: { x: number; y: number }[][],
  width: number,
  height: number,
): FaceAssessment {
  if (!faces.length) return { ok: false, message: 'No face detected — look at the camera' }
  if (faces.length > 1) return { ok: false, message: 'Only one person should be in view' }

  const landmarks = faces[0]
  const point = (index: number) => toPixels(landmarks[index], width, height)
  const eyeOne = midpoint(point(EYE_A[0]), point(EYE_A[1]))
  const eyeTwo = midpoint(point(EYE_B[0]), point(EYE_B[1]))
  const nose = point(NOSE_TIP)
  const ratio = headTurnRatio(eyeOne, eyeTwo, nose)

  const shortSide = Math.min(width, height)
  const eyeDistance = Math.hypot(eyeTwo.x - eyeOne.x, eyeTwo.y - eyeOne.y) / shortSide
  if (eyeDistance < MIN_EYE_DISTANCE) return { ok: false, message: 'Move closer to the camera', ratio }
  if (eyeDistance > MAX_EYE_DISTANCE) return { ok: false, message: 'Move a little further back', ratio }

  const faceCenter = midpoint(midpoint(eyeOne, eyeTwo), nose)
  const cx = faceCenter.x / width
  const cy = faceCenter.y / height
  if (cx < 0.3 || cx > 0.7 || cy < 0.25 || cy > 0.7) {
    return { ok: false, message: 'Center your face in the oval', ratio }
  }
  return { ok: true, ratio }
}

/** 0–1: how close the current head turn is to the pose the step asks for. */
export function poseProgress(step: LivenessStep, ratio: number): number {
  const clamp = (value: number) => Math.min(1, Math.max(0, value))
  if (step === 'center') {
    const magnitude = Math.abs(ratio)
    if (magnitude <= CENTER_MAX_RATIO) return 1
    return clamp((CENTER_PROGRESS_START - magnitude) / (CENTER_PROGRESS_START - CENTER_MAX_RATIO))
  }
  const direction = step === 'turn_left' ? 1 : -1
  return clamp((direction * ratio) / TURN_TARGET_RATIO)
}

/**
 * Progress through one step: 60% for reaching the pose, 40% for holding it.
 * Overall progress is (completed steps + this) / total steps.
 */
export function stepProgress(pose: number, heldMs: number): number {
  const hold = Math.min(1, heldMs / HOLD_MS)
  return pose >= 1 ? 0.6 + 0.4 * hold : 0.6 * pose
}
