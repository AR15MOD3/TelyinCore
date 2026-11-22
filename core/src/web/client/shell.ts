import { uiLabel } from './ui-language';
import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { installRailResize } from './rail-resize';
import { coreBrandMark } from './brand';
import { notifyAction } from './confirm-dialog';
import { pageFetch } from './status-json';
// 「壳」—— **左栏 ＋ 顶栏（两层）＋ 正文那一列 ＋ 底部那一行**（设计稿 §三 那四块）。
//
// ⚠️⚠️ **这个文件不认识任何一页**：组标 ／ 页标 ／ 顺序 ／ 图标**全部从那张页表读**
//    （`pages.ts`）⇒ 加一页**不必动这里**（那是"加一页 = 一个文件 ＋ 一行"的一半）。
//    实证：本文件里一个 `'CORE'` ／ `'WORLD'` 那样的字面量都没有，也不引任何一页
//    —— `tests/test_web.py` 那一条判的正是这件事。
// ⚠️ **零框架**：全是 `document.createElement` ／ `textContent`（不引 React，也不引别的）。
//    那条通道上的话是从外面来的（本机上谁都能塞一行）⇒ 这一层**一律 `textContent`**。
// ⚠️ **线型图标是一套 `svg`**（同一套描边、同一支笔），不是表情符号 —— 见 `ICONS`。

// 2026-10-10：那几样共用的改从 `look` 取
// （ 断开 `shell` ⇄ `pages` ⇄ `settings-page` 那个环 ）。
import { loadLook } from './look';
import { cropAvatar } from './avatar-crop';
import { botDialog } from './bot-manager';
import type { NavItem } from './pages';
import { speaker, speakerReady } from './speaker';

/** **"还没做"的那几项**（本刀加的第三类导航项）—— 它们是**入口**，而不是死链。
 *
 *  ⚠️⚠️ **为什么值得一个类型**：左栏里一个**点得动而无处可去**的项是最坏的一种
 *     （点下去像坏了；而**一个空白页**又会被读成"这一页什么都没有"）。
 *     作者对记忆那一页说的正是这件事：**入口要做、页面先不做** ⇒
 * 于是它**照旧画在左栏那一列里**（看得见），而**它不是一个链接**（点不动），
 * 旁边一句"**还没做**"。 与这一刀那条抬头同一形状：**宁可说不做，不许假装做了**。
 *  ⚠️ **它不属于 `PAGES`**：那一张表是**登记处**（登记＝真的挂上去），
 *     而 `tests/test_web.py` 那条"加一页 = 一个文件 ＋ 一行"判的就是它。
 * ⇒ 这一列是**另一条清单**（`pages.ts::SOON`）， 于是那条判据一个字不用改。 */
export interface SoonItem extends NavItem {
  /** 它落在哪一组（与 `Page.group` 同一套组标 —— 壳只认**位次**）。 */
  readonly group: string;
  /** 为什么它今天不点 —— 一句话（它就是页上那一句诚实）。 */
  readonly why: string;
}

/** **§四 那六条词汇里的第五条：线型图标**（同一套描边 —— 见 `style.css` 的 `svg {`）。 */
const STROKE: Record<string, string> = {
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': '1.6',
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
};

/** **那一组图标**（一个名字一处路径）：终端 ／ 诊断 ／ 用量 ／ 供应商 ／ 扩展 ／ 人 ／ 记忆 ／
 * 提示词 ／ 世界。 一页在它那一行里点的就是这里的一个名字（页表的 `icon`）。 */
export const ICONS: Record<string, string> = {
  chat: 'M3 4h14v10H8l-4 3v-3H3zM6 8h8M6 11h5',
  eye: 'M2 10s3-5.5 8-5.5 8 5.5 8 5.5-3 5.5-8 5.5S2 10 2 10z'
    + 'M12.5 10a2.5 2.5 0 1 0-5 0 2.5 2.5 0 1 0 5 0',
  store: 'M3 8v9h14V8M2 7l2-4h12l2 4'
    + 'M2 7v1a2 2 0 0 0 4 0V7m0 0v1a2 2 0 0 0 4 0V7'
    + 'm0 0v1a2 2 0 0 0 4 0V7m0 0v1a2 2 0 0 0 4 0V7M8 17v-5h4v5',
  grid: 'M3 3h5v5H3zM12 3h5v5h-5zM3 12h5v5H3zM12 12h5v5h-5z',
  contacts: 'M4 3h12v14H4zM2 6h3M2 10h3M2 14h3'
    + 'M12 7a2 2 0 1 0-4 0 2 2 0 1 0 4 0M7 14c.2-4 5.8-4 6 0',
  book: 'M10 5v12M10 5C8 3 5 3 2 4v12c3-1 6-1 8 1 2-2 5-2 8-1V4c-3-1-6-1-8 1',
  history: 'M3 6a7 7 0 1 1-1 6M3 2v4h4M10 6v4l3 2',
  chip: 'M5 5h10v10H5zM8 8h4v4H8zM7 2v3M13 2v3M7 15v3M13 15v3M2 7h3M2 13h3M15 7h3M15 13h3',
  power: 'M10 2.5v6.8M5.2 5.2a6.8 6.8 0 1 0 9.6 0',
  wake: 'M6.5 3.8 16 10l-9.5 6.2z',
  wait: 'M6.8 4v12M13.2 4v12',
  settings: 'M8.3 2.5h3.4l.5 2 1.4.8 2-.6 1.7 2.9-1.5 1.4v1.8l1.5 1.4-1.7 2.9-2-.6-1.4.8-.5 2H8.3l-.5-2-1.4-.8-2 .6-1.7-2.9 1.5-1.4V9l-1.5-1.4 1.7-2.9 2 .6 1.4-.8zM12.5 10a2.5 2.5 0 1 0-5 0 2.5 2.5 0 1 0 5 0',
  terminal: 'M4 4.5l4.2 4.2-4.2 4.2M10.5 13.5h6.2',
  pulse: 'M2.6 10.5h3.6l2.2-6.4 3.2 12 2.1-5.6h4.3',
  chart: 'M3 16.4V9.2M8.5 16.4V4.4M14 16.4v-5.1M2 19.2h17',
  plug: 'M7.4 3v3.6M12.6 3v3.6M5.2 6.6h9.6v2.2a4.8 4.8 0 0 1-9.6 0zM10 13.6v3.6',
  puzzle: 'M4 4.4h4.6v2.2a1.8 1.8 0 1 0 3.6 0V4.4H17v4.4h-2.2a1.8 1.8 0 1 0 0 3.6H17v4.4H4z',
  person: 'M10 4.2a2.9 2.9 0 1 0 0 5.8 2.9 2.9 0 0 0 0-5.8zM4.4 16.4c.9-2.6 3-3.9 5.6-3.9'
    + 's4.7 1.3 5.6 3.9',
  memory: 'M6.2 3.2h7.6l2.4 2.4v11.2H6.2zM8.6 7h4.2M8.6 10.4h4.2M8.6 13.8h2.4',
  note: 'M5.2 3.2h6.4l3.2 3.2v10.4H5.2zM11.4 3.2v3.4h3.4M7.6 10.2h4.8M7.6 13.2h4.8',
  layers: 'M10 3.2l6.4 3.3-6.4 3.3-6.4-3.3zM3.6 10.2l6.4 3.3 6.4-3.3M3.6 13.4l6.4 3.3 6.4-3.3',
  search: 'M8.5 3.5a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 12l4.5 4.5',
  close: 'M5.5 5.5l9 9M14.5 5.5l-9 9',
};

