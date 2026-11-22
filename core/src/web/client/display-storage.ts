/** Browser display preferences only. This namespace grants no account authority. */
import { getStatusJson } from './status-json';

const localKeys = new Set(['ui-language', 'telyincore-look-mode', 'telyincore-look-preset',
  'telyincore-look-resolved-v1', 'telyincore.rail-width']);
const sessionKeys = new Set(['plugin-nav', 'engine-drafts']);
const localEdits = new Map<string, string>();
const sessionEdits = new Map<string, string>();
let prefix: string | null = null;
let loading: Promise<boolean> | null = null;
const listeners = new Set<(prefix: string) => void>();

export function displayStoragePrefix(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as { coreRoot?: unknown; active?: unknown; bots?: unknown };
  if (typeof row.coreRoot !== 'string' || !/^(?:[A-Za-z]:[\\/]|\/)/.test(row.coreRoot)
      || /[\x00-\x1f]/.test(row.coreRoot) || row.coreRoot.length > 4096
      || typeof row.active !== 'string' || row.active.length === 0 || row.active.length > 64
      || row.active.trim() !== row.active || /[\\/\x00-\x1f]/.test(row.active)
      || !Array.isArray(row.bots) || !row.bots.some(bot => bot && typeof bot === 'object'
        && (bot as { id?: unknown }).id === row.active)) return null;
  const root = row.coreRoot.replace(/\\/g, '/').replace(/\/+$/, '');
  return `telyincore.display.v1:${encodeURIComponent(root)}:${encodeURIComponent(row.active)}:`;
}

function get(key: string, session: boolean): string | null {
  if (!(session ? sessionKeys : localKeys).has(key)) return null;
  const edits = session ? sessionEdits : localEdits;
  if (prefix === null) return edits.get(key) ?? null;
  try {
    const storage = session ? window.sessionStorage : window.localStorage;
    if (edits.has(key)) {
      storage.setItem(prefix + key, edits.get(key)!);
      edits.delete(key);
    }
    return storage.getItem(prefix + key);
  } catch { return edits.get(key) ?? null; }
}
function put(key: string, value: string, session: boolean): void {
  if (!(session ? sessionKeys : localKeys).has(key)) return;
  (session ? sessionEdits : localEdits).set(key, value);
  if (prefix === null) return; // Document-local edits remain usable during identity lookup.
  try {
    (session ? window.sessionStorage : window.localStorage).setItem(prefix + key, value);
    (session ? sessionEdits : localEdits).delete(key);
  }
  catch { /* Optional browser persistence; no server setting is changed. */ }
}
export const readDisplayLocal = (key: string): string | null => get(key, false);
export const writeDisplayLocal = (key: string, value: string): void => put(key, value, false);
export const readDisplaySession = (key: string): string | null => get(key, true);
export const writeDisplaySession = (key: string, value: string): void => put(key, value, true);

export function onDisplayScopeReady(listener: (prefix: string) => void): () => void {
  if (prefix !== null) listener(prefix);
  else listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function initializeDisplayScope(): Promise<boolean> {
  if (loading === null) loading = (async () => {
    try {
      prefix = displayStoragePrefix(await getStatusJson('/infinity/bots', 5000));
      if (prefix === null) return false;
      // Only edits made in this document are pending. Never migrate unowned
      // legacy origin keys or copy another Bot's stored display preferences.
      for (const [key, value] of localEdits) put(key, value, false);
      for (const [key, value] of sessionEdits) put(key, value, true);
      for (const listener of listeners) {
        try { listener(prefix); } catch { /* Display readers fail independently. */ }
      }
      listeners.clear();
      return true;
    } catch { return false;
      } // A failed identity read uses document defaults, not old origin state.
  })();
  return loading;
}
