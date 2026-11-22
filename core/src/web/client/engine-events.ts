/** Saved configuration only; never publish uncommitted editor selections. */
export const ENGINE_CHANGED = 'engine-changed';
export function notifyEngineChanged(): void {
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new Event(ENGINE_CHANGED));
  }
}
export function onEngineChanged(refresh: () => void): () => void {
  window.addEventListener(ENGINE_CHANGED, refresh);
  return () => window.removeEventListener(ENGINE_CHANGED, refresh);
}
