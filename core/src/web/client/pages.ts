// **那张页表 ＋ 那个路由器** —— 本刀的承重件。
//
// ⚠️⚠️ **加一页要改的地方，只有两处，而它们都在这个文件里**：
//    ① 头上那一行 `import`（照下面那两行的样子抄一行）；
//    ② `PAGES` 那一列里加**一项**（就是 `page({ … })` 那样**一行**，里面连 `file:` 都有）。
//    ⇒ 加一页 = **加一个文件**（它自己那一页）**＋ 一行**（这里那一项）。
//    实证：`tests/test_web.py` 那一条判的正是这件事 —— 每一项恰好一行、`file:` 点名的那一份
//    真的存在（`file:` 就是那个文件的名字）、`index.html` 里没有任何页标（没有第二个登记处）。
//    第 3 页要花的就是这一段：建 `world-xxx.ts`，在这儿抄一行、把 `file:` 指向它。
// ⚠️ **`group` 那个名字是给读者看的**（左栏那一段组标就写它），页表里**连着写的**同属一组 ⇒
//    落在左栏同一组里；`shell.ts` 只认**位次**（`place(i, …)`）⇒ 加一页不必动它。
// ⚠️ **路由器读 `location.hash`**（`#/live` ／ `#/engine`）：刷新之后回到**那一页**，
// 而不是每次都掉回第一页。 换页那一下要**先把上一页停下来**（`stop()`）——
//    否则两页会同时挂在同一批通道上（各自推各自的帧，而屏幕上只看得到一页）。
// ⚠️ **零框架**：没有 React ／ 没有路由库 ／ 没有组件树，一个数组 ＋ 一个函数就是全部。

import { chatPage } from './live';
import { enginePage } from './engine-page';
import { personaPage } from './persona-page';
import { memoryPage } from './memory-page';
import { pluginWorkspace } from './plugin-workspace';
import { pluginMarketPage } from './plugin-market-page';
import { infinityPluginPage } from './infinity-plugin-page';
// 2026-10-08 作者拍的「 ⇒ 三个子项」： **三个新文件 ＋ 三行**
// （ 那就是本仓"加一页"的全部代价 —— 见头上那两处 ）。
import { knowledgePage } from './knowledge-page';
import { peoplePage } from './people-page';
import { experiencePage } from './experience-page';
// 2026-10-10（外观那一页 重做）：设置那一页
// （ 它原本是一个浮层 → 现在是一页 ）。
import { settingsPage } from './settings-page';
import type { Chrome, Panel } from './shell';
import type { SoonItem } from './shell';

/** 左栏那一栏里的一项（ 由 `shell.ts` 画出来，而它自己不知道谁是谁）。 */
export interface NavItem {
  readonly id: string;
  readonly label: string;
  /** 一个线型图标的名字（`shell.ts::ICONS` 里那一个）；不填 ⇒ 那一项没有图标。 */
  readonly icon?: string;
  /** 右边那个绿点（设计稿 §三：缩进、无图标、右边一个绿点的那一项）。 */
  readonly dot?: boolean;
  /** 二级导航项：放进同组的子列表，而不是与总览、市场并列。 */
  readonly child?: boolean;
  /** 插件子项的实测状态灯；未读到状态时不猜。 */
  readonly signals?: ReadonlyArray<{
    readonly state: 'on' | 'off' | 'warn'; readonly label: string
  }>;
}

/** 一页 —— **加一页就是这一条**（见头上那两处）。 */
export interface Page extends NavItem {
  /** 它落在哪一组（组标就是这一串，页表里连着写的同属一组）。 */
  readonly group: string;
  /** 它那一份源的文件名（`src/web/client/<file>`）—— 它**就是那个文件的名字**。 */
  readonly file: string;
  /** 把这一页建出来（正文那一列是它的落点）—— 交回一个挂／亮／停的把手。 */
  readonly build: (shell: Chrome) => Panel;
  /** **不上左栏** （ 它由别处进去 —— 比如底下那颗 ⚙ ）。
   *
   * ⚠️ **为什么需要这一格** ： 这仓里「一页」与「左栏一个入口」
   * **本是同一件事** （`Page extends NavItem` ）—— 而底下那颗 ⚙
   * **已经**是一个入口了 ⇒ 再来一个就是**两个登记处**
   * （ 与页表头上那条规矩顶牛 ）。
   * ⚠️ **为什么还要留在页表里** ： 页表是**唯一的登记处** ——
   * 拿掉这一条，`#/settings` 就认不出、 刷新也回不来 。
   */
  readonly offRail?: boolean;
}

