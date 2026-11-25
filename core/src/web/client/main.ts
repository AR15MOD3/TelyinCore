import { pluginNavigationOrder } from './plugin-navigation-order';
import { startPluginRestart } from './plugin-restart';
import { startPluginUpdates } from './plugin-updates';
import { startPackageUpdates } from './package-updates';
import { corePhrase, coreSetUIText } from './ui-language';
import { syncGateState } from './shell';
import { initializeDisplayScope, onDisplayScopeReady, readDisplaySession,
  writeDisplaySession } from './display-storage';
import { pageFetch as fetch } from './status-json';
import { modelDisplayName } from './model-display';
import { onEngineChanged } from './engine-events';
import { pluginIcon, pluginSignals, navigationParent, refreshPluginHealth,
  type PluginNavigationInfo } from './plugin-ui-registry';
import { getStatusJson } from './status-json';
// 那一页的入口（esbuild 打的 bundle 就是它）。
//
// 三条路，各归各的：
//   · `GET /events` —— 那条**推**的通道（SSE）：它说的话、那一批的头一行，从这儿来；
// · `POST /say` —— **写入那条路**：你打的那一行（边界见 `./engine`）；
// · `GET /state` —— **顶上那条计数**：四个数 ＋ 端点 ／ 模型 ／ 连接状态，从这儿来。
//
// 而**壳是另一件事**：左栏 ／ 顶栏两层 ／ 正文那一列 ／ 底部那一行在 `./shell` 里建，
//    页表与路由器在 `./pages` 里 —— 这个文件只做三下：**建壳、把组摆好、把当前那一页亮出来**
//    （再加那三条路）。
// ⚠️ **这一层依旧零框架**（借形状，不借 React）：全是 `document` ＋ `fetch` ＋ `EventSource`。

// **页表要成为产物的一处出口**（本仓成法：`tools/check_test_diff.py` 那两个函数
// ——"测试要量它、而它是模块私有的"那件事有一处明说的口子）。 这里要的是**运行期**
//    看得见那张表：`tests/test_web.py` 把产物跑起来，判"页表那两页都在左栏上、点得动"。
export { PAGES } from './pages';

// **这一条与上面 `PAGES` 是同一条成法**：探针要把两帧交给那一列，而 `mountChat`
//    是 `chat.ts` 里的模块私有件 ⇒ 从这里转出去（产物那一侧才拿得到它）。
export { mountChat } from './chat';

import { createShell, loadAvatar, type Chrome, type Panel } from './shell';
import { type ChatView } from './chat';
import { loadSpeaker, speaker, loadUserLabel } from './speaker';
import { groupsOf, navItems, route, soonItems, SOON, type NavItem } from './pages';
import { machineStatus, COUNTER_KEYS, count, counterChips, ENGINE_KEYS, type State } from './state';

/**
 * **这一页读的那几个名字** —— 与 `telyincore/web.py` 发出来的那一份逐个对得上
 *   （`state.ts` 里那两张表就是它们的正本）。⚠️ 把清单摆在这一处、**声明一次**：
 *   名字与那一页分家（端点改了字段名、页面还在读旧名字）是**静默**的 —— 那一栏会
 *   安安静静画成 `—`，而没有任何一条测试会红。⇒ 这一个数组就是那颗钉子的一半
 *   （另一半在 `tests/test_web.py`：它判端点发的名字）。
 */
const READ_NAMES = [
  ...COUNTER_KEYS, ...ENGINE_KEYS, 'connected',
] as const;
void READ_NAMES;

/** 读一遍那台机器交回来的那一段（ 名字一个都不许飘 —— 见 `READ_NAMES` 那段）。 */
function machineOf(st: State): void {
  void st.connected;
  if (st.machine === null) return;
  void st.machine.tokens;
  void st.machine.messages;
  void st.machine.batches;
  void st.machine.paused;
  void st.machine.endpoint;
  void st.machine.model;
  // ⚠️ 那一格（`topbar-providername`）**删了** —— 顶栏本来就有 `topbar-model` 那一格
  //    （它由这一趟里的 `bar.model.textContent = st.machine.model` 写）⇒ 别再多写一处。
  void st.machine.profile;
  void st.machine.model_available;
}

