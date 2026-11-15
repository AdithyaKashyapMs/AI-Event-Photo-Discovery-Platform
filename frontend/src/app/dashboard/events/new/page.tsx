'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import Sidebar from '@/components/Sidebar';
import Snappy from '@/components/Snappy';

export default function CreateEventPage() {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const params = new URLSearchParams({ name: name.trim() });
      if (description.trim()) {
        params.set('description', description.trim());
      }
      const event = await apiFetch(`/events/?${params.toString()}`, { method: 'POST' });
      router.push(`/dashboard/events/${event.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create event');
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col md:flex-row">
      <Sidebar
        links={[
          { href: '/dashboard', label: 'Dashboard' },
          { href: '/dashboard/events/new', label: 'Create Event' },
        ]}
      />
      <div className="flex-1 px-4 py-8 sm:px-8">
        <div className="sticker-lg mx-auto grid max-w-3xl overflow-hidden md:grid-cols-[1fr_1.4fr]">
          <div className="block-sun flex flex-col items-center justify-center gap-3 border-b-[3px] border-ink p-6 md:border-b-0 md:border-r-[3px]">
            <div className="bubble">New event? Let&apos;s go!</div>
            <Snappy pose="camera" size={150} />
          </div>
          <div className="p-6 sm:p-8">
            <h1 className="font-display text-3xl font-extrabold">Create an event</h1>
            <p className="mt-1 font-semibold text-ink-soft">You can upload photos and share the invite right after.</p>
            {error && (
              <p className="alert alert-error mt-4" role="alert">
                {error}
              </p>
            )}
            <form onSubmit={handleSubmit} className="mt-6 space-y-4">
              <div>
                <label htmlFor="event-name" className="label">Event name</label>
                <input
                  id="event-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  maxLength={200}
                  className="input"
                  placeholder="Priya & Arjun's Wedding"
                />
              </div>
              <div>
                <label htmlFor="event-description" className="label">Description <span className="font-semibold text-ink-soft">(optional)</span></label>
                <textarea
                  id="event-description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  maxLength={2000}
                  className="input h-28"
                  placeholder="Tell your guests about the event…"
                />
              </div>
              <button type="submit" disabled={submitting || !name.trim()} className="btn btn-primary btn-lg btn-block">
                {submitting ? 'Creating…' : 'Create event'}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