/** 一个线型图标（尺寸走那一处词汇里的 `--icon`）。 */
export function icon(name: string): SVGSVGElement {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  for (const [key, value] of Object.entries(STROKE)) svg.setAttribute(key, value);
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[name] ?? '');
  svg.append(path);
  return svg;
}

/** **顶栏第一层那四枚的名字** —— 与 `state.ts::COUNTER_KEYS`（＝ `/state` 发的那四个）
 * 是同一份； 入口照这一张表把每一格写上去（"那几个数"与"它们叫什么"都在壳这一层）。 */
export const COUNTER_LABELS: Record<string, string> = {
  tokens: 'tok',
  messages: '条',
  batches: '累计批',
  paused: '状态',
};

/** **一个搜索框**（2026-10-09 作者按实物让重做的那一处 ）。
 *
 * ⚠️ **为什么它值得一个共用件**： 知识库 ／ 画像 ／ 经历**三页各要一个** ，
 * 而 本仓那条：**共用的一样只写一处** （ 与规格 §五 那句
 * 「尾那一行在三页上长得一样……别抄三遍」同一个道理 ）。
 *
 * ⚠️ **它换过两次，两次都是作者对着真页说的**（ 账留在这儿，别再走回去 ）：
 * · **第一版**是一条**光秃秃的下划线** ＋ **右对齐**的灰字 ⇒
 * 「**不够显眼**」 ／「**交互做的有点差**」；
 *     · **第二版**是一个**白底 ＋ 圆角**的框，前面加了一枚 `⌕`、后面加了自己那枚 `✕` ⇒
 * 「**现在这样的搜索栏不合风格**」 —— 因为本仓**早就有一个**搜索框：
 * `.plugins-find`（ 插件那一页那两个 ）⇒ 同一个壳里两个搜索框长得不一样 。
 * · **这一版**： **照 `.plugins-find` 那一套逐条抄** ——
 * 浅灰底（`--color-focus` ／ **不是白** ）／ 一圈 `--color-line` ／ `--radius-row` ／
 * `min-height: 34px` ／ `13px` ／ 占位走 `--color-faint` 。
 * 那一枚 `⌕` 与那枚自己画的 `✕` **都拿掉了** （ 家里那个框两样都没有 ）。
 * ⚠️ **占位那句话也照这一家的写法** ： 插件那一页写的是「**筛选：**插件名、描述、作者」
 * ⇒ 三页都改成「**筛选：**…」（ 作者 2026-10-09 逐页点名的三句 ）。
 * ⚠️ **它只交把手**： 这个函数**不认识任何一条口** （与壳那条同一条规矩 ）——
 * "敲完了去问谁"由调用方交给 `onAsk` 说 。
 * ⚠️ **它不属于 `PAGES` 那一张表** （那是**页**的登记处 ）；这里只是**壳这一层的一个件** 。
 */
export interface SearchBox {
  /** Complete search control, shared by the three WORLD pages. */
  readonly element: HTMLFormElement;
  /** The input remains available for reading the current query and focusing it. */
  readonly input: HTMLInputElement;
  /** Submit, Enter, clear, or leaving a changed field invokes the page's filter. */
  readonly onAsk: (what: (q: string) => void) => void;
}