/** 一行**就是**一页：`row` 是那一页（含它那一份源），`build` 是把它建出来的那一个。 */
function page(row: Omit<Page, 'build'>, build: (shell: Chrome) => Panel): Page {
  return { ...row, build };
}

/** 一行**就是**一个"还没做"的入口（与 `page()` 同一个形状 —— 少了 `file` ／ `build`）。 */
function soon(row: Omit<SoonItem, 'why'> & { why: string }): SoonItem {
  return row;
}

/** **页表**：今天挂三页（聊天 ＋ 引擎 ＋ 人设）—— 其余几页是加法（设计稿 §五）。 */
export const PAGES: readonly Page[] = [
  page({ group: 'CORE', id: 'live', label: '控制台', icon: 'terminal',
         file: 'live.ts' }, chatPage),
  // **刀 4：那一项落 `CORE` 并改名「模型」**（设计稿 裁定 5 ／ 10 ／ 11）：
  //    · `group`：`WORLD` → `CORE` ⇒ ⚠️ **`WORLD` 那一组随之消失**（`groupsOf()` 按首次出现
  //      收组名 ⇒ 没有一项落在它里面，组标自己就不出现，`shell.ts` **一行不改**）；
  //    · `label`：`引擎` → `模型`（它现在担两件事：看 ＋ 选）；
  // · **`id` 保留 `engine`**：`pageAt()` 对认不出的路由**掉回第一页**
  //      ⇒ 改 id 会让 `#/engine` 老书签**静默跳到别处**；
  //    · 绿点（`dot: true`）留着 —— 它是"当前那一条"的指示灯，与新语义正好配。
  // **图标：`layers` → `plug`**（2026-10-03 作者点头；设计稿 §十二第 4 条）——
  //      `layers` 在参考那边是「World 总览」的图标，而这一项改叫「模型」之后，
  //      `ICONS` 里那个**空着没人用**的 `plug`（插头）才贴它；改完 `layers` 空出来，
  // 留给将来那类"总览"的页。 判据钉着"这个名字在 `ICONS` 里"（写错只会是一条空路径）。
  page({ group: 'CORE', id: 'engine', label: '模型', icon: 'chip', dot: true,
         file: 'engine-page.ts' }, enginePage),
  // 2026-10-10（外观那一页 重做）：**设置**（ 左下角那一颗 ⚙ 跳到它 ）。
page({ group: 'CORE', id: 'settings', label: '设置', icon: 'plug',
       offRail: true, file: 'settings-page.ts' }, settingsPage),
page({ group: 'PERSONA & MEMORY', id: 'persona', label: '人设', icon: 'person',
         file: 'persona-page.ts' }, personaPage),
  // **刀 1：`INFINITY` 那一组终于有真的一页了**（作者 2026-10-05："先做 1"）
  //    —— 就是左栏那个「插件」入口（从前它在 `SOON` 里、点不动）。
  //    ⚠️ **它必须写在最后**：`groupsOf()` 按**首次出现**排 ⇒ 一个"既在 `PAGES`
  //      又在 `SOON`"的组会**跳到 `PAGES` 那一边的位置**上去；写在末尾 ⇒
  // `INFINITY` 照旧是**最后一组** （这是 `groupsOf()` 那一段自己点名的隐式依赖 ）。
  // 而 `SOON` 里那一项**同时删掉**：同一个入口不许有两个登记处
  // （"加一页 = 一个文件 ＋ 一行" ／ 而"还没做"那条清单是另一回事 ）。
  // **「记忆」那一页**（2026-10-05 作者："给记忆页开个头"）—— 它原先在 `SOON` 里
  //    （点不动），而**服务那一侧早就有了**（`web.py` 的 `/memory` 路由＋入口递的
  // `memory.everyone(home)` ）⇒ 这一刀把它接上：**一个文件 ＋ 一行** 。
  page({ group: 'PERSONA & MEMORY', id: 'memory', label: '记忆', icon: 'memory',
         file: 'memory-page.ts' }, memoryPage),
  // **「」那一组**（2026-10-08，作者拍的「①THE WORLD」）——
  // · 组名 `THE WORLD`（ 与 `CORE` ／ `PERSONA & MEMORY` 同一套大写 ）；
  // · **三个子项**： 知识库 ／ 画像 ／ 经历 —— 就是记忆页那条尾那三样 ；
  // · ⚠️ **它写在 `INFINITY` 之前**：`groupsOf()` 按**首次出现**排 ⇒
  // 那一组照旧落在**最后一组** （ 那条位次判据钉着它 —— 我先写在最后，
  // 判据当场红了 ⇒ **是它把我改对的** ）。
  // ⚠️ 那三条口**后端早就在**（`/knowledge` ／ `/people` ／ `/experience` ）⇒
  // 这里只补前端 （ 而三页都**只读** —— 与记忆那一页同一条规矩 ）。
  // ⚠️ 图标名**只许用 `shell.ts::ICONS` 里有的那几个**
  //      （可用的就这些：`terminal` ／ `pulse` ／ `chart` ／ `plug` ／ `puzzle` ／
  // `person` ／ `memory` ／ `note` ／ `layers` —— `book` ／ `messages` 都不在里面 ，
  // 而那条判据**两次**都把我拦下了 ）。
  page({ group: 'THE WORLD', id: 'knowledge', label: '知识库', icon: 'book',
         file: 'knowledge-page.ts' }, knowledgePage),
  page({ group: 'THE WORLD', id: 'people', label: '画像', icon: 'contacts',
         file: 'people-page.ts' }, peoplePage),
  page({ group: 'THE WORLD', id: 'experience', label: '经历', icon: 'history',
         file: 'experience-page.ts' }, experiencePage),
  // **刀 1：`INFINITY` 那一组终于有真的一页了**（作者 2026-10-05："先做 1"）
  //    —— 就是左栏那个「插件」入口（从前它在 `SOON` 里、点不动）。
  //    ⚠️ **它必须写在最后**：`groupsOf()` 按**首次出现**排 ⇒ 一个"既在 `PAGES`
  //      又在 `SOON`"的组会**跳到 `PAGES` 那一边的位置**上去；写在末尾 ⇒
  // `INFINITY` 照旧是**最后一组** （这是 `groupsOf()` 那一段自己点名的隐式依赖 ）。
  // 而 `SOON` 里那一项**同时删掉**：同一个入口不许有两个登记处
  // （"加一页 = 一个文件 ＋ 一行" ／ 而"还没做"那条清单是另一回事 ）。
  page({ group: 'INFINITY', id: 'plugin', label: '插件市场', icon: 'grid',
         file: 'plugin-market-page.ts' }, pluginMarketPage),
  page({ group: 'INFINITY', id: 'infinity', label: '插件总览', icon: 'puzzle',
         file: 'plugin-workspace.ts' }, pluginWorkspace),
  page({ group: 'INFINITY', id: 'infinity-entry', label: '插件配置', icon: 'puzzle',
         offRail: true, file: 'infinity-plugin-page.ts' }, infinityPluginPage),
];

