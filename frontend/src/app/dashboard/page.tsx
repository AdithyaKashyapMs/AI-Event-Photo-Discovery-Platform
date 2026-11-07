'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import Card from '@/components/Card';
import Sidebar from '@/components/Sidebar';

// Type for event data from the API
interface Event {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
}

export default function DashboardPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  // Fetch all events belonging to this host
  useEffect(() => {
    async function fetchEvents() {
      try {
        const data = await apiFetch('/events/');
        setEvents(data);
      } catch (error) {
        console.error('Failed to fetch events:', error);
      } finally {
        setLoading(false);
      }
    }
    fetchEvents();
  }, []);

  return (
    <div className="flex min-h-screen">
      <Sidebar
        links={[
          { href: '/dashboard', label: 'Dashboard' },
          { href: '/dashboard/events/new', label: 'Create Event' },
        ]}
      />
      <main className="flex-1 p-8">
        {/* Header with create button */}
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-2xl font-bold">My Events</h1>
          <button
            onClick={() => router.push('/dashboard/events/new')}
            className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
          >
            Create Event
          </button>
        </div>

        {/* Event grid */}
        {loading ? (
          <p>Loading events...</p>
        ) : events.length === 0 ? (
          <p className="text-gray-500">No events yet. Create your first event to get started.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {events.map((event) => (
              <Card key={event.id} title={event.name}>
                <button
                  type="button"
                  className="w-full text-left"
                  onClick={() => router.push(`/dashboard/events/${event.id}`)}
                >
                <p className="text-gray-500 text-sm mt-1">{event.description}</p>
                <p className="text-gray-400 text-xs mt-2">
                  Created {new Date(event.created_at).toLocaleDateString()}
                </p>
                </button>
              </Card>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}