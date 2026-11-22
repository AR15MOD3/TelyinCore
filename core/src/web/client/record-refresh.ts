/** Visible-page invalidation: one in-flight read, coalesced events, bounded fallback. */
export function recordRefresh(root: HTMLElement, read: () => Promise<void>): { start(): void; stop(): void } {
  let active = false;
  let running = false;
  let dirty = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let poll: ReturnType<typeof setInterval> | undefined;
  const flush = async (): Promise<void> => {
    timer = undefined;
    if (!active || root.hidden || document.visibilityState === 'hidden') return;
    if (running) { dirty = true; return; }
    running = true;
    try { await read(); } finally {
      running = false;
      if (active && dirty) { dirty = false; schedule(); }
    }
  };
  const schedule = (): void => {
    if (active && timer === undefined) timer = setTimeout(() => { void flush(); }, 200);
  };
  return {
    start(): void {
      if (active || typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
      active = true;
      window.addEventListener('core-records-changed', schedule);
      document.addEventListener('visibilitychange', schedule);
      poll = setInterval(schedule, 5000);
    },
    stop(): void {
      if (!active) return;
      active = false; dirty = false;
      clearTimeout(timer); clearInterval(poll); timer = undefined;
      window.removeEventListener('core-records-changed', schedule);
      document.removeEventListener('visibilitychange', schedule);
    },
  };
}