/** 造出那么一个框（ 用法：`const s = searchBox('筛选：文件名')` ）。 */
export function searchBox(placeholder: string): SearchBox {
  const form = document.createElement('form');
  form.className = 'search-box';
  form.setAttribute('role', 'search');
  form.setAttribute('aria-label', placeholder);
  const field = document.createElement('div');
  field.className = 'search-field';
  const input = document.createElement('input');
  input.type = 'search';
  input.name = 'q';
  input.className = 'find';
  input.placeholder = placeholder;
  input.setAttribute('aria-label', placeholder);
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'search-clear';
  clear.hidden = true;
  coreSetUIText(clear, 'title', corePhrase("清空筛选"));
  coreSetUIAttribute(clear, 'aria-label', corePhrase("清空筛选"));
  clear.append(icon('close'));
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'search-submit';
  coreSetUIText(submit, 'textContent', corePhrase("筛选"));
  field.append(icon('search'), input, clear);
  form.append(field, submit);
  let ask: (q: string) => void = () => { /* 调用方还没接上 ⇒ 什么都不做 */ };
  let lastAsked = '';
  const go = (): void => {
    lastAsked = input.value.trim();
    ask(lastAsked);
  };
  input.addEventListener('input', () => { clear.hidden = input.value === ''; });
  // Preserve filtering on leaving the field, without submitting twice when a button is clicked.
  input.addEventListener('blur', (ev) => {
    if (ev.relatedTarget instanceof Node && form.contains(ev.relatedTarget)) return;
    if (input.value.trim() !== lastAsked) go();
  });
  form.addEventListener('submit', (ev) => { ev.preventDefault(); go(); });
  clear.addEventListener('click', () => {
    input.value = '';
    clear.hidden = true;
    go();
    input.focus();
  });
  return { element: form, input, onAsk: (what) => { ask = what; } };
}

/** 那一行先画出来（每格一个 `—`）—— 还没读到 `/state` 的时候，它照样是一条顶栏。 */
const EMPTY_CHIPS = ['tokens', 'messages', 'batches', 'paused'] as const;

/** 分组那一段：按页表里**第一次出现**的位次摆（ 壳只认位次，不认组名）。 */
export function groupSlices(groups: readonly string[]): Array<[number, string]> {
  return groups.map((name, index) => [index, name]);
}

/** 一页交回的把手：挂 ／ 亮 ／ 停（`stop()` 让换页之后上一页真的停下来）。 */
export interface Panel {
  readonly root: HTMLElement;
  show(): void;
  stop(): void;
}

/** 顶栏那两层要的几格（`shell.ts` 建，入口把 `/state` 读到的值填进去）。 */
export interface TopbarView {
  readonly chips: HTMLElement;
  readonly right: HTMLElement;
  readonly model: HTMLElement;
  readonly provider: HTMLElement;
  /** 第二层那一格（"缓存 —" 那一处）：它报的是这台机器上过了多少 token 的上下文。 */
  /** 第二层那个绿点：它的 `title` 写"这台机器在不在转"（与暂停那一格同一个答案）。 */
  readonly status: HTMLElement;
}

/** 壳那四块的把手 —— 页与入口只从这儿拿它们要的那一格。 */
export interface Chrome {
  readonly root: HTMLElement;
  readonly rail: HTMLElement;
  readonly railNav: HTMLElement;
  readonly railFoot: HTMLElement;
  readonly topbar: HTMLElement;
  readonly topbarSummary: HTMLElement;
  readonly topbarStatus: HTMLElement;
  readonly content: HTMLElement;
  readonly bottom: HTMLElement;
  /** 顶栏那两层。 */
  readonly bar: TopbarView;
  /** 把那四个数写上去（`[key, 值]` 一串 —— 读不到时也照写，值是 `—`）。 */
  counters(pairs: ReadonlyArray<readonly [string, string]>): void;
  /** 第 **i** 组的落点（页表那一列里第 i 个组 —— 壳只认**位次**）。 */
  place(index: number, label: string, items: readonly NavItem[]): void;
  /** **那一格名字的落点** —— 名字来自她自己的档案（`speaker.ts`）：
   * 入口读好了递进来，壳只负责把它写到那三处（ 壳自己不 `fetch`）。 */
  setSpeaker(name: string): void;
}

/** 一页挂进来时交回的两样：它自己那块，外加"往哪儿落行"的地方。 */
export type PageHandles = Panel & { view: unknown };

function node(tag: string, cls?: string, text?: string | ReturnType<typeof corePhrase>): HTMLElement {
  const el = document.createElement(tag);
  if (cls !== undefined) el.className = cls;
  if (text !== undefined) { if (typeof text === 'string') el.textContent = text; else coreSetUIText(el, 'textContent', text); }
  return el;
}

