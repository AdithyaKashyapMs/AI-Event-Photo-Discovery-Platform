'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  API_BASE,
  apiFetch,
  errorMessage,
  getCurrentUser,
  signOut,
  type CurrentUser,
} from '@/lib/api'
import PrivacyNotice from '@/components/PrivacyNotice'
import AuthShell from '@/components/AuthShell'
import Snappy from '@/components/Snappy'

// Must match MIN_PASSWORD_LENGTH in backend/app/routers/auth.py
const MIN_PASSWORD_LENGTH = 8

// Invite page from the host's link or QR code (/join/{token}): sign in or
// register, then join. The token, not the event ID, grants access.
export default function JoinEventPage() {
  const params = useParams<{ token: string }>()
  const router = useRouter()
  const token = params.token

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isRegistering, setIsRegistering] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [eventName, setEventName] = useState('')
  const [eventMissing, setEventMissing] = useState(false)
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/invites/${encodeURIComponent(token)}`)
      .then((res) => {
        if (!res.ok) throw new Error('Invalid invite')
        return res.json()
      })
      .then((event) => setEventName(event.name))
      .catch(() => setEventMissing(true))
    void getCurrentUser().then(setCurrentUser)
  }, [token])

  const joinAndContinue = async () => {
    const joined: { event_id: number } = await apiFetch(
      `/invites/${encodeURIComponent(token)}/join`,
      { method: 'POST' },
    )
    router.push(`/events/${joined.event_id}/selfie`)
  }

  const joinAsCurrentUser = async () => {
    setLoading(true)
    setError('')
    try {
      await joinAndContinue()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to join event')
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const endpoint = isRegistering ? '/auth/register' : '/auth/login'
      const body = isRegistering ? { email, password, role: 'attendee' } : { email, password }
      const authRes = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const authData = await authRes.json().catch(() => null)
      if (!authRes.ok) throw new Error(errorMessage(authData, 'Authentication failed'))
      if (authData.role === 'host') {
        await signOut()
        throw new Error('This is a host account. Sign in with an attendee account to join an event.')
      }
      await joinAndContinue()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
      setLoading(false)
    }
  }

  if (eventMissing) {
    return (
      <div className="mx-auto flex min-h-[calc(100vh-64px)] max-w-lg flex-col items-center justify-center px-4 text-center">
        <Snappy pose="confused" size={170} />
        <h1 className="mt-4 font-display text-3xl font-extrabold">Hmm, this link doesn&apos;t work</h1>
        <p className="mt-2 font-semibold text-ink-soft">
          The invite link is invalid or the host has replaced it. Ask your event host for the current link or QR
          code.
        </p>
      </div>
    )
  }

  return (
    <AuthShell
      block="block-tomato"
      pose="selfie"
      bubble="You were there? Let’s find you!"
      tagline="One live selfie. Only your photos."
      title={eventName ? `Join ${eventName}` : 'Join this event'}
      subtitle="Sign in, then take a 10-second live selfie to find the photos you appear in."
    >
      {error && (
        <p className="alert alert-error mb-4" role="alert">
          {error}
        </p>
      )}

      {currentUser?.role === 'attendee' ? (
        <div className="space-y-3">
          <p className="alert alert-info">
            Signed in as <span className="font-extrabold">{currentUser.email}</span>
          </p>
          <button onClick={joinAsCurrentUser} disabled={loading} className="btn btn-primary btn-block btn-lg">
            {loading ? 'Joining…' : 'Join & find my photos'}
          </button>
          <button
            onClick={async () => {
              await signOut()
              setCurrentUser(null)
            }}
            className="btn btn-white btn-block btn-sm"
          >
            Use a different account
          </button>
        </div>
      ) : (
        <>
          {currentUser?.role === 'host' && (
            <p className="alert alert-warn mb-4">
              You are signed in as a host. Hosts manage events from the dashboard; sign in with an attendee account
              below to join.
            </p>
          )}
          <div className="mb-4 grid grid-cols-2 gap-2" role="tablist" aria-label="Account">
            <button
              type="button"
              role="tab"
              aria-selected={isRegistering}
              onClick={() => {
                setIsRegistering(true)
                setError('')
              }}
              className="tab"
            >
              I&apos;m new
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!isRegistering}
              onClick={() => {
                setIsRegistering(false)
                setError('')
              }}
              className="tab"
            >
              I have an account
            </button>
          </div>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="join-email" className="label">Email</label>
              <input
                id="join-email"
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
              <label htmlFor="join-password" className="label">Password</label>
              <input
                id="join-password"
                type="password"
                autoComplete={isRegistering ? 'new-password' : 'current-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={isRegistering ? MIN_PASSWORD_LENGTH : undefined}
                className="input"
              />
              {isRegistering && <p className="hint">At least {MIN_PASSWORD_LENGTH} characters.</p>}
            </div>
            <button type="submit" disabled={loading} className="btn btn-primary btn-block btn-lg">
              {loading ? 'Joining…' : isRegistering ? 'Create account & join' : 'Sign in & join'}
            </button>
          </form>
        </>
      )}
      <PrivacyNotice className="mt-6" />
    </AuthShell>
  )
}
