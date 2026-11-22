import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { toolReceiptFailed } from './tool-activity';
// 聊天区 —— 那条推的通道每来一帧，就往这儿放一帧。
//
// ⚠️ 一律走 `textContent`（不是 `innerHTML`）：那一帧是从外面来的话，
//    而这个页面**只听这一个来源**，别给它多开一个"能把标记当代码跑"的口子。
// ⚠️ **那一帧是什么形状**：那条通道发来的是**一个 JSON 对象**（一个字符串），四格：
// `kind` ／ `who` ／ `at` ／ `text` —— 正本在 `telyincore/console.py::_FRAME_FIELDS`。
//    · `kind === 'batch'` ⇒ 一小段灰字（那一批的头一帧，不是谁说的话）；
//    · `kind === 'msg'`   ⇒ **按 `who` 分两支**：这一头那一支没有头像、靠右、标 `MINE_NAME`；
//      另一头那一支是圆形头像 ＋ 名字（那个名字从它自己的档案读 —— 见 `speaker.ts`）；
//    · `note()` ⇒ 这一页自己的一句（说"这条路怎么了"，**不带头像、不是那边说的话**）。
// ⚠️ **零框架**：`document.createElement` ＋ `textContent`，没有 React ／ 没有模板串。

import { describeTool, rememberToolTitles } from './tool-activity';
import { NEUTRAL, speaker, userLabel } from './speaker';
// 头像那一件（2026-10-09）： 聊天区每一枚新画的圆球都得跟上
// （ 而那张图住在 `shell.ts` 里 —— 侧栏那一枚也在那儿 ）。
import { theAvatarUrl } from './shell';
import { HistoryFeed, type DisplayFrame } from './history-feed';

/**
 * **那一帧里"谁说的"那一格** —— 两个值，**都不是页上那个名字**。
 *
 * ⚠️ 它们是**角色**，不是任何人的自称：页上那个名字从它自己的档案读
 * （`speaker.ts`）⇒ 改名不动这里。
 * 正本是 `telyincore/console.py::_ME` ／ `_OTHER`（两个常量），而判据
 *    （`tests/test_frame_speaker.py`）拿产物里编译过的这一对与那两个正本当面对。
 * ⚠️⚠️ **`OTHER` 写成一个命名的字面量（不是"只在别处用一次"的临时值）**：它是
 *    线格式那一半的**正本**，而判据要从产物里**按名字**读它（本仓成法：常量留在
 *    产物里，判据读那一条字节）—— 一个只在运行时算得出来的值读不到。
 */
export const ME = 'user';
/** **另一头那一格的值** —— 与 `ME` **只差这一个字符串**，而那是分得开两个人的
 *  **唯一**依据（`isMine` 只看它）。⚠️ 错一个字母就是"两行都画成这一头"。 */
export const OTHER = 'bot';
/** **批号那一帧的 `kind`** —— 它与正本 `console.py::_A_BATCH` 同值。 */
const A_BATCH = 'batch';
/** **一句话那一帧的 `kind`** —— 与正本 `console.py::_A_MESSAGE` 同值。 */
const A_MESSAGE = 'msg';
/** **他那一支页上那个通用名** —— 谁的安装都对得上，而且**不冒充任何人**。 */
export const MINE_NAME = 'USER';
/** **她那个名字**从她自己的档案读（`speaker.ts`）—— 页上**一个字面名字都没有**。 */

/** 一帧 —— 那条通道发来的那个对象（四格，见本文件抬头）。 */
export interface Frame {
  kind: string;
  who: string | null;
  at: string | null;
  text: string;
  // **2026-10-10 卡片那一族**（`kind === 'tool'` 才带 —— 别的帧这两格是空的 ）：
  // `tool` ＝ 工具名 ； `stage` ＝ `'call'`（她调它 ）／ `'result'`（它答的 ）。
  tool?: string;
  stage?: string;
  // **2026-10-10 卡片第四刀**： 轮号（`run-{n}` ）与
  // **思考的正文** （ 只在 `kind === 'tool'` 那一族上 ）。
  run?: string;
  display_id?: string;
}

/** 那一列交回的把手：推送来的每一帧落进 `append`，这一页自己的话进 `note`。 */
export interface ChatView {
  readonly root: HTMLElement;
  listen(active: boolean): void;
  append(wire: string): void;
  refreshHistory?(): void;
  beginHistory?(): number;
  finishHistory?(ticket: number, frames: readonly DisplayFrame[]): void;
  failHistory?(ticket: number): void;
  /** **我说了一句**（2026-10-05 作者："我看不到我有没有发出去"）——
   * 发出去的那一刻就把这一条画到那一列上（自己的那一支：`ME` ⇒ `.msg.mine` ）。
   * ⚠️ 它是**回显**，不是"服务器收下了"的凭据：收下与否由 `note()` 那句说 。 */
  mine(text: string, images: readonly string[]): string;
  clearPending(id: string): void;
  note(text: string, action?: HTMLElement): void;
  /** **每画完一帧就叫一下**（2026-10-05）—— 那一页据此刷新"上下文占用"：
   * 她说完一句之后，那块面板上的数**当场**跟着变 （不必等下一次点开 ）。
   * ⚠️ 它由 `live` 那一页设（`chat.watch(…)`）；没设 ⇒ 什么也不叫 。 */
  watch(fn: () => void): void;
  /** 她那个名字到位了（或换了一份档案）⇒ 记下来，并把已经画上去的几条重画一遍。 */
  setSpeaker(name: string): void;
}