/** 左栏：一个组标 ＋ 它那几项（ 两样都是从页表读来的）—— 末了还有"还没做"的那几项。 */
function groupEl(index: number, label: string, items: readonly NavItem[],
                 soon: readonly SoonItem[] = []): HTMLElement {
  const box = node('div', 'rail-group');
  box.dataset.index = String(index);
  const groupLabel = node('div', 'rail-grouplabel'); uiLabel(groupLabel, label); box.append(groupLabel);
  let sublist: HTMLElement | null = null;
  for (const item of items) {
    const link = document.createElement('a');
    link.className = item.child ? 'rail-item rail-child' : 'rail-item';
    link.href = `#/${item.id}`;
    link.title = item.label;
    link.setAttribute('aria-label', item.label);
    link.dataset.page = item.id;
    if (item.icon !== undefined) link.append(icon(item.icon));
    const navLabel = node('span', 'rail-label', item.label);
    if (!item.id.startsWith('infinity/')) { uiLabel(navLabel, item.label); uiLabel(link, item.label, 'title'); coreSetUIAttribute(link, 'aria-label', corePhrase(item.label)); }
    link.append(navLabel);
    if (item.dot === true) link.append(node('span', 'rail-dot'));
    if (item.signals?.length) {
      const indicators = node('span', 'rail-signals');
      const description = item.signals.map((one) => one.label).join('；');
      indicators.setAttribute('aria-label', description);
      link.title = `${item.label} · ${description}`;
      link.setAttribute('aria-label', `${item.label}；${description}`);
      const summary = node('span', 'rail-signal rail-signal-summary');
      summary.dataset.state = item.signals.some(one => one.state === 'warn') ? 'warn'
        : item.signals.some(one => one.state === 'on') ? 'on' : 'off';
      summary.setAttribute('aria-hidden', 'true');
      link.append(summary);
      for (const signal of item.signals) {
        const dot = node('span', 'rail-signal');
        dot.dataset.state = signal.state;
        dot.title = signal.label;
        indicators.append(dot);
      }
      link.append(indicators);
    }
    if (item.child) {
      if (sublist === null) {
        sublist = node('div', 'rail-sublist');
        sublist.setAttribute('role', 'group');
        coreSetUIAttribute(sublist, 'aria-label', [corePhrase(""), label, corePhrase(" 子项")]);
        box.append(sublist);
      }
      sublist.append(link);
    } else {
      sublist = null;
      box.append(link);
    }
  }
  // **"还没做"的那几项**（本刀）：**画在这一组里**，而它**不是一个链接** ——
  // `data-soon` 那一格是给它自己的（样式 ／ 判据都读它）， 而 `href` **一个都没有**
  //    ⇒ 点它不会去任何地方（那正是"不是死链"这件事的做法：**没有可点的那一层**）。
  for (const one of soon) {
    const row = node('div', 'rail-item rail-soon');
    row.dataset.soon = one.id;
    if (one.icon !== undefined) row.append(icon(one.icon));
    row.append(node('span', 'rail-label', one.label));
    row.append(node('span', 'rail-soontag', corePhrase('还没做')));
    row.title = one.why;
    box.append(row);
  }
  return box;
}

/** **左下角那三个小图标**（`▶ ⚙ ⏻`）—— 一处三行：改这里就是改那一整块。
 *  ⚠️ 三个今天都**没有下家**（那一格没有做法）⇒ 各自的 `title` 说清它今天不做什么：
 *  设计稿 §三 说"那儿有三个小图标"，而**借形状**不等于**假装它们能用**。 */
/** **2026-10-10（外观那一页 ②）：预设 ＋ 那个面板** 。
 *
 * ⚠️ **作者那句纠正** ：「**这个项目会开源 …… 其他人也要可以使用这些预设
 * 的同时也可以自己自定义**」 ⇒ **两层** ： **预设**（ 随仓库 ，
 * `presets/*.json` ）＋ **自定义**（ 留在他那一台 —— 下一刀 ）。
 *
 * ⚠️ **面板里**只列这几个** —— 不是"漏了"， 是**范围** ：
 * `@theme` 里那 50 个变量分两半 （ 外观 37 ／ 骨架 13 ）。
 * 骨架那 13 个（`--persona-*` ／ `--rail-w` ／ `--content-width` …）
 * **相互 `calc`** ⇒ 改一个**版式会塌** —— 那不是外观、 是骨架 。
 * ⚠️ 判据两头都钉着（`test_the_preset_holds_the_look_and_not_the_skeleton` ）。
 *
 * ⚠️ **读不到 ⇒ 一个字都不动** （ 表里那些值照旧 ）—— 与 `/avatar`
 * 404 那条**同一条口径** ： 没有就保持原样， **不编** 。
 */

/** 现在这一套外观（ 预设读回来的 —— 空 ⇒ 表里那些照旧 ）。 */

/** 把手上这一套装进 `:root` —— 只碰这两样（ 变量 ＋ 那一页的根 ）。 */

/** **读预设**（ 页起来时一次 —— 读不到就照旧 ）。 */

/** **把手上这一套交回去** （ 写盘那一下归服务 。
 * ⚠ **写不成也不报错给用的人** ： 当场那一下**已经变了** ，
 * 而“存不住”是另一件事 （ 而它会在控制台说出来 ）。 */


/** **那一块面板**： 改一下 ⇒ 当场变 （ 存盘也接上了 ）。 */
/** **2026-10-10（外观那一页 ④）**：**下拉里挑一套就换上** 。
 * ⚠ 那几套从 `presets.json` **现读** （ 构建时生成 ）
 * —— 而不是写在这一页上 ： 加一份文件就多一套 。
 * ⚠ **读不到 ⇒ 不画那个下拉** （ 而不是画一个空的 ）。 */

/** **换上那一套** （ 换了就当场变 ＋ 存下来 ）。 */

/** **2026-10-10（三颗按钮 ⑤）：那一颗 ▶ —— 照实说出现在是什么状态** 。
 *
 * ⚠ **那一格与 `paused` 两回事** ： `paused` 讲的是「不再收新的了」
 * （ 她那一侧的事 ）； `stopped_by_hand` 讲的是**作者按下的那个暂停** 。
 * ⚠ **丢掉的那个数照实报** （ 关闸期间进来的没发出去 —— 不许瞒 ）。
 */
function gateIcon(button: HTMLElement): void {
  if (button.querySelector('.gate-icons')) return;
  const group = node('span', 'gate-icons');
  for (const name of ['wake', 'wait']) {
    const layer = node('span', 'gate-' + name);
    layer.append(icon(name));
    group.append(layer);
  }
  button.replaceChildren(group);
}

function gateBusy(button: HTMLElement): void {
  const busy = button.dataset.busy === 'true' || button.dataset.starting === 'yes';
  button.classList.toggle('busy', busy);
  button.setAttribute('aria-busy', String(busy));
  button.setAttribute('aria-disabled', String(busy));
}

const gateRevisions = new WeakMap<HTMLElement, number>();

