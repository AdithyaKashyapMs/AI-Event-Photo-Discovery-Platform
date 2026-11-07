'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import Sidebar from '@/components/Sidebar';

// Types for API responses
interface Event {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
}

interface Analytics {
  total_photos: number;
  faces_detected: number;
  processing_progress: number;
  unique_people: number;
  hierarchy: {
    branches: number;
    indexed_faces: number;
  };
}

interface ProcessingStatus {
  processed: number;
  total: number;
}

type TabName = 'overview' | 'upload' | 'processing' | 'analytics';

export default function EventDetailPage() {
  const params = useParams<{ id: string }>();
  const eventId = params.id;
  const [event, setEvent] = useState<Event | null>(null);
  const [activeTab, setActiveTab] = useState<TabName>('overview');
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [processingStatus, setProcessingStatus] = useState<ProcessingStatus | null>(null);
  const [inviteCopied, setInviteCopied] = useState(false);

  const inviteUrl =
    typeof window === 'undefined'
      ? `/events/${eventId}/join`
      : `${window.location.origin}/events/${eventId}/join`;

  async function copyInviteLink() {
    await navigator.clipboard.writeText(inviteUrl);
    setInviteCopied(true);
    window.setTimeout(() => setInviteCopied(false), 2000);
  }

  // Fetch event details on mount
  useEffect(() => {
    async function fetchEvent() {
      const data = await apiFetch(`/events/${eventId}`);
      setEvent(data);
    }
    fetchEvent();
  }, [eventId]);

  // Fetch analytics when that tab is active
  useEffect(() => {
    if (activeTab === 'analytics') {
      async function fetchAnalytics() {
        const data = await apiFetch(`/events/${eventId}/analytics`);
        setAnalytics(data);
      }
      fetchAnalytics();
    }
  }, [activeTab, eventId]);

  // Poll processing status every 3 seconds when on the processing tab
  useEffect(() => {
    if (activeTab !== 'processing') return;
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
  }, [activeTab, eventId]);

  // Tab definitions
  const tabs: { key: TabName; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'upload', label: 'Upload' },
    { key: 'processing', label: 'Processing' },
    { key: 'analytics', label: 'Analytics' },
  ];

  if (!event) return <p className="p-8">Loading...</p>;

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
        <h1 className="text-2xl font-bold mb-2">{event.name}</h1>
        <p className="mb-6 text-gray-400">{event.description}</p>

        {/* Tab navigation */}
        <div className="mb-6 flex border-b border-gray-800">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-4 py-2 font-medium ${
                activeTab === tab.key
                  ? 'border-b-2 border-blue-600 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Overview tab */}
        {activeTab === 'overview' && (
          <div>
            <p className="text-gray-400">
              Created on {new Date(event.created_at).toLocaleDateString()}
            </p>
            <div className="mt-6 max-w-2xl rounded-lg border border-gray-800 bg-gray-900 p-4">
              <h2 className="font-medium text-white">Invite attendees</h2>
              <p className="mt-1 text-sm text-gray-400">
                Send this link to attendees so they can create an account and find their photos.
              </p>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <input
                  readOnly
                  value={inviteUrl}
                  className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200"
                  aria-label="Attendee invite link"
                />
                <button
                  onClick={copyInviteLink}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
                >
                  {inviteCopied ? 'Copied' : 'Copy link'}
                </button>
              </div>
              <a
                href={inviteUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-block text-sm text-blue-600 hover:underline"
              >
                Open attendee invite page
              </a>
            </div>
          </div>
        )}

        {/* Upload tab links to upload center */}
        {activeTab === 'upload' && (
          <div>
            <a
              href={`/dashboard/events/${eventId}/upload`}
              className="bg-blue-600 text-white px-4 py-2 rounded-lg inline-block"
            >
              Go to Upload Center
            </a>
          </div>
        )}

        {/* Processing tab with live progress */}
        {activeTab === 'processing' && (
          <div>
            {processingStatus ? (
              <div>
                <p className="text-lg font-medium mb-2">
                  {processingStatus.processed} / {processingStatus.total} photos processed
                </p>
                <div className="w-full bg-gray-200 rounded-full h-4">
                  <div
                    className="bg-blue-600 h-4 rounded-full transition-all"
                    style={{
                      width: `${processingStatus.total > 0 ? (processingStatus.processed / processingStatus.total) * 100 : 0}%`,
                    }}
                  />
                </div>
              </div>
            ) : (
              <p className="text-gray-400">Loading processing status...</p>
            )}
          </div>
        )}

        {/* Analytics tab with stat cards */}
        {activeTab === 'analytics' && (
          <div>
            {analytics ? (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="Total Photos" value={analytics.total_photos} />
                <StatCard label="Faces Detected" value={analytics.faces_detected} />
                <StatCard label="Processing" value={`${analytics.processing_progress}%`} />
                <StatCard label="Unique People" value={analytics.unique_people} />
                <StatCard label="Hierarchy Branches" value={analytics.hierarchy.branches} />
                <StatCard label="Indexed Faces" value={analytics.hierarchy.indexed_faces} />
              </div>
            ) : (
              <p className="text-gray-400">Loading analytics...</p>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

// Reusable stat card component
function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
      <p className="text-sm text-gray-400">{label}</p>
      <p className="text-2xl font-bold text-white">{value}</p>
    </div>
  );
}