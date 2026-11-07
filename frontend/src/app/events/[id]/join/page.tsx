'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { API_BASE, setToken } from '@/lib/api'

// Event join page: handles account creation and event membership
export default function JoinEventPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const eventId = params.id

  // Form state for registration/login
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isRegistering, setIsRegistering] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [eventName, setEventName] = useState('Event')

  useEffect(() => {
    fetch(`${API_BASE}/events/${eventId}/public`)
      .then((res) => {
        if (!res.ok) throw new Error('Event not found')
        return res.json()
      })
      .then((event) => setEventName(event.name))
      .catch(() => setEventName('Event'))
  }, [eventId])

  // Join the event after authentication
  const handleJoinEvent = async (token: string) => {
    const res = await fetch(`${API_BASE}/events/${eventId}/join`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    })
    if (!res.ok) {
      const data = await res.json()
      throw new Error(data.detail || 'Failed to join event')
    }
  }

  // Handle form submission for register or login
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      // Register or login based on toggle
      const endpoint = isRegistering ? '/auth/register' : '/auth/login'
      const body = isRegistering
        ? { email, password, role: 'attendee' }
        : { email, password }

      const authRes = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })

      if (!authRes.ok) {
        const data = await authRes.json()
        throw new Error(data.detail || 'Authentication failed')
      }

      const authData = await authRes.json()
      const token = authData.access_token

      // Store token in cookie for subsequent requests
      setToken(token)

      // Join the event with the new token
      await handleJoinEvent(token)

      // Redirect to selfie capture page
      router.push(`/events/${eventId}/selfie`)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="max-w-md w-full bg-white rounded-lg shadow-md p-8">
        <h1 className="text-2xl font-bold text-center mb-2">Join {eventName}</h1>
        <p className="text-gray-600 text-center mb-6">
          Create an account or sign in to find your photos.
        </p>

        {error && (
          <div className="bg-red-50 text-red-700 p-3 rounded mb-4 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full border rounded-md px-3 py-2 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              className="w-full border rounded-md px-3 py-2 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? 'Joining...' : (isRegistering ? 'Create Account & Join' : 'Sign In & Join')}
          </button>
        </form>

        <p className="text-center text-sm text-gray-600 mt-4">
          {isRegistering ? 'Already have an account?' : 'Need an account?'}{' '}
          <button
            onClick={() => setIsRegistering(!isRegistering)}
            className="text-blue-600 hover:underline"
          >
            {isRegistering ? 'Sign in' : 'Register'}
          </button>
        </p>
      </div>
    </div>
  )
}