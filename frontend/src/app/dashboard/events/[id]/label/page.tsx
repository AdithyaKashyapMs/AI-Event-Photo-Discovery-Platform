'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { API_BASE, apiFetch, saveBlob } from '@/lib/api';
import Sidebar from '@/components/Sidebar';
import Snappy from '@/components/Snappy';

// Must match NOT_A_FACE in backend/app/models.py
const NOT_A_FACE = '__not_a_face__';

interface Face {
  face_id: number;
  photo_id: number;
  quality: number;
  width: number | null;
  person: string | null;
  similarity: number | null;
  crop_url: string;
}

interface Overview {
  total_faces: number;
  labelled_faces: number;
  people: { person: string; faces: number; photos: number }[];
  groups: { faces: Face[]; unlabelled: number }[];
}

type Mode = { kind: 'groups' } | { kind: 'suggestions'; person: string; faces: Face[]; labelled: Face[] };

/** One face crop that can be ticked. Crops load with the session cookie. */
function FaceTile({
  face,
  selected,
  onToggle,
  caption,
}: {
  face: Face;
  selected: boolean;
  onToggle?: () => void;
  caption?: string;
}) {
  const done = face.person !== null;
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!onToggle}
      aria-pressed={onToggle ? selected : undefined}
      className={`relative overflow-hidden rounded-xl border-[2.5px] text-left transition ${
        selected ? 'border-tomato shadow-[3px_3px_0_var(--ink)]' : 'border-ink/30'
      } ${done ? 'opacity-50' : ''} disabled:cursor-default`}
      title={`Photo ${face.photo_id} · sharpness ${Math.round(face.quality * 100)}% · ${face.width ?? '?'} px wide`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`${API_BASE}${face.crop_url}`}
        alt={`Face from photo ${face.photo_id}`}
        loading="lazy"
        className="aspect-square w-full bg-paper object-cover"
      />
      {selected && (
        <span className="absolute right-1 top-1 flex h-6 w-6 items-center justify-center rounded-full border-2 border-ink bg-tomato text-xs font-extrabold text-white">
          ✓
        </span>
      )}
      <span className="block truncate bg-white px-1.5 py-0.5 text-[11px] font-bold text-ink-soft">
        {caption ?? (face.person === NOT_A_FACE ? 'not a face' : face.person ?? `photo ${face.photo_id}`)}
      </span>
    </button>
  );
}

