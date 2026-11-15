'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { apiFetch, fetchProtectedObjectUrl } from '@/lib/api';
import Sidebar from '@/components/Sidebar';
import Snappy from '@/components/Snappy';
import EventAnalytics, { type Analytics } from '@/components/EventAnalytics';

interface Event {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  is_host: boolean;
  invite_token: string;
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


type TabName = 'overview' | 'processing' | 'analytics';

const POLL_INTERVAL_MS = 3000;

export default function EventDetailPage() {
  const params = useParams<{ id: string }>();
  const eventId = params.id;
  const router = useRouter();
  const [event, setEvent] = useState<Event | null>(null);
  const [loadError, setLoadError] = useState('');
  const [activeTab, setActiveTab] = useState<TabName>('overview');
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState('');
  const [status, setStatus] = useState<ProcessingStatus | null>(null);
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [origin, setOrigin] = useState('');
  const [inviteCopied, setInviteCopied] = useState(false);
  const [actionError, setActionError] = useState('');
  const [busyAction, setBusyAction] = useState<'reset' | 'delete' | null>(null);

  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  // The invite link carries the secret token, never just the event ID.
  const inviteUrl = event ? `${origin}/join/${event.invite_token}` : '';

  useEffect(() => {
    apiFetch(`/events/${eventId}`)
      .then((data: Event) => {
        if (!data.is_host) throw new Error('Only the event host can manage this event');
        setEvent(data);
      })
      .catch((err: Error) => setLoadError(err.message));
  }, [eventId]);

  // QR code for the invite link (served to the host only, so load it with the JWT).
  // Reloaded when the invite token is reset.
  const inviteToken = event?.invite_token;
  useEffect(() => {
    if (!inviteToken) return;
    const controller = new AbortController();
    let url: string | null = null;
    fetchProtectedObjectUrl(`/events/${eventId}/qr`, controller.signal)
      .then((objectUrl) => {
        url = objectUrl;
        setQrUrl(objectUrl);
      })
      .catch(() => setQrUrl(null));
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [inviteToken, eventId]);

  // Processing counts: polled while photos are still queued.
  useEffect(() => {
    if (!event || activeTab !== 'processing') return;
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
  }, [activeTab, event, eventId]);

  useEffect(() => {
    if (!event || activeTab !== 'analytics') return;
    setAnalyticsError('');
    apiFetch(`/events/${eventId}/analytics`)
      .then(setAnalytics)
      .catch((err: Error) => setAnalyticsError(err.message));
  }, [activeTab, event, eventId]);

  async function resetInvite() {
    if (!window.confirm('Create a new invite link? The current link and QR code will stop working. Attendees who already joined keep access.')) return;
    setBusyAction('reset');
    setActionError('');
    try {
      const data: { invite_token: string } = await apiFetch(`/events/${eventId}/invite/reset`, { method: 'POST' });
      setEvent((current) => (current ? { ...current, invite_token: data.invite_token } : current));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not reset the invite link');
    } finally {
      setBusyAction(null);
    }
  }

  async function deleteEvent() {
    const typed = window.prompt(
      `This permanently deletes "${event?.name}": all photos, the face embeddings of everyone in them, ` +
        'and every attendee\'s saved matches. Type DELETE to confirm.',
    );
    if (typed !== 'DELETE') return;
    setBusyAction('delete');
    setActionError('');
    try {
      await apiFetch(`/events/${eventId}`, { method: 'DELETE' });
      router.push('/dashboard');
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not delete the event');
      setBusyAction(null);
    }
  }

  async function copyInviteLink() {
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setInviteCopied(true);
      window.setTimeout(() => setInviteCopied(false), 2000);
    } catch {
      window.prompt('Copy this invite link:', inviteUrl);
    }
  }

  if (loadError) {
    return (
      <div className="mx-auto flex max-w-lg flex-col items-center px-4 py-16 text-center">
        <Snappy pose="sad" size={150} />
        <p className="alert alert-error mt-4">{loadError}</p>
        <Link href="/dashboard" className="btn btn-white mt-4">
          Back to dashboard
        </Link>
      </div>
    );
  }
  if (!event) {
    return (
      <div className="flex items-center justify-center gap-3 py-20 font-semibold text-ink-soft">
        <Snappy pose="search" size={70} /> Loading your event…
      </div>
    );
  }

  const tabs: { key: TabName; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'processing', label: 'Processing' },
    { key: 'analytics', label: 'Analytics' },
  ];

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
        <section className="sticker-lg block-sky mb-6 flex items-center gap-4 p-5 sm:p-6">
          <Snappy pose="camera" size={96} className="hidden shrink-0 sm:block" />
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-display text-3xl font-extrabold sm:text-4xl">{event.name}</h1>
            {event.description && <p className="mt-1 font-semibold opacity-90">{event.description}</p>}
            <p className="mt-1 text-sm font-bold opacity-80">
              Created {new Date(event.created_at).toLocaleDateString()}
            </p>
          </div>
        </section>

