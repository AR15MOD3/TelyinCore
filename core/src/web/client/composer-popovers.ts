// One non-modal inspector at a time. No model or message routes belong here.
type Entry = { trigger: HTMLButtonElement; panel: HTMLElement; label: string };
type Bounds = { left: number; top: number; width: number; height: number };
type Viewport = { left: number; top: number; width: number; height: number };

export function popoverPosition(anchor: Bounds, height: number, view: Viewport) {
  const edge = 12;
  const gap = 10;
  const width = Math.max(0, Math.min(336, view.width - edge * 2));
  const above = Math.max(0, anchor.top - view.top - edge - gap);
  const below = Math.max(0, view.top + view.height - edge
    - anchor.top - anchor.height - gap);
  const side = above >= height || above >= below ? 'above' : 'below';
  const maxHeight = side === 'above' ? above : below;
  const size = Math.min(height, maxHeight);
  const left = Math.max(view.left + edge,
    Math.min(anchor.left, view.left + view.width - edge - width));
  const top = side === 'above' ? anchor.top - gap - size
    : anchor.top + anchor.height + gap;
  return { left, top, width, maxHeight, side };
}

let active: Entry | null = null;
let wired = false;
let sequence = 0;
let observer: ResizeObserver | null = null;

function close(restore = false): void {
  if (active === null) { return; }
  const old = active;
  active = null;
  observer?.disconnect();
  if (old.panel.isConnected) { old.panel.hidePopover?.(); }
  old.panel.hidden = true;
  old.trigger.setAttribute('aria-expanded', 'false');
  old.trigger.dataset.open = 'no';
  if (restore && old.trigger.isConnected) { old.trigger.focus(); }
}

export function positionComposerPopover(): void {
  if (active === null || typeof window === 'undefined') { return; }
  const { trigger, panel } = active;
  if (!trigger.isConnected) { close(); return; }
  const vv = window.visualViewport;
  const view = { left: vv?.offsetLeft ?? 0, top: vv?.offsetTop ?? 0,
    width: vv?.width ?? window.innerWidth, height: vv?.height ?? window.innerHeight };
  panel.style.width = `${Math.max(0, Math.min(336, view.width - 24))}px`;
  // Measure intrinsic height, not the last constrained viewport's height.
  panel.style.maxHeight = 'none';
  const box = popoverPosition(trigger.getBoundingClientRect(),
    panel.getBoundingClientRect().height, view);
  panel.style.left = `${box.left}px`;
  panel.style.top = `${box.top}px`;
  panel.style.maxHeight = `${box.maxHeight}px`;
  panel.dataset.side = box.side;
}

function wire(): void {
  if (wired) { return; }
  wired = true;
  const outside = (event: Event): void => {
    if (active === null) { return; }
    const target = event.target as Node | null;
    if (target && (active.panel.contains(target) || active.trigger.contains(target))) { return; }
    close();
  };
  document.addEventListener('click', outside);
  document.addEventListener('focusin', outside);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && active !== null) {
      event.preventDefault(); close(true);
    }
  });
  if (typeof window === 'undefined') { return; }
  window.addEventListener('resize', positionComposerPopover);
  window.addEventListener('scroll', positionComposerPopover, true);
  window.visualViewport?.addEventListener('resize', positionComposerPopover);
  window.visualViewport?.addEventListener('scroll', positionComposerPopover);
  window.addEventListener('hashchange', () => close());
  window.addEventListener('pagehide', () => close());
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(positionComposerPopover);
  }
}

export function wireComposerPopovers(entries: Entry[], refresh: () => void): void {
  wire();
  for (const entry of entries) {
    const { trigger, panel, label } = entry;
    const id = `composer-inspector-${++sequence}`;
    trigger.id = `${id}-trigger`;
    panel.id = id;
    panel.className += ' composer-popover';
    panel.hidden = true;
    panel.tabIndex = -1;
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-labelledby', trigger.id);
    panel.setAttribute('popover', 'manual');
    trigger.setAttribute('aria-label', label);
    trigger.setAttribute('aria-controls', id);
    trigger.setAttribute('aria-expanded', 'false');
    const open = (): void => {
      close();
      active = entry;
      panel.hidden = false;
      panel.showPopover?.();
      trigger.setAttribute('aria-expanded', 'true');
      trigger.dataset.open = 'yes';
      positionComposerPopover();
      observer?.observe(panel);
      observer?.observe(trigger);
      refresh();
    };
    trigger.addEventListener('click', () => {
      if (active === entry) { close(); } else { open(); }
    });
    trigger.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (active !== entry) { open(); }
        panel.focus();
      }
    });
  }
}
