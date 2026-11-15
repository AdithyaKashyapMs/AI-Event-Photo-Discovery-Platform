// Base URL for the FastAPI backend. Without NEXT_PUBLIC_API_URL the API is
// assumed on port 8000 of the host serving this page, so the session cookie is
// same-site whether you open localhost or 127.0.0.1.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : 'http://localhost:8000');

export type UserRole = 'host' | 'attendee';
export interface CurrentUser {
  id: number;
  email: string;
  role: UserRole;
}

// The session token is in an HttpOnly cookie set by the API, so scripts here
// (including any injected by an XSS bug) can never read it. This companion
// cookie holds no secret; it only tells the UI that a session exists.
// Must match SESSION_HINT_COOKIE in backend/app/auth.py
const SESSION_HINT_COOKIE = 'eventsnap_signed_in';

/** Turn a FastAPI error body into one readable message. */
export function errorMessage(body: unknown, fallback = 'Request failed'): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  // Validation errors arrive as a list: [{ loc, msg, ... }]
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => String((item as { msg?: unknown }).msg ?? '').replace(/^Value error, /, ''))
      .filter(Boolean)
      .join('. ') || fallback;
  }
  return fallback;
}

/** Send the user to the login page, returning here afterwards. */
export function redirectToLogin() {
  if (typeof window === 'undefined') return;
  const next = window.location.pathname + window.location.search;
  window.location.href = `/auth/login?next=${encodeURIComponent(next)}`;
}

/** Only allow redirects to paths on this site. */
export function safeNextPath(next: string | null): string | null {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : null;
}

// Fetch wrapper that sends the session cookie and turns errors into exceptions.
export async function apiFetch(path: string, options: RequestInit = {}) {
  const isFormData = options.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers as Record<string, string>),
  };

  const signedIn = hasSession();
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'include' });

  if (!res.ok) {
    // An expired or invalid session: sign out and go to the login page. A 401
    // without a session (for example a wrong password) is reported normally.
    if (res.status === 401 && signedIn) {
      clearSessionHint();
      redirectToLogin();
    }
    const body = await res.json().catch(() => null);
    throw new Error(errorMessage(body));
  }

  return res.json();
}

/** The signed-in user, or null when there is no valid session. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  if (!hasSession()) return null;
  try {
    return await apiFetch('/auth/me');
  } catch {
    return null;
  }
}

/**
 * Load a protected file (photo or QR code) with the session cookie and return a local object URL for it.
 * The caller must revoke the URL with URL.revokeObjectURL when done.
 */
export async function fetchProtectedObjectUrl(path: string, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    signal,
  });
  if (!res.ok) throw new Error('Could not load photo');
  return URL.createObjectURL(await res.blob());
}

/** Save a Blob as a file download. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  // Firefox only starts downloads from links that are in the document.
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking immediately can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** True when the API has signed this browser in (the session itself is HttpOnly). */
export function hasSession(): boolean {
  if (typeof document === 'undefined') return false;
  return document.cookie.split('; ').some((row) => row === `${SESSION_HINT_COOKIE}=1`);
}

function clearSessionHint() {
  document.cookie = `${SESSION_HINT_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
}

/** Sign out: the API clears the HttpOnly session cookie. */
export async function signOut(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
  } finally {
    clearSessionHint();
  }
}