/** **"还没做"的那几项**（本刀加的）—— **它们不是 `PAGES` 里的东西**。
 *
 *  ⚠️⚠️ **为什么另立一条清单、而不是往 `PAGES` 里加一项**：
 *     `PAGES` 是**登记处**（登记了就是能挂上去的一页），而
 *     `tests/test_web.py::test_the_page_table_is_the_only_place_a_page_is_registered`
 * 判的正是"加一页 = 一个文件 ＋ 一行"。 把"还没做的入口"塞进那张表，
 *     那条判据就会**去要一个不存在的文件**（登记处混进了没做的东西）。
 *     ⇒ 另立一条：它**只画在左栏**，**点不动**（见 `shell.ts::SoonItem`）。
 * **依据**：作者对记忆那一页说的"入口要做、页面先不做"—— 而文档里那两处也说得清：
 *     `02:22` 记忆的实现**归插件**；`06:111` 那几条工具（`memory_search` ／
 *     `profile_read` ／ `relations_read`）**今天全是"声明了没实现"**。
 *  ⚠️ **它落在哪一组**：与 `persona` 同一组（`PERSONA & MEMORY`）—— 那一组今天已经在了
 * （`PAGES` 里那一条就是它）⇒ 加这一项**不必动 `PAGES`**、组标也不会多一个。 */
export const SOON: readonly SoonItem[] = [
  // INFINITY 的插件页已在 PAGES 中注册；SOON 仅用于不可点击的预留入口。人格、记忆和知识库属于核心功能。
];

