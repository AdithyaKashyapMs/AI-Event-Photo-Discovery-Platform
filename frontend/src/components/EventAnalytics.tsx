import type { ReactNode } from 'react';
import Link from 'next/link';
import Snappy from './Snappy';

export interface Analytics {
  total_photos: number;
  faces_detected: number;
  processing_progress: number;
  unique_people: number;
  failed_photos: number;
  processing: { total: number; done: number; duplicate: number; failed: number; pending: number };
  attendee_engagement: { total_attendees: number; attendees_with_matches: number };
  duplicate_detection: { duplicate_photos: number };
  quality_summary: { average_quality: number; low_quality_faces: number };
  hierarchy: { branches: number; indexed_faces: number };
}

// Photo-status colours, validated together for colour-blind separation (in
// this order, adjacent pairs ΔE ≥ 8). Each row also carries an icon and a
// label, so the status is never told by colour alone.
const STATUS = [
  { key: 'done', label: 'Indexed', icon: '✓', color: '#2fa572', note: 'faces searchable' },
  { key: 'pending', label: 'Waiting', icon: '⏳', color: '#d08f00', note: 'in the queue' },
  { key: 'duplicate', label: 'Duplicates', icon: '⧉', color: '#3fa7d6', note: 'skipped re-uploads' },
  { key: 'failed', label: 'Failed', icon: '✕', color: '#e5482a', note: 'could not be read' },
] as const;

const number = (value: number) => value.toLocaleString();
const percent = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

