import {confirmAction} from './confirm-dialog';
import {getStatusJson} from './status-json';
import {uiLabel, coreSetUIAttribute, corePhrase, coreSetUIText} from './ui-language';
import {readyPluginDisplay, pluginDisplayEntries, pluginDisplayProtected, clearPluginDisplay, fuzzyPluginMatch} from './plugin-display-cache';
interface Plugin {name: string; displayName?: string; installed?: boolean;}
const element = (tag: string, cls: string, label?: string): HTMLElement => {
  const el = document.createElement(tag); el.className = cls; if (label) uiLabel(el, label); return el;
};
export function pluginStorageSettings(): HTMLElement {
  const root = element('section', 'settings-card plugin-storage');
  root.append(element('h3', '', '插件页面缓存'), element('p', 'pcs-scope', '当前档案 · 当前浏览器标签页'));
  const panel = element('div', 'pcs-panel');
  const toolbar = element('div', 'pcs-toolbar');
  const search = document.createElement('input'); search.type = 'search'; search.maxLength = 128;
  coreSetUIAttribute(search, 'aria-label', corePhrase('搜索插件页面缓存'));
  uiLabel(search, '名称、关键词或缩写', 'placeholder');
  const filters = element('div', 'pcs-filters');
  let filter = 'all'; let plugins: Plugin[] = []; let ready = false; let composing = false; let page = 0;
  const rows = element('div', 'pcs-rows');
  const footer = element('div', 'pcs-footer'); const count = element('span', '');
  const pager = element('div', 'pcs-pager');
  const prev = element('button', '', '上一页') as HTMLButtonElement;
  const next = element('button', '', '下一页') as HTMLButtonElement;
  const position = element('span', ''); pager.append(prev, position, next); footer.append(count, pager);
  const message = element('p', 'pcs-message'); message.setAttribute('role', 'status');
  const draw = (): void => {
    rows.replaceChildren();
    let cached: ReturnType<typeof pluginDisplayEntries>;
    try { cached = pluginDisplayEntries(); } catch { ready = false; uiLabel(message, '无法访问浏览器缓存，未清理任何数据。'); cached = []; }
    const all = new Map(plugins.map(p => [p.name, p]));
    for (const entry of cached) if (!all.has(entry.name)) all.set(entry.name, {name:entry.name});
    const selected = [...all.values()].filter(p => {
      const has = cached.some(c => c.name === p.name);
      return (filter === 'all' || (filter === 'cached' ? has : !has)) && fuzzyPluginMatch(p.displayName || p.name, p.name, search.value);
    }).sort((a,b) => (a.displayName || a.name).localeCompare(b.displayName || b.name));
    page = Math.min(page, Math.max(0, Math.ceil(selected.length / 8) - 1));
    for (const plugin of selected.slice(page * 8, (page + 1) * 8)) {
      const cache = cached.find(c => c.name === plugin.name);
      const row = element('div', 'pcs-row'); row.dataset.plugin = plugin.name;
      const identity = element('div', 'pcs-identity');
      const icon = element('span', 'pcs-icon'); icon.textContent = (plugin.displayName || plugin.name).slice(0, 1).toUpperCase();
      const name = element('span', 'pcs-name'); name.textContent = plugin.displayName || plugin.name; name.title = plugin.name;
      identity.append(icon, name);
      const state = element('span', 'pcs-type', cache ? '页面状态' : '无缓存');
      const size = element('span', 'pcs-size'); size.textContent = cache ? `${cache.bytes} B` : '—';
      const clean = element('button', 'pcs-clean', '清理') as HTMLButtonElement;
      clean.type = 'button'; clean.disabled = !ready || !cache || pluginDisplayProtected(plugin.name);
      coreSetUIAttribute(clean, 'aria-label', [corePhrase('清理'), ' ', plugin.displayName || plugin.name]);
      if (pluginDisplayProtected(plugin.name)) uiLabel(clean, '有未保存内容，暂不清理', 'title');
      clean.onclick = () => {
        uiLabel(message, clearPluginDisplay(plugin.name) ? '已清理显示缓存；配置和未保存内容保持不变。' : '未清理：缓存不可用或有内容需要保护。'); draw();
      };
      row.append(identity, state, size, clean); rows.append(row);
    }
    if (!selected.length) rows.append(element('p', 'pcs-empty', ready ? '没有匹配的插件页面缓存。' : '正在读取…'));
    coreSetUIText(count, 'textContent', [selected.length, corePhrase(' 项'), ' · ', selected.reduce((sum,p) => sum + (cached.find(c => c.name === p.name)?.bytes ?? 0),0), ' B']);
    position.textContent = `${page + 1} / ${Math.max(1, Math.ceil(selected.length / 8))}`;
    prev.disabled = page === 0; next.disabled = (page + 1) * 8 >= selected.length;
  };
  for (const [value,label] of [['all','全部'],['cached','有缓存'],['empty','无缓存']]) {
    const button = element('button','',label) as HTMLButtonElement; button.type='button'; button.setAttribute('aria-pressed', String(value === filter));
    button.onclick = () => { filter=value; page=0; for (const child of filters.children) child.setAttribute('aria-pressed',String(child === button)); draw(); }; filters.append(button);
  }
  search.oncompositionstart = () => { composing = true; };
  search.oncompositionend = () => { composing = false; page = 0; draw(); };
  search.oninput = () => { if (!composing) { page = 0; draw(); } };
  search.onkeydown = e => { if (e.key === 'Escape' && !e.isComposing) { search.value = ''; page = 0; draw(); } };
  prev.onclick = () => { page--; draw(); }; next.onclick = () => { page++; draw(); };
  toolbar.append(search, filters); panel.append(toolbar, rows, footer); root.append(panel);
  root.append(element('p', 'pcs-hint', '仅清理页签选择等临时显示状态，不重载表单。配置、聊天记录、记忆和未保存内容均不受影响。'));
  root.append(element('p', 'pcs-hint', '用量仅含已登记的临时显示状态，不代表插件文件大小。'), message);
  void (async () => {
    ready = await readyPluginDisplay();
    try {
      const answer = await getStatusJson<{plugins?:Plugin[]}>('/plugins', 5000);
      plugins = (answer.plugins ?? []).filter(p => typeof p.name === 'string' && p.installed !== false);
    } catch { uiLabel(message, '插件列表读取失败；仍可查看本页已知的显示缓存。'); }
    if (!ready) uiLabel(message, '档案身份尚未确认，暂不允许清理。');
    draw();
  })();
  draw(); return root;
}