export default function LabelFacesPage() {
  const { id: eventId } = useParams<{ id: string }>();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [mode, setMode] = useState<Mode>({ kind: 'groups' });
  const [groupIndex, setGroupIndex] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [toRemove, setToRemove] = useState<Set<number>>(new Set());
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const data: Overview = await apiFetch(`/events/${eventId}/labelling`);
    setOverview(data);
    return data;
  }, [eventId]);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : 'Could not load faces'));
  }, [load]);

  const unfinished = useMemo(() => overview?.groups.filter((g) => g.unlabelled > 0) ?? [], [overview]);
  const group = unfinished[groupIndex % Math.max(unfinished.length, 1)];

  // Preselect the unlabelled faces of a new group.
  useEffect(() => {
    if (mode.kind === 'groups' && group) {
      setSelected(new Set(group.faces.filter((f) => f.person === null).map((f) => f.face_id)));
    }
  }, [group, mode.kind]);

  function toggle(faceId: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(faceId)) next.delete(faceId);
      else next.add(faceId);
      return next;
    });
  }

  async function save(person: string) {
    if (!selected.size) return;
    setBusy(true);
    setError('');
    try {
      await apiFetch(`/events/${eventId}/labels`, {
        method: 'PUT',
        body: JSON.stringify({ assignments: [...selected].map((face_id) => ({ face_id, person })) }),
      });
      await load();
      if (person !== NOT_A_FACE) {
        // Right after naming someone, look for their harder faces.
        await openSuggestions(person);
      } else {
        setSelected(new Set());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  }

  async function openSuggestions(person: string) {
    setError('');
    const data: { faces: Face[]; labelled: Face[] } = await apiFetch(
      `/events/${eventId}/labelling/suggestions?person=${encodeURIComponent(person)}`,
    );
    setSelected(new Set());
    setToRemove(new Set());
    setName(person);
    setMode({ kind: 'suggestions', person, faces: data.faces, labelled: data.labelled });
  }

  async function removeLabels(person: string) {
    if (!toRemove.size) return;
    setBusy(true);
    setError('');
    try {
      await apiFetch(`/events/${eventId}/labels`, {
        method: 'PUT',
        body: JSON.stringify({ assignments: [...toRemove].map((face_id) => ({ face_id, person: null })) }),
      });
      const data = await load();
      // Removing the last face of a person leaves nothing to compare with.
      if (data.people.some((p) => p.person === person)) await openSuggestions(person);
      else backToGroups();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove labels');
    } finally {
      setBusy(false);
    }
  }

  async function addSuggestions(person: string) {
    if (selected.size) await save(person);
    else backToGroups();
  }

  function backToGroups() {
    setMode({ kind: 'groups' });
    setName('');
  }

  async function exportLabels() {
    const data = await apiFetch(`/events/${eventId}/labels/export`);
    saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), `event-${eventId}-labels.json`);
  }

  const people = overview?.people ?? [];
  const progress = overview && overview.total_faces ? Math.round((overview.labelled_faces / overview.total_faces) * 100) : 0;

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
      <div className="flex-1 space-y-6 px-4 py-8 sm:px-8">
        <section className="sticker-lg block-plum flex items-center gap-4 p-5 sm:p-6">
          <Snappy pose="confused" size={90} className="hidden shrink-0 sm:block" />
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-3xl font-extrabold">Label faces</h1>
            <p className="mt-1 font-semibold opacity-90">
              Tell the app who is who, so it can be tested against the right answers. Only you see this; guests are
              never affected.
            </p>
          </div>
        </section>

        {error && <p className="alert alert-error" role="alert">{error}</p>}

        {overview && (
          <section className="sticker p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <p className="font-bold">
                <span className="font-display text-2xl font-extrabold">{overview.labelled_faces}</span> of{' '}
                {overview.total_faces} faces labelled
              </p>
              <button onClick={exportLabels} className="btn btn-white btn-sm" disabled={!people.length}>
                Export labels (JSON)
              </button>
            </div>
            <div
              className="mt-2 h-4 overflow-hidden rounded-full border-2 border-ink bg-plum-soft"
              role="progressbar"
              aria-valuenow={progress}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Faces labelled"
            >
              <div className="h-full rounded-r-full bg-plum" style={{ width: `${progress}%` }} />
            </div>
            {people.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {people.map((p) => (
                  <button
                    key={p.person}
                    onClick={() => openSuggestions(p.person).catch((err) => setError(err.message))}
                    className="chip hover:bg-sun-soft"
                    title="Find more faces of this person"
                  >
                    {p.person} · {p.photos} photo{p.photos === 1 ? '' : 's'}
                  </button>
                ))}
              </div>
            )}
          </section>
        )}

        {overview && mode.kind === 'groups' && (
          <section className="sticker p-5">
            {group ? (
              <>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="font-display text-xl font-extrabold">
                    These faces look alike ({group.faces.length})
                  </h2>
                  <span className="text-sm font-bold text-ink-soft">
                    Group {(groupIndex % unfinished.length) + 1} of {unfinished.length} to do
                  </span>
                </div>
                <p className="mt-1 text-sm font-semibold text-ink-soft">
                  Untick anyone who is a different person, then type the name. Mixed groups are fine: label one person,
                  the rest stay for later.
                </p>
                <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-8">
                  {group.faces.map((face) => (
                    <FaceTile
                      key={face.face_id}
                      face={face}
                      selected={selected.has(face.face_id)}
                      onToggle={face.person === null ? () => toggle(face.face_id) : undefined}
                    />
                  ))}
                </div>
                <form
                  className="mt-4 flex flex-col gap-2 sm:flex-row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (name.trim()) void save(name.trim());
                  }}
                >
                  <label htmlFor="person-name" className="sr-only">
                    Person&apos;s name
                  </label>
                  <input
                    id="person-name"
                    className="input sm:max-w-xs"
                    placeholder="Who is this? e.g. Rahul"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    list="known-people"
                    maxLength={60}
                    autoComplete="off"
                  />
                  <datalist id="known-people">
                    {people.map((p) => (
                      <option key={p.person} value={p.person} />
                    ))}
                  </datalist>
                  <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || !selected.size}>
                    Save {selected.size} face{selected.size === 1 ? '' : 's'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-white"
                    disabled={busy || !selected.size}
                    onClick={() => save(NOT_A_FACE)}
                  >
                    Not a face
                  </button>
                  <button
                    type="button"
                    className="btn btn-white"
                    disabled={busy}
                    onClick={() => {
                      setGroupIndex((i) => i + 1);
                      setName('');
                    }}
                  >
                    Skip
                  </button>
                </form>
              </>
            ) : (
              <div className="flex items-center gap-4">
                <Snappy pose="celebrate" size={90} />
                <div>
                  <h2 className="font-display text-xl font-extrabold">Every face is labelled</h2>
                  <p className="font-semibold text-ink-soft">
                    Run <code>python -m scripts.evaluate_matching --event-id {eventId}</code> in the backend folder.
                  </p>
                </div>
              </div>
            )}
          </section>
        )}

        {mode.kind === 'suggestions' && (
          <section className="sticker p-5">
            <h2 className="font-display text-xl font-extrabold">
              Labelled as {mode.person} ({mode.labelled.length})
            </h2>
            <p className="mt-1 text-sm font-semibold text-ink-soft">
              Least typical first, so a wrong label usually shows up at the start. Tick any face that is not{' '}
              {mode.person} and remove its label.
            </p>
            <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-8">
              {mode.labelled.map((face) => (
                <FaceTile
                  key={face.face_id}
                  face={{ ...face, person: null }}
                  selected={toRemove.has(face.face_id)}
                  onToggle={() =>
                    setToRemove((current) => {
                      const next = new Set(current);
                      if (next.has(face.face_id)) next.delete(face.face_id);
                      else next.add(face.face_id);
                      return next;
                    })
                  }
                  caption={`photo ${face.photo_id} · ${Math.round((face.similarity ?? 0) * 100)}% typical`}
                />
              ))}
            </div>
            <button
              className="btn btn-danger btn-sm mt-3"
              disabled={busy || !toRemove.size}
              onClick={() => removeLabels(mode.person)}
            >
              Remove label from {toRemove.size} face{toRemove.size === 1 ? '' : 's'}
            </button>
          </section>
        )}

        {mode.kind === 'suggestions' && (
          <section className="sticker p-5">
            <h2 className="font-display text-xl font-extrabold">Is {mode.person} in any of these?</h2>
            <p className="mt-1 text-sm font-semibold text-ink-soft">
              These unlabelled faces are the closest to {mode.person}, including ones the app finds hard (side views,
              blur, small faces in the background). Tick every one that is really {mode.person}. This is what makes the
              accuracy test honest.
            </p>
            {mode.faces.length ? (
              <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-8">
                {mode.faces.map((face) => (
                  <FaceTile
                    key={face.face_id}
                    face={face}
                    selected={selected.has(face.face_id)}
                    onToggle={() => toggle(face.face_id)}
                    caption={`${Math.round((face.similarity ?? 0) * 100)}% alike`}
                  />
                ))}
              </div>
            ) : (
              <p className="mt-4 font-semibold">No more unlabelled faces look like {mode.person}.</p>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="btn btn-primary" disabled={busy} onClick={() => addSuggestions(mode.person)}>
                {selected.size ? `Add ${selected.size} to ${mode.person}` : 'None of these — next group'}
              </button>
              <button className="btn btn-white" disabled={busy} onClick={backToGroups}>
                Back to groups
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
