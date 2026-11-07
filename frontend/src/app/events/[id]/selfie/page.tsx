'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { API_BASE } from '@/lib/api'

// Selfie capture page with camera access and file upload fallback
export default function SelfiePage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const eventId = params.id

  // Camera and capture state
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const startingRef = useRef(false)
  const [cameraActive, setCameraActive] = useState(false)
  const [cameraStarting, setCameraStarting] = useState(false)
  const [capturedImages, setCapturedImages] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [cameraError, setCameraError] = useState(false)

  // Get auth token from cookie
  const getToken = () => {
    const match = document.cookie.match(/token=([^;]+)/)
    return match ? match[1] : ''
  }

  // Start the camera stream using getUserMedia
  const startCamera = useCallback(async () => {
    if (startingRef.current || streamRef.current) return
    startingRef.current = true
    setCameraStarting(true)
    setError('')
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 640, max: 1280 }, height: { ideal: 480, max: 720 } },
        audio: false
      })
      const video = videoRef.current
      if (!video) {
        mediaStream.getTracks().forEach(track => track.stop())
        return
      }
      video.srcObject = mediaStream
      await video.play()
      streamRef.current = mediaStream
      setCameraActive(true)
      setCameraError(false)
    } catch (err) {
      // Camera not available, show file upload fallback
      setCameraError(true)
      setError('Camera access denied or unavailable. Use file upload instead.')
    } finally {
      startingRef.current = false
      setCameraStarting(false)
    }
  }, [])

  useEffect(() => {
    void startCamera()
    return () => {
      const activeStream = streamRef.current
      activeStream?.getTracks().forEach(track => track.stop())
      streamRef.current = null
      if (videoRef.current) videoRef.current.srcObject = null
    }
  }, [])

  // Capture a still frame from the video stream
  const capturePhoto = () => {
    if (!videoRef.current || !canvasRef.current) return

    const video = videoRef.current
    const canvas = canvasRef.current
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth || !video.videoHeight) {
      setError('Camera is still starting. Please wait a moment and try again.')
      return
    }

    canvas.width = video.videoWidth
    canvas.height = video.videoHeight

    // Draw the current video frame to the canvas
    const ctx = canvas.getContext('2d')
    if (ctx) {
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
      const dataUrl = canvas.toDataURL('image/jpeg', 0.8)
      if (dataUrl.length > 'data:image/jpeg;base64,'.length) {
        setCapturedImages([dataUrl])
      } else {
        setError('Could not capture an image. Please try again.')
      }
    }

    // Stop the camera stream after capture
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop())
      streamRef.current = null
      video.srcObject = null
      setCameraActive(false)
    }
  }

  // Handle file upload as fallback
  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (!files.length) return
    Promise.all(files.map((file) => new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onloadend = () => resolve(reader.result as string)
      reader.onerror = reject
      reader.readAsDataURL(file)
    }))).then(setCapturedImages).catch(() => setError('Could not read the selected selfies.'))
  }

  // Convert base64 data URL to a File object for upload
  const dataUrlToFile = (dataUrl: string, filename: string): File => {
    const arr = dataUrl.split(',')
    const mime = arr[0].match(/:(.*?);/)?.[1] || 'image/jpeg'
    const bstr = atob(arr[1])
    let n = bstr.length
    const u8arr = new Uint8Array(n)
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n)
    }
    return new File([u8arr], filename, { type: mime })
  }

  // Submit the selfie to the match endpoint
  const submitSelfie = async () => {
    if (!capturedImages.length) return
    setUploading(true)
    setError('')

    try {
      const files = capturedImages.map((image, index) =>
        dataUrlToFile(image, `selfie-${index + 1}.jpg`),
      )
      if (files.some((selfieFile) => !selfieFile.size)) {
        throw new Error('One of the selfie images is empty. Please choose another image.')
      }
      const formData = new FormData()
      files.forEach((selfieFile) => formData.append('files', selfieFile))

      // POST to the match-selfie endpoint
      const res = await fetch(
        `${API_BASE}/events/${eventId}/match-selfie`,
        {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${getToken()}` },
          body: formData
        }
      )

      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || 'Failed to match selfie')
      }

      const data = await res.json()
      const matchedPhotoIds = (data.matches || [])
        .map((match: { photo_id: number }) => match.photo_id)
        .join(',')
      const matchedScores = (data.matches || [])
        .map((match: { photo_id: number; confidence: number }) =>
          `${match.photo_id}:${match.confidence}`,
        )
        .join(',')
      const params = new URLSearchParams({
        matches: matchedPhotoIds,
        scores: matchedScores,
        total: String(data.total || 0),
        branches: String(data.search?.branches_considered || 0),
        candidates: String(data.search?.faces_considered || 0),
        indexed: String(data.search?.indexed_faces || 0),
        reduction: String(data.search?.reduction_percent || 0),
        latency: String(data.search?.latency_ms || 0),
        threshold: String(data.search?.similarity_threshold || 0),
        raw: String(data.search?.raw_results || 0),
        unique: String(data.search?.unique_photos || 0),
        best: String(data.search?.best_similarity || 0),
        average: String(data.search?.average_similarity || 0),
        noMatchReason: String(data.search?.no_match_reason || ''),
        researchLatency: String(data.search?.comparison?.research_hierarchy?.latency_ms || 0),
        researchCandidates: String(data.search?.comparison?.research_hierarchy?.candidates || 0),
        projectLatency: String(data.search?.comparison?.our_project?.latency_ms || 0),
        projectCandidates: String(data.search?.comparison?.our_project?.candidates || 0),
        selfies: String(data.selfies_processed || 1),
        branching: String(data.search?.branching_factor || 0),
        depth: String(data.search?.max_depth || 0),
      })
      router.push(`/events/${eventId}/gallery?${params.toString()}`)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  // Reset to retake the selfie
  const retake = () => {
    setCapturedImages([])
    if (!cameraError) {
      startCamera()
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-4">
      <div className="max-w-lg w-full bg-white rounded-lg shadow-md p-6">
        <h1 className="text-2xl font-bold text-center mb-2">Find Your Photos</h1>
        <p className="text-gray-600 text-center mb-6">
          Take a selfie or upload a photo so we can find you in event photos.
        </p>

        {error && (
          <div className="bg-red-50 text-red-700 p-3 rounded mb-4 text-sm">
            {error}
          </div>
        )}

        {/* Camera preview or captured image */}
        {!capturedImages.length ? (
          <div className="space-y-4">
            <div className={`relative ${cameraActive ? '' : 'hidden'}`}>
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="w-full rounded-lg bg-black"
              />
              {cameraActive && (
                <button
                  onClick={capturePhoto}
                  className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-white rounded-full w-16 h-16 border-4 border-blue-600 hover:border-blue-700"
                  aria-label="Capture photo"
                />
              )}
            </div>

            {!cameraActive && (
              <div className="space-y-3">
                {!cameraError && (
                  <button
                    onClick={startCamera}
                    className="w-full bg-blue-600 text-white py-3 rounded-md hover:bg-blue-700"
                  >
                    {cameraStarting ? 'Starting camera...' : 'Open Camera'}
                  </button>
                )}

                {/* File upload fallback */}
                <div className="text-center">
                  <p className="text-sm text-gray-500 mb-2">
                    {cameraError ? 'Upload a photo instead:' : 'Or upload one or more selfies:'}
                  </p>
                  <label className="cursor-pointer inline-block bg-gray-100 border border-gray-300 rounded-md px-4 py-2 text-sm hover:bg-gray-200">
                    Choose File
                    <input
                      type="file"
                      accept="image/*"
                      capture="user"
                      multiple
                      onChange={handleFileUpload}
                      className="hidden"
                    />
                  </label>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2">
              {capturedImages.map((image, index) => (
                <img key={image} src={image} alt={`Selfie ${index + 1}`} className="w-full rounded-lg" />
              ))}
            </div>
            <p className="text-sm text-gray-500">
              {capturedImages.length} selfie{capturedImages.length === 1 ? '' : 's'} will be fused for matching.
            </p>
            <div className="flex gap-3">
              <button
                onClick={retake}
                className="flex-1 border border-gray-300 py-2 rounded-md hover:bg-gray-50"
              >
                Retake
              </button>
              <button
                onClick={submitSelfie}
                disabled={uploading}
                className="flex-1 bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {uploading ? 'Matching...' : 'Find My Photos'}
              </button>
            </div>
          </div>
        )}

        {/* Hidden canvas for capturing video frames */}
        <canvas ref={canvasRef} className="hidden" />
      </div>
    </div>
  )
}