/** 那一格是不是一个字符串（缺省都没有 ⇒ 照 `null` 读）。 */
function asText(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** **线上那个值 ⇒ 一帧** —— 只有这一处把一条 `data:` 变成那三样（谁／哪一刻／正文）。
 *
 *  ⚠️ **它按字段读，不按位置、也不切任何字符**：那三样各是一格（见本文件抬头）。
 *  ⚠️ **读不出来就把这一条当成一整句正文**（`who` ／ `at` 都空着）：这一页**不许**因为
 *     一条读不懂的帧而崩 —— 而"读不懂"与"没带头"在页上是同一支（她那一支），
 *     于是它至少**看得见**（一条也没有、还画不出来，才是这一页最坏的一种静默）。
 *  ⚠️ **`text` 缺省是空那一串**（不是 `undefined`）：`textContent` 收到 `undefined`
 *     会落成"undefined"那几个字母 —— 那一句才是真的难看。
 */
export function asFrame(wire: string): Frame {
  try {
    const got: unknown = JSON.parse(wire);
    if (typeof got === 'object' && got !== null) {
      const it = got as Record<string, unknown>;
      // **2026-10-10：卡片那两格也要抄过来** —— 这里从前只抄四格
      // ⇒ `tool` ／ `stage` 到了页上就**没了** （ 实测：卡片画出来了 ，
      // 而工具名显示「（没名字）」 —— 因为它们在半路被丢掉 ）。
      return { kind: asText(it.kind) ?? A_MESSAGE, who: asText(it.who),
               at: asText(it.at), text: asText(it.text) ?? '',
               tool: asText(it.tool) ?? undefined,
               stage: asText(it.stage) ?? undefined,
               // **2026-10-10 卡片第四刀**： 轮号与**思考**也得抄
               // —— 同一个坑： 它们在线上有 、 而这一步不抄就**到不了页上** 。
               run: asText(it.run) ?? undefined,
               display_id: asText(it.display_id) ?? undefined,
 };
    }
  } catch {
    // 不是 JSON ⇒ 落到下面那一支（一整句正文）
  }
  return { kind: A_MESSAGE, who: null, at: null, text: wire };
}

/** **那一帧是哪一头说的** —— 判据只有一格（`who`），而**两头都点名**。
 *
 *  ⚠️⚠️ **另一头那一格也要读出来**（不是"不是这一头 ⇒ 就是另一头"）：那一条写法把
 *     "**谁也不认识的那个值**"（一条读错了 ／ 将来多出来的角色）悄悄算成另一头的话
 *     —— 而它与"另一头说过话"是**两件事**。⇒ 这一支要的是"两头都认识"这件事本身：
 *     认识 ⇒ 它自己那一支；**不认识 ⇒ 也画成她那一支**（读得懂多少画多少），
 *     而那一条路在下面那个 `?? theirs` 上，判据拿一个谁也不认识的值钉着它。
 * 于是 `ME` 与 `OTHER` **两个常量都在这一支里**（少了哪一个，这一支就是上面那一条
 *     写法）—— 而"两个值不相同"那一条判据（`tests/test_frame_speaker.py`）靠的正是
 *     它们在产物里都读得到。
 */
function whoOf(frame: Frame): string | null {
  if (frame.who === ME) return ME;
  if (frame.who === OTHER) return OTHER;
  return null;
}

/** **那一帧是不是这一头说的** —— 判据只有一格（`who`），与它是不是一句话无关。 */
/** **头像那一件**（2026-10-09）—— 聊天区**每一枚新画的圆球**都要跟上
 * （ 侧栏那一枚由 `shell.ts` 管 ； 而这一页的圆球是**一条一条现画的**
 * ⇒ 画完一条就问一句"现在有没有那张图" —— 有就把它画上去 ）。 */
function catchUpTheAvatar(one: HTMLElement): void {
  const url = theAvatarUrl();
  if (url === null) { return; }
  one.classList.add('has-pic');
  one.style.backgroundImage = `url(${url})`;
}

/** **那一刻那一格**（2026-10-09 作者：「今天的不用添加日期，不是今天就要添加，
 * 不然不好辨别。」）。
 *
 * 线上交来的是**已经判好的那一句** （ 哪一天由**入口**判 —— 本文件**不许读钟**
 * ：`test_web.py::test_the_page_reads_no_clock_of_its_own` 明写 `Date` 那一族在这里
 * **零命中** ， 理由是一次真出过的事： 第二口钟会让页上的时间与盘上对不上 ）：
 * · **今天** ⇒ `09:00` （没有日期 ）
 * · **不是今天** ⇒ `10 月 8 日 09:00` （ 照经历页那套写法 ）
 *
 * 而**左右镜像**那一条照旧 ： 对方是「日期 时刻 名字」、 我这一支是
 * 「USER 时刻 日期」 ⇒ 日期要**从前面挪到后面** —— 纯字符串重排 ，不读钟 。
 */
function theMoment(at: string, mine: boolean): string {
  // ⚠️ **不能按"第一个空格"切** —— 日期里**自己带空格**
  // （`"10 月 9 日 01:30"` ⇒ 第一个空格在 `10` 后面 ⇒ 切出来是
  // `"月 9 日 01:30 10"` —— 这一版是真页上量出来的 ）。
  // ⇒ 那一刻**永远是末尾那五个字符**（`HH:MM` ）⇒ 按它切 。
  const clock = at.slice(-5);
  if (!mine || at.length <= 5 || !/^[0-9]{2}:[0-9]{2}$/.test(clock)) { return at; }
  const day = at.slice(0, -6); // 去掉"那一刻 ＋ 一个空格"
  // ⚠️ **只有"线上判好的那一种"才重排** —— 判好的日期里**自己带空格**
  // （`"10 月 2 日 09:00"` ）； 而**没判过**的整串是 `"2026-10-02T09:00"`
  // （ 一个空格都没有 ）⇒ 用它分辨 （ 那种不该在页上出现 ，
  // 真出现了也**原样交出去**、不猜 ）。
  if (!day.includes(' ')) { return at; }
  return `${clock} ${day}`; // 日期挪到末尾
}

function isMine(frame: Frame): boolean {
  return whoOf(frame) === ME;
}

/** 一条消息（圆形头像 ＋ 名字 ＋ 那一刻 ＋ 正文那一块）—— 他那一支没有头像、靠右。
 *  ⚠️ `theirs` 是**她那一支要画的那个名字**（调用方从 `speaker()` 取 —— 页上不写死它）。 */
function bubble(frame: Frame, theirs: string = NEUTRAL): HTMLElement {
  const mine = isMine(frame);
  const row = document.createElement('div');
  row.className = mine ? 'msg mine' : 'msg';
  const body = document.createElement('div');
  body.className = 'msg-body';
  // 抬头那一行：那一刻在**前**、名字在**后**，而它们是**同一个盒子的两个孩子**
  //    （`.msg-head`）—— 布局那一半在 `style.css`（一行、不换行）。
  //    ⚠️ 名字**不吐**进那一格里：`.msg-who` 的 `textContent` 就是那个名字
  //    （判据读的正是它 —— 两个名字一起塞进去就读不出谁是谁）。
  const head = document.createElement('div');
  head.className = 'msg-head';
  if (frame.at !== null) {
    const stamp = document.createElement('span');
    stamp.className = 'msg-at';
    stamp.textContent = theMoment(frame.at, mine); // 只重排 // 那一刻来自那一帧，不是本机钟
    head.append(stamp);
  }
  const name = document.createElement('div');
  name.className = 'msg-who';
  name.textContent = mine ? userLabel() : theirs; // 名字在这一处定
  head.append(name);
  body.append(head);
  const part = document.createElement('p');
  part.className = 'msg-text';
  part.textContent = frame.text;
  body.append(part);
  const actions = document.createElement('div'); actions.className = 'msg-actions';
  const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'msg-copy';
  coreSetUIAttribute(copy, 'aria-label', corePhrase("复制这条消息")); coreSetUIText(copy, 'title', corePhrase("复制这条消息"));
  const svgNS = 'http://www.w3.org/2000/svg';
  const icon = document.createElementNS(svgNS, 'svg');
  icon.classList.add('msg-copy-icon');
  icon.setAttribute('viewBox', '0 0 20 20');
  icon.setAttribute('aria-hidden', 'true');
  const outline = document.createElementNS(svgNS, 'path');
  outline.setAttribute('d', 'M5 13H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v1 M9 7h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z');
  icon.append(outline);
  copy.append(icon);
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(frame.text);
      coreSetUIText(copy, 'title', corePhrase("复制这条消息"));
    } catch {
      coreSetUIText(copy, 'title', corePhrase("复制失败，请手动选择消息文字"));
    }
  });
  actions.append(copy); body.append(actions);
  if (mine) {
    row.append(body);
  } else {
    const avatar = document.createElement('span');
    avatar.className = 'msg-avatar';
    avatar.textContent = theirs.slice(0, 1); // 头像 ＝ 那个名字的首字（同一条规矩）
    row.append(avatar, body);
    catchUpTheAvatar(avatar);
  }
  return row;
}

