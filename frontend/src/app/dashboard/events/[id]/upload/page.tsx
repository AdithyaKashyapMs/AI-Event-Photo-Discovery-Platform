'use client';

import { useState, useCallback, useEffect } from 'react';
import { useParams } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import Sidebar from '@/components/Sidebar';

// Track each file's upload state
interface UploadFile {
  file: File;
  progress: number;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

export default function UploadPage() {
  const params = useParams<{ id: string }>();
  const eventId = params.id;
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [processingStatus, setProcessingStatus] = useState<{ processed: number; total: number } | null>(null);

  // Handle drag-and-drop events
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    // Filter to only image files
    const dropped = Array.from(e.dataTransfer.files).filter((f) =>
      f.type.startsWith('image/')
    );
    addFiles(dropped);
  }, []);

  // Add files to the upload queue
  function addFiles(newFiles: File[]) {
    const uploadFiles: UploadFile[] = newFiles.map((file) => ({
      file,
      progress: 0,
      status: 'pending',
    }));
    setFiles((prev) => [...prev, ...uploadFiles]);
  }

  // Upload all pending files sequentially
  async function uploadAll() {
    for (let i = 0; i < files.length; i++) {
      if (files[i].status !== 'pending') continue;

      // Mark file as uploading
      setFiles((prev) =>
        prev.map((f, idx) => (idx === i ? { ...f, status: 'uploading' } : f))
      );

      try {
        const formData = new FormData();
        formData.append('files', files[i].file);
        setFiles((prev) =>
          prev.map((f, idx) => (idx === i ? { ...f, progress: 50 } : f))
        );
        await apiFetch(`/events/${eventId}/photos`, {
          method: 'POST',
          body: formData,
        });

        // Mark as complete
        setFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: 'done', progress: 100 } : f
          )
        );
      } catch (error) {
        const message =
          error instanceof Error ? error.message : 'Upload failed';
        // Mark as failed
        setFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: 'error', error: message } : f
          )
        );
      }
    }
  }

  // Poll processing status after uploads start
  useEffect(() => {
    if (!files.some((f) => f.status === 'done')) return;
    async function fetchProcessingStatus() {
      const data = await apiFetch(`/events/${eventId}/analytics`);
      setProcessingStatus({
        processed: Math.round(
          data.total_photos * (data.processing_progress / 100),
        ),
        total: data.total_photos,
      });
    }

    fetchProcessingStatus();
    const interval = setInterval(fetchProcessingStatus, 3000);
    return () => clearInterval(interval);
  }, [files, eventId]);

  return (
    <div className="flex min-h-screen">
      <Sidebar
        links={[
          { href: '/dashboard', label: 'Dashboard' },
          { href: `/dashboard/events/${eventId}`, label: 'Event Overview' },
          { href: `/dashboard/events/${eventId}/upload`, label: 'Upload Photos' },
        ]}
      />
      <main className="flex-1 p-8">
        <h1 className="text-2xl font-bold mb-6">Upload Photos</h1>

        {/* Drag-and-drop zone */}
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`border-2 border-dashed rounded-lg p-12 text-center mb-6 ${
            dragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300'
          }`}
        >
          <p className="text-gray-500 mb-2">Drag and drop photos here, or</p>
          <label className="cursor-pointer bg-blue-600 text-white px-4 py-2 rounded-lg inline-block">
            Browse Files
            <input
              type="file"
              multiple
              accept="image/*"
              className="hidden"
              onChange={(e) => addFiles(Array.from(e.target.files || []))}
            />
          </label>
        </div>

        {/* File list with progress bars */}
        {files.length > 0 && (
          <div className="space-y-3 mb-6">
            {files.map((f, idx) => (
              <div key={idx} className="flex items-center gap-4 border rounded-lg p-3">
                <span className="flex-1 truncate text-sm">{f.file.name}</span>
                <div className="w-48 bg-gray-200 rounded-full h-2">
                  <div
                    className="bg-blue-600 h-2 rounded-full transition-all"
                    style={{ width: `${f.progress}%` }}
                  />
                </div>
                <span
                  className={`text-xs w-56 text-right ${
                    f.status === 'error' ? 'text-red-600' : 'text-gray-500'
                  }`}
                  title={f.error}
                >
                  {f.status === 'done'
                    ? 'Done'
                    : f.status === 'error'
                      ? f.error || 'Upload failed'
                      : `${f.progress}%`}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Upload button */}
        {files.some((f) => f.status === 'pending') && (
          <button
            onClick={uploadAll}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 mb-6"
          >
            Upload All ({files.filter((f) => f.status === 'pending').length} files)
          </button>
        )}

        {/* Real-time processing status */}
        {processingStatus && (
          <div className="mt-6 border rounded-lg p-4">
            <h2 className="font-medium mb-2">Processing Status</h2>
            <p className="text-sm text-gray-600 mb-2">
              {processingStatus.processed} / {processingStatus.total} photos processed
            </p>
            <div className="w-full bg-gray-200 rounded-full h-3">
              <div
                className="bg-green-500 h-3 rounded-full transition-all"
                style={{
                  width: `${processingStatus.total > 0 ? (processingStatus.processed / processingStatus.total) * 100 : 0}%`,
                }}
              />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}