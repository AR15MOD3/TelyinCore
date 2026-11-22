import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import type { Chrome, Panel } from './shell';
import { pageFetch as fetch } from './status-json';

interface Account {
  id: string; revision: string; userId: string; platform: string; uid: string;
  bound: boolean; loginSaved: boolean; loginState: string;
}
interface Roster {
  bot: string; accounts: Account[]; people: number; accountCount: number;
  boundPeople: number; loginPeople: number; deleteAllowed: boolean;
}
const NAME = 'core-infinity-wavesuid';
const URL = '/infinity/waves/accounts';
function node<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = ''): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag); e.className = cls; e.textContent = text; return e;
}

/** Human-facing local evidence, never a claim of live QQ/token validation. */
function loginLabel(a: Account): string {
  if (!a.loginSaved) return a.bound ? '仅绑定 UID，未保存登录凭证' : '未保存登录凭证';
  if (a.loginState === '登录信息已标记失效') return '登录凭证已标记失效';
  if (a.loginState === '已保存抽卡登录（未在线验证）') return '已保存抽卡登录凭证';
  if (a.loginState === '已保存登录（未在线验证）') return '已保存库街区登录凭证';
  return '已保存登录凭证（详细状态待核对）';
}

/** Local operator management only. No QQ sends, login or model invocation. */
export function wavesPage(shell: Chrome, _name: string): Panel {
  const root = node('section', 'page-infinity page-world waves-page');
  const title = node('h1', '', '鸣潮查询 · 配置');
  const status = node('p', 'infinity-hint', '正在读取插件状态…');
  const refresh = node('button', 'infinity-action', '刷新名单'); refresh.type = 'button';
  const pageHead = node('div', 'head'); pageHead.append(title);
  const intro = node('p', 'infinity-hint', '查看当前 Bot 的鸣潮账号与本机登录凭证。插件启停请在插件总览中操作。');
  refresh.className = 'infinity-refresh';
  const service = node('section', 'infinity-settings-card waves-service');
  const serviceInfo = node('div'); serviceInfo.append(node('h3', '', '插件状态'), status);
  const overview = node('a', 'infinity-refresh', '前往插件总览 →');
  overview.setAttribute('href', '#/infinity');
  service.append(serviceInfo, overview);
  const count = node('p', 'infinity-hint', '账号名单尚未读取');
  const hint = node('p', 'infinity-hint', '群成员各自绑定、登录自己的账号。此处仅展示本机保存情况，不代表 QQ 在线状态或库街区凭证实时有效；刷新名单不会联网验证凭证。');
  const deletion = node('p', 'infinity-hint', '删除仅移除此 Bot 保存的绑定及登录信息，不注销游戏账号。请先到插件总览停用鸣潮插件，待服务退出后刷新名单，再执行删除。');
  const table = node('table', 'waves-accounts-table');
  const head = node('thead'); const tr = node('tr');
  for (const label of ['QQ 用户', '鸣潮 UID', '绑定', '登录凭证', '操作']) tr.append(node('th', '', label));
  head.append(tr); const body = node('tbody'); table.append(head, body);
  const message = node('p', 'infinity-message'); message.setAttribute('role', 'status');
  const roster = node('section', 'infinity-settings-card waves-roster');
  const tableScroll = node('div', 'waves-table-scroll'); tableScroll.append(table);
  const rosterHead = node('div', 'waves-roster-head');
  rosterHead.append(node('h3', '', '已保存账号'), refresh);
  roster.append(rosterHead, count, tableScroll, hint, deletion);
  root.append(pageHead, intro, service, roster, message);
  let current: Roster | null = null; let enabled: boolean | null = null;
  let activeBot: string | null = null;
  let busy = false; let generation = 0;
  const buttons = (): void => {
    refresh.disabled = busy;
    for (const b of body.querySelectorAll('button')) (b as HTMLButtonElement).disabled = busy || current?.deleteAllowed !== true;
  };
  async function answer(response: Response): Promise<any> {
    const text = await response.text();
    let value: any; try { value = JSON.parse(text); } catch { value = null; }
    if (!response.ok || value?.ok === false) throw new Error(value?.said || value?.message
      || (response.status === 404 ? '当前服务尚不支持账号管理。' : '操作未完成，请刷新后核对。'));
    return value;
  }
  async function load(): Promise<void> {
    const mine = ++generation; current = null; enabled = null; activeBot = null; buttons();
    // Independent reads: account management availability never invents plugin state.
    const result = await Promise.allSettled([
      fetch('/plugins', {cache:'no-store'}).then(answer),
      fetch(URL, {cache:'no-store'}).then(answer),
      fetch('/infinity/bots', {cache:'no-store'}).then(answer),
    ]);
    if (root.hidden || mine !== generation) return;
    body.replaceChildren();
    if (result[2].status === 'fulfilled' && typeof result[2].value.active === 'string') {
      activeBot = result[2].value.active;
    }
    const plugin = result[0];
    if (plugin.status === 'fulfilled') {
      const p = plugin.value.plugins?.find((p: any) => p.name === NAME);
      if (p && p.installed !== false) {
        coreSetUIText(title, 'textContent', [(p.displayName || '鸣潮查询'), corePhrase(" · 配置")]);
        enabled = p.enabled === true;
        coreSetUIText(status, 'textContent', (p.loaded === true ? corePhrase("插件运行中") : (enabled ? corePhrase("已启用，尚未加载") : corePhrase("插件已停用"))));
      } else coreSetUIText(status, 'textContent', corePhrase("鸣潮插件尚未安装"));
    } else coreSetUIText(status, 'textContent', corePhrase("插件状态读取失败"));
    const roster = result[1];
    if (roster.status === 'rejected' || roster.value.bot !== activeBot) {
      coreSetUIText(count, 'textContent', corePhrase("账号名单暂不可用"));
      coreSetUIText(message, 'textContent', (roster.status === 'rejected' && roster.reason instanceof Error ? roster.reason.message : corePhrase("当前 Bot 无法确认，请刷新后重试。")));
    } else {
      current = roster.value as Roster;
      coreSetUIText(count, 'textContent', [[corePhrase("已保存 "), current.people, corePhrase(" 人 · "), current.accountCount, corePhrase(" 个账号 · ")], [corePhrase(""), current.boundPeople, corePhrase(" 人已绑定 · "), current.loginPeople, corePhrase(" 人已保存登录凭证")]]);
      coreSetUIText(message, 'textContent', (current.accounts.length ? corePhrase("") : corePhrase("暂未保存鸣潮账号")));
      for (const a of current.accounts) {
        const row = node('tr'); const user = node('td', '', a.userId);
        const action = node('td'); const remove = node('button', 'infinity-action', '删除'); remove.type = 'button';
        coreSetUIAttribute(remove, 'aria-label', [corePhrase("删除 "), a.userId, corePhrase(" 的鸣潮账号 "), a.uid, corePhrase("")]);
        remove.addEventListener('click', () => { void erase(a); }); action.append(remove);
        row.append(user, node('td', '', a.uid), node('td', '', a.bound ? '已绑定' : '未绑定'),
          node('td', '', loginLabel(a)), action); body.append(row);
      }
    }
    buttons();
  }
  async function erase(account: Account): Promise<void> {
    if (busy || !current?.deleteAllowed || root.hidden) return;
    const captured = current; const mine = generation;
    if (!window.confirm(`删除 QQ ${account.userId} 的鸣潮账号 ${account.uid}？\n仅移除此 Bot 保存的绑定及登录信息，不注销游戏账号。本机将保留回滚备份。`)) return;
    busy = true; buttons(); coreSetUIText(message, 'textContent', corePhrase("正在删除…"));
    try {
      const response = await fetch(URL+'/delete', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({active:captured.bot,id:account.id,revision:account.revision,confirm:true})});
      const value = await answer(response);
      if (!root.hidden && mine === generation) {
        await load();
        if (!root.hidden) message.textContent = value.message || '已删除。';
      }
    } catch (e) {
      if (!root.hidden && mine === generation) coreSetUIText(message, 'textContent', (e instanceof Error ? e.message : corePhrase("删除结果未确认，请刷新核对；不要重复提交。")));
    } finally { busy = false; buttons(); }
  }
  refresh.addEventListener('click', () => { if (!busy) void load(); });
  shell.content.replaceChildren(root);
  return {root,show() {root.hidden=false;void load();},stop() {root.hidden=true;generation++;current=null;enabled=null;activeBot=null;}};
}