/** **认页靠各自那片形状**：聊天那一页会落行，引擎那一页有那几个数的落点。 */
function isChat(view: unknown): view is ChatView {
  return typeof view === 'object' && view !== null
    && typeof (view as ChatView).append === 'function';
}

function need(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (el === null) throw new Error(`那一页的壳里少了 #${id}`);
  return el;
}

/** 那四块的**落点声明**：`index.html` 里写着它们（壳自己也会带这四个 id 上来）。 */
function anchors(): void {
  for (const id of ['rail', 'topbar', 'content', 'bottom']) need(id);
}

// 壳挂到**那个根**上（`body`）：左栏 ／ 顶栏 ／ 正文 ／ 底部那四块由 `shell.ts` 建，
//    而 `index.html` 里那四个 id 是它们的**落点声明**（判据拿它对一遍）。
void initializeDisplayScope(); // Optional display identity never blocks the shell.
anchors();
const mounted = createShell(document.body);
if (typeof window.setInterval === 'function') { startPackageUpdates(); startPluginUpdates(); startPluginRestart(); }
const shell: Chrome = mounted.shell;
const bar = mounted.topbar;
const conn = bar.provider; // 连接那一格（顶栏右边那一枚）
for (const [index, group] of groupsOf().entries()) {
  // **本刀多递一格**：那一组里"还没做"的那几项（`SOON`）—— 它们**照旧画在左栏里**，
  // 而**不是链接**（"记忆"那一页：入口要做、页面先不做）。 缺省是空 ⇒ 别处不用改。
  shell.place(index, group, navItems(group), soonItems(group, SOON));
}

