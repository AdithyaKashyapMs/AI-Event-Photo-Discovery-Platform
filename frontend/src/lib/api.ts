// Base URL for the FastAPI backend
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Centralized fetch wrapper that attaches JWT and handles errors
export async function apiFetch(path: string, options: RequestInit = {}) {
  const url = `${API_BASE}${path}`;
  const isFormData = options.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers as Record<string, string>),
  };

  // Retrieve token from browser cookies
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(url, { ...options, headers });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || 'Request failed');
  }

  return res.json();
}

// Store the JWT token in a browser cookie
export function setToken(token: string) {
  document.cookie = `token=${token}; path=/; max-age=${60 * 60 * 24}`;
}

// Remove the JWT token (logout)
export function removeToken() {
  document.cookie = 'token=; path=/; max-age=0';
}

// Read the JWT token from cookies
export function getToken(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie
    .split('; ')
    .find((row) => row.startsWith('token='));
  return match ? match.split('=')[1] : null;
}