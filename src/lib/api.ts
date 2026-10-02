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

async function failure(res: Response): Promise<ApiError> {
  const text = await res.text()
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    body = undefined
  }
  return new ApiError(res.status, res.statusText, text, body)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw await failure(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** POST, then call `onEvent` with each server-sent event's JSON `data` as it arrives. */
export async function postStream<E>(
  path: string,
  body: unknown,
  onEvent: (event: E) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) throw await failure(res)
  if (!res.body) throw new Error('The response has no body to stream.')
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffered = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffered += value
    let end: number
    while ((end = buffered.indexOf('\n\n')) >= 0) {
      const frame = buffered.slice(0, end)
      buffered = buffered.slice(end + 2)
      const data = frame
        .split('\n')
        .filter((line) => line.startsWith('data: '))
        .map((line) => line.slice(6))
        .join('\n')
      if (data) onEvent(JSON.parse(data) as E)
    }
  }
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