function paintTheGate(button: HTMLElement, machine: Record<string, unknown>): boolean {
    if (typeof machine.stopped_by_hand !== 'boolean') return false;
    button.dataset.stateKnown = 'yes';
    // ⚠️ **它们住在 `machine` 里** —— 而我第一版读的是**顶层**
    // ⇒ 恒为 `undefined` ⇒ 那颗键永远翻不动
    // （ 真页上量出来的 —— 而判据只钉"名字出现过" 。）
    const stopped = machine.stopped_by_hand === true;
    const dropped = Number(machine.dropped ?? 0);
    // 管的是哪一路 （ 照计划 §1.2 第 2 条：跟着那一格 `profile` 走 ）。
    const local = String(machine.profile ?? '') === 'Local';
    button.dataset.stopped = stopped ? 'yes' : 'no';
    button.dataset.path = local ? 'b' : 'a';
    gateIcon(button);
    coreSetUIAttribute(button, 'aria-label', (stopped ? corePhrase("唤醒：恢复处理") : corePhrase("静候：暂停新请求")));
    // 第 ⑥ 步： 上一次那一下的**一句真话** —— 空的不写 。
    const note = String(machine.gate_note ?? '');
    // **第 ⑧ 步：那台正在起** —— 这一下不挂住请求
    // （ 第 ⑦ 步量到：装模型要 20 秒到 3 分钟以上 ）
    // ⇒ 那颗键**真的转起来** 、 而它**自己**看着那一格 。
    if (machine.starting === true) {
      button.dataset.starting = 'yes';
      gateBusy(button);
      coreSetUIText(button, 'title', corePhrase("正在唤醒：本地服务就绪后恢复处理。"));
      if (button.dataset.polling !== 'yes') {
        button.dataset.polling = 'yes';
        window.setTimeout(() => {
          button.dataset.polling = 'no';
          if (button.isConnected) void loadTheGate(button);
        }, 2000);
      }
      return true;
    }
    button.dataset.starting = 'no';
    button.dataset.polling = 'no';
    gateBusy(button);
    coreSetUIText(button, 'title', (stopped ? [[corePhrase("唤醒：恢复处理。静候期间未发送 "), String(dropped)], corePhrase(" 条。")] : (machine.model_available === false ? corePhrase("静候：暂停新请求。当前本地模型未响应。") : corePhrase("静候：暂停新请求；已开始的处理不受影响。"))));
    if (note !== '') button.title = button.title + ' ｜ ' + note;
    return true;
}

/** Share the authoritative /state snapshot with the header, without another poll. */
export function syncGateState(root: HTMLElement, machine: unknown): void {
  const button = root.querySelector<HTMLElement>('.rail-run');
  if (!button || button.dataset.busy === 'true' || !machine || typeof machine !== 'object') return;
  gateRevisions.set(button, (gateRevisions.get(button) ?? 0) + 1);
  paintTheGate(button, machine as Record<string, unknown>);
}

async function loadTheGate(button: HTMLElement): Promise<boolean> {
  const revision = (gateRevisions.get(button) ?? 0) + 1;
  gateRevisions.set(button, revision);
  try {
    const got = await pageFetch('/state');
    if (!got.ok) return false;
    const box = await got.json() as Record<string, unknown>;
    if (gateRevisions.get(button) !== revision) return button.dataset.stateKnown === 'yes';
    return paintTheGate(button, (box.machine ?? {}) as Record<string, unknown>);
  } catch (why) {
    console.error('控制状态读取失败：', why);
    return false;
  }
}

/** **按下去** —— 第三种样子（ 正在起停 ： 转一下、点不动 ）。
 *
 * ⚠ **不许乐观地翻** ： 那一下发出去之后**重读一遍**
 * —— 只有服务那一头说它真的停了， 页上才翻
 * （ 否则就是一个“按了就当真”的假状态 ）。
 * ⚠ **没收下 ⇒ 照实说** （ 不说就是骗自己 ）。
 */
async function pressTheGate(button: HTMLElement): Promise<void> {
  if (button.dataset.stateKnown !== 'yes' || button.dataset.busy === 'true' || button.dataset.starting === 'yes') return;
  gateRevisions.set(button, (gateRevisions.get(button) ?? 0) + 1);
  const stopped = button.dataset.stopped === 'yes';
  const want = stopped ? '/run' : '/pause';
  button.dataset.busy = 'true';
  gateBusy(button);
  coreSetUIText(button, 'title', (stopped ? corePhrase("正在唤醒……") : corePhrase("正在进入静候……")));
  try {
    const got = await pageFetch(want, { method: 'POST' });
    if (!got.ok) {
      window.dispatchEvent(new CustomEvent('core-control-feedback', { detail: { text: '操作失败，请重试。' } }));
    } else {
      const confirmed = await loadTheGate(button);
      const text = !confirmed ? '状态待确认，请检查连接'
        : button.dataset.starting === 'yes' ? '正在唤醒…'
          : button.dataset.stopped === 'yes' ? '已进入静候' : '已唤醒';
      window.dispatchEvent(new CustomEvent('core-control-feedback', { detail: { text } }));
      window.dispatchEvent(new Event('core-control-refresh'));
    }
  } catch (why) {
    window.dispatchEvent(new CustomEvent('core-control-feedback', { detail: { text: '操作状态未确认，请检查连接。' } }));
  } finally {
    button.dataset.busy = 'false';
    gateBusy(button);
  }
}

const RAIL_ACTIONS = [
  { key: 'run', glyph: '▶', title: '唤醒 / 静候：恢复处理或暂停新请求' },
  { key: 'settings', glyph: '⚙', title: '设置 —— 主题 ／ 颜色 ／ 预设' },
  { key: 'shutdown', glyph: '⏻',
    title: '休止：结束当前服务' },
] as const;

