// What face data the platform stores, in plain language. Keep in sync with
// backend/app/routers/users.py and the README's "Configuration and Security".
export default function PrivacyNotice({ className = '' }: { className?: string }) {
  return (
    <details className={`sticker-flat bg-mint-soft p-3 text-sm font-semibold text-ink-soft ${className}`}>
      <summary className="cursor-pointer select-none font-display text-base font-extrabold text-ink">🛡️ What face data is stored?</summary>
      <ul className="mt-2 list-disc space-y-1.5 pl-4">
        <li>
          <span className="text-ink font-extrabold">Your selfie: not stored.</span> The camera frames and the face
          embedding made from them are used for this search only, then discarded.
        </li>
        <li>
          <span className="text-ink font-extrabold">Event photos: stored.</span> For every face in the photos the host
          uploads, we store a face embedding (a list of numbers describing the face) so attendees can be matched.
          This includes your face if you appear in the photos.
        </li>
        <li>
          <span className="text-ink font-extrabold">Your matches: stored.</span> We save which photos your search matched
          and their similarity scores, so only you can open them.
        </li>
        <li>
          <span className="text-ink font-extrabold">Deleting:</span> you can delete your saved matches from your
          dashboard. The event&apos;s face embeddings, photos and matches are deleted when the host deletes the
          event; ask the host if you want that done.
        </li>
      </ul>
    </details>
  )
}
