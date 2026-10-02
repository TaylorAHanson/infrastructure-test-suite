// Minimal typed fetch client. All paths are relative so the same build works
// behind the Databricks Apps proxy and the Vite dev proxy.
const BASE = '/api/v1'

/** A non-2xx response. `body` is the parsed JSON body when there is one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    statusText: string,
    readonly text: string,
    readonly body: unknown,
  ) {
    super(`${status} ${statusText}: ${text}`)
  }

  /** FastAPI's `detail`, when present. */
  get detail(): unknown {
    return this.body && typeof this.body === 'object' && 'detail' in this.body
      ? (this.body as { detail: unknown }).detail
      : undefined
  }
}

/** A human-readable message for any thrown error. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail
    if (typeof d === 'string') return d
    if (d && typeof d === 'object' && 'message' in d) return String((d as { message: unknown }).message)
    if (Array.isArray(d)) return d.map((x) => (x && typeof x === 'object' && 'msg' in x ? x.msg : String(x))).join('; ')
    return e.text || e.message
  }
  return e instanceof Error ? e.message : String(e)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    let body: unknown
    try {
      body = JSON.parse(text)
    } catch {
      body = undefined
    }
    throw new ApiError(res.status, res.statusText, text, body)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