/** **2026-10-10（三颗按钮 ④）：那一颗 ⏻ —— 休止** 。
 *
 * ⚠️ **先说清一件事** ： core 一停，**这一页背后就没有服务了** ——
 * 再想用它只能**回命令行起** （ 免得哪天按完忘了怎么回来 ）。
 * ⚠️ **"关这个页面"退一步** ： `window.close()` **只关得掉脚本自己
 * 开出来的窗口** ⇒ 能关就关、 关不掉就**照实说** （ 不假装 ）。
 * ⚠️ **真假看那一格** ： 服务真停了 ⇒ 那条推的通道**会断**
 * ⇒ 顶栏那一格翻成「断开」 —— 那**才是**证据 ， 不是我这一句 。
 */
async function shutItDown(button: HTMLElement): Promise<void> {
  if (button.dataset.busy === 'true') return;
  button.dataset.busy = 'true';
  coreSetUIText(button, 'title', corePhrase("正在休止……"));
  try {
    const got = await pageFetch('/shutdown', { method: 'POST' });
    if (!got.ok) {
      button.dataset.busy = 'false';
      coreSetUIText(button, 'title', [[corePhrase("休止未完成（HTTP "), got.status], corePhrase("）")]);
      return;
    }
    button.classList.add('done');
    coreSetUIText(button, 'title', (got.status === 202 ? corePhrase("休止请求已接受，正在等待收尾") : corePhrase("服务已休止")));
    window.close();
  } catch (why) {
    button.dataset.busy = 'false';
    coreSetUIText(button, 'title', [corePhrase("休止未完成："), String(why)]);
    console.error('休止未完成：', why);
  }
}


/** **头像那一件**（2026-10-09 作者点的 ）—— 四处都在这一块里：**
 * ① 点那个圆球 ⇒ 开文件选择（只收图片 ）；
 * ② **在浏览器里缩到 256×256**（`canvas` ＋ 居中裁切 —— 缩图不归服务 ）；
 * ③ `POST /avatar`（`Content-Type: image/png` —— 服务那边只收 PNG ）；
 * ④ 传成功 ⇒ **把页上所有圆球一起换** （侧栏那一枚 ／ 聊天区每一枚 ）。
 *
 * ⚠️ **没上传过 ⇒ 一个字都不动** —— 圆球照旧显示名字首字「特」
 * （`GET /avatar` 404 ⇒ 保持原样 ，与 `/usage` 同一条口径 ）。
 * ⚠️ **换图只改样式，不改 DOM** —— 那几枚里那个首字**留着**
 * （`.has-pic` 把它 `color: transparent` ）⇒ 图没了还能退回首字 。
 */
const AVATAR_SIDE = 256;

let avatarUrl: string | null = null;

/** 给 `chat.ts` 用： 现在那张图的地址（没有 ⇒ `null` ）。 */
export function theAvatarUrl(): string | null {
  return avatarUrl;
}

/** 页上所有圆球（侧栏那一枚 ＋ 聊天区每一枚 ）。 */
function thePictures(): HTMLElement[] {
  return [...document.querySelectorAll('.rail-avatar, .msg-avatar')] as HTMLElement[];
}

/** **把当前那张图刷到每一枚圆球上**（没有图 ⇒ 全部退回首字 ）。 */
function paintAvatars(): void {
  for (const one of thePictures()) {
    if (avatarUrl === null) {
      one.classList.remove('has-pic');
      one.style.backgroundImage = '';
    } else {
      one.classList.add('has-pic');
      one.style.backgroundImage = `url(${avatarUrl})`;
    }
  }
}

/** **读一次盘上那张**（ 404 ⇒ 什么都不做 —— 那不是错 ）。 */
let avatarRead = 0;
export async function loadAvatar(): Promise<void> {
  const read = ++avatarRead;
  try {
    const answer = await fetch('/avatar', { cache: 'no-store' });
    if (read !== avatarRead) return;
    if (answer.status === 404) {
      if (avatarUrl !== null) URL.revokeObjectURL(avatarUrl);
      avatarUrl = null; paintAvatars(); return;
    }
    if (!answer.ok) return;
    const blob = await answer.blob();
    if (read !== avatarRead) return;
    if (avatarUrl !== null) { URL.revokeObjectURL(avatarUrl); }
    avatarUrl = URL.createObjectURL(blob);
    paintAvatars();
  } catch {
    // 读不到就照旧显示首字 （ 不许把这一页带塌 ）
  } finally {
    if (read === avatarRead) {
      for (const face of thePictures()) face.removeAttribute('data-avatar-loading');
    }
  }
}

/** **把选中的那张传上去** —— 成功之后**重取一次**再刷 （ 免得拿旧缓存 ）。 */
export async function sendAvatar(file: File): Promise<boolean> {
  await cropAvatar(file, uploadAvatar);
  return true;
}

async function uploadAvatar(blob: Blob, signal?: AbortSignal): Promise<boolean> {
  const answer = await fetch('/avatar', {
    method: 'POST',
    headers: { 'Content-Type': 'image/png' },
    body: blob,
    signal,
  });
  if (!answer.ok || signal?.aborted) { return false; }
  await loadAvatar();
  return true;
}

