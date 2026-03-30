/**
 * Fetches the API bearer token from the backend on startup.
 *
 * The token is served at GET /api/auth/token (unauthenticated) and is
 * used for all subsequent API calls and WebSocket connections.
 */

const API_BASE = "http://localhost:8080/api";

let _token: string | null = null;
let _fetchPromise: Promise<string | null> | null = null;

export async function getApiToken(): Promise<string | null> {
  if (_token) return _token;

  // Avoid duplicate fetches if called concurrently
  if (_fetchPromise) return _fetchPromise;

  _fetchPromise = (async () => {
    try {
      const resp = await fetch(`${API_BASE}/auth/token`);
      if (!resp.ok) return null;
      const data = await resp.json();
      _token = data.token ?? null;
      return _token;
    } catch {
      return null;
    }
  })();

  return _fetchPromise;
}

/**
 * Wrapper around fetch() that automatically injects the bearer token.
 *
 * Accepts paths like "/api/sessions" (keeps as-is, uses relative URL
 * so Vite proxy or same-origin works) or "/sessions" (prepends /api).
 */
export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const token = await getApiToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // Use relative URLs so the Vite dev proxy handles routing
  const url = path.startsWith("/api/") ? path : `/api${path}`;
  return fetch(url, { ...options, headers });
}