        <div className="mb-6 flex flex-wrap gap-2" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              role="tab"
              aria-selected={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}
              className="tab"
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === 'overview' && (
          <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
            <div className="sticker p-5">
              <h2 className="font-display text-2xl font-extrabold">Invite your guests</h2>
              <p className="mt-1 font-semibold text-ink-soft">
                Guests open this link or scan the QR code, then take a live selfie. Each guest only sees the photos
                their face matches. The link has a secret code; reset it if it gets shared too widely.
              </p>
              <div className="mt-4 flex flex-col gap-2 sm:flex-row">
                <input readOnly value={inviteUrl} className="input min-w-0 flex-1 text-sm" aria-label="Attendee invite link" />
                <button onClick={copyInviteLink} className="btn btn-primary">
                  {inviteCopied ? 'Copied!' : 'Copy link'}
                </button>
                <button onClick={resetInvite} disabled={busyAction !== null} className="btn btn-white">
                  {busyAction === 'reset' ? 'Resetting…' : 'Reset link'}
                </button>
              </div>
              {qrUrl && (
                <div className="mt-5 flex flex-wrap items-center gap-5">
                  <img
                    src={qrUrl}
                    alt="QR code for the attendee invite link"
                    className="h-40 w-40 rounded-2xl border-[2.5px] border-ink bg-white p-2 shadow-[4px_4px_0_var(--ink)]"
                  />
                  <div className="space-y-2">
                    <p className="font-display text-lg font-extrabold">Print it. Stick it. Done.</p>
                    <p className="text-sm font-semibold text-ink-soft">Put it on tables, screens or the welcome board.</p>
                    <a href={qrUrl} download={`event-${eventId}-qr.png`} className="btn btn-sun btn-sm">
                      Download QR code
                    </a>
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-6">
              <div className="sticker bg-sun-soft p-5">
                <div className="flex items-center gap-3">
                  <Snappy pose="photo" size={80} bob={false} className="shrink-0" />
                  <div>
                    <h2 className="font-display text-xl font-extrabold">Photos</h2>
                    <p className="text-sm font-semibold text-ink-soft">Upload more or browse everything.</p>
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link href={`/dashboard/events/${eventId}/upload`} className="btn btn-primary btn-sm">
                    Upload photos
                  </Link>
                  <Link href={`/events/${eventId}/gallery`} className="btn btn-white btn-sm">
                    View all photos
                  </Link>
                </div>
              </div>

              {actionError && (
                <p className="alert alert-error" role="alert">
                  {actionError}
                </p>
              )}

              <div className="sticker bg-tomato-soft p-5">
                <h2 className="font-display text-xl font-extrabold">Delete event</h2>
                <p className="mt-1 text-sm font-semibold text-ink-soft">
                  Permanently removes the photos, the face embeddings of everyone who appears in them, and all
                  guests&apos; saved matches. This is how face data for an event is erased.
                </p>
                <button onClick={deleteEvent} disabled={busyAction !== null} className="btn btn-danger btn-sm mt-3">
                  {busyAction === 'delete' ? 'Deleting…' : 'Delete event'}
                </button>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'processing' && (
          <div className="sticker max-w-3xl p-6">
            {status ? (
              <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
                <Snappy pose={status.pending > 0 ? 'search' : 'celebrate'} size={110} className="shrink-0" />
                <div className="flex-1">
                  <p className="font-display text-2xl font-extrabold">
                    {status.pending > 0 ? 'Finding faces…' : 'All photos processed!'}
                  </p>
                  <p className="mt-1 font-semibold text-ink-soft">
                    {status.processed} of {status.total} photos processed
                  </p>
                  <div className="mt-3 h-6 w-full overflow-hidden rounded-full border-[2.5px] border-ink bg-white">
                    <div className="h-full bg-mint transition-all" style={{ width: `${status.progress}%` }} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <span className="chip bg-mint-soft">{status.done} indexed</span>
                    <span className="chip bg-sun-soft">{status.duplicate} duplicates skipped</span>
                    <span className="chip bg-tomato-soft">{status.failed} failed</span>
                    <span className="chip">{status.pending} waiting</span>
                  </div>
                </div>
              </div>
            ) : (
              <p className="font-semibold text-ink-soft">Loading processing status…</p>
            )}
          </div>
        )}

        {activeTab === 'analytics' && (
          <div>
            {analyticsError ? (
              <p className="alert alert-error">{analyticsError}</p>
            ) : analytics ? (
              <EventAnalytics data={analytics} eventId={eventId} />
            ) : (
              <p className="font-semibold text-ink-soft">Loading analytics…</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

