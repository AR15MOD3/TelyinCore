import { corePhrase, coreSetUIAttribute } from './ui-language';
import { readDisplayLocal, writeDisplayLocal, onDisplayScopeReady } from './display-storage';
const WIDTH_KEY = 'telyincore.rail-width';
const DEFAULT_WIDTH = 280;

export function railWidthBounds(viewport: number): { min: number; max: number } {
  return { min: 68, max: viewport < 560 ? 68 : Math.min(480, viewport - 360) };
}

export function clampRailWidth(width: number, viewport: number): number {
  const { min, max } = railWidthBounds(viewport);
  const bounded = Math.round(Math.max(min, Math.min(max,
    Number.isFinite(width) ? width : DEFAULT_WIDTH)));
  return bounded < 200 ? min : bounded;
}

/** Do not snap across the 132px gap while the pointer is still moving. */
export function dragRailWidth(width: number, viewport: number): number {
  const { min, max } = railWidthBounds(viewport);
  return Math.round(Math.max(min, Math.min(max, width)));
}

/** Local layout preference only; never sends settings to the service. */
export function installRailResize(root: HTMLElement, rail: HTMLElement): void {
  const handle = document.createElement('div');
  handle.className = 'rail-resizer';
  handle.tabIndex = 0;
  handle.setAttribute('role', 'separator');
  coreSetUIAttribute(handle, 'aria-label', corePhrase("调整侧边栏宽度"));
  handle.setAttribute('aria-orientation', 'vertical');
  handle.setAttribute('aria-controls', rail.id);
  handle.setAttribute('aria-description', '向左拖到底收成图标栏，向右拖开；双击恢复默认；方向键微调');
  let edited = false;
  let preferred = window.innerWidth <= 900 ? 68 : DEFAULT_WIDTH;
  try {
    const stored = readDisplayLocal(WIDTH_KEY);
    if (stored !== null && stored.trim() !== '' && Number.isFinite(Number(stored))) {
      preferred = Number(stored);
    }
  } catch { /* Storage may be disabled; resizing still works. */ }
  let width = DEFAULT_WIDTH;
  let compact = false;
  let initialized = false;
  const animations = new Set<Animation>();
  const apply = (dragging = false): void => {
    width = dragging ? dragRailWidth(preferred, window.innerWidth)
      : clampRailWidth(preferred, window.innerWidth);
    // Use the same breakpoint during dragging and after release. Below this
    // width the expanded controls cannot fit, even before the rail snaps shut.
    const nextCompact = width < 200;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
    const changing = initialized && compact !== nextCompact && !reduced;
    // FLIP the icons and footer controls instead of letting flex-direction and
    // alignment changes teleport them to their new positions.
    if (changing) {
      for (const animation of animations) animation.cancel();
      animations.clear();
    }
    const movers = changing ? Array.from(rail.querySelectorAll?.<HTMLElement>(
      '.rail-item > .icon, .rail-search > .icon, .rail-action, .rail-mark') ?? []) : [];
    const before = movers.map(element => element.getBoundingClientRect());
    if (changing && nextCompact && !dragging) {
      // Keep outgoing text briefly as a visual-only overlay so display/clip
      // changes in the existing compact layout can still fade gracefully.
      for (const label of rail.querySelectorAll?.<HTMLElement>(
          '.rail-label, .rail-brandname, .rail-name') ?? []) {
        const rect = label.getBoundingClientRect();
        if (!rect.width || !rect.height) continue;
        const ghost = document.createElement('span');
        ghost.className = 'rail-label-ghost';
        ghost.textContent = label.textContent;
        ghost.setAttribute('aria-hidden', 'true');
        const style = getComputedStyle(label);
        Object.assign(ghost.style, { left: `${rect.left}px`, top: `${rect.top}px`,
          width: `${rect.width}px`, height: `${rect.height}px`, font: style.font,
            color: style.color });
        rail.append(ghost);
        const animation = ghost.animate([{ opacity: 1 }, { opacity: 0,
          transform: 'translateX(-6px)' }],
          { duration: 120, easing: 'ease-out' });
        animation.onfinish = () => { ghost.remove(); animations.delete(animation); };
        animation.oncancel = () => { ghost.remove(); animations.delete(animation); };
        animations.add(animation);
      }
    }
    root.style.setProperty('--rail-w', `${width}px`);
    root.classList.toggle('rail-compact', nextCompact);
    document.documentElement?.removeAttribute('data-core-rail-compact');
    if (changing) {
      movers.forEach((element, index) => {
        const after = element.getBoundingClientRect();
        const from = before[index];
        const animation = element.animate([
          { transform: `translate(${from.left - after.left}px, ${from.top - after.top}px)` },
          { transform: 'translate(0, 0)' },
        ], { duration: dragging ? 120 : 260, easing: 'cubic-bezier(.22, 1, .36, 1)' });
        animations.add(animation);
        animation.onfinish = () => animations.delete(animation);
        animation.oncancel = () => animations.delete(animation);
      });
      if (!nextCompact && !dragging) {
        for (const label of rail.querySelectorAll?.<HTMLElement>(
          '.rail-label, .rail-brandname, .rail-name') ?? []) {
          const animation = label.animate([{ opacity: 0 }, { opacity: 1 }],
            { duration: 200, delay: 60, fill: 'backwards', easing: 'ease-out' });
          animations.add(animation);
          animation.onfinish = () => animations.delete(animation);
          animation.oncancel = () => animations.delete(animation);
        }
      }
    }
    compact = nextCompact;
    initialized = true;
    const { min, max } = railWidthBounds(window.innerWidth);
    handle.setAttribute('aria-valuemin', String(min));
    handle.setAttribute('aria-valuemax', String(max));
    handle.setAttribute('aria-valuenow', String(width));
  };
  const save = (): void => {
    edited = true;
    try { writeDisplayLocal(WIDTH_KEY, String(preferred)); } catch { /* Optional. */ }
  };
  let drag: { id: number; x: number; width: number; previous: number } | null = null;
  const finish = (cancel = false): void => {
    if (drag === null) return;
    const id = drag.id;
    if (cancel) preferred = drag.previous;
    drag = null;
    root.classList.remove('rail-resizing');
    if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
    apply();
    if (!cancel) save();
  };
  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag !== null) return;
    event.preventDefault();
    handle.focus();
    handle.setPointerCapture(event.pointerId);
    drag = { id: event.pointerId, x: event.clientX, width, previous: preferred };
    root.classList.add('rail-resizing');
  });
  handle.addEventListener('pointermove', (event) => {
    if (drag === null || drag.id !== event.pointerId) return;
    preferred = dragRailWidth(drag.width + event.clientX - drag.x, window.innerWidth);
    apply(true);
  });
  handle.addEventListener('pointerup', (event) => { if (event.pointerId === drag?.id) finish(); });
  handle.addEventListener('pointercancel', (event) => {
    if (event.pointerId === drag?.id) finish(true); });
  handle.addEventListener('lostpointercapture', () => finish(true));
  window.addEventListener('blur', () => finish(true));
  window.addEventListener('resize', () => apply());
  handle.addEventListener('dblclick', () => { preferred = DEFAULT_WIDTH; apply(); save(); });
  handle.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && drag !== null) { event.preventDefault(); finish(true); return; }
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || drag !== null) return;
    event.preventDefault();
    const { min, max } = railWidthBounds(window.innerWidth);
    preferred = event.key === 'Home' ? min : event.key === 'End' ? max
      : clampRailWidth(width === 68 &&
        event.key === 'ArrowRight' ? 200
          : width + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 40 : 10),
          window.innerWidth);
    apply();
    save();
  });
  root.append(handle);
  apply();
  onDisplayScopeReady(() => {
    if (edited || drag !== null) return;
    const stored = readDisplayLocal(WIDTH_KEY);
    if (stored !== null && stored.trim() !== '' && Number.isFinite(Number(stored))) {
      preferred = Number(stored); apply();
    }
  });
  // Enable motion only after initial layout, avoiding an entrance animation on reload.
  window.requestAnimationFrame?.(() => root.classList.add('rail-motion-ready'));
}
