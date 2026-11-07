'use client'

import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useParams, useSearchParams } from 'next/navigation'
import { API_BASE, apiFetch, getToken } from '@/lib/api'

interface GalleryPhoto {
  id: number
  url: string
  confidence: number
  quality: number
  created_at: string
}

export default function GalleryPage() {
  const { id } = useParams<{ id: string }>()
  const searchParams = useSearchParams()
  const hasMatchResults = searchParams.has('matches')
  const matchedPhotoIds = searchParams.get('matches') || ''
  const matchedScores = new Map(
    (searchParams.get('scores') || '')
      .split(',')
      .map((entry) => {
        const [id, score] = entry.split(':').map(Number)
        return [id, score] as const
      })
      .filter(([id, score]) => Number.isFinite(id) && Number.isFinite(score)),
  )
  const metrics = {
    total: Number(searchParams.get('total') || 0),
    branches: Number(searchParams.get('branches') || 0),
    candidates: Number(searchParams.get('candidates') || 0),
    indexed: Number(searchParams.get('indexed') || 0),
    reduction: Number(searchParams.get('reduction') || 0),
    latency: Number(searchParams.get('latency') || 0),
    threshold: Number(searchParams.get('threshold') || 0),
    raw: Number(searchParams.get('raw') || 0),
    unique: Number(searchParams.get('unique') || 0),
    best: Number(searchParams.get('best') || 0),
    average: Number(searchParams.get('average') || 0),
    noMatchReason: searchParams.get('noMatchReason') || '',
    researchLatency: Number(searchParams.get('researchLatency') || 0),
    researchCandidates: Number(searchParams.get('researchCandidates') || 0),
    projectLatency: Number(searchParams.get('projectLatency') || 0),
    projectCandidates: Number(searchParams.get('projectCandidates') || 0),
    selfies: Number(searchParams.get('selfies') || 1),
    branching: Number(searchParams.get('branching') || 0),
    depth: Number(searchParams.get('depth') || 0),
  }
  const [photos, setPhotos] = useState<GalleryPhoto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [minConfidence, setMinConfidence] = useState(0.5)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    if (hasMatchResults && !matchedPhotoIds) {
      setPhotos([])
      setLoading(false)
      return
    }
    const query = matchedPhotoIds ? `?photo_ids=${encodeURIComponent(matchedPhotoIds)}` : ''
    apiFetch(`/events/${id}/gallery${query}`)
      .then((data) => {
        setPhotos(
          (data.photos || []).map((photo: {
            id: number
            s3_key: string
            created_at: string
          }) => ({
            id: photo.id,
            url: `${API_BASE}/uploads/${photo.s3_key}`,
            confidence: matchedScores.get(photo.id) ?? 1,
            quality: 0,
            created_at: photo.created_at,
          })),
        )
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [hasMatchResults, id, matchedPhotoIds])

  const visiblePhotos = photos.filter(
    (photo) => photo.confidence >= minConfidence,
  )

  const handleKeyDown = useCallback((event: KeyboardEvent) => {
    if (lightboxIndex === null) return
    if (event.key === 'Escape') setLightboxIndex(null)
    if (event.key === 'ArrowRight' && lightboxIndex < visiblePhotos.length - 1) {
      setLightboxIndex(lightboxIndex + 1)
    }
    if (event.key === 'ArrowLeft' && lightboxIndex > 0) {
      setLightboxIndex(lightboxIndex - 1)
    }
  }, [lightboxIndex, visiblePhotos.length])

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  async function downloadAll() {
    setDownloading(true)
    try {
      const response = await fetch(`${API_BASE}/events/${id}/gallery/download`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      if (!response.ok) throw new Error('Failed to download photos')
      const blobUrl = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = `event-${id}-photos.zip`
      link.click()
      URL.revokeObjectURL(blobUrl)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to download photos')
    } finally {
      setDownloading(false)
    }
  }

  if (loading) return <p className="p-8">Loading your photos...</p>

  return (
    <main className="min-h-screen bg-gray-950 p-6 text-gray-100">
      <div className="max-w-6xl mx-auto">
        <div className="flex justify-between items-center mb-6">
          <div>
            <h1 className="text-2xl font-bold">Your Photos</h1>
            <p className="text-gray-400">{visiblePhotos.length} photos found</p>
          </div>
          <button
            onClick={downloadAll}
            disabled={downloading || visiblePhotos.length === 0}
            className="bg-blue-600 text-white px-4 py-2 rounded-md disabled:opacity-50"
          >
            {downloading ? 'Preparing ZIP...' : 'Download All'}
          </button>
        </div>

        <section className="mb-6 rounded-lg border border-gray-800 bg-gray-900 p-4">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <MetricGroup title="Face processing">
              <Metric label="Selfies processed" value={metrics.selfies} />
              <Metric label="Embedding fusion" value={`${metrics.selfies} weighted`} />
              <Metric label="Similarity threshold" value={`${Math.round(metrics.threshold * 100)}%`} />
            </MetricGroup>
            <MetricGroup title="Index search">
              <Metric label="Indexed faces" value={metrics.indexed} />
              <Metric label="Candidates searched" value={metrics.candidates} />
              <Metric label="Search latency" value={`${metrics.latency.toFixed(2)} ms`} />
            </MetricGroup>
            <MetricGroup title="Hierarchy traversal">
              <Metric label="Branches searched" value={metrics.branches} />
              <Metric label="Branching factor" value={metrics.branching} />
              <Metric label="Maximum depth" value={metrics.depth} />
              <Metric label="Search reduction" value={`${metrics.reduction.toFixed(1)}%`} />
            </MetricGroup>
            <MetricGroup title="Match filtering">
              <Metric label="Raw face results" value={metrics.raw} />
              <Metric label="Unique photos" value={metrics.unique} />
              <Metric label="Best similarity" value={`${Math.round(metrics.best * 100)}%`} />
              <Metric label="Average similarity" value={`${Math.round(metrics.average * 100)}%`} />
            </MetricGroup>
          </div>
          <p className="mt-4 text-xs text-gray-400">
            Metrics describe selfie embedding, hierarchical candidate search, similarity filtering, and photo deduplication.
          </p>
        </section>

        <section className="mb-6 rounded-lg border border-blue-900 bg-blue-950/40 p-4">
          <h2 className="mb-3 text-lg font-semibold text-white">
            Search approach comparison
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="border-b border-blue-900 text-blue-200">
                <tr>
                  <th className="p-2">Approach</th>
                  <th className="p-2">Search method</th>
                  <th className="p-2">Latency</th>
                  <th className="p-2">Candidates</th>
                </tr>
              </thead>
              <tbody className="text-gray-200">
                <tr className="border-b border-blue-900/60">
                  <td className="p-2 font-medium">Research hierarchy</td>
                  <td className="p-2">Rebuild index on each request</td>
                  <td className="p-2">{metrics.researchLatency.toFixed(2)} ms</td>
                  <td className="p-2">{metrics.researchCandidates}</td>
                </tr>
                <tr className="border-b border-blue-900/60">
                  <td className="p-2 font-medium text-green-300">Our project</td>
                  <td className="p-2">Cached hierarchy index</td>
                  <td className="p-2 text-green-300">{metrics.projectLatency.toFixed(2)} ms</td>
                  <td className="p-2">{metrics.projectCandidates}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-gray-400">
            These are measured latencies from this selfie request. Recognition accuracy requires a labeled benchmark dataset and is not inferred from latency.
          </p>
        </section>

        <label className="mb-6 block rounded-lg border border-gray-800 bg-gray-900 p-4 text-sm text-gray-200">
          Minimum confidence: {Math.round(minConfidence * 100)}%
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={minConfidence}
            onChange={(event) => setMinConfidence(Number(event.target.value))}
            className="ml-4 align-middle"
          />
        </label>

        {error && <p className="bg-red-50 text-red-700 p-3 rounded mb-4">{error}</p>}
        {visiblePhotos.length === 0 ? (
          <div className="py-12 text-center text-gray-400">
            <p>No photos found.</p>
            {metrics.noMatchReason && (
              <p className="mt-2 text-sm text-gray-500">{metrics.noMatchReason}</p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {visiblePhotos.map((photo, index) => (
              <button
                type="button"
                key={photo.id}
                onClick={() => setLightboxIndex(index)}
                className="relative rounded-lg overflow-hidden bg-white text-left"
              >
                <img src={photo.url} alt={`Event photo ${photo.id}`} className="w-full h-48 object-cover" />
                <span className="absolute top-2 right-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
                  {Math.round(photo.confidence * 100)}%
                </span>
                <span className="absolute bottom-2 left-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
                  Quality-aware match
                </span>
              </button>
            ))}
          </div>
        )}

        {lightboxIndex !== null && visiblePhotos[lightboxIndex] && (
          <div
            className="fixed inset-0 bg-black/90 z-50 flex items-center justify-center"
            onClick={() => setLightboxIndex(null)}
          >
            <img
              src={visiblePhotos[lightboxIndex].url}
              alt={`Event photo ${visiblePhotos[lightboxIndex].id}`}
              className="max-h-[90vh] max-w-[90vw] object-contain"
            />
          </div>
        )}
      </div>
    </main>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-gray-700 bg-gray-800 p-3">
      <p className="text-xs text-gray-400">{label}</p>
      <p className="mt-1 text-lg font-semibold text-white">{value}</p>
    </div>
  )
}

function MetricGroup({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div className="rounded-md border border-gray-700 bg-gray-800 p-3">
      <h3 className="mb-2 text-sm font-semibold text-blue-300">{title}</h3>
      <div className="space-y-2">{children}</div>
    </div>
  )
}