function StatTile({ label, value, detail, tint }: { label: string; value: string; detail?: string; tint: string }) {
  return (
    <div className={`sticker p-4 ${tint}`}>
      <p className="text-xs font-extrabold uppercase tracking-wide text-ink-soft">{label}</p>
      <p className="mt-1 font-display text-4xl font-extrabold leading-none">{value}</p>
      {detail && <p className="mt-2 text-sm font-semibold text-ink-soft">{detail}</p>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="sticker p-5">
      <h3 className="font-display text-xl font-extrabold">{title}</h3>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** A ratio against its whole: label + figure on one line, bar below. */
function Meter({
  label,
  part,
  whole,
  figure,
  fill,
  track,
}: {
  label: string;
  part: number;
  whole: number;
  figure: string;
  fill: string;
  track: string;
}) {
  const share = whole > 0 ? Math.min(100, (part / whole) * 100) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-bold">{label}</span>
        <span className="font-display text-lg font-extrabold tabular-nums">{figure}</span>
      </div>
      <div
        className="mt-1.5 h-4 overflow-hidden rounded-full border-2 border-ink"
        style={{ background: track }}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={whole}
        aria-valuenow={part}
        title={`${label}: ${figure}`}
      >
        <div className="h-full rounded-r-full" style={{ width: `${share}%`, background: fill }} />
      </div>
    </div>
  );
}

export default function EventAnalytics({ data, eventId }: { data: Analytics; eventId: string }) {
  const { processing, attendee_engagement: guests, quality_summary: quality, hierarchy } = data;
  const counts: Record<(typeof STATUS)[number]['key'], number> = {
    done: processing.done,
    pending: processing.pending,
    duplicate: processing.duplicate,
    failed: processing.failed,
  };

  if (processing.total === 0) {
    return (
      <div className="sticker flex flex-col items-center gap-3 p-8 text-center sm:flex-row sm:text-left">
        <Snappy pose="photo" size={120} />
        <div>
          <h3 className="font-display text-2xl font-extrabold">No photos yet</h3>
          <p className="mt-1 font-semibold text-ink-soft">Upload some photos and the numbers will show up here.</p>
          <Link href={`/dashboard/events/${eventId}/upload`} className="btn btn-primary mt-4">
            Upload photos
          </Link>
        </div>
      </div>
    );
  }

  const facesPerBranch = hierarchy.branches > 0 ? hierarchy.indexed_faces / hierarchy.branches : 0;

  return (
    <div className="space-y-6">
      {/* Headline numbers */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          tint="bg-sun-soft"
          label="Photos"
          value={number(processing.total)}
          detail={`${number(processing.done)} indexed`}
        />
        <StatTile
          tint="bg-sky-soft"
          label="Faces found"
          value={number(data.faces_detected)}
          detail={processing.done ? `≈ ${(data.faces_detected / processing.done).toFixed(1)} per photo` : undefined}
        />
        <StatTile tint="bg-plum-soft" label="People spotted" value={number(data.unique_people)} detail="estimated by face grouping" />
        <StatTile
          tint="bg-mint-soft"
          label="Guests matched"
          value={`${number(guests.attendees_with_matches)} / ${number(guests.total_attendees)}`}
          detail="found their photos"
        />
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        {/* How the uploaded photos split by status */}
        <Panel title="Photo processing">
          <div className="flex h-5 gap-[2px] overflow-hidden rounded-full border-2 border-ink bg-white" aria-hidden>
            {STATUS.map((status) =>
              counts[status.key] > 0 ? (
                <div
                  key={status.key}
                  style={{ flexGrow: counts[status.key], background: status.color }}
                  title={`${status.label}: ${number(counts[status.key])} (${percent(counts[status.key], processing.total)}%)`}
                />
              ) : null,
            )}
          </div>
          <ul className="mt-4 divide-y-2 divide-dashed divide-ink/15">
            {STATUS.map((status) => (
              <li key={status.key} className="flex items-center gap-3 py-2">
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2 border-ink text-xs font-extrabold text-white"
                  style={{ background: status.color }}
                  aria-hidden
                >
                  {status.icon}
                </span>
                <span className="flex-1">
                  <span className="font-bold">{status.label}</span>
                  <span className="ml-2 text-sm font-semibold text-ink-soft">{status.note}</span>
                </span>
                <span className="font-display text-lg font-extrabold tabular-nums">{number(counts[status.key])}</span>
                <span className="w-12 text-right text-sm font-bold tabular-nums text-ink-soft">
                  {percent(counts[status.key], processing.total)}%
                </span>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-6">
          <Panel title="Guests">
            <div className="space-y-4">
              <Meter
                label="Found their photos"
                part={guests.attendees_with_matches}
                whole={guests.total_attendees}
                figure={`${percent(guests.attendees_with_matches, guests.total_attendees)}%`}
                fill="var(--mint)"
                track="var(--mint-soft)"
              />
              <p className="text-sm font-semibold text-ink-soft">
                {guests.total_attendees === 0
                  ? 'No guests have joined yet. Share the invite link or QR code.'
                  : `${number(guests.attendees_with_matches)} of ${number(guests.total_attendees)} guests who joined have saved matches.`}
              </p>
            </div>
          </Panel>

          <Panel title="Face quality">
            <div className="space-y-4">
              <Meter
                label="Average sharpness"
                part={quality.average_quality}
                whole={1}
                figure={`${Math.round(quality.average_quality * 100)}%`}
                fill="var(--sky)"
                track="var(--sky-soft)"
              />
              <Meter
                label="Blurry faces"
                part={quality.low_quality_faces}
                whole={data.faces_detected}
                figure={`${number(quality.low_quality_faces)} of ${number(data.faces_detected)}`}
                fill="var(--tomato)"
                track="var(--tomato-soft)"
              />
            </div>
          </Panel>
        </div>
      </div>

      <Panel title="Search index">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[
            ['Faces indexed', number(hierarchy.indexed_faces)],
            ['Hierarchy branches', number(hierarchy.branches)],
            ['Faces per branch', facesPerBranch ? facesPerBranch.toFixed(1) : '–'],
          ].map(([label, value]) => (
            <div key={label} className="sticker-flat bg-paper p-3">
              <dt className="text-xs font-extrabold uppercase tracking-wide text-ink-soft">{label}</dt>
              <dd className="mt-1 font-display text-2xl font-extrabold">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-sm font-semibold text-ink-soft">
          The adaptive hierarchy groups similar faces so each search first checks only the closest branches.
        </p>
      </Panel>
    </div>
  );
}
