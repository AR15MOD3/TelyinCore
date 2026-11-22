/** Merge identities, never message text. Repeated words are distinct messages. */
export type DisplayFrame = { display_id?: string; [key: string]: unknown };
export function mergeHistory(history: readonly DisplayFrame[],
  live: readonly DisplayFrame[]): DisplayFrame[] {
  const result: DisplayFrame[] = [];
  const known = new Set<string>();
  const id = (frame: DisplayFrame) => typeof frame.display_id === 'string' &&
    frame.display_id ? frame.display_id : null;
  for (const frame of history) {
    const key = id(frame);
    if (key && known.has(key)) continue;
    result.push(frame); if (key) known.add(key);
  }
  for (let i = 0; i < live.length; i++) {
    const frame = live[i], key = id(frame);
    if (key && known.has(key)) continue;
    // Preserve the stream's order around the next shared historical anchor.
    let before = -1;
    for (let j = i + 1; j < live.length; j++) {
      const next = id(live[j]);
      if (next && known.has(next)) { before = result.findIndex(row => id(row) === next); break; }
    }
    if (before < 0) result.push(frame); else result.splice(before, 0, frame);
    if (key) known.add(key);
  }
  return result;
}

export class HistoryFeed {
  private sequence = 0;
  private loading = false;
  private live: DisplayFrame[] = [];
  private visible = new Set<string>();
  constructor(private append: (wire: string) => void,
              private replace: (frames: readonly DisplayFrame[]) => void) {}
  begin(): number { this.loading = true; return ++this.sequence; }
  receive(wire: string): void {
    let frame: DisplayFrame;
    try { frame = JSON.parse(wire); } catch { this.append(wire); return; }
    if (!frame || typeof frame !== 'object' || Array.isArray(frame)) { this.append(wire); return; }
    const key = typeof frame.display_id === 'string' ? frame.display_id : '';
    if (key && this.live.some(row => row.display_id === key)) return;
    this.live.push(frame);
    if (this.loading || key && this.visible.has(key)) return;
    if (key) this.visible.add(key);
    this.append(wire);
  }
  finish(ticket: number, frames: readonly DisplayFrame[]): void {
    if (ticket !== this.sequence) return;
    const merged = mergeHistory(frames, this.live);
    this.visible = new Set(merged.map(row => row.display_id).filter((id): id is string => !!id));
    this.replace(merged); this.loading = false;
  }
  fail(ticket: number): void {
    if (ticket !== this.sequence) return;
    this.loading = false;
    for (const frame of this.live) {
      const key = frame.display_id;
      if (key && this.visible.has(key)) continue;
      this.append(JSON.stringify(frame)); if (key) this.visible.add(key);
    }
  }
}
