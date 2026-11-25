/** Only typed display preferences (tab, list page, expansion). Never store form values or server responses here.
 * sessionStorage isolates this Bot's current browser tab; clearing never emits a reload.
 */
import {initializeDisplayScope, onDisplayScopeReady} from './display-storage';
let scope: string | null = null;
onDisplayScopeReady(prefix => { scope = prefix + 'plugin-page-display.v1:'; });
const validName = (name: string): boolean => /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(name);
const guards = new Map<string, () => boolean>();
export function protectPluginDisplay(name: string, unsafe: () => boolean): () => void {
  guards.set(name, unsafe);
  return () => { if (guards.get(name) === unsafe) guards.delete(name); };
}
export function pluginDisplayProtected(name: string): boolean {
  try { return guards.get(name)?.() ?? false; } catch { return true; }
}
export const readyPluginDisplay = (): Promise<boolean> => initializeDisplayScope();
function key(name: string): string | null { return scope && validName(name) ? scope + name : null; }
export type DisplaySlot = 'host-tab' | 'view-tab' | 'list-page' | 'details-open';
export const displayCacheCapability = {version:1, slots:['view-tab','list-page','details-open']};
function validValue(slot: DisplaySlot, value: unknown): value is number | boolean {
  if (slot === 'details-open') return typeof value === 'boolean';
  const max = slot === 'host-tab' ? 9 : slot === 'view-tab' ? 31 : slot === 'list-page' ? 100000 : -1;
  return Number.isInteger(value) && Number(value) >= 0 && Number(value) <= max;
}
export function readPluginDisplay(name: string, slot: DisplaySlot): number | boolean | null {
  const k = key(name); if (!k) return null;
  try {
    const raw = sessionStorage.getItem(k + ':' + slot);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw); return validValue(slot, value) ? value : null;
  } catch { return null; }
}
export function writePluginDisplay(name: string, slot: DisplaySlot, value: unknown): boolean {
  const k = key(name); if (!k || !validValue(slot, value)) return false;
  try { sessionStorage.setItem(k + ':' + slot, JSON.stringify(value)); return true; }
  catch { return false; }
}
export const readPluginTab = (name: string): number | null => readPluginDisplay(name, 'host-tab') as number | null;
export const writePluginTab = (name: string, tab: number): void => { writePluginDisplay(name, 'host-tab', tab); };
const slots: DisplaySlot[] = ['host-tab','view-tab','list-page','details-open'];
export function pluginDisplayEntries(): Array<{name: string; bytes: number; records: number; valueBytes: number}> {
  const rows = new Map<string, {bytes:number; records:number; valueBytes:number}>();
  if (!scope) return [];
  // Exact owned schema only: unknown records never become clearable by scanning.
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i); if (!k?.startsWith(scope)) continue;
    const parts = k.slice(scope.length).split(':');
    const [name, slot] = parts as [string, DisplaySlot];
    if (parts.length !== 2 || !validName(name) || !slots.includes(slot) || readPluginDisplay(name, slot) === null) continue;
    const value = sessionStorage.getItem(k)!;
    const row = rows.get(name) ?? {bytes:0, records:0, valueBytes:0};
    row.bytes += new TextEncoder().encode(k + value).length;
    row.valueBytes += new TextEncoder().encode(value).length;
    row.records++; rows.set(name, row);
  }
  return Array.from(rows, ([name,entry]) => ({name,...entry}));
}
export function clearPluginDisplay(name: string): boolean {
  const k = key(name); if (!k || pluginDisplayProtected(name)) return false;
  try {
    const targets = slots.filter(slot => readPluginDisplay(name, slot) !== null).map(slot => k + ':' + slot);
    if (!targets.length) return false;
    for (const target of targets) sessionStorage.removeItem(target);
    return targets.every(target => sessionStorage.getItem(target) === null);
  } catch { return false; }
}
/** Opaque sandbox frames cannot choose a Bot, plugin name, key or filesystem path. */
export function pluginDisplayBridge(options: {name:string; frame:HTMLIFrameElement;
  active:()=>boolean; epoch:()=>number}): (event:MessageEvent)=>void {
  return event => {
    if (event.source !== options.frame.contentWindow || !options.active()) return;
    const request = event.data;
    if (!request || request.type !== 'telyincore:display:request' ||
        typeof request.id !== 'string' || !/^[0-9]{1,12}$/.test(request.id)) return;
    const frame = options.frame.contentWindow; const epoch = options.epoch();
    void (async () => {
      const ready = await readyPluginDisplay();
      if (!options.active() || epoch !== options.epoch() || frame !== options.frame.contentWindow) return;
      const valid = ready && displayCacheCapability.slots.includes(request.slot);
      let ok = false; let value: number | boolean | null = null;
      if (valid && request.action === 'read') { value = readPluginDisplay(options.name, request.slot); ok = true; }
      else if (valid && request.action === 'write') {
        ok = writePluginDisplay(options.name, request.slot, request.value);
        if (ok) value = readPluginDisplay(options.name, request.slot);
      }
      frame?.postMessage({type:'telyincore:display:result',id:request.id,ok,value}, '*');
    })();
  };
}
export function fuzzyPluginMatch(name: string, id: string, query: string): boolean {
  const normalize = (s: string): string => s.normalize('NFKC').toLowerCase().replace(/[\s._-]+/g, '');
  const aliases: Record<string, string> = {'core-infinity-qq':'QQ 接入 jieru xiaoxi',
    'core-infinity-wavesuid':'WavesUID waves', 'jingjing-town':'jingjing town 晶晶 小镇 xiaozhen'};
  const words = [name, id, ...(name + ' ' + id + ' ' + (aliases[id] ?? '')).split(/\s+/)].map(normalize);
  return query.slice(0, 128).trim().split(/\s+/).filter(Boolean).every(token => {
    const q = normalize(token);
    return words.some(c => {
      if (c.includes(q)) return true;
      if (q.length >= 2 && c.length <= q.length * 5) {
        let n = 0; for (const char of c) if (char === q[n]) n++; if (n === q.length) return true;
      }
      if (q.length < 4 || Math.abs(c.length - q.length) > 2) return false;
      let row = Array.from({length: c.length + 1}, (_, i) => i);
      for (let i = 0; i < q.length; i++) {
        const next = [i + 1];
        for (let j = 0; j < c.length; j++) next[j + 1] = Math.min(next[j] + 1, row[j + 1] + 1, row[j] + (q[i] === c[j] ? 0 : 1));
        row = next;
      }
      return row[c.length] <= Math.min(2, Math.floor(q.length / 4));
    });
  });
}
