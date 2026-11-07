'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { getToken, removeToken } from '../lib/api';
import { useEffect, useState } from 'react';

export default function Navbar() {
  const pathname = usePathname();
  const [loggedIn, setLoggedIn] = useState(false);

  // Re-check auth state on every route change
  useEffect(() => {
    setLoggedIn(!!getToken());
  }, [pathname]);

  const handleLogout = () => {
    removeToken();
    setLoggedIn(false);
    window.location.href = '/';
  };

  return (
    <nav className="bg-gray-900 border-b border-gray-800 px-6 py-4">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <Link href="/" className="text-xl font-bold text-white">
          EventSnap
        </Link>
        <div className="flex items-center gap-4">
          {loggedIn ? (
            <>
              <Link href="/dashboard" className="text-gray-300 hover:text-white">
                Dashboard
              </Link>
              <button onClick={handleLogout} className="text-gray-300 hover:text-white">
                Logout
              </button>
            </>
          ) : (
            <>
              <Link href="/auth/login" className="text-gray-300 hover:text-white">
                Login
              </Link>
              <Link
                href="/auth/register"
                className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
              >
                Sign Up
              </Link>
            </>
          ) }
        </div>
      </div>
    </nav>
  );
}