/** 左下角那块：圆形头像 ＋ 名字 ＋ 三个小图标（▶ ⚙ ⏻）。 */
function railFoot(): HTMLElement {
  const foot = node('div', 'rail-foot');
  const who = node('div', 'rail-who');
  // **那一枚圆球**（2026-10-09 作者：「点击特字圆球可以打开浏览本地的 JPG 这类图片格式，
  // 给 BOT 上传头像…」）—— 它现在**点得动** ：点它开文件选择 。
  const face = node('span', 'rail-avatar', speaker().slice(0, 1));
  face.setAttribute('data-avatar-loading', 'true');
  coreSetUIText(face, 'title', corePhrase("选择图片并裁剪头像"));
  face.tabIndex = 0;
  face.setAttribute('role', 'button');
  coreSetUIAttribute(face, 'aria-label', corePhrase("选择图片并裁剪头像"));
  face.style.cursor = 'pointer';
  const picker = node('input', 'rail-pick') as HTMLInputElement;
  picker.type = 'file';
  // ⚠️ **不许写 `image/` 后面那颗星** —— 判据剥注释那一步是
  // `re.sub(r"\/\*.*?\*\/", "", text, flags=re.S)` （**非贪婪** ）
  // ⇒ 那两个字一出现就会被当成注释开头 、 一路吞到很远的下一个 `*/`
  // （ 实测：把 `rail-action` 那一段整块吞掉 ⇒ `test_web.py` 那条当场红 ）。
  // ⇒ 列明白这四种 （ 反正缩完之后一律是 PNG ）。
  picker.accept = 'image/png,image/jpeg,image/webp,image/gif';
  picker.hidden = true;
  face.addEventListener('click', () => { picker.click(); });
  face.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); picker.click(); }
  });
  picker.addEventListener('change', () => {
    const chosen = picker.files === null ? undefined : picker.files[0];
    if (chosen !== undefined) {
      void sendAvatar(chosen).catch(error => {
        void notifyAction('头像无法更新', error instanceof Error
          ? error.message : '图片无法打开，请换一张图片。');
      });
    }
    picker.value = ''; // 同一个文件选第二次也要响
  });
  who.append(face, picker);
  const name = node('button', 'rail-name', speaker());
  coreSetUIText(name, 'title', corePhrase("双击修改显示名（不更改文件夹名）"));
  name.addEventListener('dblclick', () => {
    void botDialog(name.textContent || speaker())
      .catch(error => { void notifyAction('显示名无法更新', String(error)); });
  });
  who.append(name);
  foot.append(who);
  void loadAvatar();
  void loadLook();
  const actions = node('div', 'rail-actions');
  for (const { key, title, glyph } of RAIL_ACTIONS) {
    const button = node('button', `rail-action rail-${key}`, glyph);
    button.title = title;
    button.setAttribute('aria-label', title);
    if (key === 'shutdown') button.replaceChildren(icon('power'));
    if (key === 'settings') button.replaceChildren(icon('settings'));
    if (key === 'run') gateIcon(button);
    // **2026-10-10：那一颗 ⚙ 开始做事** （ 外观那一页 ② ）。
    // 2026-10-10（三颗按钮 ⑤）： 那一颗 ▶ **起来先读一次**
    // （ 读的是 `/state` 那两格 —— 而它**只读、不按** ）。
    if (key === 'run') {
      button.addEventListener('click', () => { void pressTheGate(button); });
      button.setAttribute('data-initial-loading', 'true');
      void loadTheGate(button).finally(() => button.removeAttribute('data-initial-loading'));
    }
    if (key === 'settings') {
      // 2026-10-10（重做 ④）： 跳到**设置那一页**
      // （ 不再弹那个浮层 —— 它从前只有六行 ）。
      button.addEventListener('click', () => { location.hash = '#/settings'; });
    }
    if (key === 'shutdown') {
      // 2026-10-10（三颗按钮 ④）： 那一颗**真去做事**
      // （ 它从前只亮着 —— `title` 那句「页面上没有这一下」作废 ）。
      button.addEventListener('click', () => { void shutItDown(button); });
    }
    actions.append(button);
  }
  foot.append(actions);
  return foot;
}

/** 顶栏那两层（ 第一层：四枚计数 ＋ 右边那两格；第二层：绿点 ＋ 模型名 ＋ 缓存那一格）。
 *
 *  ⚠️⚠️ **本波改了第一层的末尾**（Minor #2）：`已连接`那一枚原先挂在**第二层**，
 *     而设计稿 §三 与实物都是"**第一层**：… ＋ `当前模型供应商`〔灰胶囊〕 ＋ `已连接`"
 *     （实测实物：`.pill.on` 在 `y=14`，与计数同一行；第二层是会话那几张卡）。
 *     ⇒ 它现在跟着 `.topbar-right` 一起落在第一层的末尾，而第二层只剩
 *     **绿点 ＋ 名字 ＋ 缓存那一格**（不再重复报一次连接）。
 * 判据落在 `tests/test_web.py` 那一条：**`.topbar-provider` 必须是
 *     `.topbar-summary` 的子节点**，且**不在** `.topbar-status` 里 —— 搬回去当场红。 */
function topbar(): HTMLElement {
  const bar = node('header', 'topbar');
  const summary = node('div', 'topbar-summary');
  const right = node('div', 'topbar-right');
  // ⚠️ **2026-10-05 删掉我上一笔多加的那一格**（`topbar-providername`）：
  //    顶栏**本来就有** `topbar-model` 那一格（入口从 `/state` 写它）⇒ 我那一格没人写、
  //    于是永远显示它那个默认的 `—`（作者："把那个 - 去掉就行"）。
  right.append(node('span', 'topbar-providerlabel', corePhrase('当前模型')),
               node('span', 'topbar-model', '—'),
               providerChip());
  summary.append(node('div', 'topbar-chips'), right);
  const row = node('div', 'topbar-status');
  row.append(node('span', 'sdot'), node('span', 'topbar-name', speaker()));
  bar.append(summary, row);
  return bar;
}

/** 那一枚灰胶囊（"已连接"／"断开"／"读不到"）—— 它的字由入口从 `/state` 写。 */
function providerChip(): HTMLElement {
  return node('span', 'topbar-provider', corePhrase('连接中…'));
}

