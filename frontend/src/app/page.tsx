import Link from 'next/link';
import Snappy, { type SnappyPose } from '@/components/Snappy';

const SCENES: { pose: SnappyPose; block: string; step: string; title: string; text: string }[] = [
  {
    pose: 'camera',
    block: 'block-sky',
    step: '1',
    title: 'The host snaps away',
    text: 'Photographers upload the whole event. We find every face in every photo, automatically.',
  },
  {
    pose: 'selfie',
    block: 'block-tomato',
    step: '2',
    title: 'You take a live selfie',
    text: 'Look straight, turn left, turn right. Ten seconds that prove it is really you, not a photo of you.',
  },
  {
    pose: 'celebrate',
    block: 'block-mint',
    step: '3',
    title: 'Your photos, only yours',
    text: 'Every photo you appear in, in one gallery. Download them all in one tap.',
  },
];

const FEATURES: { pose: SnappyPose; title: string; text: string; tint: string }[] = [
  {
    pose: 'shield',
    title: 'Private by design',
    text: 'Attendees only ever see photos their own face matched. Your selfie is never stored.',
    tint: 'bg-mint-soft',
  },
  {
    pose: 'search',
    title: 'Finds you in the crowd',
    text: 'An adaptive face index narrows the search, then double-checks so no photo of you is missed.',
    tint: 'bg-sky-soft',
  },
  {
    pose: 'photo',
    title: 'Join with a QR code',
    text: 'Hosts share a secret link or QR code. No app to install, works on any phone.',
    tint: 'bg-sun-soft',
  },
];

export default function HomePage() {
  return (
    <div>
      {/* Hero */}
      <section className="block-sun border-b-[2.5px] border-ink">
        <div className="mx-auto grid max-w-6xl items-center gap-8 px-4 py-14 sm:px-6 md:grid-cols-[1.2fr_1fr] md:py-20">
          <div>
            <span className="chip mb-5">Weddings · college fests · marathons · conferences</span>
            <h1 className="font-display text-5xl font-extrabold leading-[1.05] sm:text-6xl">
              Find every photo
              <br />
              you&apos;re in.
            </h1>
            <p className="mt-5 max-w-xl text-lg font-semibold text-ink/80">
              Hundreds of event photos, and you are in maybe twenty of them. Take one quick live selfie and
              EventSnap hands you exactly those. Nobody else&apos;s, and nobody else gets yours.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/auth/register" className="btn btn-primary btn-lg">
                Find my photos
              </Link>
              <Link href="/auth/register" className="btn btn-white btn-lg">
                I&apos;m hosting an event
              </Link>
            </div>
          </div>
          <div className="relative flex justify-center md:justify-end">
            <div className="bubble absolute left-2 top-0 hidden sm:block md:left-6">Is that me? It is me!</div>
            <Snappy pose="selfie" size={260} className="mt-14" />
          </div>
        </div>
      </section>

      {/* How it works: three ad-style scenes */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <h2 className="font-display text-4xl font-extrabold">How it works</h2>
        <p className="mt-2 text-lg font-semibold text-ink-soft">Three scenes. No scrolling through 2,000 photos.</p>
        <div className="mt-8 grid gap-6 md:grid-cols-3">
          {SCENES.map((scene) => (
            <article key={scene.step} className="sticker-lg overflow-hidden">
              <div className={`${scene.block} relative flex justify-center border-b-[3px] border-ink pb-2 pt-6`}>
                <span className="absolute left-4 top-4 flex h-10 w-10 items-center justify-center rounded-full border-[2.5px] border-ink bg-white font-display text-xl font-extrabold text-ink">
                  {scene.step}
                </span>
                <Snappy pose={scene.pose} size={150} />
              </div>
              <div className="p-5">
                <h3 className="font-display text-2xl font-extrabold">{scene.title}</h3>
                <p className="mt-2 font-semibold text-ink-soft">{scene.text}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="border-y-[2.5px] border-ink bg-white">
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-14 sm:px-6 md:grid-cols-3">
          {FEATURES.map((feature) => (
            <div key={feature.title} className={`sticker flex items-center gap-4 p-4 ${feature.tint}`}>
              <Snappy pose={feature.pose} size={92} bob={false} className="shrink-0" />
              <div>
                <h3 className="font-display text-xl font-extrabold">{feature.title}</h3>
                <p className="mt-1 text-sm font-semibold text-ink-soft">{feature.text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Call to action */}
      <section className="block-plum border-b-[2.5px] border-ink">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-6 px-4 py-14 text-center sm:px-6 md:flex-row md:text-left">
          <div className="flex -space-x-6">
            <Snappy pose="wave" size={110} />
            <Snappy pose="camera" size={110} className="[animation-delay:0.4s]" />
            <Snappy pose="celebrate" size={110} className="[animation-delay:0.8s]" />
          </div>
          <div className="flex-1">
            <h2 className="font-display text-4xl font-extrabold">Hosting an event?</h2>
            <p className="mt-2 text-lg font-semibold text-white/90">
              Create an event, upload your photos, share one QR code. Your guests do the rest.
            </p>
          </div>
          <Link href="/auth/register" className="btn btn-sun btn-lg">
            Create a free account
          </Link>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-4 py-8 text-sm font-semibold text-ink-soft sm:px-6">
        EventSnap · selfies are checked live and never stored.
      </footer>
    </div>
  );
}
