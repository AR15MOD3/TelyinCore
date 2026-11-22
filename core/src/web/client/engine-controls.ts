import { corePhrase, coreSetUIAttribute } from './ui-language';
/** Model-page menus: one visual language, keyboard navigation, no browser history. */
export interface ComboItem { value: string; label: string; field?: string; }
const selects = new Map<HTMLSelectElement, () => void>();
const menus = new Map<HTMLElement, () => void>();
let serial = 0, listening = false;
function node(tag: string, cls: string, text = ''): HTMLElement {
  const el = document.createElement(tag); el.className = cls; el.textContent = text; return el;
}
function remember(box: HTMLElement, close: () => void): void {
  menus.set(box, close);
  if (listening) return;
  listening = true;
  const outside = (event: Event): void => {
    for (const [root, hide] of menus) {
      if (!root.contains(event.target as Node)) hide();
    }
  };
  document.addEventListener('pointerdown', outside);
  document.addEventListener('click', outside);
}
export function resetControls(): void {
  for (const hide of menus.values()) hide();
  menus.clear(); selects.clear();
}
export function refreshControls(): void { for (const sync of selects.values()) sync(); }
function menuFor(anchor: HTMLElement, items: () => ComboItem[],
                 selected: () => string, choose: (item: ComboItem) => void): {
  menu: HTMLElement; open: (filter?: string) => void; close: () => void;
  key: (event: KeyboardEvent) => void;
} {
  const menu = node('div', 'engine-menu'); menu.id = `engine-menu-${++serial}`;
  menu.setAttribute('role', 'listbox'); menu.hidden = true;
  anchor.setAttribute('aria-controls', menu.id); anchor.setAttribute('aria-expanded', 'false');
  let rows: ComboItem[] = [], active = -1;
  const close = (): void => {
    menu.hidden = true; anchor.setAttribute('aria-expanded', 'false');
    anchor.removeAttribute('aria-activedescendant');
  };
  const highlight = (): void => {
    Array.from(menu.children).forEach((option, index) => {
      option.classList.toggle('active', index === active);
    });
    const current = menu.children[active] as HTMLElement | undefined;
    if (current) {
      anchor.setAttribute('aria-activedescendant', current.id);
      current.scrollIntoView?.({ block: 'nearest' });
    }
  };
  const open = (filter = ''): void => {
    for (const hide of menus.values()) hide();
    const query = filter.trim().toLowerCase();
    rows = items().filter(it => `${it.label} ${it.value}`.toLowerCase().includes(query));
    menu.replaceChildren();
    if (!rows.length) menu.append(node('div', 'engine-empty', '没有匹配项，可直接输入'));
    active = rows.findIndex(it => it.value === selected() || it.field === selected());
    rows.forEach((it, index) => {
      const option = node('div', 'engine-option', it.field ?? it.label);
      option.id = `${menu.id}-${index}`; option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === active));
      if (it.field && it.value !== it.field) {
        option.append(node('small', 'engine-option-id', it.value));
      }
      option.addEventListener('pointermove', () => { active = index; highlight(); });
      option.addEventListener('mousedown', event => { event.preventDefault(); });
      option.addEventListener('click', () => { choose(it); close(); });
      menu.append(option);
    });
    const rect = anchor.getBoundingClientRect?.();
    menu.dataset.side = rect && rect.top > window.innerHeight - rect.bottom
      && window.innerHeight - rect.bottom < 190 ? 'above' : 'below';
    menu.hidden = false; anchor.setAttribute('aria-expanded', 'true'); highlight();
  };
  const key = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' || event.key === 'Tab') { close(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (menu.hidden) open();
      if (rows.length) active = active < 0
        ? event.key === 'ArrowDown' ? 0 : rows.length - 1
        : (active + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
      highlight();
    } else if (event.key === 'Enter' && !menu.hidden) {
      event.preventDefault(); if (rows.length) choose(rows[active < 0 ? 0 : active]); close();
    }
  };
  return { menu, open, close, key };
}
export function combo(cell: HTMLInputElement, items: () => ComboItem[],
                      onPick?: (item: ComboItem) => void): HTMLElement {
  const box = node('div', 'engine-combo');
  cell.autocomplete = 'off'; cell.spellcheck = false;
  cell.setAttribute('autocomplete', 'off'); cell.setAttribute('autocorrect', 'off');
  cell.setAttribute('autocapitalize', 'off'); cell.setAttribute('aria-autocomplete', 'list');
  cell.setAttribute('role', 'combobox'); cell.setAttribute('aria-haspopup', 'listbox');
  cell.setAttribute('data-lpignore', 'true'); cell.setAttribute('data-1p-ignore', 'true');
  const choose = (item: ComboItem): void => {
    cell.value = item.field ?? item.value; onPick?.(item);
    cell.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const list = menuFor(cell, items, () => cell.value, choose);
  const toggle = node('button', 'engine-menu-toggle') as HTMLButtonElement;
  toggle.type = 'button'; toggle.tabIndex = -1; coreSetUIAttribute(toggle, 'aria-label', corePhrase("展开候选"));
  toggle.addEventListener('mousedown', event => {
    event.preventDefault();
    if (list.menu.hidden) { cell.focus(); list.open(); } else list.close();
  });
  cell.addEventListener('focus', () => { list.open(); cell.select(); });
  cell.addEventListener('click', () => { if (list.menu.hidden) list.open(); });
  cell.addEventListener('input', () => list.open(cell.value));
  cell.addEventListener('keydown', list.key);
  cell.addEventListener('blur', list.close);
  cell.addEventListener('change', list.close);
  box.append(cell, toggle, list.menu); remember(box, list.close);
  (cell as HTMLInputElement & { openAll?: () => void }).openAll = () => list.open();
  return box;
}
export function selectControl(select: HTMLSelectElement, label: string): HTMLElement {
  const box = node('div', 'engine-combo engine-select');
  const trigger = node('button', 'engine-input engine-select-trigger') as HTMLButtonElement;
  trigger.type = 'button'; trigger.id = `${select.id}-button`;
  trigger.setAttribute('aria-label', label);
  trigger.setAttribute('aria-haspopup', 'listbox');
  select.hidden = true; select.setAttribute('aria-hidden', 'true'); select.tabIndex = -1;
  const sync = (): void => {
    const chosen = Array.from(select.options).find(option => option.value === select.value);
    trigger.textContent = chosen?.textContent ?? '上游默认'; trigger.disabled = select.disabled;
    trigger.title = select.title;
  };
  const list = menuFor(trigger, () => Array.from(select.options).map(option => ({
    value: option.value, label: option.textContent ?? option.value,
  })), () => select.value, item => {
    select.value = item.value; sync();
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  trigger.addEventListener('click', () => {
    if (trigger.disabled) return;
    if (list.menu.hidden) list.open(); else list.close();
  });
  trigger.addEventListener('keydown', list.key);
  trigger.addEventListener('blur', list.close);
  select.addEventListener('change', sync);
  box.append(select, trigger, list.menu); remember(box, list.close);
  selects.set(select, sync); sync(); return box;
}