/** Consecutive observable tool activity is one collapsed group; speech stays in order. */
interface ToolStep {
  name: string;
  ref: string;
  call?: Frame;
  result?: Frame;
  element: HTMLButtonElement;
  context: HTMLElement;
}
interface ToolGroup {
  row: HTMLElement;
  summary: HTMLElement;
  body: HTMLElement;
  inspector: HTMLElement;
  selected: ToolStep | null;
  view: 'result' | 'arguments';
  steps: ToolStep[];
}
const toolGroups = new WeakMap<HTMLElement, ToolGroup>();
const handoffGroups = new WeakMap<HTMLElement, ToolGroup>();
let toolInspectorSerial = 0;
const toolLabels: Record<string, string> = { pending_read: '读取待议记录', decide_change: '处理设定修订',
  propose_change: '提出设定修订', profile_add: '写入画像', profile_read: '读取画像',
  kb_search: '检索知识', kb_read: '阅读知识', memory_search: '检索记忆',
  relations_read: '查看关系', read_events: '查阅经历', skill_read: '查看做法',
  handoff_note: '交接笔记', skill_note: '记录做法', self_account_add: '补充自述', self_account_set: '更新自述', finish_turn: '结束本轮' };

function toolText(frame: Frame): string {
  const text = (frame.text || '').trim();
  if (!text) return frame.stage === 'result' ? '回执未提供正文。' : '未记录调用参数。';
  if (frame.stage !== 'result' && text === '{}') return '无需参数。';
  return frame.text;
}

