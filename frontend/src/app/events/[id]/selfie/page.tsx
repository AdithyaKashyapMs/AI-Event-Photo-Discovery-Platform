'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import type { FaceLandmarker } from '@mediapipe/tasks-vision'
import { API_BASE, errorMessage, hasSession, redirectToLogin } from '@/lib/api'
import PrivacyNotice from '@/components/PrivacyNotice'
import Snappy, { type SnappyPose } from '@/components/Snappy'
import {
  HOLD_MS,
  STEP_LABELS,
  assessFace,
  poseProgress,
  stepProgress,
  type LivenessStep,
} from '@/lib/liveness'

type Phase = 'loading' | 'ready' | 'starting' | 'running' | 'verifying' | 'failed'

type Challenge = { challenge_id: string; steps: LivenessStep[]; expires_in: number }

// Keep in sync with the installed @mediapipe/tasks-vision version.
const MEDIAPIPE_WASM_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.1/wasm'
const FACE_MODEL_URL = '/models/face_landmarker.task'
// Finish well before the server-side challenge expires.
const CHALLENGE_SAFETY_MARGIN_S = 15

// MediaPipe's WebAssembly runtime writes its routine info and warning logs
// (for example "INFO: Created TensorFlow Lite XNNPACK delegate for CPU") to
// console.error, which the Next.js dev overlay reports as errors. Send those
// glog-style lines to console.debug; real errors (E-prefixed) still reach
// console.error.
let mediaPipeLogFilterInstalled = false
function routeMediaPipeInfoLogsToDebug() {
  if (mediaPipeLogFilterInstalled) return
  mediaPipeLogFilterInstalled = true
  const originalError = console.error.bind(console)
  console.error = (...args: unknown[]) => {
    const first = args[0]
    if (typeof first === 'string' && /^(INFO: |[IW]\d{4} \d\d:\d\d:\d\d)/.test(first)) {
      console.debug(...args)
      return
    }
    originalError(...args)
  }
}
// MediaPipe binds console.error when a tracker is created, so install the
// filter as soon as this page's code loads, before any tracker exists.
if (typeof window !== 'undefined') routeMediaPipeInfoLogsToDebug()

// One shared face tracker for the whole app. React's development mode mounts
// the page twice, and closing a freshly loaded tracker on the first unmount
// just to load another one is slow and noisy, so it is loaded once and reused.
let faceLandmarkerPromise: Promise<FaceLandmarker> | null = null
function getFaceLandmarker(): Promise<FaceLandmarker> {
  faceLandmarkerPromise ??= loadFaceLandmarker().catch((error) => {
    faceLandmarkerPromise = null
    throw error
  })
  return faceLandmarkerPromise
}

async function loadFaceLandmarker(): Promise<FaceLandmarker> {
  routeMediaPipeInfoLogsToDebug()
  const { FaceLandmarker, FilesetResolver } = await import('@mediapipe/tasks-vision')
  const fileset = await FilesetResolver.forVisionTasks(MEDIAPIPE_WASM_URL)
  const create = (delegate: 'GPU' | 'CPU') =>
    FaceLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: FACE_MODEL_URL, delegate },
      runningMode: 'VIDEO',
      // Track two faces so a second person in view can be reported.
      numFaces: 2,
    })
  try {
    return await create('GPU')
  } catch {
    return await create('CPU')
  }
}

