import { readDisplayLocal, writeDisplayLocal } from './display-storage';
import type { NavItem } from './pages';

const orderKey = 'plugin-nav-order';
const units = (list: HTMLElement) => Array.from(list.children) as HTMLElement[];
function readOrder(): string[] {
  try {
    const value: unknown = JSON.parse(readDisplayLocal(orderKey) || '[]');
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}
function button(label: string, className: string, text?: string) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = className;
  b.title = label;
  b.setAttribute('aria-label', label);
  if (text) b.textContent = text;
  return b;
}
function glide(list: HTMLElement, change: () => void, exclude?: HTMLElement) {
  const siblings = units(list).filter((el) => el !== exclude);
  const before = siblings.map((el) => el.getBoundingClientRect().top);
  change();
  siblings.forEach((el, i) => {
    el.getAnimations?.().forEach((a) => a.cancel());
    const delta = before[i] - el.getBoundingClientRect().top;
    if (Math.abs(delta) > 1 && !window.matchMedia('(prefers-reduced-motion: reduce)').matches)
      el.animate?.([{ transform: 'translateY(' + delta + 'px)' }, { transform: 'none' }], {
        duration: 165,
        easing: 'cubic-bezier(.2,.75,.25,1)',
      });
  });
}

export function pluginNavigationOrder(rail: HTMLElement, refresh: () => void) {
  let editing = false;
  function mount(entries: readonly NavItem[], rememberNew = false) {
    const overview = rail.querySelector<HTMLElement>('[data-page="infinity"]');
    const tree = overview?.parentElement?.querySelector<HTMLElement>('.rail-sublist');
    const changed = () => rail.dispatchEvent(new Event('plugin-navigation-order'));
    if (!overview || !tree) { changed(); return; }
    const order = readOrder();
    const rank = new Map(order.map((id, i) => [id, i]));
    const parents = new Map(entries.map((item) => [item.id, item.navigationParent]));
    const nodes = new Map<string, HTMLElement>();
    const lists = new Map<string, HTMLElement>();
    for (const link of Array.from(tree.querySelectorAll<HTMLElement>('a[data-page]'))) {
      const unit = document.createElement('div');
      unit.className = 'plugin-nav-unit';
      const id = link.dataset.page!;
      unit.dataset.navId = id;
      const row = document.createElement('div');
      row.className = 'plugin-nav-row';
      row.append(link);
      const grip = button('拖动' + link.textContent + '，也可用上下方向键排序', 'plugin-nav-grip');
      row.append(grip);
      unit.append(row);
      nodes.set(id, unit);
    }
    // A missing parent becomes a root. Cyclic declarations must keep every page reachable.
    function parentOf(id: string) {
      const parent = parents.get(id);
      if (!parent || !nodes.has(parent)) return;
      const visited = new Set([id]);
      let next: string | undefined = parent;
      while (next && nodes.has(next)) {
        if (visited.has(next)) return;
        visited.add(next);
        next = parents.get(next);
      }
      return parent;
    }
    const ids = [...nodes.keys()].sort(
      (a, b) => (rank.get(a) ?? order.length) - (rank.get(b) ?? order.length)
    );
    for (const id of ids) {
      const parent = parentOf(id);
      let list = tree;
      if (parent) {
        if (!lists.has(parent)) {
          const children = document.createElement('div');
          children.className = 'plugin-nav-children';
          nodes.get(parent)!.append(children);
          lists.set(parent, children);
        }
        list = lists.get(parent)!;
      }
      list.append(nodes.get(id)!);
    }
    if (rememberNew && ids.some(id => !rank.has(id))) {
      writeDisplayLocal(orderKey, JSON.stringify([...order, ...ids.filter(id => !rank.has(id))]));
    }
    if (nodes.size < 2) { changed(); return; }
    const toolbar = document.createElement('div');
    toolbar.className = 'plugin-nav-toolbar';
    overview.before(toolbar);
    toolbar.append(overview);
    const edit = button('调整插件顺序', 'plugin-nav-edit');
    edit.innerHTML =
      '<svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><path d="M2 4h10M2 8h7M2 12h4m7-5v8m-3-3 3 3 3-3"/></svg>';
    toolbar.append(edit);
    const controls = document.createElement('div');
    controls.className = 'plugin-nav-actions';
    controls.hidden = true;
    const cancel = button('取消排序', 'plugin-nav-action', '取消');
    const done = button('保存插件顺序', 'plugin-nav-action is-primary', '完成');
    controls.append(cancel, done);
    toolbar.after(controls);
    const live = document.createElement('span');
    live.className = 'plugin-nav-live';
    live.setAttribute('role', 'status');
    toolbar.append(live);
    function close(save: boolean) {
      endDrag(false);
      if (save)
        writeDisplayLocal(
          orderKey,
          JSON.stringify(
            Array.from(
              tree!.querySelectorAll<HTMLElement>('[data-nav-id]'),
              (el) => el.dataset.navId
            )
          )
        );
      editing = false;
      refresh();
      rail.querySelector<HTMLButtonElement>('.plugin-nav-edit')?.focus();
    }
    edit.onclick = () => {
      editing = true;
      tree.classList.add('is-sorting');
      controls.hidden = false;
      edit.hidden = true;
      changed();
      tree.querySelector<HTMLButtonElement>('.plugin-nav-grip')?.focus({ preventScroll: true });
    };
    cancel.onclick = () => close(false);
    done.onclick = () => close(true);

    type Drag = {
      unit: HTMLElement;
      list: HTMLElement;
      handle: HTMLElement;
      ghost: HTMLElement;
      pointer: number;
      offset: number;
      x: number;
      y: number;
      start: HTMLElement | null;
      raf: number;
    };
    let drag: Drag | null = null;
    function endDrag(commit: boolean) {
      if (!drag) return;
      const d = drag;
      drag = null;
      cancelAnimationFrame(d.raf);
      if (!commit) glide(d.list, () => d.list.insertBefore(d.unit, d.start));
      d.unit.classList.remove('is-placeholder');
      const destination = d.unit.getBoundingClientRect();
      const offset = d.ghost.getBoundingClientRect().top - destination.top;
      d.ghost.remove();
      if (commit && !window.matchMedia('(prefers-reduced-motion: reduce)').matches)
        d.unit.animate?.(
          [
            { transform: 'translateY(' + offset + 'px)', backgroundColor: 'var(--workspace-nav)' },
            { transform: 'none', backgroundColor: 'transparent' },
          ],
          { duration: 160, easing: 'cubic-bezier(.2,.78,.28,1)' }
        );
      if (tree!.hasPointerCapture(d.pointer)) tree!.releasePointerCapture(d.pointer);
      d.handle.focus({ preventScroll: true });
      changed();
    }
    function withinList(d: Drag) {
      const rect = d.list.getBoundingClientRect();
      return (
        d.x >= rect.left - 12 &&
        d.x <= rect.right + 4 &&
        d.y >= rect.top - 4 &&
        d.y <= rect.bottom + 4
      );
    }
    function frame() {
      if (!drag) return;
      const d = drag,
        bounds = rail.getBoundingClientRect();
      const inside = d.x >= bounds.left && d.x <= bounds.right;
      if (inside && d.y >= bounds.top && d.y <= bounds.bottom) {
        if (d.y < bounds.top + 32) rail.scrollTop -= 7;
        if (d.y > bounds.bottom - 32) rail.scrollTop += 7;
        // Compare stationary layout slots, independent of siblings' animated visual positions.
        const peers = withinList(d) ? units(d.list) : [];
        const position = peers.indexOf(d.unit);
        let target = position;
        for (let i = 0; i < peers.length; i++) {
          if (i === position) continue;
          const el = peers[i],
            transform = getComputedStyle(el).transform;
          const shift = transform === 'none' ? 0 : new DOMMatrixReadOnly(transform).m42;
          const midpoint = el.getBoundingClientRect().top - shift + el.offsetHeight / 2;
          if (i > position && d.y > midpoint) target = i;
          if (i < position && d.y < midpoint) {
            target = i;
            break;
          }
        }
        if (target !== position) {
          glide(
            d.list,
            () =>
              d.list.insertBefore(
                d.unit,
                target < position ? peers[target] : peers[target].nextSibling
              ),
            d.unit
          );
          changed();
        }
      }
      d.ghost.style.transform = 'translateY(' + (d.y - d.offset) + 'px)';
      d.raf = requestAnimationFrame(frame);
    }
    tree.addEventListener('pointerdown', (e) => {
      const handle = (e.target as HTMLElement).closest<HTMLElement>('.plugin-nav-grip');
      if (!handle || !editing || drag || e.button !== 0) return;
      e.preventDefault();
      const unit = handle.closest<HTMLElement>('[data-nav-id]')!,
        rect = unit.getBoundingClientRect();
      const ghost = unit.cloneNode(true) as HTMLElement;
      ghost.className = 'plugin-nav-ghost';
      ghost.setAttribute('aria-hidden', 'true');
      ghost.inert = true;
      ghost.setAttribute('popover', 'manual');
      Object.assign(ghost.style, {
        left: rect.left + 'px',
        width: rect.width + 'px',
        transform: 'translateY(' + rect.top + 'px)',
      });
      // Keep the ghost in the rail so it inherits the active theme and the same link metrics.
      rail.append(ghost);
      ghost.showPopover?.();
      unit.classList.add('is-placeholder');
      tree.setPointerCapture(e.pointerId);
      drag = {
        unit,
        handle,
        list: unit.parentElement!,
        ghost,
        pointer: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        offset: e.clientY - rect.top,
        start: unit.nextElementSibling as HTMLElement | null,
        raf: 0,
      };
      frame();
    });
    tree.addEventListener('pointermove', (e) => {
      if (drag?.pointer === e.pointerId) {
        drag.x = e.clientX;
        drag.y = e.clientY;
      }
    });
    tree.addEventListener('pointerup', (e) => {
      if (drag?.pointer !== e.pointerId) return;
      drag.x = e.clientX;
      drag.y = e.clientY;
      cancelAnimationFrame(drag.raf);
      frame();
      const r = rail.getBoundingClientRect();
      endDrag(
        withinList(drag) &&
          e.clientX >= r.left &&
          e.clientX <= r.right &&
          e.clientY >= r.top &&
          e.clientY <= r.bottom
      );
    });
    for (const type of ['pointercancel', 'lostpointercapture'])
      tree.addEventListener(type, (e) => {
        if (drag?.pointer === (e as PointerEvent).pointerId) endDrag(false);
      });
    tree.addEventListener('click', (e) => {
      if (!editing) return;
      const row = (e.target as HTMLElement).closest<HTMLElement>('.plugin-nav-row');
      if (row) {
        e.preventDefault();
        e.stopPropagation();
        row.querySelector<HTMLElement>('.plugin-nav-grip')?.focus();
      }
    });
    tree.addEventListener('keydown', (e) => {
      if (!editing) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        if (drag) endDrag(false);
        else close(false);
        return;
      }
      const handle = (e.target as HTMLElement).closest<HTMLElement>('.plugin-nav-grip');
      if (!handle || drag || !['ArrowUp', 'ArrowDown'].includes(e.key)) return;
      e.preventDefault();
      const unit = handle.closest<HTMLElement>('[data-nav-id]')!,
        list = unit.parentElement!;
      const up = e.key === 'ArrowUp',
        sibling = up ? unit.previousElementSibling : unit.nextElementSibling;
      if (sibling) {
        glide(list, () => list.insertBefore(unit, up ? sibling : sibling.nextSibling));
        handle.focus();
        changed();
        live.textContent =
          '当前位置 ' + (units(list).indexOf(unit) + 1) + ' / ' + list.children.length;
      }
    });
    changed();
  }
  return {
    mount,
    get editing() {
      return editing;
    },
  };
}