const knownToolTitles = new WeakMap<HTMLElement, Map<string, string>>();
function activityText(step: ToolStep, chat: HTMLElement): string {
  return describeTool(step.name, step.call?.text, step.result?.text,
    toolLabels[step.name] || '调用工具', knownToolTitles.get(chat));
}

function showTool(group: ToolGroup): void {
  for (const step of group.steps) step.element.setAttribute('aria-expanded', String(group.selected === step));
  const step = group.selected;
  group.inspector.hidden = !step;
  group.inspector.replaceChildren();
  if (!step) return;
  const failed = toolReceiptFailed(step.name, step.result?.text);
  const head = document.createElement('div'); head.className = 'tool-inspector-head';
  const identity = document.createElement('div'); identity.className = 'tool-inspector-identity';
  const name = document.createElement('span'); name.className = 'tool-inspector-title'; name.textContent = step.name; name.title = step.name;
  const caption = document.createElement('span'); caption.className = 'tool-inspector-caption'; caption.textContent = toolLabels[step.name] || '工具详情';
  identity.append(name, caption);
  const state = document.createElement('span'); state.className = 'tool-inspector-status' + (failed ? ' has-error' : '');
  coreSetUIText(state, 'textContent', (failed ? corePhrase("执行失败") : (step.result ? ((step.call ? corePhrase("已返回") : corePhrase("仅有回执"))) : corePhrase("等待回执"))));
  const close = document.createElement('button'); close.className = 'tool-inspector-close'; close.type = 'button';
  coreSetUIAttribute(close, 'aria-label', corePhrase("收起工具详情"));
  close.addEventListener('click', () => { const button = group.selected?.element; group.selected = null; showTool(group); button?.focus(); });
  head.append(identity, state, close);
  let parameters = step.call ? toolText(step.call) : '未记录对应调用参数。';
  if (step.call && step.call.text.trim() && step.call.text.trim() !== '{}') {
    try { parameters = JSON.stringify(JSON.parse(step.call.text), null, 2); } catch { /* Preserve plain arguments. */ }
  }
  const output = step.result ? toolText(step.result) : '尚未收到回执。';
  const toolbar = document.createElement('div'); toolbar.className = 'tool-inspector-toolbar';
  const tabs = document.createElement('div'); tabs.className = 'tool-inspector-tabs'; tabs.setAttribute('role', 'tablist'); coreSetUIAttribute(tabs, 'aria-label', corePhrase("工具详情内容"));
  for (const [key, label] of [['result', '执行回执'], ['arguments', '调用参数']] as const) {
    const tab = document.createElement('button'); tab.type = 'button'; tab.className = 'tool-inspector-tab';
    tab.textContent = label; tab.id = group.inspector.id + '-' + key; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(group.view === key));
    tab.tabIndex = group.view === key ? 0 : -1; tab.setAttribute('aria-controls', group.inspector.id + '-pane');
    tab.addEventListener('click', () => { group.view = key; showTool(group);
      (group.inspector.children[1].children[0].children[key === 'result' ? 0 : 1] as HTMLElement).focus(); });
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      group.view = event.key === 'Home' ? 'result' : event.key === 'End' ? 'arguments' : key === 'result' ? 'arguments' : 'result';
      showTool(group);
      (group.inspector.children[1].children[0].children[group.view === 'result' ? 0 : 1] as HTMLElement).focus();
    });
    tabs.append(tab);
  }
  const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'tool-inspector-copy'; coreSetUIText(copy, 'textContent', corePhrase("复制"));
  const content = group.view === 'arguments' ? parameters : output;
  copy.addEventListener('click', () => { void (async () => {
    try { await navigator.clipboard.writeText(content); coreSetUIText(copy, 'textContent', corePhrase("已复制")); }
    catch { coreSetUIText(copy, 'textContent', corePhrase("复制失败")); }
  })(); });
  toolbar.append(tabs, copy);
  const pane = document.createElement('pre'); pane.tabIndex = 0; coreSetUIAttribute(pane, 'aria-label', corePhrase("工具详情正文，可滚动查看")); pane.className = 'tool-inspector-pane' + (group.view === 'arguments' ? ' is-code' : '');
  pane.id = group.inspector.id + '-pane'; pane.setAttribute('aria-labelledby', group.inspector.id + '-' + group.view);
  pane.textContent = content; pane.setAttribute('role', 'tabpanel'); coreSetUIAttribute(pane, 'aria-label', (group.view === 'arguments' ? corePhrase("调用参数") : corePhrase("执行回执")));
  group.inspector.append(head, toolbar, pane);
}

