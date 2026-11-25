import { getStatusJson } from './status-json';
import { icon } from './shell';

type Hit = { title: string; kind: string; words: string; href: string };
/** Search within the navigation rail. The current page stays interactive. */
export function installQuickSearch(rail: HTMLElement): void {
  const nav = rail.querySelector<HTMLElement>('.rail-nav');
  if (!nav) return;
  const trigger = document.createElement('button');
  trigger.type = 'button'; trigger.className = 'rail-search';
  trigger.append(icon('search'), document.createTextNode('搜索'));
  const key = document.createElement('kbd');
  key.textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K';
  trigger.append(key);
  trigger.setAttribute('aria-label', '搜索页面和插件');
  trigger.setAttribute('aria-controls', 'rail-search-panel');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-keyshortcuts', 'Control+K Meta+K');
  rail.querySelector('.rail-brand')?.after(trigger);

  const panel = document.createElement('section');
  panel.id = 'rail-search-panel'; panel.className = 'rail-search-panel'; panel.hidden = true;
  panel.setAttribute('aria-label', '搜索页面和插件');
  const header = document.createElement('div'); header.className = 'rail-search-field';
  const input = document.createElement('input');
  input.type = 'search'; input.placeholder = '搜索页面或插件';
  input.setAttribute('role', 'combobox'); input.setAttribute('aria-label', '搜索页面和插件');
  input.setAttribute('aria-expanded', 'false'); input.setAttribute('aria-controls', 'quick-search-results');
  input.setAttribute('aria-autocomplete', 'list');
  const close = document.createElement('button'); close.type = 'button';
  close.append(icon('close')); close.setAttribute('aria-label', '关闭搜索'); close.title = '关闭搜索（Esc）';
  const count = document.createElement('p'); count.className = 'rail-search-count';
  const list = document.createElement('div');
  list.id = 'quick-search-results'; list.className = 'rail-search-results'; list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '搜索结果');
  const note = document.createElement('p'); note.className = 'rail-search-note';
  note.setAttribute('role', 'status');
  const footer = document.createElement('p'); footer.className = 'rail-search-help';
  footer.textContent = '↑ ↓ 选择 · Enter 打开 · Esc 返回';
  header.append(icon('search'), input, close);
  panel.append(header, count, list, note, footer); trigger.after(panel);

  let selected = 0, matches: Hit[] = [], plugins: Hit[] = [], epoch = 0;
  let previous: Element | null = null;
  const words: Record<string, string> = {
    live: '聊天 控制台 console chat', engine: '模型 API 接口 密钥 model',
    persona: '人设 角色 persona', memory: '记忆 memory', people: '画像 人物 QQ 账号关联 profile',
    experience: '经历 对话历史 history', knowledge: '知识库 knowledge',
    plugin: '插件市场 安装 market', infinity: '插件总览 已安装 更新 plugins',
  };
  const catalog = (): Hit[] => {
    const map = new Map<string, Hit>();
    for (const link of nav.querySelectorAll<HTMLAnchorElement>('a.rail-item[href]')) {
      const href = link.getAttribute('href')!; const id = href.replace(/^#\//, '');
      map.set(href, {title: link.querySelector('.rail-label')?.textContent || link.textContent || id,
        kind: id.startsWith('infinity/') ? '插件配置' : '页面', words: words[id] || id, href});
    }
    for (const hit of plugins) if (!map.has(hit.href)) map.set(hit.href, hit);
    map.set('#/settings', {title: '高级设置', kind: '页面', words: '外观 主题 配色 存储 网络 代理 settings', href: '#/settings'});
    return [...map.values()];
  };
  const dismiss = (restoreFocus = true): void => {
    if (panel.hidden) return;
    epoch++; panel.hidden = true; trigger.hidden = false; nav.hidden = false;
    document.body.classList.remove('rail-searching');
    trigger.setAttribute('aria-expanded', 'false'); input.setAttribute('aria-expanded', 'false');
    if (restoreFocus) {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
      else trigger.focus();
    }
  };
  const choose = (hit: Hit): void => {
    dismiss(); location.hash = hit.href;
    if (document.body.classList.contains('compact-menu-open'))
      (document.querySelector('.compact-menu-toggle') as HTMLButtonElement)?.click();
  };
  const paint = (): void => {
    const query = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    matches = catalog().filter(hit => query.every(q => (hit.title + ' ' + hit.words).toLowerCase().includes(q)));
    selected = Math.min(selected, Math.max(0, matches.length - 1)); list.replaceChildren();
    count.textContent = query.length ? `找到 ${matches.length} 项` : '页面与插件';
    matches.forEach((hit, i) => {
      const row = document.createElement('div'); row.className = 'rail-search-hit';
      row.id = 'quick-search-hit-' + i; row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(i === selected));
      const label = document.createElement('span'); label.textContent = hit.title;
      const kind = document.createElement('small'); kind.textContent = hit.kind;
      row.append(label, kind); row.onclick = () => choose(hit); list.append(row);
    });
    if (matches.length) input.setAttribute('aria-activedescendant', 'quick-search-hit-' + selected);
    else {
      input.removeAttribute('aria-activedescendant');
      const empty = document.createElement('p'); empty.className = 'rail-search-empty';
      empty.textContent = '没有找到页面或插件，试试其他关键词。'; list.append(empty);
    }
  };
  const open = async (): Promise<void> => {
    if (document.querySelector('dialog[open], [role="alertdialog"]')) return;
    if (window.matchMedia('(max-width: 720px)').matches && !document.body.classList.contains('compact-menu-open'))
      (document.querySelector('.compact-menu-toggle') as HTMLButtonElement)?.click();
    if (!panel.hidden) { input.focus(); return; }
    previous = document.activeElement;
    trigger.hidden = true; nav.hidden = true; panel.hidden = false;
    document.body.classList.add('rail-searching');
    trigger.setAttribute('aria-expanded', 'true'); input.setAttribute('aria-expanded', 'true');
    input.value = ''; selected = 0; plugins = []; note.textContent = '正在读取插件…';
    paint(); input.focus();
    const mine = ++epoch;
    try {
      const data = await getStatusJson<{plugins: {name: string; displayName?: string; installed?: boolean}[]}>('/plugins');
      if (mine !== epoch || panel.hidden) return;
      plugins = (data.plugins || []).filter(p => p.installed !== false).map(p => ({title: p.displayName || p.name,
        kind: '插件配置', words: p.name, href: '#/infinity/' + encodeURIComponent(p.name)}));
      note.textContent = ''; paint();
    } catch { if (mine === epoch && !panel.hidden) note.textContent = '插件暂不可用，仍可搜索页面。'; }
  };
  trigger.onclick = () => { void open(); }; close.onclick = () => dismiss();
  input.oninput = () => { selected = 0; paint(); list.scrollTop = 0; };
  panel.addEventListener('keydown', e => {
    if (e.isComposing) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); dismiss(); }
  });
  input.addEventListener('keydown', e => {
    if (e.isComposing || !matches.length) return;
    if (e.key === 'Enter') { e.preventDefault(); choose(matches[selected]); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); selected = (selected + (e.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
      paint(); document.getElementById('quick-search-hit-' + selected)?.scrollIntoView({block: 'nearest'});
    }
  });
  // Wait for click so collapsing a compact rail cannot move the target between
  // pointer-down and pointer-up. Keep focus on the control the user clicked.
  document.addEventListener('click', event => {
    if (panel.hidden) return;
    const path = event.composedPath();
    if (path.includes(trigger) || path.includes(header)
      || path.some(node => node instanceof Element && node.classList.contains('rail-search-hit'))) return;
    // The list fills the rail, so most of its area can be blank. A click there
    // dismisses search too; dragging/clicking its scrollbar must still scroll.
    if (event.target === list) {
      const box = list.getBoundingClientRect();
      if (event.clientX >= box.left + list.clientLeft + list.clientWidth) return;
    }
    dismiss(false);
  }, true);
  window.addEventListener('hashchange', () => dismiss(false));
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
      e.preventDefault(); if (!e.repeat) void open();
    }
  });
}
