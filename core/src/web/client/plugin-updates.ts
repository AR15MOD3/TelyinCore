import { onDisplayScopeReady, readDisplayLocal, writeDisplayLocal } from './display-storage';
/** Shared read-only update state. Installation remains an explicit operator action. */
import { pageFetch as fetch } from './status-json';
export interface PluginUpdate {
  name: string; displayName: string; current: string; latest?: string; tag?: string; repository: string;
  status: 'pending'|'available'|'current'|'ahead'|'prerelease'|'managed'|'unsupported'|'unavailable'|'unreleased';
  stale?: boolean; error?: string; message?: string; checkedAt?: number;
}
interface UpdateState {rows: PluginUpdate[]; checking: boolean; message?: string;}
let state: UpdateState = {rows: [], checking: false};
let pending: Promise<void>|undefined;
let failure = '';
let started = false;
let loaded = false;
export const updateFor = (name: string) => state.rows.find(row => row.name === name);
export const pluginUpdateSnapshot = () => ({...state,error:failure});
const muteKey = 'plugin-update-muted';
function mutedNames(): string[] {
  try {
    const names: unknown = JSON.parse(readDisplayLocal(muteKey) || '[]');
    return Array.isArray(names) ? names.filter((n): n is string => typeof n === 'string') : [];
  } catch { return []; }
}
export const updateMuted = (name: string) => mutedNames().includes(name);
export function setUpdateMuted(name: string, mute: boolean) {
  const names = new Set(mutedNames());
  if (mute) names.add(name); else names.delete(name);
  writeDisplayLocal(muteKey, JSON.stringify([...names]));
  paint();
}
const available = () => state.rows.filter(row => row.status === 'available' && !updateMuted(row.name));
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const node = document.createElement(tag); node.textContent = text; node.className = cls; return node;
};
export function updateText(row: PluginUpdate): string {
  if (row.status === 'available') return `${row.current} → ${row.latest}${row.stale ? ' · 上次发现，待复查' : ' · 可更新'}`;
  if (row.stale && row.status !== 'pending') return row.error || '检查已过期，等待复查';
  if (row.status === 'current') return `${row.current} · 已是最新`;
  if (row.status === 'ahead') return `${row.current} · 高于公开版本 ${row.latest}`;
  if (row.status === 'prerelease') return `${row.latest} 为预发布版本`;
  if (row.status === 'pending') return '等待检查';
  return row.message || row.error || '暂时无法确认';
}
function summary(): string {
  if (failure) return failure;
  if (state.checking) return '正在后台检查插件更新…';
  const count = available().length;
  const unknown = state.rows.filter(row => row.status === 'pending' || row.status === 'unavailable' || row.stale).length;
  const unsupported=state.rows.filter(row=>row.status==='unsupported').length;
  const checked=state.rows.filter(row=>['current','ahead','prerelease','unreleased'].includes(row.status)&&!row.stale).length;
  return count ? `${count} 个插件发现新版本${unknown ? `，${unknown} 项待复查` : ''}`
    : unknown ? `${unknown} 个插件尚未确认版本` : checked ? `已检查 ${checked} 个插件，暂无更新` : state.rows.some(row=>row.status==='available') ? '暂无需要提示的插件更新' : unsupported ? `${unsupported} 个插件无法自动检查，请查看来源` : state.rows.length ? '内置插件随 Core 更新' : loaded ? '暂无已安装插件' : '等待读取插件更新状态';
}
function paint() {
  document.querySelectorAll<HTMLElement>('[data-plugin-update-summary]').forEach(node => node.textContent = summary());
  document.querySelectorAll<HTMLElement>('[data-plugin-update-name]').forEach(node => {
    const row = updateFor(node.dataset.pluginUpdateName!);
    node.hidden = !row || row.status !== 'available' || updateMuted(row.name);
    const text = node.querySelector<HTMLElement>('[data-plugin-update-text]');
    if (text && row) text.textContent = updateText(row);
    node.classList.toggle('has-update', row?.status === 'available');
  });
  const top = document.querySelector<HTMLButtonElement>('.topbar-plugin-updates');
  if (top) {top.textContent = available().length ? `插件更新 ${available().length}` : '插件更新'; top.hidden = available().length === 0; top.title = summary(); top.classList.toggle('has-update', available().length > 0);}
  window.dispatchEvent(new Event('plugin-updates-changed'));
}
let manual: Promise<void> | undefined;
async function readUpdates(force: boolean) {
  const response = await fetch('/plugin-updates' + (force ? '?refresh=1' : ''), {cache:'no-store'});
  if (!response.ok) throw Error(response.status === 503 || response.status === 404
    ? '此 Core 尚未启用插件更新检查' : '暂时无法读取插件更新');
  const next = await response.json();
  if (!Array.isArray(next.rows) || typeof next.checking !== 'boolean') throw Error('更新状态格式异常');
  state = next;
  loaded = true;
  failure = '';
  paint();
}
async function observeUpdates(refresh: boolean) {
  try {
    const deadline = Date.now() + 90000;
    await readUpdates(refresh);
    // Older services acknowledge a forced scan before their worker picks it up.
    let verifyQueued = refresh;
    while (verifyQueued || state.checking) {
      verifyQueued = false;
      if (Date.now() >= deadline) throw Error('检查耗时较长，可稍后再查。');
      await new Promise(resolve => setTimeout(resolve, 1250));
      await readUpdates(false);
    }
  } catch (error) {
    state = {...state, checking:false, rows:state.rows.map(row=>({...row,stale:true}))};
    failure = error instanceof Error && error.name !== 'StatusTimeoutError'
      ? error.message : '读取更新状态超时，可重新检查。';
    paint();
  }
}
export function checkPluginUpdates(refresh = false): Promise<void> {
  if (manual) return manual;
  if (refresh) {
    manual = Promise.resolve(pending).then(() => observeUpdates(true)).finally(() => { manual = undefined; });
    return manual;
  }
  return pending ??= observeUpdates(false).finally(() => { pending = undefined; });
}
export function pluginUpdateNotice(name?: string): HTMLElement {
  const box = el('section', '', 'plugin-update-strip');
  const text = el('span');
  if (name) {box.dataset.pluginUpdateName = name; text.dataset.pluginUpdateText = '';box.hidden = true;}
  else {text.dataset.pluginUpdateSummary = ''; text.textContent = summary();}
  const button = el('button', name ? '查看更新 →' : '查看更新 →', 'infinity-action');
  button.type = 'button';button.addEventListener('click', () => openPluginUpdates(name, button));
  box.append(text, button);
  if (name) {const row = updateFor(name);if (row && row.status === 'available' && !updateMuted(row.name)) {box.hidden = false;text.textContent = updateText(row);}}
  return box;
}
let requested: {name?: string}|null = null;
export function takePluginUpdateRequest() {const value=requested;requested=null;return value;}
export function openPluginUpdates(name?: string, _opener?: HTMLElement) {
  const updates=available();
  requested={name:name||(updates.length===1?updates[0].name:undefined)};
  if(location.hash!=='#/infinity') location.hash = '#/infinity';
  else window.dispatchEvent(new Event('plugin-updates-open'));
}
export function pluginUpdatesPanel() {
  const root=el('section','','plugin-update-panel');
  root.setAttribute('aria-label','插件更新');
  const controls=el('div','','plugin-update-controls');
  const search=el('input');search.type='search';search.placeholder='搜索插件更新';search.setAttribute('aria-label','搜索插件更新');
  const filter=el('select');filter.setAttribute('aria-label','更新状态筛选');
  for(const [value,label] of [['all','全部状态'],['available','可更新'],['unknown','待确认']]) filter.add(new Option(label,value));
  const check=el('button','检查更新','btn ghost');check.type='button';
  check.onclick=async()=>{check.disabled=true;await checkPluginUpdates(true);check.disabled=false;};
  controls.append(search,filter,check);
  const hint=el('p',summary(),'plugin-update-hint');hint.setAttribute('role','status');
  const list=el('div','','plugin-update-list');
  const foot=el('p','服务启动时检查，空闲时每 6 小时复查。只检查正式 Release；不包含开发分支、预发布版或未发布的标签。','plugin-update-footnote');
  root.append(controls,hint,list,foot);
  let renderKey='';
  const render=()=>{
    hint.textContent=[summary(),state.message].filter(Boolean).join(' · ');
    const key=JSON.stringify([state.rows,filter.value,search.value,failure]);
    if(key===renderKey)return;renderKey=key;list.replaceChildren();
    const rows=state.rows.filter(row=>(row.name+' '+row.displayName).toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()) && (filter.value==='all'||filter.value==='available'&&row.status==='available'||filter.value==='unknown'&&(row.stale||['pending','unavailable'].includes(row.status))))
      .sort((a,b)=>Number(b.status==='available')-Number(a.status==='available')||a.name.localeCompare(b.name));
    if(!rows.length) list.append(el('p',failure?'读取失败，请稍后重新检查。':'此筛选下暂无插件。','plugin-empty'));
    for(const row of rows){
      const card=el('article','','plugin-update-item');
      const heading=el('div','','plugin-update-item-heading');
      const identity=el('div');identity.append(el('h3',row.displayName||row.name),el('span',row.name,'plugin-update-package'));
      const version=el('span',updateText(row),'plugin-version-status');version.classList.toggle('available',row.status==='available');
      heading.append(identity,version);card.append(heading);
      const meta=el('div','','plugin-update-item-meta');
      if(row.checkedAt) meta.append(el('span','上次检查 '+new Date(row.checkedAt*1000).toLocaleString()));
      if(row.error) meta.append(el('span',row.error));
      if(/^[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+$/.test(row.repository)){
        const link=el('a','来源与更新说明 ↗');link.href='https://github.com/'+row.repository;link.target='_blank';link.rel='noopener noreferrer';meta.append(link);
      }
      card.append(meta);
      if(row.status==='available'){
        const guide=el('section');guide.append(el('h3','升级步骤'));
        guide.append(el('p','等待当前任务结束并备份配置和自定义文件。在插件市场的「本机插件」卸载旧版，再从「发现插件」安装新版，安装前核对市场版本。卸载会停用所有 Bot 共用的该插件，配置和缓存保留。安装后检查配置，再按需启用。'));
        card.append(guide);
      }
      list.append(card);
    }
  };
  search.oninput=render;filter.onchange=render;
  return {root,render,search(name=''){search.value=name;filter.value='all';render();}};
}
export function startPluginUpdates() {
  if (started) return;started=true;
  onDisplayScopeReady(prefix => {
    paint();
    window.addEventListener('storage', event => {
      if (event.key === prefix + muteKey || event.key === null) paint();
    });
  });
  const row=document.querySelector('.topbar-status');
  if(row){const button=el('button','插件更新','topbar-update topbar-plugin-updates');button.type='button';button.hidden=true;button.onclick=()=>openPluginUpdates(undefined,button);row.append(button);}
  void checkPluginUpdates();
  window.setInterval(()=>{if(!document.hidden)void checkPluginUpdates();},60000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void checkPluginUpdates();});
  window.addEventListener('plugins-changed',()=>{void checkPluginUpdates();});
}