// Selfie page: a live head-turn check guards the search, so a photo taken from
// social media cannot be used to find someone else's photos.
export default function SelfiePage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const eventId = params.id

  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const landmarkerRef = useRef<FaceLandmarker | null>(null)
  const frameRequestRef = useRef<number | null>(null)

  // Per-challenge tracking state, kept in refs so the 30–60 fps loop does not
  // re-render the page on every frame.
  const challengeRef = useRef<Challenge | null>(null)
  const stepIndexRef = useRef(0)
  const holdStartRef = useRef<number | null>(null)
  const capturesRef = useRef<Promise<Blob>[]>([])
  const deadlineRef = useRef(0)
  const lastVideoTimeRef = useRef(-1)
  const lastTimestampRef = useRef(0)

  const [phase, setPhase] = useState<Phase>('loading')
  const [loadingMessage, setLoadingMessage] = useState('Starting camera…')
  const [steps, setSteps] = useState<LivenessStep[]>([])
  const [stepIndex, setStepIndex] = useState(0)
  const [percent, setPercent] = useState(0)
  const [hint, setHint] = useState('')
  const [faceOk, setFaceOk] = useState(false)
  const [error, setError] = useState('')
  const [fatal, setFatal] = useState(false)

  const stopLoop = useCallback(() => {
    if (frameRequestRef.current !== null) {
      cancelAnimationFrame(frameRequestRef.current)
      frameRequestRef.current = null
    }
  }, [])

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }, [])

  // Start the camera and load the face tracker once.
  useEffect(() => {
    let disposed = false

    const startCamera = async () => {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 640, max: 1280 }, height: { ideal: 480, max: 720 } },
        audio: false,
      })
      if (disposed) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const video = videoRef.current
      if (!video) return
      video.srcObject = stream
      await video.play()
    }

    const init = async () => {
      if (!hasSession()) {
        redirectToLogin()
        return
      }
      try {
        await startCamera()
      } catch {
        if (!disposed) {
          setFatal(true)
          setError(
            'Camera access is required. A live camera check makes sure only you can search for your photos. ' +
              'Allow camera access in your browser settings and reload the page.',
          )
        }
        return
      }
      try {
        if (!disposed) setLoadingMessage('Loading face tracker…')
        const landmarker = await getFaceLandmarker()
        if (disposed) return
        landmarkerRef.current = landmarker
        setPhase('ready')
      } catch {
        if (!disposed) {
          setFatal(true)
          setError('Could not load the face tracker. Check your internet connection and reload the page.')
        }
      }
    }

    void init()
    return () => {
      disposed = true
      stopLoop()
      stopCamera()
      // The shared tracker is kept for the next visit, not closed.
      landmarkerRef.current = null
    }
  }, [stopCamera, stopLoop])

  // Capture the current video frame at full resolution (unmirrored).
  const captureFrame = (): Promise<Blob> => {
    const video = videoRef.current
    const canvas = document.createElement('canvas')
    if (!video) return Promise.reject(new Error('Camera is not available'))
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
    return new Promise((resolve, reject) =>
      canvas.toBlob(
        (blob) => (blob && blob.size ? resolve(blob) : reject(new Error('Could not capture a frame'))),
        'image/jpeg',
        0.92,
      ),
    )
  }

  const failChallenge = useCallback(
    (message: string) => {
      stopLoop()
      challengeRef.current = null
      setPhase('failed')
      setFaceOk(false)
      setError(message)
    },
    [stopLoop],
  )

  // Send the captured frames; the server verifies liveness and then matches.
  const submitFrames = async (challenge: Challenge) => {
    setPhase('verifying')
    setHint('')
    try {
      const blobs = await Promise.all(capturesRef.current)
      const formData = new FormData()
      blobs.forEach((blob, index) => formData.append('files', blob, `liveness-${index + 1}.jpg`))
      formData.append('challenge_id', challenge.challenge_id)

      const res = await fetch(`${API_BASE}/events/${eventId}/match-selfie`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(errorMessage(data, 'Failed to match selfie'))
      }

      stopCamera()
      // Matched photos are saved on the server; the URL only carries what the
      // gallery shows about this search.
      const query = new URLSearchParams({
        searched: '1',
        total: String(data.total || 0),
        floor: String(data.search?.candidate_floor || 0),
        threshold: String(data.search?.similarity_threshold || 0),
        noMatchReason: String(data.search?.no_match_reason || ''),
        liveScore: String(data.liveness?.live_score || 0),
      })
      router.push(`/events/${eventId}/gallery?${query.toString()}`)
    } catch (err) {
      failChallenge(err instanceof Error ? err.message : 'Failed to match selfie')
    }
  }

  // Per-frame tracking loop: measure the head pose, update the running
  // percentage and capture a frame once each pose has been held.
  const trackFrame = () => {
    frameRequestRef.current = requestAnimationFrame(trackFrame)
    const video = videoRef.current
    const landmarker = landmarkerRef.current
    const challenge = challengeRef.current
    if (!video || !landmarker || !challenge) return
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth) return
    // Only analyse new video frames.
    if (video.currentTime === lastVideoTimeRef.current) return
    lastVideoTimeRef.current = video.currentTime

    const now = performance.now()
    if (now > deadlineRef.current) {
      failChallenge('Time ran out. Please start the check again.')
      return
    }
    // MediaPipe requires strictly increasing timestamps.
    const timestamp = Math.max(now, lastTimestampRef.current + 1)
    lastTimestampRef.current = timestamp

    const result = landmarker.detectForVideo(video, timestamp)
    const assessment = assessFace(result.faceLandmarks, video.videoWidth, video.videoHeight)
    const index = stepIndexRef.current
    const step = challenge.steps[index]
    const total = challenge.steps.length

    let progress = 0
    if (assessment.ok) {
      const pose = poseProgress(step, assessment.ratio)
      if (pose >= 1) {
        holdStartRef.current ??= now
      } else {
        holdStartRef.current = null
      }
      const held = holdStartRef.current === null ? 0 : now - holdStartRef.current
      progress = stepProgress(pose, held)
      setFaceOk(true)
      setHint(pose >= 1 ? 'Hold still…' : step === 'center' ? 'Face the camera directly' : 'Keep turning…')

      if (held >= HOLD_MS) {
        capturesRef.current.push(captureFrame())
        holdStartRef.current = null
        const next = index + 1
        stepIndexRef.current = next
        setPercent(Math.round((next / total) * 100))
        if (next >= total) {
          stopLoop()
          challengeRef.current = null
          void submitFrames(challenge)
          return
        }
        setStepIndex(next)
        return
      }
    } else {
      holdStartRef.current = null
      setFaceOk(false)
      setHint(assessment.message)
    }
    setPercent(Math.round(((index + progress) / total) * 100))
  }

  const startCheck = async () => {
    setError('')
    setPhase('starting')
    try {
      const res = await fetch(`${API_BASE}/events/${eventId}/liveness/challenge`, {
        method: 'POST',
        credentials: 'include',
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(errorMessage(data, 'Could not start the check'))
      const challenge = data as Challenge

      challengeRef.current = challenge
      stepIndexRef.current = 0
      holdStartRef.current = null
      capturesRef.current = []
      lastVideoTimeRef.current = -1
      deadlineRef.current = performance.now() + (challenge.expires_in - CHALLENGE_SAFETY_MARGIN_S) * 1000
      setSteps(challenge.steps)
      setStepIndex(0)
      setPercent(0)
      setHint('')
      setPhase('running')
      stopLoop()
      frameRequestRef.current = requestAnimationFrame(trackFrame)
    } catch (err) {
      failChallenge(err instanceof Error ? err.message : 'Could not start the check')
    }
  }

  const running = phase === 'running'
  const currentStep = steps[stepIndex]
  const ovalColor = running ? (faceOk ? 'border-mint' : 'border-sun') : 'border-white/80'

  // The coach mascot acts out what is happening.
  const coach: { pose: SnappyPose; block: string; line: string } =
    fatal || phase === 'failed'
      ? { pose: 'sad', block: 'block-tomato', line: 'Oops! Let’s try that again.' }
      : phase === 'verifying'
        ? { pose: 'search', block: 'block-sky', line: 'Checking it’s really you… then finding your photos!' }
        : running && currentStep
          ? {
              pose: 'selfie',
              block: 'block-sun',
              line: faceOk ? STEP_LABELS[currentStep].instruction : hint || 'Show me your face!',
            }
          : phase === 'loading'
            ? { pose: 'camera', block: 'block-sky', line: 'Getting the camera ready…' }
            : { pose: 'wave', block: 'block-sun', line: 'Ready? Look at the camera and press start!' }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="font-display text-4xl font-extrabold">Find your photos</h1>
        <p className="mt-1 font-semibold text-ink-soft">
          A 10-second live check proves it&apos;s really you, then we find you in every event photo.
        </p>
      </div>

      {error && (
        <div className="alert alert-error mb-5" role="alert">
          {error}
        </div>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[1.5fr_1fr]">
        {!fatal && (
          <div className="sticker-lg overflow-hidden">
            {/* Camera preview, mirrored like a mirror; captured frames are not mirrored. */}
            <div className="relative aspect-[4/3] overflow-hidden border-b-[3px] border-ink bg-ink">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="h-full w-full object-cover"
                style={{ transform: 'scaleX(-1)' }}
              />
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div className={`h-[70%] w-[42%] rounded-[50%] border-[5px] border-dashed transition-colors ${ovalColor}`} />
              </div>
              {running && currentStep && (
                <div className="absolute inset-x-0 top-3 flex justify-center px-3">
                  <span className="chip pop-in bg-white px-4 py-1.5 text-base" key={stepIndex}>
                    <span className="text-xl leading-none">{STEP_LABELS[currentStep].arrow}</span>
                    {STEP_LABELS[currentStep].instruction}
                  </span>
                </div>
              )}
              {phase === 'loading' && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 font-display text-lg font-bold text-white">
                  <Snappy pose="camera" size={90} />
                  {loadingMessage}
                </div>
              )}
            </div>

            <div className="space-y-4 p-5">
              {/* Running percentage below the camera */}
              {(running || phase === 'verifying') && (
                <div aria-live="polite">
                  <div className="mb-2 flex items-baseline justify-between">
                    <span className="font-display text-lg font-extrabold">
                      {phase === 'verifying' ? 'Verifying on the server…' : 'Liveness check'}
                    </span>
                    <span className="font-display text-4xl font-extrabold tabular-nums text-tomato">{percent}%</span>
                  </div>
                  <div
                    className="h-7 w-full overflow-hidden rounded-full border-[2.5px] border-ink bg-white"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={percent}
                  >
                    <div
                      className={`h-full border-r-[2.5px] border-ink transition-[width] duration-150 ${
                        phase === 'verifying' ? 'animate-pulse bg-mint' : 'bg-sun'
                      }`}
                      style={{ width: `${percent}%`, borderRightWidth: percent > 0 && percent < 100 ? undefined : 0 }}
                    />
                  </div>
                  {running && hint && (
                    <p className={`mt-2 text-sm font-bold ${faceOk ? 'text-ink-soft' : 'text-tomato'}`}>{hint}</p>
                  )}
                </div>
              )}

              {steps.length > 0 && (running || phase === 'verifying') && (
                <ol className="grid grid-cols-4 gap-2 text-center text-xs">
                  {steps.map((step, index) => {
                    const done = index < stepIndex || phase === 'verifying'
                    const active = index === stepIndex && running
                    return (
                      <li
                        key={index}
                        className={`rounded-xl border-[2.5px] border-ink px-1 py-2 font-extrabold ${
                          done ? 'bg-mint text-white' : active ? 'bg-sun shadow-[3px_3px_0_var(--ink)]' : 'bg-white text-ink-soft'
                        }`}
                      >
                        <span className="block text-lg leading-none">{done ? '✓' : STEP_LABELS[step].arrow}</span>
                        <span className="mt-1 block">{STEP_LABELS[step].title}</span>
                      </li>
                    )
                  })}
                </ol>
              )}

              {(phase === 'ready' || phase === 'failed' || phase === 'starting' || phase === 'loading') && (
                <div className="space-y-3">
                  <button
                    onClick={startCheck}
                    disabled={phase === 'starting' || phase === 'loading'}
                    className="btn btn-primary btn-lg btn-block"
                  >
                    {phase === 'starting'
                      ? 'Starting…'
                      : phase === 'loading'
                        ? 'Getting ready…'
                        : phase === 'failed'
                          ? 'Try again'
                          : 'Start live check'}
                  </button>
                  <p className="text-center text-sm font-semibold text-ink-soft">
                    Look straight, then turn your head when asked. All angles are combined to match you in more
                    poses.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Coach panel */}
        <aside className="space-y-4">
          <div className={`sticker-lg ${coach.block} flex flex-col items-center gap-3 p-6`}>
            <div className="bubble pop-in self-stretch text-center" key={coach.line}>
              {coach.line}
            </div>
            <Snappy pose={coach.pose} size={170} />
          </div>
          <div className="sticker p-4">
            <h2 className="font-display text-lg font-extrabold">Tips for a quick check</h2>
            <ul className="mt-2 space-y-1.5 text-sm font-semibold text-ink-soft">
              <li>💡 Face a light; avoid a bright window behind you.</li>
              <li>🙂 Keep your whole face inside the oval.</li>
              <li>👤 Only you in the frame.</li>
              <li>🐢 Turn slowly and hold each pose a moment.</li>
            </ul>
          </div>
          <PrivacyNotice />
        </aside>
      </div>
    </div>
  )
}
