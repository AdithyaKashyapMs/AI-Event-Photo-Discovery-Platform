'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { hasSession, signOut } from '../lib/api';
import { SnappyHead } from './Snappy';

export default function Navbar() {
  const pathname = usePathname();
  const [loggedIn, setLoggedIn] = useState(false);

  // Re-check auth state on every route change
  useEffect(() => {
    setLoggedIn(hasSession());
  }, [pathname]);

  const handleLogout = async () => {
    await signOut();
    setLoggedIn(false);
    window.location.href = '/';
  };

  return (
    <nav className="sticky top-0 z-40 border-b-[2.5px] border-ink bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
        <Link href="/" className="flex items-center gap-2" aria-label="EventSnap home">
          <SnappyHead size={34} />
          <span className="font-display text-2xl font-extrabold leading-none">
            Event<span className="text-tomato">Snap</span>
          </span>
        </Link>
        <div className="flex items-center gap-2 sm:gap-3">
          {loggedIn ? (
            <>
              <Link href="/dashboard" className="btn btn-white btn-sm">
                Dashboard
              </Link>
              <button onClick={handleLogout} className="btn btn-sun btn-sm">
                Logout
              </button>
            </>
          ) : (
            <>
              <Link href="/auth/login" className="btn btn-white btn-sm">
                Login
              </Link>
              <Link href="/auth/register" className="btn btn-primary btn-sm">
                Sign up
              </Link>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