/** Render observed activity as prose with inline tool links, never invented thoughts. */
function appendTool(chat: HTMLElement, frame: Frame): HTMLElement {
  let group = toolGroups.get(chat);
  if (!group || chat.children[chat.children.length - 1] !== group.row) {
    const row = document.createElement('section'); row.className = 'line tool tool-group';
    const summary = document.createElement('div'); summary.className = 'tool-head';
    const body = document.createElement('div'); body.className = 'tool-process';
    const inspector = document.createElement('section'); inspector.className = 'tool-inspector'; inspector.hidden = true;
    inspector.id = 'tool-inspector-' + (++toolInspectorSerial);
    coreSetUIAttribute(inspector, 'aria-label', corePhrase("工具用法与回执"));
    row.append(summary, body, inspector);
    group = { row, summary, body, inspector, selected: null, view: 'result', steps: [] };
    toolGroups.set(chat, group); chat.append(row);
  }
  const name = frame.tool || '未命名工具';
  const ref = (frame.display_id || '').replace(/:function_(call|result)$/, '');
  const stage = frame.stage === 'result' ? 'result' : 'call';
  let step = ref ? group.steps.find(s => s.ref === ref && s.name === name) : undefined;
  if (!ref && stage === 'result') {
    const pending = group.steps.filter(s => s.name === name && s.call && !s.result && !s.ref);
    if (pending.length === 1) step = pending[0];
  }
  if (!step) {
    const element = document.createElement('button'); element.type = 'button';
    const context = document.createElement('span'); context.className = 'tool-process-context';
    step = { name, ref, element, context };
    const selectedStep = step;
    const selectedGroup = group;
    element.addEventListener('click', () => {
      selectedGroup.selected = selectedGroup.selected === selectedStep ? null : selectedStep;
      selectedGroup.view = 'result';
      showTool(selectedGroup);
    });
    if (group.steps.length) {
      const separator = document.createElement('span'); separator.className = 'tool-process-separator'; separator.textContent = '；'; group.body.append(separator);
    }
    group.steps.push(step); group.body.append(context, element);
  }
  step[stage] = frame;
  let titles = knownToolTitles.get(chat);
  if (!titles) { titles = new Map(); knownToolTitles.set(chat, titles); }
  if (stage === 'result') rememberToolTitles(frame.text, titles);
  const failed = toolReceiptFailed(step.name, step.result?.text);
  step.element.className = 'tool-process-link' + (failed ? ' has-error' : step.result ? ' is-returned' : ' is-pending');
  step.context.textContent = activityText(step, chat) + ' ';
  step.element.textContent = name;
  if (name === 'handoff_note') { coreSetUIText(step.context, 'textContent', corePhrase("交接笔记 · 点击查看 ")); handoffGroups.set(chat, group); }
  coreSetUIText(step.element, 'title', [[name, corePhrase(" · ")], ((failed ? corePhrase("执行失败") : (step.result ? ((step.call ? corePhrase("已返回") : corePhrase("仅有回执"))) : corePhrase("等待回执"))))]);
  coreSetUIAttribute(step.element, 'aria-label', [step.element.title, corePhrase("，查看参数和回执")]);
  step.element.setAttribute('aria-expanded', String(group.selected === step));
  step.element.setAttribute('aria-controls', group.inspector.id);
  const calls = group.steps.filter(s => s.call).length;
  const results = group.steps.filter(s => s.result).length;
  const failures = group.steps.filter(s => toolReceiptFailed(s.name, s.result?.text)).length;
  group.summary.replaceChildren();
  const title = document.createElement('span'); title.className = 'tool-group-title'; coreSetUIText(title, 'textContent', corePhrase("过程"));
  const count = document.createElement('span'); count.className = 'tool-group-count'; coreSetUIText(count, 'textContent', [[[calls, corePhrase(" 次调用 · ")], results], corePhrase(" 条回执")]);
  group.summary.append(title, count);
  if (failures) {
    const state = document.createElement('span'); state.className = 'tool-group-status has-error'; coreSetUIText(state, 'textContent', [failures, corePhrase(" 项失败")]); group.summary.append(state);
  }
  if (group.selected === step) showTool(group);
  return group.row;
}

