'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { apiFetch, safeNextPath } from '../../../lib/api';
import AuthShell from '@/components/AuthShell';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await apiFetch('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      // Return to the page that asked for a login, if any.
      const next = safeNextPath(new URLSearchParams(window.location.search).get('next'));
      router.push(next ?? '/dashboard');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      block="block-sky"
      pose="wave"
      bubble="Hey, welcome back!"
      tagline="Your photos are right where you left them."
      title="Log in"
      subtitle="Good to see you again."
    >
      {error && <p className="alert alert-error mb-4" role="alert">{error}</p>}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="login-email" className="label">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="input"
            placeholder="you@example.com"
          />
        </div>
        <div>
          <label htmlFor="login-password" className="label">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="input"
          />
        </div>
        <button type="submit" disabled={loading} className="btn btn-primary btn-block btn-lg">
          {loading ? 'Logging in…' : 'Log in'}
        </button>
      </form>
      <p className="mt-5 text-center font-semibold text-ink-soft">
        New here?{' '}
        <Link href="/auth/register" className="font-extrabold text-tomato underline decoration-2 underline-offset-2">
          Create an account
        </Link>
      </p>
    </AuthShell>
  );
}