/** Installed packages get their own routes beneath the two Infinity index links. */
let displayScopeReady = false;
let pluginNavPending = false;
let pluginNavKey = '';
let pluginNavGeneration = 0;
let latestPluginEntries: NavItem[] | null = null;
const navigationOrder = pluginNavigationOrder(shell.railNav, () => {
  pluginNavKey = '';
  if (latestPluginEntries !== null) placePluginNav(groupsOf().indexOf('INFINITY'), latestPluginEntries);
  else restorePluginNav();
});
function restorePluginNav(): void {
if (navigationOrder.editing) return;
try {
  const cached = JSON.parse(readDisplaySession('plugin-nav') || '[]') as NavItem[];
  if (Array.isArray(cached) && cached.every(one => typeof one.id === 'string'
      && one.id.startsWith('infinity/') && typeof one.label === 'string')) {
    const index = groupsOf().indexOf('INFINITY');
    if (index >= 0 && cached.length) {
      // Metadata can stabilize navigation, but cached health is not live health.
      shell.place(index, 'INFINITY', [...navItems('INFINITY'),
        ...cached.map(one => ({ ...one, signals: undefined, dot: false }))]);
      navigationOrder.mount(cached);
    }
  }
} catch { /* Storage may be disabled; live discovery remains authoritative. */ }
}
restorePluginNav();
onDisplayScopeReady(() => {
  displayScopeReady = true;
  if (latestPluginEntries !== null) {
    pluginNavKey = '';
    placePluginNav(groupsOf().indexOf('INFINITY'), latestPluginEntries);
  }
  else restorePluginNav();
});
function pluginEntries(plugins: readonly PluginNavigationInfo[]): NavItem[] {
  return plugins
    .filter((one) => one.installed !== false && one.name)
    .map((one): NavItem => {
      const signals = pluginSignals(one);
      return { id: `infinity/${encodeURIComponent(one.name)}`,
        label: one.displayName || one.name, icon: pluginIcon(one), child: true, signals,
        navigationParent: navigationParent(one) };
    });
}
function placePluginNav(index: number, entries: NavItem[]): void {
  latestPluginEntries = entries;
  const key = JSON.stringify(entries);
  if (key === pluginNavKey || navigationOrder.editing) return;
  pluginNavKey = key;
  try { writeDisplaySession('plugin-nav', JSON.stringify(entries)); }
  catch { /* Optional refresh cache. */ }
  shell.place(index, 'INFINITY', [...navItems('INFINITY'), ...entries]);
  navigationOrder.mount(entries, displayScopeReady);
  const active = location.hash.replace(/^#\/?/, '') || 'live';
  for (const link of shell.railNav.querySelectorAll<HTMLElement>('.rail-item')) {
    link.classList.toggle('active', link.dataset.page === active);
  }
}
async function refreshPluginNav(): Promise<void> {
  if (pluginNavPending) return;
  const index = groupsOf().indexOf('INFINITY');
  if (index < 0) return;
  pluginNavPending = true;
  const generation = ++pluginNavGeneration;
  try {
    // Shared with the QQ page's own read; the rail never waits for health.
    const answer = await getStatusJson<{ wired?: boolean;
      plugins?: PluginNavigationInfo[] }>('/plugins', 5000);
    if (!answer.wired) return;
    const plugins = answer.plugins ?? [];
    placePluginNav(index, pluginEntries(plugins));
    // A slow or failed `/infinity/qq` must not hold back the plugin metadata
    // above. When health settles, redraw only the signal lights.
    void refreshPluginHealth(plugins).then(() => {
      if (generation !== pluginNavGeneration) return;
      placePluginNav(index, pluginEntries(plugins));
    });
  } catch { /* Preserve the two static entries when the plugin list is unavailable. */ }
  finally { pluginNavPending = false; }
}
void refreshPluginNav();

window.addEventListener('plugins-changed', () => { void refreshPluginNav(); });
if (typeof window.setInterval === 'function') window.setInterval(() => {
  if (document.visibilityState !== 'hidden') {
    void refreshPluginNav(); 
  }
}, 3000);

/** 当前那一页的把手（换页那一下由 `pages.ts::route` 交回）。 */
let panel: Panel = null as unknown as Panel;
/** 当前那一页的那片把手（认不出来 ⇒ `null`）。 */
let view: unknown = null;
/** 聊天那一列（推来的每一帧落进它）—— 别的页上没有它。 */
let chat: ChatView | null = null;

/** **她那个名字落地那一下**：写进壳那三处 ＋ 这一列（画过的也重画）。 */
function applyNames(): void {
  shell.setSpeaker(speaker());
  if (chat !== null) chat.setSpeaker(speaker());
}

// **开机读一次**（`GET /persona`）—— 读不到 ⇒ `speaker()` 就是那个中性词（不抛、不崩）。
void Promise.all([loadSpeaker(), loadUserLabel()]).then(applyNames);
window.addEventListener('bot-display-changed', () => {
  void Promise.all([loadSpeaker(true), loadUserLabel(true)]).then(applyNames);
});

// 换页：`route()` 那一下（它自己会停上一页、亮新那一页），这里只跟着重读那几个数。
function switchPage(): void {
  panel = route(shell);
  view = (panel as unknown as { view?: unknown }).view;
  chat = isChat(view) ? view : null;
  applyNames(); // 换页之后新那一列也要拿到那个名字
  void refresh();
}

/**
 * 顶上那几个数 ／ 引擎那一栏 —— 这一页上一个数都不写死，全从 `GET /state` 来。
 *
 * ⚠️⚠️ **两条失败的路都得收住**（本波补的，而它补的是一个真的哑巴状态）：
 *    · `fetch('/state')` 交回非 200（服务在收摊、路由出错）；
 *    · 响应不是 JSON（半截的代理页、别的东西占着那个端口）。
 *    两条都会让这里**抛**，而它是 `void refresh()` 调的 ⇒ 抛出来没人接
 *    ⇒ **这一页一个字都不说**，顶栏照旧画着上一次那几个数（一张过期的收据，
 *    而它看起来与"还接着"一模一样）—— 那正是这一页唯一不许做的事：静默。
 * ⇒ 整个函数包在 `try/catch` 里，而 `catch` 那一下**把连接那一格翻掉**。
 * 它写的是「读不到」，**不是**「断开」：那是**两件事**（推的那条通道好好的、
 *      而 `/state` 读不出来是另一回事）—— 这一页上多一个词，是为了少一个谎。
 */
let stateRead = 0;
async function refresh(): Promise<void> {
  const read = ++stateRead;
  const usageRead = fetch('/usage').then(r => r.ok ? r.json() : null).catch(() => null);
  let where = '读 /state';
  try {
    const got = await fetch('/state');
    if (!got.ok) throw new Error(`/state 回了 ${got.status}`);
    const st = (await got.json()) as State;
    // **2026-10-09：那一格改读「上下文占用」那个总数** （作者：「工具这个到底进不进
    // 上下文，进的话就要算进去。」 —— 而工具**真的进请求体** ：
    // `generate.py:146-148` 的 `body["tools"] = [dict(one) for one in tools]` ）。
    // ⚠️ **为什么不在 `/state` 那一头改** ： 那一头算的是 `console.request_size()`
    // （ **只按会话算** ⇒ 漏掉「工具 · 插件」那一整块 ； 真页量到：
    // `/usage` 的 `total` **5165** ／ `/state` 的 `tokens` **4172** ／ 差 **993** ）。
    // 我在那一头改了八次**都没让它生效** （ 账在计划 §十一 ）⇒ 改走这一条：
    // **两个显示共用同一份数据** ⇒ 结构上不可能再对不上 ， 后端一个字不用动 。
    // 问不到 ⇒ 退回旧那一路 （ 不编一个数 ）。
    const total: number | null = null;
    void usageRead.then(box => {
      if (read !== stateRead || typeof box?.total !== 'number') return;
      shell.counters(counterChips({ ...st.machine, tokens: box.total, context_estimate_source: box.estimate_source }));
    });
    if (read !== stateRead) return;
    where = '写机器那几个数';
    machineOf(st);
    syncGateState(shell.root, st.machine);
    where = '写顶栏那几格';
    // **第一行那个 `tok` 也走同一个总数**（2026-10-09 ）—— 它与第二行那一格
    // 是**同一件事的另一个显示** （ 都念 `st.machine.tokens` ⇒ 两处都少
    // 「工具 · 插件」那 993 ）。 不改 `state.ts` ： 在这儿把那一个字段**替掉** 。
    shell.counters(counterChips(total === null ? st.machine : { ...st.machine, tokens: total }));
    window.dispatchEvent(new CustomEvent('core-console-state', { detail: st.machine }));
    const localHealth = st.machine?.model_available;
    coreSetUIText(conn, 'textContent', (localHealth === false ? corePhrase("模型离线") : (!st.connected ? corePhrase("页面断开") : corePhrase("已连接"))));
    conn.dataset.on = String(st.connected && localHealth !== false);
    conn.dataset.model = localHealth === false ? 'offline'
      : localHealth === true ? 'online' : 'unknown';
    coreSetUIText(conn, 'title', (localHealth === false ? corePhrase("本地模型未响应；QQ 消息仍可入站，但 Agent 暂时无法生成回复。") : (localHealth === true ? corePhrase("本地模型探活成功。") : corePhrase("仅表示页面连接状态；当前模型未做探活。"))));
    bar.model.dataset.empty = st.machine === null || st.machine.model === null ? 'true' : 'false';
    bar.model.textContent = st.machine === null || st.machine.model === null
      ? '—' : modelDisplayName(st.machine.model);
    bar.model.title = (st.machine?.model ? st.machine.model + ' · ' : '') + '打开模型配置';
    // 缓存那一格：今天没有缓存这回事 ⇒ 它报的是**这台机器上过了多少 token 的上下文**
    //   （`paused` 那两格另说）—— 一个格子一个真数，而不是一个装饰。
    // **这一格现在读 `/usage` 的 `total`** （ 与右下角那块**同源** ⇒ 两个显示
    // 不可能再对不上 ）； 问不到 ⇒ 退回 `st.machine.tokens` （ 不编 ）。
    shell.counters(counterChips(
      total === null ? st.machine : { ...st.machine, tokens: total },
    ));
    where = '画那一页';
    // **这一句删掉了**（2026-10-05）：`isEngine` 与 `renderEngine` **全树都没有定义**
    // ⇒ 每次 `refresh()` 都抛 `ReferenceError` ⇒ 被外层 `catch` 吞成「读不到」，
    //    而顶栏那几个数被写成 `—`（作者那张图里的 `tok — 条 — 累计批 — 状态 —` 就是它）。
    // ⚠️ 为什么一直没被发现：判据只读源，**没有一条真的把 `refresh()` 跑一遍** 。
    // 而那一句本来也不需要：**这一页自己取自己那点数据**（`GET /engine`），
    //      不必让壳替它推一份（那份推的接口本来就不存在）。
  } catch (exc) {
    // **别把真原因吞成一句「读不到」**（2026-10-05）：`/state` 明明读到了、
    // 而某一句画的时候抛了 ⇒ 页上只能说"读不到"，排查的人只能猜 。
    //    ⇒ 说清**是哪一段**抛的，并把异常留在控制台里。
    if (read !== stateRead) return;
    console.error('refresh 抛在：', where, exc);
    conn.dataset.on = 'false';
    conn.dataset.model = 'unknown';
    coreSetUIText(conn, 'title', corePhrase("无法刷新连接状态，请检查服务后重试。"));
    coreSetUIText(conn, 'textContent', corePhrase("状态读取失败"));
    bar.model.dataset.empty = 'true';
    bar.model.textContent = '—';
    bar.model.title = ''; 
    shell.counters(COUNTER_KEYS.map((key) => [key, '—'] as const)); // 一格一格写 `—`
  }
}

// 那条推的通道：连上就订阅（重连带在 EventSource 里）；每来一帧就落一行、
//   再把那几个数重读一遍 —— 于是"你打一句"之后，顶栏那几个数会跟着动。
const events = new EventSource('/events');
// Resnapshot after subscription opens: events between the initial HTTP read
// and SSE subscription are recovered without guessing from matching text.

events.onmessage = (e: MessageEvent<string>) => {
  try {
    const frame = JSON.parse(e.data);
    if (frame.kind === 'control_state') {
      window.dispatchEvent(new CustomEvent('core-console-state', { detail: frame }));
      void refresh();
      return;
    }
  } catch { /* Ordinary frame parsing remains with the chat view. */ }
  if (chat !== null) chat.append(e.data);
  window.dispatchEvent(new Event('core-records-changed'));
  void loadUserLabel().then(applyNames); // 帧只从这一处进（谁在说话由它自己认）
  void refresh();
};
events.onopen = () => {
  chat?.refreshHistory?.();
  window.dispatchEvent(new Event('core-records-changed'));
  void loadAvatar();
  void Promise.all([loadSpeaker(true), loadUserLabel(true)]).then(applyNames);
  coreSetUIText(conn, 'textContent', corePhrase("已连接"));
  void refresh();
};
events.onerror = () => {
  conn.dataset.on = 'false';
  coreSetUIText(conn, 'textContent', corePhrase("断开"));
  // 本波补的那一下：**通道断了就重读一遍那几个数**（不是只改一个词）。
  //    ⚠️ 理由是"暂停"那一格**只能**在这一刻翻过来：`paused` 为真只有两条路
  //    （输入到头 ／ 收工信号立起），而那两条都发生在**收摊**那一刻 —— 收摊会把
  //    这条推的通道关掉 ⇒ 页面能感觉到的**唯一**信号就是这里（`EventSource.onerror`；
  //    它之后会自带重连）。不重读的话，那一格会永远停在"运行中"。
  // 而服务真没了的时候，这一句会走 `refresh()` 的 `catch` ⇒ 那一格写「读不到」。
  void refresh();
};

onEngineChanged(() => { void refresh(); });
window.addEventListener('core-control-refresh', () => { void refresh(); });
window.addEventListener('hashchange', switchPage);

switchPage();
// 模型若在无人发消息时退出，SSE 不会触发刷新；定时探活避免留下旧的“在线”。
if (typeof window.setInterval === 'function') {
  window.setInterval(() => { void refresh(); }, 15_000);
}
