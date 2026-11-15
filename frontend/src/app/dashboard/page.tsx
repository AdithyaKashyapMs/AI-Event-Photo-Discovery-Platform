'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiFetch, getCurrentUser, redirectToLogin, type CurrentUser } from '@/lib/api';
import Card from '@/components/Card';
import Sidebar from '@/components/Sidebar';
import Snappy from '@/components/Snappy';

interface Event {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
}

const ACCENTS = ['block-sun', 'block-sky', 'block-tomato', 'block-mint', 'block-plum'];

const HOST_LINKS = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/dashboard/events/new', label: 'Create Event' },
];

export default function DashboardPage() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [privacyMessage, setPrivacyMessage] = useState('');
  const [deleting, setDeleting] = useState(false);

  async function deleteSavedMatches() {
    if (!window.confirm('Delete your saved photo matches in every event? You can find them again with a new live selfie search.')) return;
    setDeleting(true);
    setPrivacyMessage('');
    try {
      const data: { detail: string } = await apiFetch('/users/me/biometric-data', { method: 'DELETE' });
      setPrivacyMessage(data.detail);
    } catch (err) {
      setPrivacyMessage(err instanceof Error ? err.message : 'Could not delete your matches');
    } finally {
      setDeleting(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const current = await getCurrentUser();
      if (!current) {
        redirectToLogin();
        return;
      }
      if (cancelled) return;
      setUser(current);
      try {
        const data: Event[] = await apiFetch('/events/');
        if (!cancelled) setEvents(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load events');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const isHost = user?.role === 'host';

  const firstName = user?.email.split('@')[0] ?? '';

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col md:flex-row">
      {isHost && <Sidebar links={HOST_LINKS} />}
      <div className="flex-1 px-4 py-8 sm:px-8">
        {/* Greeting banner */}
        <section
          className={`sticker-lg mb-8 flex items-center gap-4 overflow-hidden p-5 sm:p-6 ${isHost ? 'block-sky' : 'block-sun'}`}
        >
          <Snappy pose={isHost ? 'camera' : 'wave'} size={110} className="hidden shrink-0 sm:block" />
          <div className="flex-1">
            <h1 className="font-display text-3xl font-extrabold sm:text-4xl">
              {user ? `Hi, ${firstName}!` : 'Hi!'}
            </h1>
            <p className="mt-1 font-semibold opacity-90">
              {isHost
                ? 'Your events, your photos. Upload, share the QR code, and let guests find themselves.'
                : 'Here are the events you joined. Pick one and find your photos.'}
            </p>
          </div>
          {isHost && (
            <Link href="/dashboard/events/new" className="btn btn-sun btn-lg hidden sm:inline-flex">
              + New event
            </Link>
          )}
        </section>

        {error && <p className="alert alert-error mb-6">{error}</p>}

        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-2xl font-extrabold">{isHost ? 'My events' : 'My joined events'}</h2>
          {isHost && (
            <Link href="/dashboard/events/new" className="btn btn-primary btn-sm sm:hidden">
              + New event
            </Link>
          )}
        </div>

        {loading ? (
          <div className="flex items-center gap-3 font-semibold text-ink-soft">
            <Snappy pose="search" size={60} /> Looking for your events…
          </div>
        ) : events.length === 0 ? (
          <div className="sticker flex flex-col items-center gap-3 p-8 text-center sm:flex-row sm:text-left">
            <Snappy pose={isHost ? 'photo' : 'confused'} size={120} />
            <div>
              <h3 className="font-display text-2xl font-extrabold">
                {isHost ? 'No events yet' : 'You haven’t joined an event yet'}
              </h3>
              <p className="mt-1 font-semibold text-ink-soft">
                {isHost
                  ? 'Create your first event, upload the photos, and share the QR code with your guests.'
                  : 'Open the invite link or scan the QR code your event host shared.'}
              </p>
              {isHost && (
                <Link href="/dashboard/events/new" className="btn btn-primary mt-4">
                  Create an event
                </Link>
              )}
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            {events.map((event, index) => (
              <Card key={event.id} title={event.name} accent={ACCENTS[index % ACCENTS.length]}>
                {event.description && <p className="line-clamp-2 font-semibold text-ink-soft">{event.description}</p>}
                <p className="mt-2 text-xs font-bold uppercase tracking-wide text-ink-soft">
                  Created {new Date(event.created_at).toLocaleDateString()}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {isHost ? (
                    <Link href={`/dashboard/events/${event.id}`} className="btn btn-primary btn-sm">
                      Manage event
                    </Link>
                  ) : (
                    <>
                      <Link href={`/events/${event.id}/selfie`} className="btn btn-primary btn-sm">
                        Find my photos
                      </Link>
                      <Link href={`/events/${event.id}/gallery`} className="btn btn-white btn-sm">
                        My photos
                      </Link>
                    </>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}

        {user && !isHost && (
          <section className="sticker mt-10 flex max-w-3xl flex-col gap-4 bg-mint-soft p-5 sm:flex-row sm:items-center">
            <Snappy pose="shield" size={90} bob={false} className="shrink-0" />
            <div>
              <h2 className="font-display text-xl font-extrabold">Your data</h2>
              <p className="mt-1 text-sm font-semibold text-ink-soft">
                Your selfies are never stored. We keep the list of photos your searches matched so only you can open
                them. Face embeddings from event photos belong to each event and are deleted when its host deletes
                the event.
              </p>
              <button onClick={deleteSavedMatches} disabled={deleting} className="btn btn-white btn-sm mt-3">
                {deleting ? 'Deleting…' : 'Delete my saved matches'}
              </button>
              {privacyMessage && <p className="mt-3 text-sm font-semibold">{privacyMessage}</p>}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
