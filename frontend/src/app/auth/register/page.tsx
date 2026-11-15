'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { apiFetch } from '../../../lib/api';
import AuthShell from '@/components/AuthShell';
import Snappy from '@/components/Snappy';

// Must match MIN_PASSWORD_LENGTH in backend/app/routers/auth.py
const MIN_PASSWORD_LENGTH = 8;

type Role = 'host' | 'attendee';

const ROLE_OPTIONS: { value: Role; title: string; text: string }[] = [
  { value: 'attendee', title: 'Find my photos', text: 'I was at an event' },
  { value: 'host', title: 'Host an event', text: 'I upload the photos' },
];

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('attendee');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await apiFetch('/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password, role }),
      });
      router.push('/dashboard');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      block={role === 'host' ? 'block-sky' : 'block-sun'}
      pose={role === 'host' ? 'camera' : 'selfie'}
      bubble={role === 'host' ? 'Say cheese, everyone!' : 'Let’s find you!'}
      tagline={
        role === 'host'
          ? 'Upload once. Every guest gets their own photos.'
          : 'One live selfie. Every photo you are in.'
      }
      title="Create your account"
      subtitle="Takes about 20 seconds."
    >
      {error && <p className="alert alert-error mb-4" role="alert">{error}</p>}
      <form onSubmit={handleSubmit} className="space-y-4">
        <fieldset>
          <legend className="label">I want to…</legend>
          <div className="grid grid-cols-2 gap-3">
            {ROLE_OPTIONS.map((option) => (
              <label
                key={option.value}
                className={`sticker-flat flex cursor-pointer items-center gap-2 p-2.5 transition-colors focus-within:outline focus-within:outline-[3px] focus-within:outline-sky ${
                  role === option.value ? (option.value === 'host' ? 'bg-sky-soft' : 'bg-sun-soft') : 'bg-white'
                } ${role === option.value ? 'shadow-[3px_3px_0_var(--ink)]' : ''}`}
              >
                <input
                  type="radio"
                  name="role"
                  value={option.value}
                  checked={role === option.value}
                  onChange={() => setRole(option.value)}
                  className="sr-only"
                />
                <Snappy pose={option.value === 'host' ? 'camera' : 'selfie'} size={38} bob={false} />
                <span>
                  <span className="block font-display font-extrabold leading-tight">{option.title}</span>
                  <span className="block text-xs font-semibold text-ink-soft">{option.text}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <label htmlFor="register-email" className="label">Email</label>
          <input
            id="register-email"
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
          <label htmlFor="register-password" className="label">Password</label>
          <input
            id="register-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={MIN_PASSWORD_LENGTH}
            className="input"
          />
          <p className="hint">At least {MIN_PASSWORD_LENGTH} characters.</p>
        </div>
        <button type="submit" disabled={loading} className="btn btn-primary btn-block btn-lg">
          {loading ? 'Creating…' : 'Create account'}
        </button>
      </form>
      <p className="mt-5 text-center font-semibold text-ink-soft">
        Already have an account?{' '}
        <Link href="/auth/login" className="font-extrabold text-tomato underline decoration-2 underline-offset-2">
          Log in
        </Link>
      </p>
    </AuthShell>
  );
}