/**
 * **贴到底**（2026-10-11 作者：「发消息，自动回到消息的最底部；从别的网页切过来，
 * 也能自动到最底部，不然每次都是从最顶部慢慢滑下来」）。
 *
 * 为什么不是一句 `el.scrollTop = el.scrollHeight` 就完：真正在滚的那一格可能是它的
 * **祖先**（外层的页面／shell 一改，滚动的就换人了）——所以从这一格往上找，
 * 凡是"内容比它自己高"的那几格都跟着贴到底。
 */
export function stickToTheBottom(el: HTMLElement | null | undefined): void {
  // ⚠️ `undefined` 也要挡住：`undefined !== null` 为真 ⇒ 会走进来读它那一格而崩
  // （2026-10-11 判据当场抓到：`Cannot read properties of undefined (reading 'scrollHeight')`）。
  if (!el) return;
  for (let one: HTMLElement | null = el; one; one = one.parentElement) {
    if (one.scrollHeight > one.clientHeight + 1) {
      one.scrollTop = one.scrollHeight;
    }
  }
}

// Empty internal batches remain in the event log, without taking up chat space.
const visibleBatches = new WeakMap<HTMLElement, { heading: HTMLElement; content: boolean }>();

export function appendFrame(chat: HTMLElement, frame: Frame,
                            theirs: string = NEUTRAL): HTMLElement {
  // **交回画的那个元素**（2026-10-05）："我说的话"那一条要往里塞缩略图
  // ⚠️ 加返回值**不动**既有调用（它们本来就不接返回值 ）。
  let drawn: HTMLElement;
  const batch = visibleBatches.get(chat);
  const handoffReceipt = frame.kind === 'tool' && frame.tool === 'handoff_note';
  if (batch && !handoffReceipt && frame.kind !== A_BATCH && frame.kind !== 'batch-status' && frame.kind !== 'system-notice') {
    batch.content = true;
    batch.heading.style.display = '';
  }
  if (frame.kind === 'system-notice') {
    const row = document.createElement('details');
    row.className = 'console-system-notice';
    const summary = document.createElement('summary');
    const body = document.createElement('div'); body.className = 'console-system-detail';
    const text = frame.text.replace(/^\[system\]\s*/i, '');
    const heading = text.split(/[。\n]/)[0];
    coreSetUIText(summary, 'textContent', [corePhrase("系统 · "), ((heading.length > 48 ? [heading.slice(0, 48), corePhrase("…")] : heading))]);
    summary.title = frame.at || '展开系统事件详情';
    body.textContent = text;
    row.append(summary, body);
    const handoff = /^交接完/.test(text) ? handoffGroups.get(chat) : undefined;
    if (handoff && chat.contains(handoff.row)) {
      // Keep the real, persisted receipt beside the wake notice that follows it.
      handoff.row.prepend(row); chat.append(handoff.row);
      toolGroups.set(chat, handoff); handoffGroups.delete(chat);
    } else {
      chat.append(row);
    }
    drawn = row;
  } else if (frame.kind === A_BATCH) {
    const row = document.createElement('p');
    row.className = 'line batch';
    row.style.display = 'none';
    visibleBatches.set(chat, { heading: row, content: false });
    coreSetUIText(row, 'textContent', [corePhrase("第 "), frame.text.slice(2, -1), corePhrase(" 批")]);
    drawn = row;
    chat.append(row);
  } else if (frame.kind === 'batch-status') {
    const row = document.createElement('div');
    row.className = 'console-control-note console-completed';
    // Errors and interruptions must remain visible even without a reply.
    const exceptional = /失败|停止|中断|错误/.test(frame.text);
    if ((!batch || !batch.content) && !exceptional) row.style.display = 'none';
    if (batch && exceptional) batch.heading.style.display = '';
    const label = document.createElement('span');
    label.textContent = frame.text
      .replace(/^本轮处理已结束$/, '本轮已结束')
      .replace(/^已处理 (\d+) 秒$/, '用时 $1 秒')
      .replace(/^用时 0 秒$/, '已结束 · 不足 1 秒')
      .replace(/^已停止 · 已处理 (\d+) 秒$/, '已停止 · 用时 $1 秒');
    if (frame.at) label.title = frame.at;
    row.append(label);
    drawn = row;
    chat.append(row);
  } else if (frame.kind === 'tool') {
    // **2026-10-10 作者：「但不是以对话的形式，而是类似一种卡片，贴在上面，供我查看
    // 这批里的思考过程和调用了什么工具。」**
    // ⚠️ **它**不是**说话那一支** —— 无头像 ／ 不靠右 ／ 不进 `bubble()` 。
    // **日常收起** ： `<details>` 天生就是这个 ⇒ 一行够看
    // （工具名 ＋ 参数摘要 ）， 点开才有全文 （ 不写 JS ）。
    drawn = appendTool(chat, frame);
  } else {
    drawn = bubble(frame, theirs);
    chat.append(drawn);
  }
  stickToTheBottom(chat);
  return drawn;
}