/** 组标那一段（按**第一次出现**的位次排 —— 于是左栏那一列与页表同序）。
 *
 * ⚠️ **刀 0 起它也从 `SOON` 收组名**（从前只从 `PAGES` 收）：一个"只有 `SOON`、
 *     没有 `PAGES` 项"的组，从前**连组标都不出现** —— `groupsOf()` 交不出那个名字
 *     ⇒ `shell.place()` 从没被调到 ⇒ 那一整组看不见。
 * **位次的唯一依据就是这一句**：先 `pages` 后 `soon`、各自按首次出现 ⇒
 *     于是 `INFINITY`（只在 `SOON` 里、写在最后）今天落在**最后一组**。
 *  ⚠️ **依赖（写在这里，因为它是隐式的）**：以后谁在 `PAGES` 里加一页 `INFINITY`，
 *     那个组会**跳到那一页的位置**上去 —— 判据钉着"今天的位次 ＝ 最后一组"，
 *     那时它会红，而那一红就是提醒"位次变了，说清为什么"。
 */
export function groupsOf(pages: readonly Page[] = PAGES,
                         soon: readonly SoonItem[] = SOON): string[] {
  const seen: string[] = [];
  for (const one of [...pages, ...soon]) if (!seen.includes(one.group)) seen.push(one.group);
  return seen;
}

/** 一组里那几项（ 页表里那一行就是它的全部 —— 那两处落点不在导航项里）。 */
export function navItems(group: string, pages: readonly Page[] = PAGES): NavItem[] {
  return pages
    .filter((one) => one.group === group)
    // **`offRail` 的那些不进左栏** （ 它们由别处进去 —— 比如那颗 ⚙ ）。
    .filter((one) => one.offRail !== true)
    .map(({ id, label, icon, dot }) => ({ id, label, icon, dot }));
}

/** **一组里"还没做"的那几项**（本刀加的）—— 与 `navItems()` **同一个形状**。
 *
 *  ⚠️ **它只挑这一组的**（`id` ／ `label` ／ `icon` 照旧从表里读）⇒
 *     "记忆"那一项落在哪一组是 `SOON` 里写的那一格，而壳照旧**只认位次**。 */
export function soonItems(group: string, soon: readonly SoonItem[] = SOON): SoonItem[] {
  return soon.filter((one) => one.group === group);
}

/** 那一页（按 `#/…` 里的名字找）；找不到（或者空）⇒ 第一页。
 *  ⚠️ 那个 `#` 后面**斜杠可有可无**：手打的 `#engine` 与点出来的 `#/engine` 是同一页
 *  （实测：导航项自己那一行写的是 `#/engine`，而地址栏里那个 `#` 后面跟着的是它）。 */
export function pageAt(route: string, pages: readonly Page[] = PAGES): Page {
  const id = route.replace(/^#\/?/, '');
  if (id.startsWith('infinity/') && id.length > 'infinity/'.length) {
    return pages.find((one) => one.id === 'infinity-entry') ?? pages[0];
  }
  return pages.find((one) => one.id === id) ?? pages[0];
}

/** 当前那一页（地址上那个 `#/…` 的答案）。 */
export function currentPage(pages: readonly Page[] = PAGES): Page {
  return pageAt(location.hash, pages);
}

/** 一页挂进来时的把手（ 各页交回的 `view` 形状不同 ⇒ 那两处落点由入口去认）。 */
export type Handles = Panel & { view: unknown };

/** 换页那一下要停的那个人（上一页）—— 一页都没挂过的时候是 `null`。 */
let live: Handles | null = null;

/**
 * **那个路由器**：把地址上那一页亮出来，并把左栏那一项点起来。
 *
 * ⚠️ **三下都要做**：左栏高亮跟着换（`active`）、上一页 `stop()`、新那一页 `show()`
 *    —— 少中间那一下，上一页照旧挂在它那条通道上（两页各推各的帧，而只看得到一页）。
 * ⚠️ 交回**新那一页的把手** —— 入口拿它把推送过来的每一帧落到正文那处。
 */
export function route(shell: Chrome, pages: readonly Page[] = PAGES): Handles {
  const one = currentPage(pages);
  const selected = one.id === 'infinity-entry'
    ? location.hash.replace(/^#\/?/, '') : one.id;
  for (const el of shell.railNav.querySelectorAll<HTMLElement>('.rail-item')) {
    el.classList.toggle('active', el.dataset.page === selected);
  }
  if (live !== null) live.stop();
  live = one.build(shell) as Handles;
  live.show();
  return live;
}
