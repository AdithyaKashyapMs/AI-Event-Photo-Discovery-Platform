'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import Snappy from '@/components/Snappy'
import { API_BASE, apiFetch, fetchProtectedObjectUrl, hasSession, redirectToLogin, saveBlob } from '@/lib/api'

interface ApiPhoto {
  id: number
  url: string
  thumbnail_url: string
  similarity: number | null
  created_at: string
}

interface GalleryResponse {
  photos: ApiPhoto[]
  scope: 'all' | 'matched'
  is_host: boolean
  page: number
  per_page: number
  total: number
  similarity_threshold: number
}

interface GalleryPhoto extends ApiPhoto {
  thumbnailObjectUrl: string
}

const PAGE_SIZE = 100

// Summary passed from the selfie page after a live search.
function readSearchSummary(searchParams: URLSearchParams) {
  const number = (key: string) => Number(searchParams.get(key) || 0)
  return {
    total: number('total'),
    floor: number('floor'),
    threshold: number('threshold'),
    liveScore: number('liveScore'),
    noMatchReason: searchParams.get('noMatchReason') || '',
  }
}

export default function GalleryPage() {
  const { id } = useParams<{ id: string }>()
  const searchParams = useSearchParams()
  // Set by the selfie page right after a search.
  const justSearched = searchParams.get('searched') === '1'
  const metrics = readSearchSummary(searchParams)

  const [photos, setPhotos] = useState<GalleryPhoto[]>([])
  const [info, setInfo] = useState<Omit<GalleryResponse, 'photos'> | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [minConfidence, setMinConfidence] = useState<number | null>(null)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  // Object URLs created on this page, revoked on unmount.
  const objectUrls = useRef<string[]>([])
  const controller = useRef<AbortController | null>(null)

  const loadPage = useCallback(
    async (page: number) => {
      // After a search a host sees their own matches; otherwise the server
      // decides (attendees always get their matches only).
      const query = new URLSearchParams({ page: String(page), per_page: String(PAGE_SIZE) })
      if (justSearched) query.set('matched_only', 'true')
      const data: GalleryResponse = await apiFetch(`/events/${id}/gallery?${query}`)
      const signal = controller.current?.signal
      const loaded = await Promise.all(
        data.photos.map(async (photo): Promise<GalleryPhoto | null> => {
          try {
            const objectUrl = await fetchProtectedObjectUrl(photo.thumbnail_url, signal)
            objectUrls.current.push(objectUrl)
            return { ...photo, thumbnailObjectUrl: objectUrl }
          } catch {
            return null
          }
        }),
      )
      setInfo({
        scope: data.scope,
        is_host: data.is_host,
        page: data.page,
        per_page: data.per_page,
        total: data.total,
        similarity_threshold: data.similarity_threshold,
      })
      setPhotos((previous) => [
        ...(page === 1 ? [] : previous),
        ...loaded.filter((photo): photo is GalleryPhoto => photo !== null),
      ])
      return data
    },
    [id, justSearched],
  )

  useEffect(() => {
    if (!hasSession()) {
      redirectToLogin()
      return
    }
    controller.current = new AbortController()
    setLoading(true)
    setError('')
    loadPage(1)
      .then((data) => {
        // Start the slider at the server's match threshold for matched results.
        setMinConfidence(data.scope === 'matched' ? metrics.threshold || data.similarity_threshold : null)
      })
      .catch((err: Error) => {
        if (err.name !== 'AbortError') setError(err.message)
      })
      .finally(() => setLoading(false))
    const urls = objectUrls.current
    return () => {
      controller.current?.abort()
      urls.forEach((url) => URL.revokeObjectURL(url))
      objectUrls.current = []
    }
    // metrics.threshold is read once for the initial slider value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadPage])

  const visiblePhotos =
    minConfidence === null
      ? photos
      : photos.filter((photo) => photo.similarity === null || photo.similarity >= minConfidence)

  // Full-size image for the lightbox, loaded on demand.
  useEffect(() => {
    if (lightboxIndex === null) return
    const photo = visiblePhotos[lightboxIndex]
    if (!photo) return
    const abort = new AbortController()
    let url: string | null = null
    setLightboxUrl(null)
    fetchProtectedObjectUrl(photo.url, abort.signal)
      .then((objectUrl) => {
        url = objectUrl
        setLightboxUrl(objectUrl)
      })
      .catch(() => setLightboxUrl(photo.thumbnailObjectUrl))
    return () => {
      abort.abort()
      if (url) URL.revokeObjectURL(url)
    }
    // Only reload when the selected photo changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lightboxIndex, visiblePhotos[lightboxIndex ?? -1]?.id])

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (lightboxIndex === null) return
      if (event.key === 'Escape') setLightboxIndex(null)
      if (event.key === 'ArrowRight' && lightboxIndex < visiblePhotos.length - 1) {
        setLightboxIndex(lightboxIndex + 1)
      }
      if (event.key === 'ArrowLeft' && lightboxIndex > 0) {
        setLightboxIndex(lightboxIndex - 1)
      }
    },
    [lightboxIndex, visiblePhotos.length],
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  // Host only: permanently delete one photo and everything derived from it.
  async function deletePhoto(photo: GalleryPhoto) {
    if (
      !window.confirm(
        'Delete this photo? It is removed for everyone, together with the faces found in it and any guest matches. This cannot be undone.',
      )
    )
      return
    setDeletingId(photo.id)
    setError('')
    try {
      await apiFetch(`/photos/${photo.id}`, { method: 'DELETE' })
      URL.revokeObjectURL(photo.thumbnailObjectUrl)
      setPhotos((current) => current.filter((item) => item.id !== photo.id))
      setInfo((current) => (current ? { ...current, total: Math.max(0, current.total - 1) } : current))
      setLightboxIndex(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the photo')
    } finally {
      setDeletingId(null)
    }
  }

  async function loadMore() {
    if (!info) return
    setLoadingMore(true)
    try {
      await loadPage(info.page + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load more photos')
    } finally {
      setLoadingMore(false)
    }
  }

  async function downloadVisible() {
    setDownloading(true)
    setError('')
    try {
      // The server only includes photos this user is allowed to see.
      const selectedIds = visiblePhotos.map((photo) => photo.id).join(',')
      const response = await fetch(
        `${API_BASE}/events/${id}/gallery/download?photo_ids=${encodeURIComponent(selectedIds)}`,
        { credentials: 'include' },
      )
      if (!response.ok) throw new Error('Failed to download photos')
      saveBlob(await response.blob(), `event-${id}-photos.zip`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to download photos')
    } finally {
      setDownloading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 font-display text-xl font-bold text-ink-soft">
        <Snappy pose="search" size={130} />
        Looking for your photos…
      </div>
    )
  }

  if (!info) {
    return (
      <div className="mx-auto flex max-w-lg flex-col items-center px-4 py-16 text-center">
        <Snappy pose="sad" size={150} />
        <h1 className="mt-4 font-display text-3xl font-extrabold">Couldn&apos;t open these photos</h1>
        <p className="alert alert-error mt-4">{error || 'Something went wrong. Please try again.'}</p>
        <Link href="/dashboard" className="btn btn-white mt-5">
          Back to dashboard
        </Link>
      </div>
    )
  }

  const matchedScope = info.scope === 'matched'
  const hasMore = !!info && photos.length < info.total && info.page * info.per_page < info.total
  // After a search the server reports the lowest score this user may see;
  // otherwise only photos at or above the match threshold were saved.
  const sliderFloor = justSearched && metrics.floor ? metrics.floor : info?.similarity_threshold ?? 0.55
  const found = visiblePhotos.length

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      {/* Header banner */}
      <section
        className={`sticker-lg mb-6 flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:p-6 ${
          matchedScope ? (found ? 'block-mint' : 'block-sun') : 'block-sky'
        }`}
      >
        <Snappy
          pose={matchedScope ? (found ? 'celebrate' : justSearched ? 'confused' : 'selfie') : 'photo'}
          size={110}
          className="hidden shrink-0 sm:block"
        />
        <div className="flex-1">
          <h1 className="font-display text-3xl font-extrabold sm:text-4xl">
            {matchedScope
              ? found
                ? `Found you in ${found} photo${found === 1 ? '' : 's'}!`
                : 'Your photos'
              : 'All event photos'}
          </h1>
          <p className="mt-1 font-semibold opacity-90">
            {matchedScope
              ? justSearched && metrics.liveScore > 0
                ? 'Live check passed. Your matches are saved, so you can come back any time.'
                : 'Only you can see these. Search again if new photos were added.'
              : `${info?.total ?? 0} processed photo${info?.total === 1 ? '' : 's'} · you are the host, so you see everything.`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {(matchedScope && !info?.is_host) || justSearched ? (
            <Link href={`/events/${id}/selfie`} className="btn btn-white">
              Search again
            </Link>
          ) : null}
          {info?.is_host && justSearched && (
            <Link href={`/events/${id}/gallery`} className="btn btn-white">
              Show all photos
            </Link>
          )}
          <button onClick={downloadVisible} disabled={downloading || found === 0} className="btn btn-primary">
            {downloading ? 'Preparing ZIP…' : `Download ${found} photo${found === 1 ? '' : 's'}`}
          </button>
        </div>
      </section>

      {matchedScope && minConfidence !== null && photos.length > 0 && (
        <label className="sticker-flat mb-6 flex flex-wrap items-center gap-3 px-4 py-3 text-sm font-bold">
          <span>Minimum similarity: {Math.round(minConfidence * 100)}%</span>
          <input
            type="range"
            min={Math.min(sliderFloor, minConfidence)}
            max="1"
            step="0.01"
            value={minConfidence}
            onChange={(event) => setMinConfidence(Number(event.target.value))}
            className="accent-[var(--tomato)]"
          />
          <span className="chip">
            {found} of {photos.length} photos
          </span>
        </label>
      )}

      {error && (
        <p className="alert alert-error mb-4" role="alert">
          {error}
        </p>
      )}

      {found === 0 ? (
        <div className="sticker mx-auto flex max-w-2xl flex-col items-center gap-3 p-8 text-center">
          {matchedScope && !justSearched ? (
            <>
              <Snappy pose="selfie" size={140} />
              <h2 className="font-display text-2xl font-extrabold">Let&apos;s find you!</h2>
              <p className="font-semibold text-ink-soft">Take a 10-second live selfie to find the photos you appear in.</p>
              <Link href={`/events/${id}/selfie`} className="btn btn-primary btn-lg mt-2">
                Find my photos
              </Link>
            </>
          ) : (
            <>
              <Snappy pose="confused" size={140} />
              <h2 className="font-display text-2xl font-extrabold">No photos of you… yet</h2>
              <p className="font-semibold text-ink-soft">
                {metrics.noMatchReason || 'Nothing matched. The host may still be uploading, so try again later.'}
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:gap-5 md:grid-cols-3 lg:grid-cols-4">
          {visiblePhotos.map((photo, index) => (
            <div
              key={photo.id}
              className={`group relative transition-transform hover:-translate-y-1 hover:rotate-[-1deg] ${
                deletingId === photo.id ? 'opacity-50' : ''
              }`}
            >
              <button
                type="button"
                onClick={() => setLightboxIndex(index)}
                className="sticker block w-full overflow-hidden text-left focus-visible:outline-[3px] focus-visible:outline-sky"
                aria-label={`Open photo ${photo.id}`}
              >
                <img
                  src={photo.thumbnailObjectUrl}
                  alt={`Event photo ${photo.id}`}
                  className="h-44 w-full object-cover sm:h-52"
                />
              </button>
              {photo.similarity !== null && (
                <span className="chip pointer-events-none absolute right-2 top-2 bg-white">
                  {Math.round(photo.similarity * 100)}% match
                </span>
              )}
              {info.is_host && (
                <button
                  type="button"
                  onClick={() => void deletePhoto(photo)}
                  disabled={deletingId !== null}
                  className="btn btn-danger btn-sm absolute bottom-2 right-2 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                  aria-label={`Delete photo ${photo.id}`}
                  title="Delete photo"
                >
                  {deletingId === photo.id ? 'Deleting…' : '🗑 Delete'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {hasMore && (
        <div className="mt-8 text-center">
          <button onClick={loadMore} disabled={loadingMore} className="btn btn-white">
            {loadingMore ? 'Loading…' : `Load more (${photos.length} of ${info?.total})`}
          </button>
        </div>
      )}

      {lightboxIndex !== null && visiblePhotos[lightboxIndex] && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/90 p-4"
          onClick={() => setLightboxIndex(null)}
          role="dialog"
          aria-label="Photo viewer"
        >
          {lightboxUrl ? (
            <img
              src={lightboxUrl}
              alt={`Event photo ${visiblePhotos[lightboxIndex].id}`}
              className="max-h-[85vh] max-w-[92vw] rounded-2xl border-[3px] border-white object-contain"
            />
          ) : (
            <Snappy pose="search" size={110} />
          )}
          <div className="absolute bottom-4 flex flex-wrap items-center justify-center gap-2">
            <p className="chip bg-white">
              {lightboxIndex + 1} / {visiblePhotos.length} · ← → to browse · Esc to close
            </p>
            {info.is_host && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  void deletePhoto(visiblePhotos[lightboxIndex])
                }}
                disabled={deletingId !== null}
                className="btn btn-danger btn-sm"
              >
                {deletingId !== null ? 'Deleting…' : '🗑 Delete photo'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