/** 那一句没送出去时画的一条（ 它说的是"这条路怎么了"，不是"那边说了什么"）。 */
export function appendNote(chat: HTMLElement, text: string, action?: HTMLElement): void {
  const row = document.createElement('p');
  row.className = 'line note console-control-note console-feedback';
  const label = document.createElement('span');
  label.textContent = text;
  if (action) label.append(action);
  row.append(label);
  chat.append(row);
  stickToTheBottom(chat);
}

/**
 * **把正文那一列建出来**（聊天那一页那一处）—— 交回一个挂／落帧／提醒的把手。
 *
 * ⚠️ **那一句开场白删掉了**（2026-10-09 作者：「这一列是那边说过的话。 刷新之后
 * 旧话会重放回来。把这个删掉。」）—— 它说的是"历史重放还没接上"那件事实 ，
 * 而历史重放 2026-10-05 就接上了 ⇒ 那句从那天起就不再成立 （账在
 * `mountChat` 里那一处 ）。 别再加回去 。
 */
export function mountChat(host: HTMLElement): ChatView {
  const column = document.createElement('div');
  column.className = 'live';
  // **2026-10-09 作者：「也给终端页加一个标题 · 英文翻译，标题大小尺寸那些都和其他
  // 页的一样。」** —— 用 `TERMINAL`： 直译 （ 其余七页也都是那个东西的英文名 ）；
  // `CMD` 是 Windows 命令解释器的专名 ，而这一页是「一页对话 ＋ 一个输入框」
  // ⇒ 用它会把读的人带偏 。
  // 那一档（`19px` ／ `600` ／ 行高 29.45 ／ 上面那 3px ）在 `.live > .page-title` 上
  // —— 与其余七页**同一个数** 。
  const title = document.createElement('h1');
  title.className = 'page-title';
  coreSetUIText(title, 'textContent', corePhrase("终端 · TERMINAL"));
  const stream = document.createElement('div');
  stream.className = 'live-stream';

  // 2026-10-11：切回这一页／这一格重新可见时，也贴到底。
  // 挂一次就够（这一列建一次）；用 visibilitychange ＋ focus 两个口，
  // 因为"从别的网页切过来"在不同浏览器上走的不一定是同一个事件。
  const back = (): void => {
    if (document.visibilityState !== 'hidden') stickToTheBottom(stream);
  };
  document.addEventListener('visibilitychange', back);
  window.addEventListener('focus', back);
  back();
  column.append(title, stream);
  host.replaceChildren(column);
  let theirs = speaker(); // 这一列现在用的那个名字（一处，随这一列走）
  let watcher: (() => void) | null = null; // "画完一帧就叫一下"那一个（见接口上那条 ）
  // **已经画上去、还没等到那一帧的"我说的话"**（2026-10-05 作者："为什么我的话发了两遍"）：
  // 回显那一条是**这一页自己画的** ，而服务**还会把它推回来**（`/events` 那条路 ）
  // ⇒ 同一个东西画两遍 。⇒ 这里按 FIFO 记着，收到"自己那一支、同文"的帧时**认领**它
  // （认领 ⇒ **不重画** ，并把帧里的时间戳补到已经画好的那一条上 ）。
  // ⚠️ 按**文本**对（帧里没有"那一次发送"的号 —— `Frame` 只有谁／哪一刻／正文）；
  // 同文发两次时会一次认一条（先来的先认 ）。
  const pending: { id: string; text: string; row: HTMLElement }[] = [];
  let intentSequence = 0;
  const view: ChatView = {
    root: column,
    listen: (active: boolean): void => {
      document.removeEventListener('visibilitychange', back);
      window.removeEventListener('focus', back);
      if (active) {
        document.addEventListener('visibilitychange', back);
        window.addEventListener('focus', back);
      }
    },
    append: (wire: string): void => {
      const frame = asFrame(wire);
      // Pending UI is retired by that exact POST response, never by matching
      // somebody else's same-text historical/live message.
      // 2026-10-11（作者看出来的）：**批号那一行要落在它这一批的最前面**。
      // 自己那一句是"按下发送就立刻画"的（见 mine ⇒ pending 记着它），
      // 而批号随服务那一批回来 ⇒ 直接追加会让批号落在那句话**下面**，
      // 读的人就分不清这一批是哪两句（第 9 批 ／ 你那一句 ／ 她那一句的顺序）。
      // ⇒ 有还没被认领的"我说的话"时，把批号插到它们**前面**。
      if (frame.kind === A_BATCH && pending.length > 0) {
        const head = appendFrame(stream, frame, theirs);
        stream.insertBefore(head, pending[0].row);
        head.style.display = '';
        const batch = visibleBatches.get(stream);
        if (batch) batch.content = true;
        if (watcher !== null) { watcher(); }
        return;
      }
      appendFrame(stream, frame, theirs);
      if (watcher !== null) { watcher(); } // 有新的一帧 ⇒ 那一块的数当场跟着变
    },
    watch: (fn: () => void): void => { watcher = fn; },
    /** 名字到位（或换了一份档案）⇒ 记下来 ＋ **把已经画上去的那些重画一遍**。 */
    setSpeaker: (name: string): void => {
      theirs = name;
      for (const row of stream.querySelectorAll('.msg')) {
        if (row.classList.contains('mine')) {
          const who = row.querySelector('.msg-who');
          if (who !== null) who.textContent = userLabel();
          continue;
        } // 他那一支的名字不归它管
        const who = row.querySelector('.msg-who');
        const face = row.querySelector('.msg-avatar');
        if (who !== null) who.textContent = theirs;
        if (face !== null) face.textContent = theirs.slice(0, 1);
      }
    },
    /** **我说的话** —— 立刻画一条自己的气泡；带图的话把缩略图也画上 。
     * ⚠️ 画完滚到底（`appendFrame` 自己会滚一次，而缩略图是在那之后加的 ⇒ 再滚一次 ）。 */
    mine: (text: string, images: readonly string[]): string => {
      const row = appendFrame(stream, { kind: A_MESSAGE, who: ME, at: null, text }, theirs);
      const id = String(++intentSequence);
      pending.push({ id, text, row });
      row.setAttribute('data-delivery', 'pending');
      const stamp = row.querySelector('.msg-at');
      if (stamp) coreSetUIText(stamp, 'textContent', corePhrase("发送中，尚未确认"));
      if (images.length > 0) {
        const strip = document.createElement('div');
        strip.className = 'msg-thumbs';
        for (const url of images) {
          const picture = document.createElement('img');
          picture.src = url;
          picture.alt = '发出去的一张图';
          strip.append(picture);
        }
        row.append(strip);
        // **落点跟着滚动那一块改** （2026-10-09 统一滚动那一刀）：
        // 滚动归**页那一层**（`column` —— 见 `style.css` 的 `.live` ）
        // ⇒ 这里也得滚 `column`
        // （ 还写 `stream` 的话，「最新一行够不着」）。
        column.scrollTop = column.scrollHeight;
      }
      return id;
    },
    clearPending: (id: string): void => {
      const index = pending.findIndex(item => item.id === id);
      if (index >= 0) pending.splice(index, 1)[0].row.remove();
    },
    note: (text: string, action?: HTMLElement): void => appendNote(stream, text, action),
  };
  // **这一句抬头现在是真话**（2026-10-05 改的，理由写在这儿）：
  // 从前它写的是「刷新之后旧话就不在了 —— 历史重放**还没有**」 ——
  // 而那一刻它是**真的** ；历史重放（`GET /history` ⇒ 逐条 `append` ）接上之后，
  // 它就成了一句**与树对不上**的话 ⇒ 换成下面这一句 。
  // ⚠️ **这里不写它原来的字样**：有一条判据按"那句在不在"钉着它 ，
  // 注释里写了就成"判据自己打自己"（这一页已经踩过三次 ）。
  // **2026-10-09 作者：「这一列是那边说过的话。 刷新之后旧话会重放回来。
  // 把这个删掉。」** —— 那句开场白**不再画**了
  // （ 它当年说的是"历史重放还没接上"那件事实 ，而那一句从 2026-10-05 接上之后就
  // **不再成立** —— 见上面那一段注释 ）。
  const directAppend = view.append;
  const feed = new HistoryFeed(directAppend, frames => {
    stream.replaceChildren();
    for (const frame of frames) directAppend(JSON.stringify(frame));
    for (const item of pending) stream.append(item.row);
  });
  view.append = wire => feed.receive(wire);
  view.beginHistory = () => feed.begin();
  view.finishHistory = (ticket, frames) => feed.finish(ticket, frames);
  view.failHistory = ticket => feed.fail(ticket);
  return view;
}
