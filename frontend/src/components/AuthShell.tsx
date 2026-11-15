import type { ReactNode } from 'react';
import Snappy, { type SnappyPose } from './Snappy';

interface AuthShellProps {
  /** Colour block behind the mascot, e.g. "block-sun". */
  block: string;
  pose: SnappyPose;
  bubble: string;
  tagline: string;
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}

// Two-panel layout for login, sign-up and invite pages: an ad-style colour
// block with a mascot on one side, the form card on the other.
export default function AuthShell({ block, pose, bubble, tagline, title, subtitle, children }: AuthShellProps) {
  return (
    <div className="mx-auto flex min-h-[calc(100vh-64px)] max-w-5xl items-center px-4 py-10 sm:px-6">
      <div className="sticker-lg grid w-full overflow-hidden md:grid-cols-2">
        <div className={`${block} relative hidden flex-col items-center justify-center gap-4 border-r-[3px] border-ink p-8 md:flex`}>
          <div className="bubble self-start">{bubble}</div>
          <Snappy pose={pose} size={210} />
          <p className="max-w-xs text-center font-display text-xl font-bold leading-snug">{tagline}</p>
        </div>
        <div className="p-6 sm:p-10">
          <div className={`${block} -mx-6 -mt-6 mb-6 flex items-end gap-3 border-b-[3px] border-ink px-6 pt-4 md:hidden`}>
            <Snappy pose={pose} size={84} bob={false} />
            <p className="pb-4 font-display text-lg font-bold leading-snug">{bubble}</p>
          </div>
          <h1 className="font-display text-3xl font-extrabold">{title}</h1>
          {subtitle && <div className="mt-1 font-semibold text-ink-soft">{subtitle}</div>}
          <div className="mt-6">{children}</div>
        </div>
      </div>
    </div>
  );
}