/**
 * 把壳建出来，挂到**那个根**上（`document.body`），并把四块交回给调用方。
 *
 * ⚠️ **它只建壳**：左栏那几项由入口按页表摆（`place`），顶栏那几个数由入口从 `/state`
 *    填 —— 于是壳不必认识那个端点，也不必认识任何一页。
 * ⚠️ `body` 上**不挂别的东西**（`replaceChildren`）：那一页的全部就是这一层壳。
 */
export function createShell(root: HTMLElement): { shell: Chrome; topbar: TopbarView } {
  const rail = node('aside', 'rail');
  rail.id = 'rail';
  const brand = node('div', 'rail-brand');
  const mark = coreBrandMark();
  brand.title = 'TelyinCore';
  brand.append(mark, node('span', 'rail-brandname', 'TelyinCore'));
  const nav = node('nav', 'rail-nav');
  const foot = railFoot();
  rail.append(brand, nav, foot);

  const bar = topbar();
  bar.id = 'topbar';
  const content = node('main', 'content');
  content.id = 'content';
  const bottom = node('footer', 'bottom');
  bottom.id = 'bottom';

  // 落点：左栏（`aside.rail`）与右边那一列（`.console` = 顶栏 ＋ 正文 ＋ 底部那一行）。
  //   ⚠️ 用 `replaceChildren` 而不是拼字符串 —— 那四块是**元素**，不是标记。
  const column = node('div', 'console');
  column.append(bar, content, bottom);
  const menuToggle = node('button', 'compact-menu-toggle', corePhrase('菜单')) as HTMLButtonElement;
  menuToggle.type = 'button'; menuToggle.setAttribute('aria-controls', 'rail');
  menuToggle.setAttribute('aria-expanded', 'false');
  const closeMenu = () => { root.classList.remove('compact-menu-open'); menuToggle.setAttribute('aria-expanded', 'false'); };
  menuToggle.addEventListener('click', () => {
    const open = root.classList.toggle('compact-menu-open');
    menuToggle.setAttribute('aria-expanded', String(open));
  });
  rail.addEventListener('click', event => { if ((event.target as HTMLElement).closest('.rail-item')) closeMenu(); });
  root.addEventListener('keydown', event => { if (event.key === 'Escape' && root.classList.contains('compact-menu-open')) { closeMenu(); menuToggle.focus(); } });
  root.replaceChildren(rail, column, menuToggle);
  installRailResize(root, rail);

  const query = (cls: string): HTMLElement => bar.querySelector(cls) as HTMLElement;
  // **那三格名字**（左栏那两格在 `foot` 底下，顶栏那一格在 `bar` 底下）——
  //    `setSpeaker()` 是它们唯一的写入口 ⇒ 名字换一处就够，页上不再有第二处字面名字。
  const face = foot.querySelector('.rail-avatar') as HTMLElement;
  const railName = foot.querySelector('.rail-name') as HTMLElement;
  const topName = query('.topbar-name');
  const chips = query('.topbar-chips');
  const counters = (pairs: ReadonlyArray<readonly [string, string]>): void => {
    chips.replaceChildren();
    for (const [key, value] of pairs) {
      const chip = node('span', 'chip');
      chip.dataset.key = key;
      const label = node('span'); coreSetUIText(label, 'textContent', [corePhrase(COUNTER_LABELS[key] ?? key), ' ']);
      chip.append(label, node('b', undefined, value));
      chips.append(chip);
    }
  };
  counters(EMPTY_CHIPS.map((key) => [key, '—'] as const)); // 先画一条空的顶栏
  const shell: Chrome = {
    root,
    rail,
    railNav: nav,
    railFoot: foot,
    topbar: bar,
    topbarSummary: query('.topbar-summary'),
    topbarStatus: query('.topbar-status'),
    content,
    bottom,
    bar: {
      chips,
      right: query('.topbar-right'),
      model: query('.topbar-model'),
      provider: query('.topbar-provider'),
      status: query('.sdot'),
    },
    counters,
    /** 第 **i** 组的落点（页表那一列里第 i 个组 —— 壳只认**位次**）。
     * **本刀多收一格**：`soon`（"还没做"的那几项）—— **缺省是空**，
     *     于是既有那几处调用一个字都不用改（`place(0, 'CORE', [...])` 照旧）。 */
    place(index: number, label: string, items: readonly NavItem[],
          soon: readonly SoonItem[] = []): void {
      // 组挂到**那一列导航**上（不是挂在 `aside` 上）：路由器那一下找的是
      //   `shell.railNav.querySelectorAll('.rail-item')` —— 挂错了地方，那一栏照旧画得出来，
      //   而"当前那一页"的高亮**一次都不会亮**（实测：本波第一版就是这么错的，
      //   而读文本判不出来 ⇒ 那一处由"把产物跑一遍"那条测试钉着）。
      const next = groupEl(index, label, items, soon);
      const previous = nav.querySelector<HTMLElement>(`.rail-group[data-index="${index}"]`);
      if (previous) previous.replaceWith(next);
      else nav.append(next);
    },
    /** **把那个名字写进那三处**（`.rail-avatar` 的首字 ／ `.rail-name` ／ `.topbar-name`）
     * —— 名字从她自己的档案读（`speaker.ts`），而壳自己**不 fetch**：入口读好递进来。 */
    setSpeaker(name: string): void {
      face.textContent = name.slice(0, 1);
      railName.textContent = name;
      topName.textContent = name;
      for (const label of [railName, topName]) {
        label.setAttribute('data-name-loading', String(!speakerReady()));
      }
    },
  };
  return { shell, topbar: shell.bar };
}