export function snowlumaSessionSettings(): HTMLElement {
  const root = element('section', 'settings-card plugin-storage');
  root.append(element('h3', '', 'SnowLuma 管理登录会话'),
    element('p', 'pcs-scope', '当前 Bot · Core 后台'));
  const description = element('p', 'pcs-hint',
    '清除后，Core 下次同步协议配置时会用已保存的管理密码重新登录。');
  const status = element('p', 'pcs-message');
  status.setAttribute('role', 'status');
  const clear = element('button', 'pcs-clean', '清除登录会话') as HTMLButtonElement;
  clear.type = 'button';
  clear.disabled = true;
  let active = '';
  root.append(description, clear, status);

  const refresh = async (): Promise<void> => {
    clear.disabled = true;
    try {
      const response = await fetch('/infinity/qq/management-session', {cache: 'no-store'});
      if (!response.ok) throw new Error();
      const state = await response.json();
      active = typeof state.active === 'string' ? state.active : '';
      clear.disabled = !(active && state.available === true && state.hasSession === true);
      uiLabel(status, state.available !== true ? '当前运行方式不支持会话清理。'
        : state.hasSession === true ? '已保存登录会话' : '没有已保存的登录会话');
    } catch {
      uiLabel(status, '暂时无法读取 SnowLuma 登录会话状态。');
    }
  };

  clear.onclick = async () => {
    if (clear.disabled) return;
    clear.disabled = true;
    let message = '';
    try {
      if (!await confirmAction('清除 SnowLuma 登录会话',
        `清除 ${active} 的 Core 管理登录会话。已保存的密码、QQ 接入和会话路由会保留。`,
        '清除会话') || !root.isConnected) return;
      const response = await fetch('/infinity/qq/clear-management-session', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({active}),
      });
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error(result.said || '清除会话失败。');
      message = result.cleared ? '已清除登录会话。' : '没有需要清除的登录会话。';
    } catch (error) {
      message = error instanceof Error && error.message ? error.message : '清除会话失败。';
    } finally {
      await refresh();
      if (message) uiLabel(status, message);
    }
  };
  void refresh();
  return root;
}
