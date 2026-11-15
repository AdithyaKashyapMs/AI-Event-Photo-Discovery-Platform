'use client';

import { useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import Sidebar from '@/components/Sidebar';
import Snappy from '@/components/Snappy';

interface QueuedFile {
  id: number;
  file: File;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

interface ProcessingStatus {
  total: number;
  processed: number;
  done: number;
  duplicate: number;
  failed: number;
  pending: number;
  progress: number;
}

// Must match MAX_UPLOAD_BYTES in backend/app/routers/events.py
const MAX_UPLOAD_MB = 30;
const POLL_INTERVAL_MS = 3000;

export default function UploadPage() {
  const params = useParams<{ id: string }>();
  const eventId = params.id;
  const [files, setFiles] = useState<QueuedFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [status, setStatus] = useState<ProcessingStatus | null>(null);
  const nextId = useRef(0);

  function addFiles(newFiles: File[]) {
    const queued = newFiles
      .filter((file) => file.type.startsWith('image/'))
      .map((file): QueuedFile => {
        const tooLarge = file.size > MAX_UPLOAD_MB * 1024 * 1024;
        return {
          id: nextId.current++,
          file,
          status: tooLarge ? 'error' : 'pending',
          error: tooLarge ? `Larger than ${MAX_UPLOAD_MB} MB` : undefined,
        };
      });
    setFiles((prev) => [...prev, ...queued]);
  }

  function updateFile(id: number, changes: Partial<QueuedFile>) {
    setFiles((prev) => prev.map((f) => (f.id === id ? { ...f, ...changes } : f)));
  }

  // Upload pending files one at a time so each gets its own result.
  async function uploadAll() {
    if (uploading) return;
    setUploading(true);
    const pending = files.filter((f) => f.status === 'pending');
    for (const queued of pending) {
      updateFile(queued.id, { status: 'uploading' });
      try {
        const formData = new FormData();
        formData.append('files', queued.file);
        await apiFetch(`/events/${eventId}/photos`, { method: 'POST', body: formData });
        updateFile(queued.id, { status: 'done' });
        setUploadedCount((count) => count + 1);
      } catch (error) {
        updateFile(queued.id, {
          status: 'error',
          error: error instanceof Error ? error.message : 'Upload failed',
        });
      }
    }
    setUploading(false);
  }

  // Poll processing status after the first successful upload, until the
  // worker has finished every queued photo.
  useEffect(() => {
    if (uploadedCount === 0) return;
    let timer: number | undefined;
    let cancelled = false;
    async function poll() {
      try {
        const data: ProcessingStatus = await apiFetch(`/events/${eventId}/processing-status`);
        if (cancelled) return;
        setStatus(data);
        if (data.pending > 0) timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    }
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [uploadedCount, eventId]);

  const pendingCount = files.filter((f) => f.status === 'pending').length;

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col md:flex-row">
      <Sidebar
        links={[
          { href: '/dashboard', label: 'Dashboard' },
          { href: `/dashboard/events/${eventId}`, label: 'Event Overview' },
          { href: `/dashboard/events/${eventId}/upload`, label: 'Upload Photos' },
          { href: `/events/${eventId}/gallery`, label: 'All Photos' },
          { href: `/dashboard/events/${eventId}/label`, label: 'Label Faces' },
        ]}
      />
      <div className="flex-1 px-4 py-8 sm:px-8">
        <h1 className="font-display text-4xl font-extrabold">Upload photos</h1>
        <p className="mt-1 font-semibold text-ink-soft">
          Drop the whole shoot in. We find every face, skip duplicates, and keep the photos private.
        </p>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            addFiles(Array.from(e.dataTransfer.files));
          }}
          className={`sticker-lg mt-6 flex flex-col items-center gap-3 border-dashed p-8 text-center transition-colors sm:p-10 ${
            dragging ? 'bg-sky-soft' : 'bg-sun-soft'
          }`}
        >
          <Snappy pose="photo" size={130} />
          <p className="font-display text-2xl font-extrabold">
            {dragging ? 'Drop them! I’ve got them.' : 'Drag & drop photos here'}
          </p>
          <label className="btn btn-primary cursor-pointer">
            Browse files
            <input
              type="file"
              multiple
              accept="image/jpeg,image/png,image/webp,image/bmp,image/tiff"
              className="hidden"
              onChange={(e) => {
                addFiles(Array.from(e.target.files || []));
                // Allow choosing the same file again later.
                e.target.value = '';
              }}
            />
          </label>
          <p className="text-sm font-semibold text-ink-soft">JPEG, PNG, WebP, BMP or TIFF, up to {MAX_UPLOAD_MB} MB each.</p>
        </div>

        {pendingCount > 0 && (
          <button onClick={uploadAll} disabled={uploading} className="btn btn-primary btn-lg mt-6">
            {uploading ? 'Uploading…' : `Upload all (${pendingCount} file${pendingCount === 1 ? '' : 's'})`}
          </button>
        )}

        {status && (
          <div className="sticker mt-6 flex max-w-3xl flex-col gap-4 p-5 sm:flex-row sm:items-center">
            <Snappy pose={status.pending > 0 ? 'search' : 'celebrate'} size={90} className="shrink-0" />
            <div className="flex-1">
              <h2 className="font-display text-xl font-extrabold">
                {status.pending > 0 ? 'Finding faces in your photos…' : 'All done! Guests can search now.'}
              </h2>
              <p className="mt-1 text-sm font-semibold text-ink-soft">
                {status.processed} / {status.total} photos processed
                {status.duplicate > 0 && ` · ${status.duplicate} duplicates skipped`}
                {status.failed > 0 && ` · ${status.failed} failed`}
              </p>
              <div className="mt-3 h-5 w-full overflow-hidden rounded-full border-[2.5px] border-ink bg-white">
                <div className="h-full bg-mint transition-all" style={{ width: `${status.progress}%` }} />
              </div>
            </div>
          </div>
        )}

        {files.length > 0 && (
          <ul className="mt-6 max-w-3xl space-y-2">
            {files.map((f) => (
              <li key={f.id} className="sticker-flat flex items-center gap-3 px-4 py-2.5">
                <span className="min-w-0 flex-1 truncate font-semibold">{f.file.name}</span>
                <span
                  className={`chip ${
                    f.status === 'error'
                      ? 'bg-tomato-soft'
                      : f.status === 'done'
                        ? 'bg-mint-soft'
                        : f.status === 'uploading'
                          ? 'bg-sky-soft'
                          : ''
                  }`}
                  title={f.error}
                >
                  {f.status === 'done'
                    ? 'Uploaded ✓'
                    : f.status === 'error'
                      ? f.error || 'Upload failed'
                      : f.status === 'uploading'
                        ? 'Uploading…'
                        : 'Waiting'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
