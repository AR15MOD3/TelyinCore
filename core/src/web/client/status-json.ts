/** Shared, bounded page reads. Only simultaneous GETs are coalesced, never cached.
 * Writes are never retried. They invalidate pending reads before and after the
 * write, including uncertain failures: old snapshots must not repaint new state.
 * Streaming/event channels and binary assets keep their dedicated transports.
 */
export class StatusHttpError extends Error {
  constructor(readonly url: string, readonly status: number) {
    super(`${url} returned ${status}`); this.name = 'StatusHttpError';
  }
}
export class StatusTimeoutError extends Error {
  constructor(readonly url: string, readonly timeoutMs: number) {
    super(`${url} timed out after ${timeoutMs}ms`); this.name = 'StatusTimeoutError';
  }
}
export class StaleStatusError extends Error {
  constructor(readonly url: string) {
    super(`${url} response was invalidated`); this.name = 'StaleStatusError';
  }
}
interface Snapshot { status: number; ok: boolean; body: string; }
const inFlight = new Map<string, { url: string; promise: Promise<Snapshot> }>();
export function invalidateStatus(url: string): void {
  for (const [key, entry] of inFlight) if (entry.url === url) inFlight.delete(key);
}
export function invalidateStatuses(urls: readonly string[]): void {
  for (const url of urls) invalidateStatus(url);
}
function snapshot(url: string, init: RequestInit, timeoutMs: number): Promise<Snapshot> {
  // Headers affect the representation. Never combine callers with different ones.
  const headers = typeof Headers === 'function'
    ? Array.from(new Headers(init.headers).entries()).sort()
    : init.headers ?? {};
  const key = JSON.stringify([url, headers, init.credentials ?? 'same-origin']);
  const shared = inFlight.get(key);
  if (shared) return shared.promise;
  let promise: Promise<Snapshot>;
  promise = (async () => {
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        (async () => {
          const response = await fetch(url, { ...init, cache: 'no-store',
            signal: controller?.signal });
          // Capture text once; every consumer gets an independent json/text reader.
          const body = typeof response.text === 'function'
            ? await response.text() : JSON.stringify(await response.json());
          if (inFlight.get(key)?.promise !== promise) throw new StaleStatusError(url);
          return { status: response.status ?? (response.ok ? 200 : 500), ok: response.ok, body };
        })(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            controller?.abort(); reject(new StatusTimeoutError(url, timeoutMs));
          }, timeoutMs);
        }),
      ]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      if (inFlight.get(key)?.promise === promise) inFlight.delete(key);
    }
  })();
  inFlight.set(key, { url, promise });
  return promise;
}
/** JSON/text APIs only. Preserve HTTP status and error body for existing forms. */
export async function pageFetch(url: string, init: RequestInit = {},
  timeoutMs = 8000): Promise<Response> {
  if ((init.method ?? 'GET').toUpperCase() !== 'GET') {
    inFlight.clear();
    try { return await fetch(url, init); }
    finally { inFlight.clear(); }
  }
  const result = await snapshot(url, init, timeoutMs);
  // Do not return a shared Response stream: two json() consumers would conflict.
  return { ok: result.ok, status: result.status,
    text: async () => result.body, json: async () => JSON.parse(result.body),
  } as Response;
}
export async function getStatusJson<T>(url: string, timeoutMs = 8000): Promise<T> {
  const response = await pageFetch(url, {}, timeoutMs);
  if (!response.ok) throw new StatusHttpError(url, response.status);
  return await response.json() as T;
}
