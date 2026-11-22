import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { pageLoad } from './page-load';
import { confirmAction } from './confirm-dialog';
import { pageFetch as fetch } from './status-json';
// **页三：人设那一页**（`#/persona`，左栏那一组 `PERSONA & MEMORY` 里那一项）
//   —— 她读的那十段，从上到下铺开：左列**段名**（彩色标签）｜ 中间**正文 ＋ 行号**
//   （点一块就地改）｜ 右列**归属 ＋ 文件名**。
//
// ⚠️ **这一条口子能改她是谁**（`telyincore/web.py::PERSONA_BOUNDARY_LINE`）：
//    「只听回环」照旧**只是**「这一台机器」，而**这一台机器上的谁都改得动它** ——
//    它不比 `POST /say` 更紧，也不比它更松（同一条边界，**本刀刻意不鉴权**）。
// 那一句在页上由 `BOUNDARY` 说出来（"写入那条路"那一段）。
//
// ⚠️ **两张清单，谁都不许丢**（`PersonaPort` 的类文档那一段）：
//    段序**十段**（`segments`）× 那张表**十一行**（`rows`）—— 表里有、段序里没有的那两行
//    走 `unplaced()`，而**「示例对话」是十段里的第 9 段、今天一份文件都没有** ⇒
// 它要在上面显形（"这一段还没有文件"），**不许**从十段变成九段 。
//
// ⚠️ **零框架**：全是 `document.createElement` ＋ `textContent`（没有 React ／ 没有组件树）。
// **一个 `innerHTML` 都没有**（与壳那一层同一条规矩）：那几段正文是从盘上读来的，
//      而"盘上来的东西"永远不当标记用。
// ⚠️ **不发 `/state` ／ `/events` ／ `/say`**：本页只走那两条人设路（`GET` ／ `POST /persona`）。
// ⚠️ **时钟可注入**：那串时间戳由**这一页**挑，而"怎么读现在"从 `options.now` 递进来
//    —— 本模块里 `new Date()` 只出现在**缺省那一格**（测试递一个替身，就不碰墙上时钟）。

import type { Chrome, Panel } from './shell';
import { soonItems } from './pages';
import { personaMemoryTail } from './persona-memory';

/** **写那条路的边界**（同一个意思在 `telyincore/web.py::PERSONA_BOUNDARY_LINE` 上）。
 *
 *  ⚠️⚠️ 它说的是这一条口子**多出来的那一条**：改的是**人格那几段本身** ——
 *     「只听回环」照旧**不等于**「已鉴权」，而**这一台机器上的谁都改得动它**。
 * 收得比 `POST /say` 更紧的那一处（`Origin` ／ 令牌）**本刀刻意不做** ⇒ 别把
 * "只听回环"读成"已经够紧了" 。
 * ⚠️ **它是一行纯文本**（`textContent`，这一页不认标记）： 那些 `**` 会**原样显示**
 *     在页上（本刀在真浏览器上量到过那一处）⇒ 页面上给人看的那几句里一个都不用。 */
export const BOUNDARY = '写入那条路：这一页能改她是谁。边界照旧是「只听回环」'
  + '—— 那不等于已鉴权：这一台机器上的谁都改得动它。';

/** **那一句边界的短版**（挂在"写进去了"那一句后面 —— 抬头里那句长的太占地方）。 */
const BOUNDARY_LINE_SHORT = '写入那条路：这一页能改她是谁 —— 「只听回环」不等于已鉴权。';

/** ⚠️⚠️ **这里从前有一句 `export const NO_FILE_LINE = '这一段还没有文件'`** ——
 * **2026-09-30 作者又看一眼，把那句话本身圈掉**：「关于这个，也去掉。」
 *
 *  ⚠️ **它当时在页上出现两处**（同一句话）：正文那一列那一格 ／ 右列那一格
 * ⇒ **两处一起走**（他圈的是那一句话，不是其中一处）。
 * **于是"这一段有没有文件"这件事在页上不再显形** —— 前面几刀先去掉那张卡片、
 *     再去掉那行路径、再去掉那 6px 的灰点，这一句是**最后一个记号** ⇒ 走完之后，
 *     没有文件的那一段在页上**就是一个空块**（旗子 ＋ 那条线 ＋ 标题 ＋ 空的正文）。
 * 那正是作者要的： **不许再往那一格补一个记号回来** 。
 *  ⚠️ **这个常量连同它那一行一起删了**（不是留着不用 —— 留着就是一句不再画出去的话，
 * 将来有人会拿它当"页上还在"的证据）。 "哪些块有文件"照旧记在 `presenceMark()`
 *     那两个属性上（`data-file` ／ `data-files`：机器读得到、页上不显形）。 */

/** **那一条导航**（本刀加的）—— 这一页很长（十段 ＋ 两块，实测约七个屏幕高）。
 *
 *  ⚠️⚠️ **参考那一页没有这一件**（本轮核过：它左列那面旗子 `cursor: auto`、没有 `href`
 *     ／ `onclick`、也不是 `button`；全页零 `toc` ／ `anchor` ／ `jump`；`position: sticky`
 * ／ `fixed` 只有它的抬头与 CodeMirror 自己那两个）⇒ 这一条**是我们自己定的**，
 * 不是"参考已答、照它做"。 依据是作者那一句"因为这个页面很长，所以做一个导航"。
 *  ⚠️ **它不抢那三列的位次**：粘在那条正文列里、**在抬头之下**（那三个格子是什么宽
 *     一个像素都不动 —— 判断那一处形状读的是**这一段自己的**两列，不是页面坐标）。
 * **它说的两件事都从页上**那一处**读**（不另写一份清单）：
 *     · 哪一块**有未保存的改动** ⇒ 读那一段自己的 `data-dirty`（`markDirty()` 那一处写的）；
 *     · 哪一块**只读** ⇒ 读它自己的 `data-readonly`。
 * 少一处、就少一件"页上有、导航里没有"的事 —— 长页里那正是要找的东西。
 *
 * **2026-09-30 作者再看一眼之后：这一条上没有名字了**（`NAV_LABEL` 整个删掉）——
 *     他把这一条裁下来给的那句话是「做的太突兀了，没有和 webui 的设计风格融合进去」
 * ＋「移除第一页这个标题头。没用。」 那一行开头的「这一页」**就是**他指的那个
 *     标题头：一条**页内**的导航把自己说成"这一页"，而翻到这一页的人早就知道自己在
 *     哪一页上 —— 那三个字一个信息都不带。
 *     ⚠️ **形状上的账在 `style.css` 的 `.persona-nav` 那一条上**（那一排改成了**这一页
 *     自己的**旗子：直角 ＋ 一枚箭头尖，坐在那条 2px 的薄荷线上 —— 与左列那十面同形状）。 */

/** 那一串时间戳的**形状**（与 `telyincore/web.py::_STAMP` 同一条）。 */
const A_STAMP = /^\d{8}-\d{6}$/;

/** 这一页要的那两条路（ 与 `web.py` 那两条**同一条**：`GET` 读 ／ `POST` 保存）。 */
const REPORT_ROUTE = '/persona';
const SAVE_ROUTE = '/persona';

/** 保存那一版的缺省那一格（与 `web.py::DEFAULT_VERSION_SLOT` 同一个词）。 */
const DEFAULT_SLOT = 'edit';

/** **同一秒撞名**那一条的三个记号 —— 服务那侧那三句异常原文里各有一个。
 *
 *  ⚠️⚠️ **为什么按记号认、而不按原文相等**：那是 `web.py::_accept_a_save` 里
 * `str(exc)` 交回来的**异常原文**（ 那三句原话本刀是从**实物**上跑出来的，
 *     不是照名字猜的：`那一版已经在盘上了：人设-<时间戳>.md —— 同一秒里的第二次保存
 *     要换一格 slot`），而**页上不许把它端出来**（Task 2 §九④ 点名的正是这一件）。
 *     ⇒ 认出记号 ⇒ 换成 `COLLISION_LINE`（人话 ＋ 说清下一步）。
 *  ⚠️ **那三个记号都是"撞名"这一件事自己的词**（头一句 ／ 尾一句 ／ 那个异常名）——
 *     名字越界那种 400 里一个都找不到，于是它照实报（那是**另一件事**）。 */
const COLLISION_MARKS: readonly string[] = ['那一版已经在盘上了', '同一秒里的第二次保存',
                                            'FileExistsError'];

/** 撞名之后**那句人话**（说清"怎么走"：等一秒，或者换一格）。
 *
 *  ⚠️ **纯文本**（见 `BOUNDARY` 上面那一段）：那一对 `**` 在页上就是两个星号。 */
export const COLLISION_LINE = '这一版没写成：同一秒里已经存过一版了 —— 等一秒再按一次「保存」，'
  + '或者换一格版本名（slot）再存。盘上原来那一版一个字都没动。';

/** **"这一版留下了没有"的三个答案** —— 页上各说各的话（不合成一个"出错了"）。 */
export type SaveOutcome =
  | { readonly kind: 'saved'; readonly bytes: number; readonly version: string;
      readonly history: string | null; readonly left: boolean }
  | { readonly kind: 'conflict'; readonly line: string }
  | { readonly kind: 'collision'; readonly line: string }
  | { readonly kind: 'refused'; readonly line: string };

/** 「每一格都拿得走」那一份：一次 `GET /persona` 之后页上要的四样。
 *
 *  ⚠️ 只列**页真的读**的那几格 —— 多列一格就多一处"它以后能不能变"的牵扯。 */
export interface PersonaReport {
  readonly persona_scope?: string;
  readonly order: readonly string[];
  readonly homes: readonly string[];
  readonly missing: readonly string[];
  /** **本刀核过一次**：这一格照旧是一串**名字**（T1 的形状，没动）——
   *  那两块要的文件名与"能不能改"在 `rows` 里（同一份 `report()`），`label` 是配对的键。 */
  readonly unplaced: readonly string[];
  readonly segments: readonly PersonaSegment[];
  /** 表里的每一行（`label` ／ `files` ／ `file_infos`）—— **第二张清单要的信息在这儿**。 */
  readonly rows?: readonly PersonaRow[];
}

/** 表里的**一行**（本刀要的那几格 —— 键名照服务那一份）。
 *
 * **本波加的那一格：`file_infos[].body`** —— **服务一直就在交它**
 *     （`web.py::file_info()` 的 `info["body"] = text`，本波在真产物上核过：
 *     `GET /persona` 的 `rows[].file_infos[].body` 就是那一份的全文），
 *     只差这一份类型**没有列它** ⇒ 页那一侧读不到。
 *  ⚠️⚠️ **那两块要的正身就是它**：作者要的是"**与那十段同一条形状**" —— 旗子 ／
 *     顶上那条 2px 的薄荷 ／ **正文**（一份文件的那几行 ＋ 行号那一列）。
 * 而正文那一列在页上是**从这一格摊开的**（`besideLines()`）：没有它，
 *     那两块就只剩旗子与那条线，正文那一格**永远是空的**。
 * ⚠️ **`exists` 照收**（它与 `body` 在同一处一行交回来）： 一声不响地加一格
 *     比"这一页真的读了它"更糟 —— 那正是 `02:127` 那条要防的（面板读的值 ＝ 实际生效的值）。
 *  ⚠️ **`body` 是 `null` 的时候照旧画那一句"这一份还是占位"**：那是"这一份读不出来"
 *     那一档（服务那一侧的三段账写在 `file_info()` 上），不是"没有正文这一列"。
 * **`placeholder` 也是读的一格**（本波起）： 它是"**抬头那条注释里写着「占位」**"
 *     —— 那一份**整份都不是正文**（是一张"东西在别处"的施工说明）⇒
 *     `besideLines()` 照它**丢掉那一份的行**。⚠️ 那一格**从前只用来多说一句话**
 *     （`besideRow()` 末尾那一句人话），本波起它同时决定"那一份的正文摊不摊"。 */
export interface PersonaRow {
  readonly label: string;
  readonly files?: readonly string[];
  readonly file_infos?: ReadonlyArray<{ readonly name?: string; readonly writable?: boolean;
                                        readonly placeholder?: boolean;
                                        readonly exists?: boolean;
                                        readonly body?: string | null;
                                        readonly shape?: readonly PersonaShape[] }>;
}

/** **一件承重的形状**（服务那一份 `persona_shape()` 交回来的那一格 —— 本波那条 Critical）。
 *
 *  ⚠️⚠️ **页这一侧只画**：哪几行锁，**全从这一格来**
 *     —— 页上不另写一份判据（"认 `▎` ／ `#` ／ `**`"这件事一个字符都不在这一页上）。
 * 两份清单一定会漂：服务加一类形状、而页上那份没跟上 ⇒ 页上看着能改、
 *     一按保存就是 409 —— 那正是本仓最忌的那种失效。
 * ⚠️ `start` ／ `end` 是**整份正文里的字符偏移**（半开区间）—— **不是行内偏移**：
 *     抬头那条注释**可以跨好几行**，行内偏移表达不了它。
 *  ⚠️⚠️ **服务那一份里还有 `why` ／ `cite` 两格**（那一行人话 ＋ 那一处出处）——
 * **`why` 今天照旧一个字都不读**（作者 2026-09-30 指着页上那两句说
 *     "这种解释不需要告诉我。把这类都删了"）⇒ 它不进这一份类型，页上一个字也不摆。
 * **`cite` 这一刀重新读回来了**（2026-09-30 晚，作者："这些字体颜色不对，
 *     而且还是没法编辑"）—— ⚠️ **读它不是要摆出来**（那一格照旧一个字都不上页），
 *     而是**它同时是类名**：服务那一侧 `_a_shape_key()` 写着"`cite` 同时是类名
 *     （比较时拿它当键）"，而**五类里只有 `**` 那一类，作者是能改的、也是该改的**
 *     （同一条 docstring：键是"这一行有几个 `**`"，把里头那句话改写掉照旧算数 ——
 *     "那正是编辑该做的事，不该拦"）。⇒ 页上要按类分档，就得知道**是哪一类**
 * （见 `SHAPE_CLASS_EMPHASIS` ／ `lockedLines()`）。 那两格进不进这一份类型，
 *     判的是"页上摆不摆"；`cite` 从来不是拿来摆的。 */
export interface PersonaShape {
  readonly line: number;
  readonly start: number;
  readonly end: number;
  /** 那一类形状的**类名**（服务那一侧 `_CITE_*` 那五条 —— 比较时拿它当键）。 */
  readonly cite?: string;
}

/** 一段（**服务那一份 `segments()` 的形状** —— 归属没有出处时是 `null`）。
 *
 * **本刀加的那一格**（那一条**硬轴**：能改 ／ 不能改）：`writable`
 * —— 判断在**服务那一侧**（`web.py::may_be_written()`），页这一侧**只画**
 *     （`02:127`：面板读的值＝实际生效的值，**由同一个函数算** ⇒ 页上不许另写一份清单）。
 * ⚠️ 服务那一份里还有一个 `why_read_only`（"为什么不能改"那一句）—— **页上不读它**
 *     （作者 2026-09-30：这种解释不要）、于是它也不在这一份类型里。 */
export interface PersonaSegment {
  readonly name: string;
  readonly owner: string | null;
  readonly owner_source?: string | null;
  readonly file?: string | null;
  readonly body?: string | null;
  readonly revision?: string | null;
  readonly missing?: boolean;
  readonly files?: ReadonlyArray<{ readonly name: string; readonly placeholder?: boolean;
                                   readonly shape?: readonly PersonaShape[];
                                   readonly sections?: ReadonlyArray<{ id: string; body: string }>;
                                    readonly revision?: string | null }>;
  readonly writable?: boolean;
}

/** 一次保存要递过去的那一份（ 与 `web.py::_accept_a_save` 读的那几格一致）。 */
export interface SaveAsk {
  readonly name: string;
  readonly text: string;
  readonly stamp: string;
  readonly slot: string;
  readonly expect: string;
  readonly section?: string;
}

function editableSegments(report: PersonaReport): PersonaSegment[] {
  return (report.segments ?? []).flatMap(segment => {
    const file = segment.files?.find(item => item.name === segment.file);
    if (file?.sections !== undefined) {
      return file.sections.map(section => {
        const key = file.name + '#' + section.id;
        const heading = /^#{1,6}\s+([^\r\n]+)/.exec(section.body)?.[1] ?? section.id;
        return { ...segment, name: heading, file: key, body: section.body,
          files: [{ name: key, revision: file.revision }] };
      });
    }
    if (report.persona_scope !== 'user-authored' || (segment.file ?? '').startsWith('你定的/')
        || segment.files?.some(item => item.name.startsWith('你定的/'))) return [segment];
    return [];
  });
}

/** 这一页要的几样**从外面递进来**的东西（ 一个都不从里面现取 —— 测试才判得动）。 */
export interface PersonaDeps {
  /** 读一次现在（交回 `YYYYMMDD-HHMMSS`）—— 缺省那一格才碰 `new Date()`。 */
  readonly now: () => string;
  /** 那两条路怎么走（缺省 `fetch`；测试递一个替身，就不碰真盘）。 */
  readonly send: (url: string, init?: RequestInit) => Promise<Response>;
}

/** **那串时间戳怎么长**（`YYYYMMDD-HHMMSS`）—— 本地时间六个格子，两位一位不少。
 *
 * ⚠️ 形状与服务那侧那条 `_STAMP` 一致； 而"哪一秒"是**这一页**挑的
 *     （那个服务一次都不读时钟 —— 它的 `POST` 缺了 `stamp` 就 400）。 */
export function stampOf(when: Date): string {
  const pad = (value: number): string => String(value).padStart(2, '0');
  return `${when.getFullYear()}${pad(when.getMonth() + 1)}${pad(when.getDate())}`
    + `-${pad(when.getHours())}${pad(when.getMinutes())}${pad(when.getSeconds())}`;
}

/** **那一串时间戳说得出口吗**（页这一侧先看一眼，省一次注定 400 的往返）。 */
export function isAStamp(stamp: unknown): boolean {
  return typeof stamp === 'string' && A_STAMP.test(stamp);
}

/** **渲染后大约几个 token** —— 一个**明说的估计**，不是 tokenizer。
 *
 *  ⚠️⚠️ **为什么页上那个数必须先说清它不是"数出来的"**：这一页手上没有 tokenizer
 *     （那要模型那一侧的口径），而"渲染后约 M tokens"那个数**必须跟着你改的字动**
 * —— 一个写死的数就是一张画出来的收据 。
 *  ⚠️ 两档粗分：CJK 一个字约一个 token，其余（拉丁 ／ 空白 ／ 标点）约四分之一
 *     ⇒ **它只保证一件事**：字多了这个数就大（页上写"约"，理由写在这儿）。 */
export function tokenEstimate(text: string): number {
  let wide = 0;
  let rest = 0;
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0;
    const cjk = (code >= 0x2e80 && code <= 0x9fff) || (code >= 0xac00 && code <= 0xd7a3)
      || (code >= 0xf900 && code <= 0xfaff) || (code >= 0xff00 && code <= 0xff60);
    if (cjk) wide += 1;
    else rest += 1;
  }
  return wide + Math.ceil(rest / 4);
}

/** **那一段多长**（正文按 `\n` 切成几行）—— 行号那一列就是它。
 *
 *  ⚠️ CRLF ／ CR 都先归一成 LF：行号那一列是**人读的那一份**，不是字节那一份
 * （ 而**写回去**的是屏幕上那几行的 `\n` 拼接 —— 见 `editorLine()` 里那一句）。 */
export function linesOf(text: string): string[] {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
}

/** **撞名了没有**（那三句异常原文 —— 见 `COLLISION_MARKS`）。 */
export function isCollision(text: string): boolean {
  return COLLISION_MARKS.some((mark) => text.includes(mark));
}

/** **那一次保存的答案**（状态码 ＋ 服务交回的那一段）⇒ 页上要说的话。
 *
 *  ⚠️⚠️ **它是这一页最要紧的一处人话**：同一秒里的第二次保存，服务那侧**当场抛**
 *     （它不许盖掉前一版 —— 见 `web.py::save_text`），而**页上不许把那句异常原文端出来**。
 *     ⇒ 认得出就换成 `COLLISION_LINE`（说清"等一秒"或者"换一格"）；
 *        认不出（名字越界 ／ JSON 不对 ……）就照实说，但照旧是人话。 */
export function outcomeOf(status: number, body: string): SaveOutcome {
  if (status >= 200 && status < 300) {
    return { kind: 'saved', bytes: 0, version: '', history: null, left: false };
  }
  if (status === 409) {
    try {
      const got = JSON.parse(body);
      if (got.error === 'persona_revision_conflict') {
        return { kind: 'conflict', line: '版本冲突：草稿已保留。读取新版手动合并，或确认重载后放弃草稿。' };
      }
    } catch { /* Shape refusals are human-readable text, not revision conflicts. */ }
  }
  if (status === 400 && isCollision(body)) {
    return { kind: 'collision', line: COLLISION_LINE };
  }
  return { kind: 'refused', line: `这一版没写成（服务回 ${status}）：${body.trim()}` };
}

/** 保存那一版**留在行尾那一格里的提示**（参考那一页在光标那一行后面挂一句
 * "按 Ctrl+S 保存"）—— **它是空的时候不占位**（`textContent === ''` ⇒ CSS 里不画底）。
 *
 *  ⚠️⚠️ **它的名字不许叫 `saveHint`**：`personaPage()` 里已经有一个**同名的局部量**
 *     （抬头那句"未保存的改动……"），而那个名字在下面**先把模块级这个函数遮掉了** ⇒
 *     `hintNode("")` 那一处会去"调用"一个字符串（`TypeError`），而它**只在运行时报**出来
 * —— 本刀在产物那一趟上量到过那一次：十段一段都没画出来，而测试只看到"页不响应"。 */
function hintNode(text: string): HTMLElement {
  const hint = document.createElement('span');
  hint.className = 'persona-savehint';
  hint.textContent = text;
  return hint;
}

/** **那两块在页上叫什么**（作者定的 —— **2026-09-30 晚他把第一次那一条推翻了**）。
 *
 *  ⚠️⚠️ **改的是"标题"，不是"文件名"**：那张表里那两个名字（`她写的那两份` ／
 *     `世界认知`）是**表的说法**，不是给人看的（作者原话："取名通俗易懂一点"）。
 * 而**文件原名照旧在右列那一格**（`她写的/自述.md` 那些）——
 *     ⇒ 页上给的是**两个名字**：一个是给人读的，一个是**真的那一条路径**。
 * **本波那一刀改的就是左边这一个**：上一版它叫「我」（出处是那一份自己写的
 *     原话 `12:31`："这份是**我自己**的，不是人设"）—— 作者指着页上那一面旗说：
 *
 *     > 「**把那个 我 改成 自述。感觉没有必要单独改名，该是什么就是什么就行。**」
 *
 * ⇒ **块的名字 ＝ 它那一份文件的名字**：「我」是**另起**的名字，作废。
 *       ⚠️ **写进去的是文件名的词干、不是那条路径**：页那一侧**一个 `.md` 都不许出现**
 *       （`…_the_read_only_rule_is_not_written_a_second_time…` 那条判据钉着它）——
 *       "真的是哪一份"照旧只在**服务那一份**里（`file_infos[].name`）。
 *     ⚠️ **这一个名字今天两处用**（**旗子**那一面 ＋ **导航**那一项）：`nameOfRow()`
 * 也读这张表 ⇒ 两处同一个说法。 上一版正是两个说法（旗子「我」／导航那一项
 * 走 `data-beside` ＝ 表的说法）⇒ **同一块在页上两个名字** 。
 *  ⚠️ **第二个照原名**（作者裁的）：`世界认知` 四个字、不难懂，而"她的世界"会与
 *     十段里那一段「世界」（`你定的/世界.md`：她所在的那个世界）挨着看混起来。
 *  ⚠️ **不在这张表里的行** ⇒ 它的标题就是它自己那一格的名字（**一个都不许丢**）。 */
const THE_READABLE_TITLE: Record<string, string> = {
  '她写的那两份': '自述',
  '世界认知': '世界认知',
};

/** **那两块正文那一列的来源**（本波加的 —— 它们今天**有正文**了）。
 *
 *  ⚠️⚠️ **为什么要有它**：作者那两句「**它们应该长得和别的块一样** —— 旗子 ／ 顶上
 * 那条 2px 的薄荷 ／ **正文**（那个文件的那几行，带行号那一列）」⇒ 正文那一列
 * 得**从服务交回来的那几份文件里摊开**。 而它是**同一支笔**画出来的：
 *     `fixedLine()`（"一行正文，而它是死的" —— 一个 `contenteditable` 都没有）。
 *  ⚠️⚠️ **一个 `Draft`（**不是**每份文件一个编辑面）**：这两块**一个编辑面都没有**
 * （作者定的那一条：它们只读，不要编辑面）⇒ 那几份文件的正文**连成一份文件**
 *     摆出来 —— 行号那一列于是**一条**、从 1 数到底（`她写的那两份` 底下是**两条**
 *     路径 ⇒ 那个数不许被画成一个点就丢掉）。
 *  ⚠️ **那几份的次序照服务交回来的那一个**（`files[]` 的次序就是表里的次序）——
 *     页上**不重排**、不按名字排（另排一次就是第三份次序）。
 *  ⚠️ **读不到正文的那几份跳过**（`body` 是 `null` ／ 缺这一格）：它们**没有消失**
 *     —— "这一份读不出来"这件事在 `file_infos[]` 上照旧读得到。
 * **本波新加的那一条**（作者那一句：「**自述这个文件不是空白的吗。为什么现在看
 *     有这么多字段。**」）：**抬头写着「占位」的那一份，它那几行不是正文** ——
 *     那是一张"东西在别处"的施工说明（`<!-- 占位 · 这里不是正文 -->` ＋ 一张
 * "正文在哪"的表 ＋ 一句"不要重建、不要复制、不要覆盖"）⇒ **把它摊出来，
 *     就是这一页在撒谎**：那几句话对"某一次搬文件"是真的，而当"她的自述"读
 *     **一个字都不作数**。
 * ⇒ 判据**不由这一页自己认**（页那一侧一个字符串都不认）：服务那一侧
 *     一直在报 `placeholder`（`file_info()`：它读的是**抬头那条注释里有没有「占位」**）
 * ⇒ 页这一侧**照它跳过那一份的行**（ 而它**不再多说那一句话** —— 见下面那一条）。
 *     ⚠️ **跳过的是那一份的正文，不是那一块**：一行都不剩的块照旧立着（旗子 ／
 *     顶上那条 2px），左列 ／ 右栏那几格一个数都不动；
 * 而**同一行里另有真正文的照旧摊开**（跳过的是那一份的行，不是整行）。
 *  ⚠️ **一份正文都没有** ⇒ 交回 `null`（不是空 `Draft`）：正文那一列于是**一行都不摆**
 *     —— 那一块只剩旗子 ／ 顶上那条 2px。
 *     ⚠️ **那一枚「这一块人不能改」的牌子本波走了**（作者 2026-10-01：不可输入光标
 *     状态不必再提醒一句）⇒ 这两行里那两处点名的东西**只剩旗子与那条线**。
 * **本波（作者 2026-10-01）**：「**就是空着，源文件是空的那就该是空的。**」
 *     ⇒ 这一格**从前**多说的那一句"这一份还是占位"连同它那一档样式一起走掉了
 *     （"页上摆什么 ＝ 那一份文件里有什么"，**仅此而已**）。
 *  ⚠️⚠️ **本波新加的那一条**：`body` 是**空串**（**盘上那一份就是 0 字节**）⇒ 那几行
 * **一行都不算**。 为什么非有它不可：`linesOf('')` 交回来的是 `['']`（**一格空串**，
 *     不是空数组 —— `split('\n')` 的形状），摊出来就是**一行带行号的空行** ⇒
 * 那一份明明 0 字节，页上却**凭空多出一行**（行号那一列还写着 `1`）。
 * 判的是**空串**、不是"去空白之后为空"：一个真写了换行的文件（`"\n"` ＝ 1 字节）
 *     照旧按它自己的形状摊开 —— 页这一侧**不替那一份文件做减法**。
 *  ⚠️ **`name` 取第一份的文件名**（与服务那一侧 `segments()` 的 `file` 同一支笔：
 *     "一段两份文件时，**第一份说了算**"）；一份名字都没有 ⇒ 空串（它只当草稿的键，
 *     这两块的草稿**一次都不写盘** —— 它们没有编辑面、也没有保存那一趟）。 */
function besideLines(info: PersonaRow | undefined): Draft | null {
  const infos = info?.file_infos ?? [];
  const name = infos[0]?.name ?? '';
  const lines: string[] = [];
  for (const one of infos) {
    // **那一份抬头写着"占位"** ⇒ 它那几行一个字都不摊（见上面这一段）。
    if (one.placeholder === true) continue;
    if (one.body === null || one.body === undefined) continue;
    // **那一份就是 0 字节** ⇒ 一行都没有（见上面那一条 —— `['']` 不是"没有行"）。
    if (one.body === '') continue;
    // **开头那一块 `<!-- … -->` 页上不画**（本波那条通用规矩 —— 见
    // `SHAPE_CLASS_COMMENT`）： **一块一块地判**（`file_infos[]` 里每一份有它
    //   自己那一格 `shape`）—— 一块里两份文件，藏的是**各自开头那一条**。
    const hidden = hiddenIn(one.shape, one.body);
    linesOf(one.body).forEach((text, index) => {
      if (hidden.has(index)) return;
      lines.push(text);
    });
  }
  return lines.length === 0 ? null : { name, lines };
}

/** 一段一格草稿（按**文件**存 —— 段名与文件名不是一回事）。 */
interface Draft { readonly name: string; readonly lines: string[];
  revision?: string | null; base?: string; generation?: number }

function draftOf(segment: PersonaSegment): Draft | null {
  if (segment.file === null || segment.file === undefined) return null;
  return { name: segment.file, lines: linesOf(segment.body ?? ''),
    revision: segment.revision ?? segment.files?.find(one => one.name === segment.file)?.revision,
    base: segment.body ?? '', generation: 0 };
}

/** 一个元素（一处建法，与壳那一层同一支笔）。 */
function node<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string,
  text?: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (cls !== undefined) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

/** **从"谁拿着焦点"找回它所在的那一个编辑面**（本波：一块一个面，见 `attachSurface()`）。
 *
 *  ⚠️⚠️ **两条路都要认**（这一页的两个运行面各差一样）：
 *     · **真浏览器**：拿焦点的**就是面自己**（`.persona-body`）⇒ 第一步就命中；
 *     · **测试那个 DOM 替身**：焦点可能落在**面里面的一格正文**上 ⇒ 得往上走。
 *       而 `parentElement`（真 DOM）与 `parent`（替身）**各只有一样** ⇒ 两条都写在这里，
 *       "这一格属于哪个面"就只有一个答案（少一条，`flushFocused()` 在那一趟上就找不到它）。
 *  ⚠️ 往上走有个 `8` 跳的闸：一个环形的 parent 链不许把这一页挂死。
 */
function surfaceOf(el: HTMLElement | null): HTMLElement | null {
  let one = el as unknown as
    { classList?: Pick<DOMTokenList, "contains">; parentElement?: unknown;
      parent?: unknown } | null;
  for (let hops = 0; one !== null && one !== undefined && hops < 8; hops += 1) {
    if (one.classList !== undefined && one.classList.contains('persona-body')) {
      return one as unknown as HTMLElement;
    }
    one = (one.parentElement ?? one.parent ?? null) as typeof one;
  }
  return null;
}

/** 归属那一格的**颜色档**（ 四个家里三个各有自己的档，读不到的那一个另算）。 */
function ownerClass(owner: string | null): string {
  if (owner === null) return 'persona-owner none';
  if (owner === '跟框架走') return 'persona-owner framework';
  if (owner.startsWith('归 ')) return 'persona-owner world';
  return 'persona-owner bot';
}

/** **右列那一格 ＝ 落点记号**（作者 2026-09-30 前后三次说这一处）。
 *
 *  ⚠️⚠️ **上一刀只删了盒子，字还在**：作者原话是
 *     「关于卡片，**你没有把字也一起删掉**。`你定的/存在方式.md`，这个字还留在页面上」
 * ⇒ **那一行路径整个去掉**（不是只去掉它的底 ／ 圆角 ／ 边框）。
 * **第二刀那枚点也删掉了** —— 他圈着它说的是
 * 「还有这个灰色圆球是什么，也删掉。」⇒ 于是这一格只剩**一句话**。
 * **本波（2026-09-30 第三次）连那一句话也删掉了** —— 他把那一句圈起来说
 * 「关于这个，也去掉。」⇒ **两个分支今天都空着**：
 *       · **没有文件** ⇒ 这一格**空着**；· **有文件** ⇒ 这一格**也空着**。
 *  ⚠️⚠️ **那两个属性照旧挂着**（本波一行都没动它们）：作者划掉的是**画出来的**东西，
 *     不是"机器读得到的记账"—— `data-file` 是那两种状态的正身，`data-files` 记的是
 *     **份数**（第二张清单里那一块（`自述`）底下有**两条**路径 ⇒ 那个数得有人记得住，
 * 那条判据读的就是它）。 它是**给判据读的**，不是"把状态藏进属性里"。
 *  ⚠️⚠️ **"两种状态不许删成一样"那一笔账今天结清了**（它现在**就是**一样的）：
 *     前三刀是一层层往下剥（盒子 → 字 → 点 → 那一句话），而**每一刀都是同一个方向** ——
 * 到最后页上**不再有任何记号说"这一段有没有文件"**，那是作者要的。
 * 于是 `style.css` 里 `.persona-presence` 那一条下面那段账**重写过**：
 *     它从前写着"区别挪成了少一句话"，今天那句话没有了。
 * ⚠️ **不许自己再补一个记号回来** —— 那是他第四次要划掉的东西。
 *  ⚠️ **两个分支还是同一个行盒**（见 `style.css` 那一条）⇒ 右列的高度一个像素都不变。 */
function presenceMark(count: number): HTMLElement {
  const hasFile = count > 0;
  const mark = node('span', 'persona-presence');
  mark.dataset.file = hasFile ? 'yes' : 'no';
  mark.dataset.files = String(count);
  return mark;
}

/** **这一段归谁**（`null` ⇒ 树里**没有**这一段的归属）——
 *
 *  ⚠️ **它判的是"这一段的字归不归你写"**（不是猜的文件的属性）：
 *     · **读不到归属**的那一段（T1 那条 FINDING）⇒ 照"不归你"那一档画 ——
 * **不知道**就不许当成"是你的"（这是那一侧的错向：宁可显得谨慎）；
 * · **世界认知**那一行**不在那十段里**（`unplaced`）⇒ 它没有这一段，
 *       于是它那一段的文件照旧按它自己写的那份声明走（今天读不到 ⇒ 也是"不归你"）。
 *  ⚠️ 它**只影响画法**：那十段**照旧全都能改**（设计稿 §四：十段都能改，而页上标出归属）。
 *  ⚠️⚠️ **它与"能不能改"是两条轴**（本刀那条硬轴）：这一条是**软**的（"这是谁的"），
 * 而那一条是**硬**的（"动了会坏"）—— 页上**不许把它们混成一种灰**
 *     （见 `may_write()`：那一条是**服务**给的，页这一侧只画）。 */
function ownedByTheAuthor(owner: string | null): boolean {
  return owner === '你定的';
}

/** **"人"能不能改这一段** —— 判断**不在这一页**，而在服务交回来的那一格。
 *
 *  ⚠️⚠️ **这就是 `02:127` 那条的用法**（"面板读的值＝实际生效的值，**由同一个函数算**"）：
 *     页上**一次都不猜**（不问"它住哪一层"、不看文件名）—— 服务说 `writable: false`，
 * 这一页就画成只读。 于是在别处加一条只读规矩时，**这一页一个字都不用改**。
 *  ⚠️ **那一格缺了的时候**（服务那一侧的旧版 ／ 替身没有它）⇒ 按"**能改**"画：
 *     一条"读不到就锁上"的页会把整页变成只读（那是更坏的一种失效）。 */
function mayWrite(segment: PersonaSegment): boolean {
  return segment.writable !== false;
}

/** **这一段里哪几行是承重的形状**（交回的就是**那几个行号**）——
 * **从服务那一份 `shape` 来**（`GET /persona` 的 `files[].shape`），页上**不另写判据**。
 *
 *  ⚠️⚠️ **本波那条 Critical 的第二半**：一份文件里那几处**承重的形状**
 *     （抬头那条注释 ／ `#+ ` 标题 ／ `▎` 那一节 ／ `|` 那一行 ／ 一对一对的 `**`）
 *     删掉 ／ 拆散一处，这一份的功能就没了（作者原话：小则这一段提示词直接失效，
 * 大则整个文件的功能失效）。 而**哪几行是那几处**这件事由服务算（`persona_shape()`）
 *     —— 页只把行号摊开、把那几行锁住。
 *
 *  ⚠️⚠️ **页上不摆那一句 `why`**（作者 2026-09-30 指着页上那两句说"这种解释不需要
 *     告诉我。把这类都删了"）⇒ 这一函数今天只答一个问题——**哪几行锁**；
 *     服务交回来的 `why` **这一页一个字都不读**（而那一条判据照旧
 *     全在服务那一侧：`save_text()` 那道 409 一个字没动）。
 *  ⚠️ **跨行的那一件**（抬头那条注释）在服务那一份里是**一条**记录、落在它**开头那一行**：
 *     这里拿 `body.slice(start, end)` 数一下它跨了几行，把那几行**一起锁住**
 *     —— 只锁第一行的话，底下那几行照旧可以删。
 * ⚠️ **一行可以同时落在两类上**（两个行号并成一条 —— 锁一次就够）； 而**读不到 `shape`**
 *     （旧版服务 ／ 替身没有那一格）⇒ 空表 ⇒ **一行都不锁**：那是"读不到就不锁"那一侧，
 *     与 `mayWrite()` 同一条错向（宁可显得宽松，也不要把整页锁死）。
 *
 * **2026-09-30 晚：`**` 那一类不再锁**（作者指着 `关系` 那一块的正文说
 *     「**这些字体颜色不对，而且还是没法编辑**」）。
 *
 *  ⚠️⚠️ **根在哪（量出来的，不是猜的）**：那一类（`_CITE_EMPHASIS`）锁的是**整行**，
 *     而**服务那一侧只把"星号成对"当承重** —— `web.py::_a_shape_key()` 逐字写着：
 *
 * > ⑤ `**` 那一类：键是**那一行有几个**…… 因为强调那一格**只有"成对"这件事
 *     > 是承重的**：把里头那句话改写掉，星号照旧在、这一行的语气照旧归你
 * > —— **那正是编辑该做的事** 不该拦。
 *
 *     也就是说：**页上把整行封住，封掉的正是服务说"该由作者改"的那件事**。
 *     而在真产物上量到的后果是：`关系` 那一份 **39 行里 17 行**被封
 *     （`**说话人**在你眼里是什么位置…` ／ `**最特别的人**…` ／ `**亲近**…` 全是），
 *     那一块有一半的字**看上去是灰的、点了也打不进字**——正是他这一句。
 *
 * **于是分档**：`cite` 是**类名**（服务那一侧写着"比较时拿它当键"）⇒ 这一函数
 *     按类判 —— **`**` 那一类不锁**（它是作者要改的正文），**另外四类照旧锁死**
 * （抬头注释 ／ `#+ ` 标题 ／ `▎` 那一节 ／ `|` 那一行）： 那四类的承重件是
 *     **那一段字符本身**（`_a_shape_key()`：键就是 `text[start:end]`）⇒ 一个字都改不得
 *     —— **页上锁住，与服务那道 409 说的正是同一件事**（这类改动今天本来就写不下去）。
 *
 *  ⚠️ **读不到 `cite`**（旧版服务 ／ 替身没那一格）⇒ **照旧锁**（"读不到"那一侧与
 *     `mayWrite()` 相反：这一处宁可保守 —— 分类分不出来时，把保护留着）。
 *  ⚠️ **它不放松任何东西**：那一类的正文照旧整份交给服务，**拆掉一对 `**` 照旧 409**
 *     （`save_text()` 顶上那道闸一个字没动）⇒ 页上不锁 ≠ 页上能毁。
 */
export const SHAPE_CLASS_EMPHASIS = '12:32（写法只有一条纪律：**观测、推测、事实分开写**）';

/** **抬头那条注释那一类的类名**（服务那一侧 `web.py::_CITE_COMMENT`）——
 * 服务交回来的 `shape` 里**第 ① 类**就是它：**文件开头那一块 `<!-- … -->`**
 *  （`persona_shape()`：**一整块一条**，**可能跨好几行** —— `你定的/破甲.md` 就是 8 行）。
 *
 *  ⚠️⚠️ **它是什么**：**造这一份文件时给人看的施工说明／作者契约** ——
 *  「这一份是她每轮都会读到的那一段」「不许出现停用句清单里的词」「跟框架走，
 * 她能读，不能写」那一类。 **那些是造它的时候的规矩，不是她读到的正文**
 *  （作者 2026-10-01：「**这些也是代码侧的注释解释，和正文没有任何关系的，
 *  这些其实也应该隐藏起来。**」）。
 *
 *  ⚠️ **页上照旧不认一个字符串**：判据是**类名**，而类名是**服务算出来的**
 *     （`persona_shape()` 那一格 `cite`）—— 这一页**不认 `<!--`**、也不认 `-->`。
 * 页这一侧只做一件事：**拿 `start` ／ `end` 把那几行算出来，然后不画它们**
 *     （见 `hiddenIn()` ／ `hiddenLines()`）。
 *
 *  ⚠️⚠️ **它照旧是"承重的形状"**（`lockedLines()` 照旧收它）—— 藏的是**画不画**，
 *     不是"它算不算数"：那一格**还在服务那一份的账上**，写那一侧那道闸一个字没动。
 * **而归属那一列照样读它**（服务 `owner_of()` 走的正是这条注释，
 * `owner_source === 'comment'`）⇒ **页上不画它，不影响归属那一格**。 */
export const SHAPE_CLASS_COMMENT =
  'web.py::_lead_comment() → owner_of() ／ 边界.md:1（按部署可覆盖）';

/** **这一份里哪几行是"抬头那条注释"，页上不画**（0 起的**草稿下标**）。
 *
 *  ⚠️ **它算的是"那一块注释盖住了哪几行"**：`one.line` 是**1 起**的起始行，
 *     而那一块**可能跨好几行** ⇒ 盖住的行数 ＝ `body.slice(start, end)` 里的换行数
 * ＋ 1（ 与 `lockedLines()` **同一条算法** —— 两处各写一份就会漂）。
 *  ⚠️ **判的是类名**（见 `SHAPE_CLASS_COMMENT`）：页上不认 `<!--`。
 * ⚠️ **只收第 ① 类**： 只有**开头那一块**是"这一份文件的抬头说明"
 *     —— 正文**中间**的注释可能是正文的一部分，**那种照旧画**
 * （ 本机实测 2026-10-01：那 12 份里**每一处 `<!--` 都在第 1 行**，
 *     中间那种今天**一份都没有** —— 见报告）。
 *  ⚠️ **读不到 `shape`**（旧版服务 ／ 替身没那一格）⇒ 交回**空集**：一个字都不藏
 *     （与 `lockedLines()` 那条"读不到就保守"**相反** —— 那一处不锁会毁文件，
 *     这一处不藏只多画一行注释，两边的代价不一样）。 */
function hiddenIn(shape: readonly PersonaShape[] | undefined,
                  body: string): Set<number> {
  const out = new Set<number>();
  for (const one of shape ?? []) {
    if (one.cite !== SHAPE_CLASS_COMMENT) continue;
    const extra = Array.from(body).slice(one.start, one.end).join('').split('\n').length - 1;
    for (let at = 0; at <= extra; at += 1) out.add(one.line - 1 + at);
  }
  return out;
}

/** **这一段里哪几行是抬头那条注释**（ 照 `segment.files[]` 里**它那一份**的 `shape`）。 */
function hiddenLines(segment: PersonaSegment): Set<number> {
  const file = (segment.files ?? []).find((one) => one.name === segment.file);
  const body = segment.body ?? '';
  // ⚠️⚠️ **2026-10-12（接手那一刀）**：这一处里从前还多着一段（重画那一批 `d084bad`
  //    带进来的）"顺手把可见区的**头尾空行**、以及**连续空行**也一起藏"的逻辑，
  //    它压着一句英文注释（`Keep original draft offsets: the hidden whitespace survives
  //    a save intact.`）。★ 那一段**不是**"抬头那条注释"这一档：
  //    · 藏哪几行这一支**只有一处出处** —— `hiddenIn(file?.shape, body)`，由**服务**算
  //      （`shape` 里第 ① 类，见 `SHAPE_CLASS_COMMENT`）；
  //    · 而正文那一列摆的就是**那一份文件里有的那几行**（空行照摆）⇒ 藏了空行，
  //      画出来的第一行**就不是它真实的行号**了（判据量到 `['5','6','7','8']`
  //      vs 要 `['4'..'9']`；`test_web.py::test_the_leading_comment_is_not_drawn_on_
  //      any_block` 那句"行号照旧是它真实的行号"正是防这个）。
  //    ⇒ 那一段整个收掉：这一支回到"**只藏 `shape` 里点名的那几行**"。
  return hiddenIn(file?.shape, body);
}

function lockedLines(segment: PersonaSegment): Set<number> {
  const out = new Set<number>();
  const file = (segment.files ?? []).find((one) => one.name === segment.file);
  const body = segment.body ?? '';
  for (const one of file?.shape ?? []) {
    // **那一类不锁**（作者要改的就是它 —— 理由整段在上面）：
    //   ⚠️ 判的是**类名**，不是那一行长什么样 —— 页上照旧不认 `**` ／ 不认 `▎`。
    if (one.cite === SHAPE_CLASS_EMPHASIS) continue;
    const extra = Array.from(body).slice(one.start, one.end).join('').split('\n').length - 1;
    for (let at = 0; at <= extra; at += 1) out.add(one.line + at);
  }
  return out;
}

/** 归属那一格的那几个字（ 读不到 ⇒ `—`：空串与"读不到"不是一件事）。 */
function ownerText(owner: string | null): string {
  return owner === null ? '—' : owner;
}

/**
 * **把人设那一页建出来**（ 它就是页表里 `persona` 那一项要的 `build`）。
 *
 * ⚠️ 页表那一处只传 `shell` 一个实参（`page({…}, personaPage)`）⇒ 那两样依赖**全部**有
 * 缺省值； 而测试要一条**不读墙上时钟**、也**不碰真盘**的路 ⇒ 它们从 `options` 递进来。
 */
export function personaPage(shell: Chrome, options: Partial<PersonaDeps> = {}): Panel {
  const now = options.now ?? ((): string => stampOf(new Date()));
  const send = options.send ?? ((url: string, init?: RequestInit): Promise<Response> =>
    fetch(url, init));

  const column = node('div', 'page-persona persona-studio');
  /** **正文那一列**（第二轮改的：**居中** —— 参考那一页"宽度上限 ＋ 左右 auto"）。
   *  ⚠️ `.page-persona` 照旧是那个会滚的块；这一条是它里面**居中那一层**。 */
  const stack = node('div', 'persona-column');
  const head = node('div', 'persona-head');

  // 2026-10-11: compact title and one metadata row; save feedback appears only as needed.
  const title = node('h1', 'persona-title', '人设 · PERSONA');
  const count = node('span', 'persona-count', '0 段 · 渲染后约 0 tokens');
  const saveHint = node('span', 'persona-hint', '修改后保存 · Ctrl+S');
  const saveButton = node('button', 'btn persona-save', '保存');
  saveButton.type = 'button';
  coreSetUIText(saveButton, 'title', corePhrase("保存当前章节，保留其他未保存草稿"));
  const reloadButton = node('button', 'btn ghost persona-reload', '重载');
  reloadButton.type = 'button';
  coreSetUIText(reloadButton, 'title', corePhrase("重新读取已保存的人设"));
  const status = node('div', 'persona-status');
  const statusLine = node('span', 'persona-status-line');
  const receipt = node('details', 'persona-receipt');
  receipt.hidden = true;
  const receiptSummary = node('summary', 'persona-receipt-summary', '详情');
  const receiptText = node('p', 'persona-receipt-text');
  receipt.append(receiptSummary, receiptText);
  status.append(statusLine, receipt);
  status.dataset.state = 'idle';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.hidden = true;
  const bar = node('div', 'persona-bar');
  const meta = node('div', 'persona-meta');
  meta.append(count, saveHint);
  bar.append(meta, status);
  const actions = node('div', 'persona-actions');
  actions.append(saveButton, reloadButton);
  head.append(title, actions, bar);

  /** **那一条导航**（本刀加的）—— 它住在**抬头之下、那十段之上**，`position: sticky`
   *  粘在那条正文列的最上面（`top: 0` —— 这一格是那个滚动容器里的项）。
   *  ⚠️ **它一个数都不自己算**：目标那一块、它有没有未保存的改动，
   *     全从**那一段自己的 DOM 上**读（`refreshNav()`）。
   * ⚠️ **"哪一块只读"它今天不读了**（作者 2026-09-30：*「不用这个只读，没必要。」*）
   *     —— 那一件事在**那一块自己**身上照旧读得到（见 `refreshNav()` 那一笔账）。
   *  ⚠️ **`data-active` 那一格是它自己的**（不是那十段身上那个 —— 那一个是"光标在这一段里"）。 */
  const navList = node('nav', 'persona-navlist', '');
  coreSetUIAttribute(navList, 'aria-label', corePhrase("人设章节"));
  const nav = node('div', 'persona-nav');
  /** ⚠️ **这里从前是 `nav.append(navTitle, navList)`** —— 开头那个 `span`（「这一页」）
   * 被作者划掉了（账留在 `NAV_LABEL` 那一处）： 它说的是**页名**，而这一条是
   *  **页内**的一条路 —— 那三个字一个信息都不带。 */
  nav.append(navList);

  /** **第二张清单**（本刀改的：它不再是"剩下那几行"，而是**正经的块**）。
   *
   *  ⚠️ **顺序照作者定的那一条**：**十段** ⇒ **自述** ⇒ **世界认知** ——
   * 而这两块**走的是同一处落点**（`otherList`）， 顺序就是服务交回来那一份的顺序
   *     （那张表里的次序：`她写的那两份` 在前、`世界认知` 在后）。 */
  const list = node('div', 'persona-segments');
  /** ⚠️⚠️ **那一句抬头今天没有了**（作者 2026-10-01：「**这个去掉，让空行贴合。**」）。
   *
   *  它从前就建在这一处：`node('h2', 'persona-otherhead', '')`，字在 `draw()` 里按
   *  `report().unplaced.length` 写（"这张表里还有这 N 行 —— 它们不在段序里"）。
   * **作者圈的是那一句，而它占的那一片空行跟着一起收掉**：
   *     那 16px（`.persona-other` 的 `pt-16`）＋ 那一行字 ＋ 8px（`pb-8`）
   * ＋ 12px（`.persona-beside` 的列距）—— 两条样式都走了（见 `style.css`）。
   * ⇒ **那一摞贴在那十段底下**：第一块顶上那条 2px 的线今天落在**它自己那条边**里
   *     （与第二块起、与上面十段**同一处规矩**）。
   *  ⚠️ **页上一个字都不摆**（不许拿别的说法 ／ 提示 ／ `title` 补回来 —— 作者说的是
   * "去掉"）； 而**那个数照旧是算出来的**：`beside.length` 决定 `otherBox.hidden`，
   *     而 `load()` 那一句状态照旧按它写"表里另多出 N 行"。
   *  ⚠️ **`otherBox` 与 `otherList` 两样照旧**（`hidden` 那一格与那两块本身都还有判据钉着）。 */
  const otherList = node('div', 'persona-beside');
  const otherBox = node('section', 'persona-other');
  otherBox.append(otherList);
  otherBox.hidden = true; // 一行都没有的时候，那一块整个不出现

  stack.append(head, nav, list, otherBox, personaMemoryTail());
  column.append(stack);
  shell.content.replaceChildren(column);

  /** 手上那几段（ 编辑改的是它 —— 保存时读的也是它，不读 DOM）。 */
  let segments: readonly PersonaSegment[] = [];
  /** 改过的那几格（按**文件**存）：没动过的段**一次都不写盘**。 */
  const drafts = new Map<string, Draft>();
  /** 改过的文件（按改的先后 —— 保存那一趟看的就是它）。 */
  const dirty: string[] = [];
  let editEpoch = 0;
  let saving = false;
  const conflictBox = node('section', 'persona-conflict');
  conflictBox.hidden = true;
  head.append(conflictBox);

  const say = (line: string, state: string, detail = ''): void => {
    const long = line.length > 100 || line.includes('\n');
    coreSetUIText(statusLine, 'textContent', (long ? corePhrase("操作未完成，请查看详情。") : line));
    receiptText.textContent = detail || (long ? line : '');
    receipt.hidden = receiptText.textContent === '';
    receipt.open = false;
    status.dataset.state = state;
    status.hidden = line === '';
  };

  /** 抬头那两个数（ 两个都从**手上这一份**算 —— 与正文那一列同源）。 */
  const refreshHead = (): void => {
    let tokens = 0;
    for (const segment of segments) {
      const draft = segment.file === null || segment.file === undefined
        ? null : (drafts.get(segment.file) ?? draftOf(segment));
      tokens += draft === null ? 0 : tokenEstimate(draft.lines.join('\n'));
    }
    coreSetUIText(count, 'textContent', [corePhrase(""), segments.length, corePhrase(" 段 · 渲染后约 "), tokens, corePhrase(" tokens")]);
    coreSetUIText(saveHint, 'textContent', (dirty.length > 0 ? [corePhrase(""), dirty.length, corePhrase(" 段未保存 · Ctrl+S 保存当前章节")] : corePhrase("修改后保存 · Ctrl+S")));
    saveHint.dataset.dirty = String(dirty.length > 0);
    coreSetUIText(saveButton, 'textContent', (saving ? corePhrase("保存中…") : corePhrase("保存")));
  };

  /* ── **那一条导航**（本刀加的） ─────────────────────────────────────────── */

  /** 那一块现在在页上叫什么（ 从**那一块自己的**那个格子读：段序里那十段是 `data-segment`，
   * 表里多出来那两块是 `data-beside`）—— **而"叫什么"只有一处**：
   *  `THE_READABLE_TITLE` 那一张表（本波起那两块**也走它** ⇒ 旗子与导航那一项同一个说法）。
   *  ⚠️ **上一版不是这样**：导航那一项直接读 `data-beside`（表的说法）⇒ 那一块在旗子上
   * 叫「我」、在导航里叫「她写的那两份」——**同一块两个名字** 。
   *  ⚠️ 其余两处调用（`markDirty()` ／ `markAllSaved()`）只遍历 `list` 里**那十段**
   *  ——段名一个都不在那张表里 ⇒ 这三处的读法一个字都没变。 */
  const nameOfRow = (row: HTMLElement): string => {
    const label = row.dataset.segment ?? row.dataset.beside ?? '';
    return THE_READABLE_TITLE[label] ?? label;
  };

  /** **那一块的锚点 ＝ 那一块自己**（2026-09-30 晚订正 —— 从前落在"块里第一行 `#+ `"）。
   *
   *  作者那一句：「**破甲的导航跳转还是有问题。**」
   *
   *  ⚠️⚠️ **改前落在"标题那一行"上，而他在真页上看到的正是它的反例** ——
   *     本波量到的（`127.0.0.1:7799/#/persona`、`1264 × 653`、硬刷新、真鼠标点导航）：
   *
   *     | 点哪一项 | 落下的那一行 | 那一块的顶 | **旗子（名字）** | 被藏到导航之上的行 |
   *     |---|---|---|---|---|
   *     | `存在方式` | 第 **1** 行（＝标题行） | 视口 `140.31` | 视口 `−11.9`（半掩在那条底下，看得见） | **0** |
   *     | `破甲` | 第 **10** 行 `## 亲密与放开` | 视口 `−170.44` | 视口 **`−320.65`** | **8**（整段抬头注释） |
   *
   * ⇒ **十块与那三块**（`破甲` ／ `边界` ／ `通道形状` —— 文件开头是抬头注释的那三块）
   *     **在页上是两种行为**：点前一块，你看见它的名字；点后三块，那一块**从名字那面旗
   * 到它自己都一起被滚出了屏幕**。 而那一刀自己当时就记下了这一脚
   *     （它写的原话在后两行里；那份报告随工作区删掉了，别再去找）：「三块注释开头的块落到标题行后，
   *     **模块名那面旗被滚到导航之上**」—— 他这一句说的就是它。
   *
   * **为什么落在"那一块自己的头"上才对**（不是妥协，是"十三项一条规矩"）：
   *     · **名字与标题在那三块里根本不在一处**：`破甲` 的**名字**是**旗子**（左列那一面），
   *       而它的第一条 `#+ ` 是 `## 亲密与放开`（在**十行**之下、`−320.65` 之上）；
   *     · **那十块里"名字"与"标题"本来就是同一处**（块的第一行就是 `## 存在方式` 这样的
   *       标题）⇒ 落在头上与落在标题行上，对那十块差**那一块正文自己的 9px 内边距**
   * —— 于是"落在头上"这条规矩**对十三项是同一条**：
   *       **点一下，那一块从它的名字那面旗开始，出现在你眼前**；
   *     · 落下的仍是**那一块自己的东西**（`[data-navrow]` 那一格），不是按名字去找。
   *
   *  ⚠️ **顺带还清了一笔债**：这一处从前**自己认标记**（`A_TITLE = /^#{1,6}\s/` —— 全页
   *     唯一一处），因为服务交回来的 `shape` 只说"哪几行锁着"、不说"哪一行是标题"。
   * 落点改成"那一块自己"之后，**页上一个标记都不用认了**（`A_TITLE` ／
   *     `titleLineOf()` ／ 行上那三处 `data-navat` 一起走掉）。
   *
   *  ⚠️ 那几块**一个字都没有**的（`示例对话` ／ **只读那一份里读不到正文的那几块**）
   * 照旧落在**那一块自己**上 —— 现在十三项走的是同一句，不再有"两种落法"。
   *     ⚠️⚠️ **本波那两块也有正文了**（它们不再是"一个字都没有"的那一档）——
   *     落点这一句**一个字不用改**：它本来落的就是那一块自己。
   */
  const anchorOf = (row: HTMLElement): HTMLElement => row;

  /** 那一块的**归属那一档**（`bot` ／ `framework` ／ `world` ／ `none`）—— 照读那一枚
   *  的衣服，不重算：那一档是 `ownerClass()` 算出来的，导航里另算一份就会漂。 */
  const toneOf = (row: HTMLElement): string => {
    const badge = row.querySelector('.persona-home .persona-owner');
    if (badge === null) return '';
    const cls = (badge.className || '').split(' ');
    return (cls[cls.length - 1] ?? '').replace(/[^a-z]/g, '');
  };

  /** **按页上真实的次序列一遍**（ 唯一的来源就是 DOM 自己）——
   *  十段（`list` 里）＋ 表里多出来那两块（`otherList` 里），各挑**打过头的那一行**。
   *  ⚠️ **不许另立一份名字表**：将来段序增删，导航跟着动（"两份会漂"那条纪律）。 */
  const navRows = (): HTMLElement[] => [
    ...(list.querySelectorAll<HTMLElement>('.persona-segment[data-navrow]')),
    ...(otherList.querySelectorAll<HTMLElement>('.persona-segment[data-navrow]')),
  ];

  /** 把"现在这一块"那一枚点亮（ 同时一次只亮一个）。 */
  const markActive = (index: number | null): void => {
    for (const one of navList.querySelectorAll<HTMLElement>('.persona-navitem')) {
      one.setAttribute('aria-current',
        index !== null && one.dataset.navIndex === String(index) ? 'location' : 'false');
      if (index === null || one.dataset.navIndex !== String(index)) delete one.dataset.on;
      else one.dataset.on = 'true';
    }
  };

  /** **跳到那一块**：算出来的是"让那一块的**标题那一行**落在那一条导航的下沿之下"那一个滚动量。
   *
   *  ⚠️⚠️ **这一格的名字不叫 `top`**（这一版踩过一脚，实测抓到的）：
   *     浏览器里 `top` 是**一个全局**（`window.top` —— 那个 Window 自己），
   *     而这里若写 `const top = …` 之外的 `top`，拿到的是那个**对象**：
   *     `scrollTo({ top: <Window> })` 里那个值 `Number(Window)` 是 **NaN**
   *     ⇒ `scrollTo` **静默什么都不做**（点一下**不滚**，而那一枚"现在这一块"照旧点亮
   *     —— 本波实测到的正是这一对症状：`dataset.navtop` 打出来是 `[object Window]`）。
   * ⇒ 这一格叫 `jumpTo`，而那一个**局部**常量也照着它取名字。
   * ⚠️⚠️ **它走 `scroller.scrollTop = …`，不是 `scrollTo({…})`**（ 第三脚，实测抓到的）：
   *     同一个元素、同一个数，`scrollTop` 直接赋值**当场就滚**（实测 5813），
   *     而 `scrollTo` 那一版在**点击那条路**上**静默什么都不做**
   *     （实测：`Element.prototype.scrollTo` 记下来的是
   *     `[{top: 5813, behavior: 'auto'}, before=0, after=0]` —— 调到了、值也对、就是不动；
   * 同一个元素在控制台里 `scrollTo` 是好的）。 这一页要的只是"滚到那一个位置"，
   *     而 `scrollTop` 那条路**没有第二层语义可以出岔子**（没有 behavior、没有字典）。
   *     ⚠️ 一个"点了不滚"的导航比没有导航更坏：它看着像坏了。
   * ⚠️⚠️ **那个滚动的那一格是找出来的，不是 `column.parentElement`**（ 第四脚，抓到根因）：
   *     本页 `column` 的**父节点是 `#content`**（壳那一层，`overflow: hidden` ——
   *     `.page-persona` 那一格**不是**它的父节点，`scrollTop` 打上去纹丝不动）：
   *     实测那一下 `scroller.className === 'content'`、`scrollHeight === clientHeight === 913`
   *     ⇒ `scrollTop = 5813` 落下去就是 **0**（⚠️ 这正是前面三脚"调到了、值也对、就是不动"）。
   * ⇒ 顺着父链找**第一个真的能滚的**那一格（`overflow-y` 是 `auto` ／ `scroll`），
   *       找不到就退回 `column`（本页上找不到是不可能的 —— `#content` 那条 `overflow: hidden`
   *       是壳的纪律）。
   * **2026-09-30 作者那一句「偏的有点多」的根（本刀改的）**：从前这里减的是
   * `anchor.offsetHeight` —— **那一行自己的高**（实测 21.6px），**不是那一条导航的高**；
   *     而 `scroller.scrollTop + anchor.getBoundingClientRect().top` 又**把这一格的顶端当成了
   *     视口原点**（这一格的顶在视口 `87.63`，不是 `0`）。⇒ 两笔加起来，落点偏了 **≈110px**：
   *     在真产物上逐块量过 —— 十二块那一行落在视口 **`29.6`**，而它**该在 `140.1`**
   *     （那一条粘住之后的下沿），也就是说点一下，那一块的**头五行**一起被滚到屏幕外面去了。
   * ⇒ 今天落点是**算出来的**（三个数全从页上读，见 `theLine()`）：**那一块自己**在
   *     这一格的内容里的位置 −（这一格的上内边距 ＋ 那一条自己的 `top` ＋ **那一条的实测高**
   *     ＋ `NAV_GAP`）。
   * **落下的是"那一块自己的头"**（`anchorOf()` —— 2026-09-30 晚订正；那一处的账里
   *     摆着两档视口下逐项量到的数：`存在方式` 落下去旗子在视口 `−11.9`、0 行被藏；
   *     `破甲` 从前落在第 10 行 `## 亲密与放开` 上，那一块的顶在 `−170.44`、**旗子在
   *     `−320.65`**、8 行被藏 ⇒ 他那一句「破甲的导航跳转还是有问题」量到的就是它）。
   */
  const scrollerOf = (): HTMLElement => {
    // ⚠️⚠️ **向上那一跳不写死 `parentElement`**：那一台 DOM 替身**只有 `parent`**
    //    （它没有 `parentElement` —— 本波实测：`Cannot read properties of undefined
    //    (reading 'parentElement')`，而那一条判据跑到这一页时整页**跑不起来**）。
    //    ⇒ 两个都认：先 `parentElement`，没有再退回 `parent`。
    const up = (node: HTMLElement): HTMLElement | null =>
      (node.parentElement ?? (node as unknown as { parent?: HTMLElement | null }).parent ?? null);
    for (let node = up(column); node !== null; node = up(node)) {
      // ⚠️ `getComputedStyle` 在那一台替身上也不一定有（那一条判据只判结构 ＋ 属性，
      //    不判布局）⇒ 读不到就**当它不是滚动那一格**，继续往上走。
      let flow = '';
      try {
        flow = window.getComputedStyle(node).overflowY;
      } catch {
        flow = '';
      }
      if (flow === 'auto' || flow === 'scroll') return node;
    }
    return column;
  };

  /** **落点与粘住的那一条之间留的那一点空** —— 它**就是**"现在这一块"那条线用的
   *  同一个数（下面 `trackActive()` 里那一处）：两处各写一个数，跳过去之后**高亮的那一项
   *  会与落下的那一块差一位**（落点正好压在那条线上，而判据走 `<=` ⇒ 必须同一个数）。 */
  const NAV_GAP = 8;
  const sideRail = (): boolean => {
    try {
      return window.getComputedStyle(navList).flexDirection === 'column';
    } catch {
      return false;
    }
  };

  /** **粘住的那一条的下沿在哪儿**（离这一格可见顶端的距离）—— 三个数**全从页上读**：
   *
   *   · **它钉在哪儿** ＝ 这一格自己的 `padding-top` ＋ 那一条自己的 `top`：
   * 实测（在真产物上改一处量一处）—— `padding-top` 是 `30` ／ `100` ／ `0` 时，
   *     那一条粘住之后钉在 **`30` ／ `100` ／ `0`**；`top: -10px` 时钉在 **`20`**。
   * ⇒ 这一截是**读出来的**，不是"减一个 30"—— 它还叫 `top: 0` 的账。
   *   · **它有多高** ＝ `getBoundingClientRect().height`（实测 `22.5`）。
   *     ⚠️ **不许用 `offsetHeight`**：它取整成 `23`（实测），落点会偏半像素；
   *   · **留的那一点空** ＝ `NAV_GAP`。
   *
   *  ⚠️ 那一台 DOM 替身上没有 `getComputedStyle`（与 `scrollerOf()` 同一条余量）——
   *     读不到就**只算那一条的高**，不抛。
   */
  const theLine = (): number => {
    const flow = scrollerOf();
    // A vertical chapter rail sits beside the document, so its height must not
    // be subtracted from the chapter landing point. The compact rail sits above it.
    if (sideRail()) return 20;
    let stuck = 0;
    try {
      stuck = (Number.parseFloat(window.getComputedStyle(flow).paddingTop) || 0)
        + (Number.parseFloat(window.getComputedStyle(nav).top) || 0);
    } catch {
      stuck = 0;
    }
    return stuck + nav.getBoundingClientRect().height + NAV_GAP;
  };

  const jump = (row: HTMLElement): void => {
    const scroller = scrollerOf();
    const anchor = anchorOf(row);
    const box = scroller.getBoundingClientRect();
    // 它（＝那一块自己）在**这一格的内容里**的位置：视口坐标先减掉这一格自己的顶，
    //   再加上已经滚过去的量。
    const here = anchor.getBoundingClientRect().top - box.top + scroller.scrollTop;
    // ⚠️ **取整往上**（`Math.ceil`）：落点**正好压在那条线上**，而滚动的量是**整像素**的
    //    ⇒ 四舍五入会把落点推到那条线**下面不到 1px**（实测那一块偏 `0.22px`），
    // 而"现在这一块"那条判据走 `<=` ⇒ **点完那一项自己反而不亮**（亮的是上一项）。
    //    往上取整 ⇒ 落点**永远压在线的不低于处**（`0 .. −1px`），那一项必亮。
    scroller.scrollTop = Math.max(0, Math.ceil(here - theLine()));
  };

  /** **刚点过的那一块** ＋ **它把这一格滚到了哪个位置** —— `trackActive()` 先认它一眼。
   *
   *  ⚠️⚠️ **为什么"点出来的那一趟"要单独认**（作者 2026-09-30 晚那一刀的实测）：
   *     页滚到底之后，**最后那几块永远够不到那条线**（不能再滚了）⇒ 落点被 `maxScroll`
   *     钳住。而**被钳住的那几项落到的是同一个位置** —— 实测 `2040 × 986` 上
   *     `示例对话` ／ `语气示例` ／ `她写的那两份` ／ `世界认知` **四项**交回来的
   *     `scrollTop` **都是 `6542`**（同一档视口 `1264 × 653` 上那两项也都是 `8933`）。
   * ⇒ **只看位置的规矩不可能把那四项分开**：不管它怎么算，四项都只能亮同一枚。
   *     所以"点谁亮谁"这件事**只能由那一跳自己带**：`jumpedTo` 在这儿记下，
   *     `trackActive()` 在下面**只认这一趟**（位置对不上就当场作废，见那一处）。
   *  ⚠️ 它**不是**"点过就一直钉着"：认过一趟就清掉，此后手滚一格都按位置重算。 */
  let jumpedTo: HTMLElement | null = null;
  let jumpedAt = -1;

  /** **点一下那一项该做的两件事**（跳 ＋ 点亮）， 并把"这一趟是点出来的"记下来。 */
  const jumpAndMark = (row: HTMLElement, index: number): void => {
    jump(row);
    markActive(index);
    jumpedTo = row;
    jumpedAt = scrollerOf().scrollTop;
  };

  /** **把导航铺一遍**（画一次 ／ 每次改动之后各一次）。
   *  ⚠️ **那件事照旧从页上读**：`data-dirty`（`markDirty()` 那一处写的）—— 导航**不自己记**。
   * **本波去掉的那一枚记号是「只读」**（作者：*「不用这个只读，没必要。」*）——
   *     · **那几项照旧在**（它们仍然是跳到那一块去的那一条路 —— 去掉的**只是那一枚记号**）；
   * · **"哪一块只读"这件事在页上照旧读得到**（在**那一块自己**身上：
   *       `data-readonly`（那层 4% 的墨色底）／ 正文那一格点不开）——
   *       导航里那一枚只是它的**一个**下家，而作者说它不必有。
   *       ⚠️ 那两处里的**牌子**（「这一块人不能改」）**本波也走了**（作者 2026-10-01：
   *       不可输入光标状态不必再提醒一句）⇒ 这一句里点名的那两样今天只剩
   *       `data-readonly` 与"点不开"。
   *     ⚠️ ⚠️ **`data-readonly` 也一并不打了**：它从前是那一枚记号的**唯一**来源
   *       （`item.dataset.readonly` ⇒ `[data-readonly="true"]` 那条染色规则 ⇒ 那颗记号）
   *       ⇒ 留着它就是把一枚**谁也读不到**的记号装在一项上（那正是本仓最忌的
   *       "两份会漂"—— 页上改了、而这一项照旧挂着一个"只读"的底）。
   * 于是"导航里**没有任何**'只读'的痕迹"这件事**是判据判得动的**
   *       （见 `test_..._drops_the_read_only_chip_from_the_nav`）。
   *  ⚠️ **"未保存"那一枚照旧在**（作者这一次一个字节都没提它）。 */
  const refreshNav = (): void => {
    navList.replaceChildren();
    const rows = navRows();
    rows.forEach((row, index) => {
      const item = node('button', 'persona-navitem');
      item.type = 'button';
      if (row.dataset.dirty === 'true') item.dataset.dirty = 'true';
      item.dataset.navIndex = String(index);
      item.dataset.tone = toneOf(row);
      const text = node('span', 'persona-navtext', nameOfRow(row));
      item.append(text);
      // 那一枚记号：**有未保存的改动** —— 一件事一个词，读得出来。
      // ⚠️ **"只读"那一枚今天不在这儿了**（本波的账在上面）。
      if (row.dataset.dirty === 'true') item.append(node('span', 'persona-navmark', '未保存'));
      coreSetUIText(item, 'title', [corePhrase(""), nameOfRow(row), corePhrase(" —— 点一下跳到那一块")]);
      if (row.dataset.dirty === 'true') item.title += '（有没保存的改动）';
      item.addEventListener('click', () => {
        jumpAndMark(row, index);
      });
      navList.append(item);
    });
    // **"记忆（还没做）"那一项与左栏同一批**（`SOON` 那一格就是它）——
    //    它点不动（`disabled`），因为那一页真的还没有。
    for (const one of soonItems('PERSONA & MEMORY')) {
      const item = node('button', 'persona-navitem', one.label);
      item.type = 'button';
      item.dataset.soon = 'true';
      item.disabled = true;
      item.title = `${one.label} —— ${one.why}`;
      navList.append(item);
    }
    markActive(null);
  };

  /** **哪一块是"现在这一块"** —— 一条**滚动**上的判据（不是 `IntersectionObserver`）。
   *
   * ⚠️⚠️ **为什么不用 `IntersectionObserver`**（ 本波实测抓到的一脚）：它只在**交叉状态
   *     *变化*时**报一次 ⇒ 一路滚回最上面时那一枚**照旧停在中间那一块**上
   *     （实测：`scrollTop = 2600` 亮"世界"，滚回 `0` 之后**还是**"世界"）。
   *     判别条用的是"哪一块是现在这一块"，而那是一个**连续**的问题（滚到哪就亮到哪），
   * 不是一个"进出视口"的事件。⇒ 换成每一下滚动**现算一次**：**上沿已经越过那条线
   *     的最后一块**就是它（那条线 ＝ 粘住的那一条的下沿 ＋ 一点余量）。
   *  ⚠️ **它量的是那一块自己的顶**（与那一跳**同一个锚点** —— `anchorOf()`）：两个数
   *     共用一个 `NAV_GAP`、一个锚点 ⇒ **点完那一下，落下的那一项自己必亮**
   *     （落点永远压在线的不低于处，而这条判据走 `<=`）。
   *  ⚠️ **它一个自己的状态都不存**：`navList` 上那几项 `data-nav-index` 与 `[data-navrow]`
   *     那几块的次序**同一个**（两处都按页上真实的次序来）⇒ 这一处只是把它们对齐。
   *  ⚠️ 那一颗 `scroll` 的钉子挂在**那一格**上（`scrollerOf()` —— 它不一定是 `column`
   *     的父节点，见上面那一脚），而 `stop()` 里要拔掉：换页之后那一格已经不在屏幕上，
   *     钉子留在那儿就是一处漏。
   */
  const scroller = scrollerOf();

  /** **这一格滚到底了吗** —— 滚下去了才可能"到底"，所以 `most > 0` 那半条是**承重**的
   *  （一页短得不用滚的时候，`scrollTop` 一直是 0，`0 >= 0` 会把"最后那一块"判给一整页）。
   *  ⚠️ 那个数**算出来**（`scrollHeight - clientHeight`），不写死：两档视口上量到的是
   *     `8933`（`1264 × 653`）与 `6542`（`2040 × 986`）—— 都是整数，而钳住时浏览器交回来的
   *     `scrollTop` **正是那个数本身** ⇒ 这一处用 `>=` 就够了（不用留余量）。 */
  const atBottom = (): boolean => {
    const most = scroller.scrollHeight - scroller.clientHeight;
    return most > 0 && scroller.scrollTop >= most;
  };

  const trackActive = (): void => {
    const rows = navRows();
    if (rows.length === 0) return;
    // **点出来的那一趟：那一项自己就是"现在这一块"**（见 `jumpedTo` 那段账）。
    //    ⚠️ **只认一趟，并且要对得上位置**：对不上就说明这一趟滚动不是那一跳带来的
    //    （人自己滚的），当场作废、走下面那条按位置的规矩 —— 一个"点过就一直钉着"的实现
    //    会让导航从此不再跟着手滚走。
    if (jumpedTo !== null) {
      const who = scroller.scrollTop === jumpedAt ? rows.indexOf(jumpedTo) : -1;
      jumpedTo = null;
      if (who >= 0) { markActive(who); return; }
    }
    const line = sideRail()
      ? scroller.getBoundingClientRect().top + 24
      : nav.getBoundingClientRect().bottom + NAV_GAP;
    let here = 0;
    rows.forEach((row, index) => {
      if (anchorOf(row).getBoundingClientRect().top <= line) here = index;
    });
    // **滚到底 ＝ 最后那一块**（作者 2026-09-30 晚：「滚到底的时候算最后那一块」）。
    //    ⚠️⚠️ 这一条治的是**滚不到那几块**：页到底之后，最后那两块／四块的顶**永远在
    //    那条线下面**（`1264 × 653`：`她写的那两份` 497 ／ `世界认知` 555，而线在 148；
    //    `2040 × 986`：那四项 148.5 ／ 208.6 ／ 830.0 ／ 887.4）⇒ 上面那条"上沿越过那条线"
    // 照它的定义只会说"还是上一块"（实测亮的是 `语气示例` ／ `通道形状`）。
    //    既然已经滚到底，"现在这一块"就是**最后那一块** —— 那才是这一页读到底的样子。
    if (atBottom()) here = rows.length - 1;
    markActive(here);
  };

  /** **某一格改过了** ⇒ 那一块打上 `data-dirty`（导航里那一枚"未保存"从它来）。
   *  ⚠️ **它是那一处唯一的写法**（`take()` 与保存那两条路都走它）⇒ "导航里那份清单"
   *     与"页上那份提示"不会漂。 */
  const markDirty = (name: string): void => {
    editEpoch++;
    const draft = drafts.get(name);
    if (draft) draft.generation = (draft.generation ?? 0) + 1;
    const changed = draft !== undefined && draft.lines.join('\n') !== linesOf(draft.base ?? '').join('\n');
    const at = dirty.indexOf(name);
    if (changed && at < 0) dirty.push(name);
    if (!changed && at >= 0) dirty.splice(at, 1);
    // **按文件找回那一块**（`segments` 那一份是页上唯一记着"哪一块落在哪个文件"的地方）。
    const hit = segments.filter((one) => one.file === name).map((one) => one.name);
    for (const row of list.querySelectorAll<HTMLElement>('.persona-segment')) {
      if (hit.includes(nameOfRow(row))) {
        const baseline = linesOf(draft?.base ?? '');
        const sectionChanged = changed && Array.from(row.querySelectorAll<HTMLElement>('.persona-line[data-line]'))
          .some(line => draft?.lines[Number(line.dataset.line)] !== baseline[Number(line.dataset.line)]);
        if (sectionChanged) row.dataset.dirty = 'true';
        else {
          delete row.dataset.dirty;
          for (const hint of row.querySelectorAll('.persona-savehint')) hint.textContent = '';
        }
      }
    }
    refreshNav();
  };

  /** 保存那一版**留在行尾那一格里的提示**（参考那一页在光标那一行后面挂一句）。
   *  ⚠️ **它是空的时候不占位**（`textContent === ''` ⇒ CSS 那一处不画底）。 */
  const HINT_UNSAVED = '未保存 · Ctrl+S';
  const HINT_SAVED = '已保存';
  let activeHint: { row: HTMLElement; name: string } | null = null;

  // Only the current editing line owns the inline status. Focus is not an edit.
  const showHintOn = (row: HTMLElement, name: string): void => {
    activeHint = { row, name };
    for (const one of row.closest('.persona-segment')?.querySelectorAll('.persona-savehint') ?? []) one.textContent = '';
    const here = row.querySelector('.persona-savehint');
    if (here !== null && row.closest<HTMLElement>('.persona-segment')?.dataset.dirty === 'true') here.textContent = HINT_UNSAVED;
  };

  /** 保存成功 ⇒ 屏幕上还亮着的提示都换成"已保存"（ 不再显示"未保存"）。
   * ⚠️ **导航里那几枚也要一起换**（ 那一条是"长页里一眼找到"的地方）：那一块写下去了
   *     ⇒ 它身上那个 `data-dirty` 要摘掉、导航重铺一遍（两件事同一处做）。 */
  const markAllSaved = (): void => {
    if (activeHint !== null && dirty.includes(activeHint.name)) {
      const hint = activeHint.row.querySelector('.persona-savehint');
      if (hint !== null) hint.textContent = HINT_UNSAVED;
    }
    for (const row of list.querySelectorAll<HTMLElement>('.persona-segment')) {
      const file = segments.find(one => one.name === nameOfRow(row))?.file;
      if (file && dirty.includes(file)) continue;
      for (const one of row.querySelectorAll('.persona-savehint')) {
        if (one.textContent !== '') one.textContent = HINT_SAVED;
      }
    }
    // **写下去的那几块**：把 `data-dirty` 摘掉（`dirty` 那一列里已经没有它们的文件了）
    // —— 判据就是**那一列**，不是另记一份"哪些存过了"。
    for (const row of list.querySelectorAll<HTMLElement>('.persona-segment[data-dirty]')) {
      const segment = segments.find((one) => one.name === nameOfRow(row));
      const file = segment?.file;
      if (file === null || file === undefined || !dirty.includes(file)) delete row.dataset.dirty;
    }
    refreshNav();
  };

  /** **一行正文，而它是"死的"**（只读那一份的每一行）——
   *
   * ⚠️⚠️ **它与 `editorLine()` 差在**： **`contenteditable` 一个都没有** ⇒
   *     **点不开**（作者那条要求：不能改的**点不开**，而不是"点了没反应"）。
   * 而**行号照旧在**：读的人要能指着第几行说话（看不见行号就没法讨论）。
   *  ⚠️ **`data-fixed`** 是给它自己的（判据 ／ 样式都读它 —— "这一页上真的没有那一格"）。
   */
  const fixedLine = (draft: Draft, index: number): HTMLElement => {
    const row = node('div', 'persona-line');
    row.dataset.fixed = 'true';
    row.append(node('span', 'persona-lineno', String(index + 1)));
    row.append(node('div', 'persona-linetext', draft.lines[index] ?? ''));
    return row;
  };

  /** **一行正文，而它是一处承重的形状**（本波那条 Critical）——
   * **点不开**（与只读那一份同一条做法：`contenteditable` 一个都没有）；
   * **而它旁边今天一个字的解释都没有**（作者 2026-09-30：那种解释不要 ——
   *    "这一行锁着"这件事从**它点不开**上就看得出来）。
   *
   *  ⚠️⚠️ **为什么锁的是"行"**（而不是"那几个字符"）：这一页的编辑单元就是**一行**
   * （`contenteditable` 一格一行）—— 锁半个行要做一套富文本，而作者那条要求是
   *     "别把承重的字符删掉" ⇒ **锁住那一行**就够了（那一行照旧看得见、选得中）。
   * 而"哪几行"由服务算（`lockedLines()`）—— 页上不认 `▎` ／ 不认 `**`。
   *  ⚠️ **`data-shape`** 是给它自己的（判据 ／ 样式都读它 —— "这一行是被那道判据锁住的"，
   *     与"这一份整个只读"（`data-fixed` ／ `data-readonly`）**不是一件事**）。 */
  const shapeLine = (draft: Draft, index: number): HTMLElement => {
    const row = node('div', 'persona-line');
    row.dataset.fixed = 'true';
    row.dataset.shape = 'true';
    row.append(node('span', 'persona-lineno', String(index + 1)));
    // **本波（一块一个面之后）它是面里面的一个"原子块"**：`contenteditable="false"`
    // —— **它照旧看得见、照旧选得中**（今天也是如此），只是**改不动**。
    //     ⚠️ 一行一个面的时候这一格不需要它（这一格压根不是编辑宿主）；现在**必须有**：
    //       面是它的父亲 ⇒ 光标一进去它就是可编辑正文（被拖进选中 ／ 被退格吃掉 ／
    //       而被写回草稿时它那两个字会变成这一行的开头）。
    const cell = node('div', 'persona-linetext', draft.lines[index] ?? '');
    cell.setAttribute('contenteditable', 'false');
    row.append(cell);
    return row;
  };

  /** **一块正文**（点它就地改）：一行号 ＋ 一格里那一条文字 ＋ 那一格行尾的提示。
   *
   *  ⚠️ 落 `blur` 才把字收回草稿（不是每敲一下）⇒ 一次编辑不产生一串中间态。
   *  ⚠️ 收回来的那一份**就是 `drafts` 里那一个对象**（不是另建一份）：`take()` 改的那
   *     一行，保存那一趟读到的必须是同一份 —— 两处各拿一个副本，就是"改了却没存下去"
   *     那一类事故（本刀在产物那一趟上量到过：抬头那个数与保存递出去的正文**都没动**）。
   */
  const editorLine = (draft: Draft, index: number): HTMLElement => {
    const row = node('div', 'persona-line');
    const number = node('span', 'persona-lineno', String(index + 1));
    row.dataset.line = String(index); // **这一行在草稿里的下标**（收字时读它）
    const text = node('div', 'persona-linetext', draft.lines[index] ?? '');
    // **`contenteditable` 不再挂在这一格上**（本波：**一块一个面**，见 `attachSurface()`）
    // —— 那一行 `text.setAttribute('contenteditable', 'plaintext-only')` 就是
    //     "拖拽只能选中一行"那件事的根（上一刀量到的：选中被夹在起头那个宿主里）。
    const lineHint = hintNode(''); // 只在这一行被改过之后才有字
    row.append(number, text, lineHint);
    return row;
  };

  /** **一个块 ＝ 一个编辑面**（本波重写的那一处 —— 作者那句"拖拽只能选中一行"的根）。
   *
   *  ⚠️⚠️ **根在哪**（那一刀量出来的那一串数就在下面几行里 —— 而它当时记的那份报告
   *     随工作区删掉了，`.gitignore:5` 收着的目录）：**选中的范围被夹在它起头那个
   *     编辑宿主里** —— `Selection.modify('extend','forward','line')`（浏览器给 Shift+↓
   *     用的正是它）从**一格 `contenteditable` 行**里出发 ⇒ 卡死在那一行自己的长度上
   *     （`0 → 20 → 36 → 36 → 36`，`focus` 永远是那一行）；从一个**不可改**的行里出发
   *     ⇒ 一路扫下去（`0 → 22 → 55 → 102`，跨行、甚至跨块）。
   *     而这一页从前 **175 个 `contenteditable` ＝ 一行一个** ⇒ 每一次拖拽都被夹住。
   * ⇒ 宿主收成**一块一个**：`.persona-body` 自己带 `contenteditable="plaintext-only"`。
   *
   * **锁住的那几行**（上一刀那句"下一刀更简单"，本波照做）：它们是面里面带
   *     `contenteditable="false"` 的**原子块** —— 照旧看得见、照旧选得中，只是改不动。
   * **行号那一列与行尾那句提示也必须封成原子块**（行号尤其致命：它一进面就是这一块
   *     正文的一部分 ⇒ 拖一下能把那个数字拖进选中、退格能吃、写回草稿时它是这一行的开头）。
   * 那一句"未保存 · Ctrl+S"从前是系统的蓝底白字（它没有 `::selection` 规则）——
   *     一个面之后它必然被卷进选中 ⇒ **它得一起走 `--color-mint-select`**（CSS 那一处）。
   *
   * **落盘那一趟跟着换**：从前是**逐行的 `blur` ⇒ `take()` ⇒ `draft.lines[index]`**；
   *     一个面之后是**面上一趟 `blur` ⇒ 把面上每一行逐个收回草稿**（`data-line` 就是下标）。
   * 交出去的仍然是**整份正文**（`draft.lines.join('\n')`，见 `saveDirty()`）
   * ⇒ **服务那一侧那道形状闸（409）一个字没动、照旧有的撞**。
   *
   *  ⚠️⚠️ **两件事必须钉住**（上一刀那条 Critical ＋ 那一句"行数不许变"）：
   *     · **行数不许变**：`Enter` 排在面上（`keydown` ＋ `beforeinput` 的 `insertParagraph`
   *       ／ `insertLineBreak` 两档各一道）—— 一个面之后浏览器会**自己造一个新块**，
   *       那正是"行数变了"；**跨行的删**（拖选一大片再退格 ／ `Delete` 跨行）与
   *       **带换行的粘**也各排一道；
   * · **锁住的行不许被删掉**：上面那条原子块 ＋ **每一趟先把"这一块的形状还在不在"
   *       回量一遍**（`shapeIntact()`）—— 不在就把**草稿保持原样**、并把这一块**从草稿
   * 重铺**（`rebuildSurface()`）⇒ **草稿永远不会被改坏的那个 DOM 污染**。
   */
  const attachSurface = (surface: HTMLElement, draft: Draft, locks: Set<number>,
                         hidden: Set<number>): void => {
    surface.setAttribute('contenteditable', 'plaintext-only');
    // 两个"不该被编辑"的族：行号那一列 ／ 行尾那句提示（两个都住在面里面）。
    const seal = (): void => {
      for (const one of surface.querySelectorAll('.persona-lineno')) {
        one.setAttribute('contenteditable', 'false');
      }
      for (const one of surface.querySelectorAll('.persona-savehint')) {
        one.setAttribute('contenteditable', 'false');
      }
    };
    seal();
    // **这一块建出来时的形状**（回量时照它比）：几行 ＋ 哪几行锁着。
    //   ⚠️⚠️ **本波（抬头那条注释不画）**：这里的"几行"数是**画出来的**行数
    // （草稿行数 **减去**藏起来的行）—— `shapeIntact()` 比的是**DOM 里那几行**
    //   （`rowsNow()`），拿草稿行数去比就**永远不等** ⇒ 每一次收字都被判"形状不在"
    // ⇒ 草稿一个字都收不回来、那一块被反复重铺（**改不动了** ）。
    surface.dataset.rows = String(draft.lines.length - hidden.size);
    surface.dataset.locked = [...locks].filter((line) => !hidden.has(line - 1)).join(',');

    /** 真浏览器里拿焦点的就是面自己（光标由 Selection 给）——**替身那一趟没有 Selection**。 */
    const chosen = (): Selection | null => {
      const sel = selectionNow();
      return sel !== null && sel.rangeCount > 0 ? sel : null;
    };
    /** **只问"有没有 Selection"、不问"它有没有 Range"**（那一支补偿笔要的是它）：
     *  ⚠️⚠️ 在 `mousedown` 那一瞬间，**还没有人选中任何东西** ⇒ `rangeCount` 常常是 **0**
     *     ⇒ 用 `chosen()` 那一支会**当场 return**、补偿那一支一次都起不来
     *     （本波踩过这一脚：从锁住的行起手拖，量到的还是 `length = 4`、`focus` 同一行）。 */
    const selectionNow = (): Selection | null => {
      const win = typeof window === 'undefined' ? undefined : window;
      if (win === undefined || typeof win.getSelection !== 'function') return null;
      const sel = win.getSelection();
      return sel === null || sel === undefined ? null : sel;
    };
    const caret = (): Range | null => {
      const sel = chosen();
      return sel === null ? null : sel.getRangeAt(0);
    };
    /** 一个节点往上找**它所在的那一行**。⚠️ 两条路：真 DOM 有 `parentElement`，
     *  替身只有 `parent`（那两条都写在这儿，"这一格是哪一行"就只有一个答案）。 */
    const rowOf = (start: Node | null): HTMLElement | null => {
      type Step = { classList?: Pick<DOMTokenList, "contains">; parentElement?: unknown;
        parent?: unknown };
      let one = start as unknown as Step | null;
      for (let hops = 0; one !== null && one !== undefined && hops < 8; hops += 1) {
        if (one.classList !== undefined && one.classList.contains('persona-line')) {
          return one as unknown as HTMLElement;
        }
        one = (one.parentElement ?? one.parent ?? null) as typeof one;
      }
      return null;
    };
    const rowsNow = (): NodeListOf<HTMLElement> =>
      surface.querySelectorAll<HTMLElement>('.persona-line');
    /** **这一块的形状还在不在**（行数 ／ 锁住的那几行）—— 收字之前先问它。 */
    const shapeIntact = (): boolean => {
      if (rowsNow().length !== Number(surface.dataset.rows ?? '0')) return false;
      const want = (surface.dataset.locked ?? '') === ''
        ? 0 : String(surface.dataset.locked).split(',').length;
      return surface.querySelectorAll('.persona-line[data-shape="true"]').length === want;
    };
    /** 光标正好粘在**这一行的头上**（退格会跨出这一行 ⇒ 行数要变）。 */
    const atHeadOf = (row: HTMLElement): boolean => {
      const range = caret();
      const cell = row.querySelector<HTMLElement>('.persona-linetext');
      if (range === null || cell === null || !range.collapsed) return false;
      return range.startContainer === cell && range.startOffset === 0;
    };
    /** 光标正好粘在**这一行的尾巴上**（Delete 会跨出这一行 ⇒ 行数要变）。 */
    const atTailOf = (row: HTMLElement): boolean => {
      const range = caret();
      const cell = row.querySelector<HTMLElement>('.persona-linetext');
      if (range === null || cell === null || !range.collapsed) return false;
      return range.startContainer === cell
        && range.startOffset >= (cell.textContent ?? '').length;
    };
    const stopTheEvent = (ev: Event): void => {
      const one = ev as unknown as { preventDefault?: () => void; stopPropagation?: () => void };
      if (typeof one.preventDefault === 'function') one.preventDefault();
      if (typeof one.stopPropagation === 'function') one.stopPropagation();
    };
    /** 把纯文本插在光标处（**不带换行** —— 一个面之后换行就是"行数变了"）。 */
    const insertPlain = (text: string): void => {
      const sel = chosen();
      if (text === '' || sel === null) return;
      const range = sel.getRangeAt(0);
      range.deleteContents();
      const made = document.createTextNode(text);
      range.insertNode(made);
      range.setStartAfter(made);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
    };
    /** **把面从草稿重铺**（结构被动过的时候走它 —— 草稿是唯一的正本）。
     *
     * ⚠️⚠️ **本波**：重铺**照旧跳过藏起来的那几行**（抬头那条注释）—— 两处
     *     （首铺 ／ 重铺）**同一条判据**，少一处就会出现"重铺之后它又冒出来了"。 */
    const rebuildSurface = (): void => {
      const at = Number(rowOf(caret()?.startContainer ?? null)?.dataset.line ?? '-1');
      surface.replaceChildren();
      draft.lines.forEach((_one, index) => {
        if (hidden.has(index)) return;
        surface.append(locks.has(index + 1) ? shapeLine(draft, index) : editorLine(draft, index));
      });
      seal();
      const rows = surface.querySelectorAll<HTMLElement>('.persona-line[data-line]');
      // ⚠️ **`at` 是草稿下标、`rows` 只是画出来那几行** —— 藏了几行之后两者不再同一步长
      //    ⇒ **按 `data-line` 去找那一行**（不是拿下标当序号），找不到（那一行藏在抬头里）
      //    就落回**画出来的第一行**。
      let back: HTMLElement | undefined;
      if (at >= 0) {
        for (let i = 0; i < rows.length; i += 1) {
          if (Number(rows[i]?.dataset.line) === at) { back = rows[i]; break; }
        }
        if (back === undefined) back = rows[0];
      } else {
        back = rows[rows.length - 1];
      }
      const sel = chosen();
      const cell = back?.querySelector('.persona-linetext') ?? null;
      if (sel !== null && cell !== null) {
        const range = document.createRange();
        range.selectNodeContents(cell);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      refreshHead();
    };
    /** **把面上每一行收回草稿**（一位一位读 `data-line` —— 行号就是草稿里的下标）。
     *  ⚠️ **先回量形状**：形状不在 ⇒ 一个字都不收（草稿保持原样）＋ 把这一块重铺。 */
    const takeSurface = (): void => {
      if (!shapeIntact()) { rebuildSurface(); return; }
      const hit: HTMLElement[] = [];
      for (const row of surface.querySelectorAll<HTMLElement>('.persona-line[data-line]')) {
        const index = Number(row.dataset.line);
        const cell = row.querySelector<HTMLElement>('.persona-linetext');
        if (cell === null) continue;
        const got = cell.textContent ?? ''; // Read source text, independent of wrapping/layout.
        if (got === draft.lines[index]) continue;
        draft.lines[index] = got;
        hit.push(row);
      }
      if (hit.length === 0) return;
      // **那一列的记账只有这一处**（`markDirty()`）：`dirty` 那一列 ＋ 那一块的
      //    `data-dirty`（导航里那一枚"未保存"从它来）一起动 —— 两处各写一次就会漂。
      markDirty(draft.name);
      showHintOn(rowOf(caret()?.startContainer ?? null) ?? hit[hit.length - 1], draft.name);
      refreshHead();
    };

    // **`Enter` 拦在面上**（从前拦在每一行上）：一个面之后浏览器**自己会造一个新块**
    //    —— 那正是"行数变了"。`keydown` 与 `beforeinput` 各一道（两道各自都能拦住它）。
    surface.addEventListener('keydown', (ev: Event): void => {
      if ((ev as KeyboardEvent).key === 'Enter') stopTheEvent(ev);
    });
    surface.addEventListener('beforeinput', (ev: Event): void => {
      const one = ev as unknown as InputEvent;
      const kind = one.inputType ?? '';
      if (kind === 'insertParagraph' || kind === 'insertLineBreak') { stopTheEvent(ev); return; }
      if (kind.startsWith('insertFromPaste') || kind.startsWith('insertFromDrop')) {
        stopTheEvent(ev);
        insertPlain((one.data ?? '').replace(/[\r\n]+/g, ' '));
        takeSurface();
        return;
      }
      if (!kind.startsWith('delete')) return;
      const range = caret();
      if (range === null) return;
      const from = rowOf(range.startContainer);
      // **跨行的删**（拖选那一大片里包含了一个行边界）：行数会变 ⇒ 拦下。
      if (!range.collapsed) {
        const to = rowOf(range.endContainer);
        if (from !== null && to !== null && from !== to) stopTheEvent(ev);
        return;
      }
      if (from === null) return;
      if (kind.includes('Forward')) { if (atTailOf(from)) stopTheEvent(ev); return; }
      if (atHeadOf(from)) stopTheEvent(ev);
    });
    surface.addEventListener('paste', (event: ClipboardEvent): void => {
      event.preventDefault();
      insertPlain((event.clipboardData?.getData('text/plain') ?? '').replace(/[\r\n]+/g, ' '));
      takeSurface();
    });
    surface.addEventListener('input', takeSurface);
    surface.addEventListener('blur', takeSurface);

    /** **从"锁住的那一行"上按下去也要能拖**（本波在真产物上量到的一个**真回归**）。
     *
     *  ⚠️⚠️ **现象**：`contenteditable="false"` 那一块在编辑面里是一个**原子块** ⇒
     *     鼠标从**它里面**按下去，Blink 起的是"只选这一块"那一支笔，**拖不出这一行**
     *     （本波量到：从锁住的那一行起手拖 200px，`getSelection().toString().length`
     *     只有 **4**、`anchor` 与 `focus` 同一行；而**从可改的那一行起手**、
     *     同一套拖法量到 **80**、跨 **5** 行）。
     * ⇒ 这不是"整页还选不动"，是**那一支笔在原子块里画不出来** ⇒ 在这里**补一支**：
     *     按下去先自己起一个 `Range`，之后每一动 `extend` 一次（松手就拆掉）。
     *     ⚠️ **它只在按在 `[data-fixed="true"]` 的那几行里时接管** —— 其余一切
     *        （在可改的行里按下去 ／ 一切键盘）照旧走浏览器那一支，这里一个字都不碰。
     *     ⚠️ 那一对钉子挂在 `document` 上（手拖到面外面也要跟着）——**松手就拔掉**。 */
    let dragging = false;
    const pointRange = (x: number, y: number): Range | null => {
      type Doc = { caretRangeFromPoint?: (a: number, b: number) => Range | null };
      const doc = document as unknown as Doc;
      return typeof doc.caretRangeFromPoint === 'function' ? doc.caretRangeFromPoint(x, y) : null;
    };
    const extendTo = (one: MouseEvent): void => {
      const sel = selectionNow();
      const to = pointRange(one.clientX, one.clientY);
      if (sel === null || to === null) return;
      sel.extend(to.startContainer, to.startOffset);
    };
    const stopDrag = (): void => {
      dragging = false;
      document.removeEventListener('mousemove', extendTo);
      document.removeEventListener('mouseup', stopDrag);
    };
    surface.addEventListener('mousedown', (ev: Event): void => {
      const one = ev as unknown as MouseEvent;
      const row = rowOf(one.target as unknown as Node ?? null);
      if (row === null || row.dataset.fixed !== 'true') return;
      const sel = selectionNow();
      const start = pointRange(one.clientX, one.clientY);
      if (sel === null || start === null) return;
      stopTheEvent(ev); // 浏览器那一支不起了（这一支替它走）
      sel.removeAllRanges();
      sel.addRange(start);
      dragging = true;
      document.addEventListener('mousemove', extendTo);
      document.addEventListener('mouseup', stopDrag);
    });

    // 光标进来 ⇒ 把"按 Ctrl+S"那一句挂到**光标那一行**上（参考那一页的同一个行为）。
    const firstRow = (): HTMLElement | null => surface.querySelector('.persona-line[data-line]');
    const followCaret = (): void => {
      const row = rowOf(caret()?.startContainer ?? null) ?? firstRow();
      if (row !== null) showHintOn(row, draft.name);
    };
    surface.addEventListener('focus', followCaret);
    surface.addEventListener('keyup', followCaret);
    surface.addEventListener('mouseup', followCaret);
  };

  /** **把光标那一格的字收回草稿**（Ctrl+S 之前 ／ 离开这一页之前，两处都要先走它）。
   *
   *  ⚠️⚠️ **为什么非有它不可**（本波量出来的那条 Minor，而它是最坏的一种失效）：
   *     `dirty` 只在 `take()` 里写，而 `take()` 绑在 `blur` 与回车那两下上
   * ⇒ **正在打的那一行，字只在 DOM 里** —— 一按 Ctrl+S，`saveDirty()` 读到的是
   * "没有改动"，页上还回一句"**没有改动 —— 这一下没碰盘**" 。
   *     对一个承诺是"这一页说的是实话：它说出的每一句都对应真的一件事"的页面来说，
   *     **页说"没改动"而改动就坐在屏幕上**是最坏的一种。
   * 那个按钮之所以看不出来，是因为 `mousedown` 会先把焦点拿走（于是先 `blur` 了）
   *     —— 也就是说，**这一条只在键盘那条路上发作**。
   *  ⚠️ 两处调它（`onKey()` 与 `stop()`）—— **一处判据**：少一处就又有一条路漏过去。
   */
  const flushFocused = (): void => {
    const el = document.activeElement as HTMLElement | null;
    if (el === null || el === undefined) return;
    // **一个面之后，拿焦点的是那一块正文那一格**（`.persona-body`）——
    //    真浏览器里 `activeElement` 就是它；而"从任意一格往上找回它所在的那个面"
    //    这一条路照旧留着（替身那一趟焦点可能落面里面的一格正文上）。
    const surface = surfaceOf(el);
    if (surface !== null) surface.blur();
  };

  /** **一段**：左列那条**标签带**（段名那一面旗子）｜ 行号列 ＋ 正文 ｜ 右列归属 ＋ 文件名。
   *
   *  ⚠️ **元素与那四格不是同一个次序**：`persona-body` 在源里排第三，而它在版面上**跨
   *     第 2 ／ 3 两格**（`grid-column: 2 / span 2`，在 CSS 那一处）—— ⇒ 源里那个次序
   *     就是"读起来"的次序（段名 ⇒ 正文 ⇒ 归属），而版面照参考那一页摆。
   * ⚠️ **`data-owned`** 决定整段的画法（旗子那一档 ＋ 正文那一档）： 见
   *     `ownedByTheAuthor()` —— **读不到归属照"不归你"画**（宁可显得谨慎）。
   *  ⚠️ **`data-here`** 是本刀加的一处**交互**（参考那一页有 hover/active）：鼠标停进来
   * 或者光标进这一格 ⇒ 整段提一层底。 它**不是选中**，也不改任何判据。
   */
  const segmentRow = (segment: PersonaSegment): HTMLElement => {
    const row = node('section', 'persona-segment');
    row.dataset.segment = segment.name;
    if (!ownedByTheAuthor(segment.owner)) row.dataset.owned = 'other';
    // **硬的这一条**（本刀）：能不能改 —— 那一步**不在这一页**（见 `mayWrite()`）。
    const writable = mayWrite(segment);
    if (!writable) row.dataset.readonly = 'true';
    // **悬停与"光标在里头"是两层**（本波拆开的）：参考那一页量出来是
    //    **悬停 ink 4% ／ 选中再深一档薄荷** —— 而这一页从前**只有一个 `data-here`**
    //    ⇒ 两个状态糊成一个值（作者的第三张图说的正是"选中是另一档"）。
    row.addEventListener('mouseenter', () => { row.dataset.here = 'true'; });
    row.addEventListener('mouseleave', () => { delete row.dataset.here; });
    row.addEventListener('focusin', () => { row.dataset.active = 'true'; });
    row.addEventListener('focusout', () => { delete row.dataset.active; });
    const left = node('div', 'persona-name');
    left.append(node('span', 'persona-tag', segment.name));
    const middle = node('div', 'persona-body');
    const draft = segment.file === null || segment.file === undefined
      ? null : (drafts.get(segment.file) ?? draftOf(segment));
    if (!writable) {
      // **那一枚牌子照旧在**："这一份整个不能改"是**事实**（与"这一段还没有文件"
      //   同一个族），不是解释。
      // ⚠️⚠️ **而"为什么不能改"那一句今天不摆了**（作者 2026-09-30：这种解释不要）——
      //    服务那一份里的 `why_read_only` 照旧交回来，这一页**不读它**。
      // 正文本身照旧一行一行摆出来（看不见内容就没法判断要不要动它）。
      middle.append(node('p', 'persona-lock', '这一份人不能改'));
    }
    // **本波（2026-09-30 第三次）：没有文件的那一段，正文那一列整个空着** ——
    // 作者把那一句"这一段还没有文件"圈起来说「关于这个，也去掉。」⇒ 那个
    //    `else` 于是反过来写（`if (draft !== null)`）：**没有文件 ⇒ 什么都不画**。
    // 那一块的形状照旧在（旗子 ／ 那条线 ／ 标题），只是正文那一格是空的。
    // ⚠️ **不要在这里补一个字回来** （"空着"就是这一刀要的结果）。
    if (draft !== null) {
      // **哪几行是承重的形状**（那一条 Critical）： 由**服务**算（`lockedLines()`），
      // 而被锁住的那几行**点不开** —— 而页上**一个字都不解释**（`why` 那一格不读）。
      const locks = writable ? lockedLines(segment) : new Set<number>();
      // **本波（作者 2026-10-01）**：**开头那一块 `<!-- … -->` 页上不画** ——
      //    那是"造这份文件时给人看的施工说明／作者契约"，**与正文没有关系**
      // （见 `SHAPE_CLASS_COMMENT`）。 判据由**服务**给（`shape` 里第 ① 类），
      //    页这一侧只把**那几行**算出来、然后**不画它们**。
      //    ⚠️⚠️ **只不画、不删**：`draft.lines` **一个字都不动**（收字那一趟按 `data-line`
      // 逐行读 ⇒ 藏起来的那几行**从来没有被谁碰过**）⇒ **保存递出去的仍是整份正文，
      // 那条注释一个字不丢** （丢它就是"把归属那一列的出处删了" ）。
      // 行号照旧是**草稿下标 ＋ 1** ⇒ 藏了几行之后，画出来的第一行**照旧是它真实的
      //    行号**（`破甲` 那一条藏 8 行 ⇒ 画出来从 `9` 起，不是从 `1` 起）。
      const hidden = hiddenLines(segment);
      draft.lines.forEach((_one, index) => {
        if (hidden.has(index)) return;
        if (!writable) {
          middle.append(fixedLine(draft, index));
        } else if (locks.has(index + 1)) {
          middle.append(shapeLine(draft, index));
        } else {
          middle.append(editorLine(draft, index));
        }
      });
      // **一个块 ＝ 一个编辑面**（本波那一刀，见 `attachSurface()`）：只读那一份不挂面
      //     —— 它那些行今天本来就没有一个 `contenteditable`（"点不开"是它的正身）。
      if (writable) attachSurface(middle, draft, locks, hidden);
    }
    const right = node('div', 'persona-home');
    // ★ 2026-10-12（接手那一刀）**补回来的那一格**：重画那一批（`d084bad`）把这一处
    //   包进了 `if (!ownedByTheAuthor(segment.owner))` ⇒ **归你自己的那几段，页上
    //   不再写「你定的」**（另外三个家 ／ "读不到"那一档照旧画）。那一处也是**没留账
    //   的一处删**：设计稿右列那一格写的是"归属 ＋ 文件名"，而判据那一侧要的正是
    //   "三个家都在页上认得出来"（`ownerClass()` 那四档一个都没少）。
    //   ⇒ 恢复成**无条件画**（读不到 ⇒ `—`：空串与"读不到"不是一件事）。
    right.append(node('span', ownerClass(segment.owner), ownerText(segment.owner)));
    // **本波（作者 2026-09-30 第二次说这一处）**：那一行路径**整个去掉** ——
    //    「关于卡片，**你没有把字也一起删掉**。`你定的/存在方式.md`，这个字还留在页面上」
    //    ⇒ 这一格从此只摆**落点记号**：有文件 ⇒ 一枚点；没有文件 ⇒ 那一句
    // "这一段还没有文件"（ 那是**状态**，不是路径 —— 它照旧在）。
    //    ⚠️ **路径的名字这一页不再画**（上一刀留下的那一行平字也一起走）：见 `presenceMark()`。
    //    ⚠️ **这一格今天没有 `title`**（从前那一句是"这一段的落点：…"—— 而名字就在这一格上；
    //       一句悬停文字去解释"这一格是什么"＝作者 2026-09-30 说的那类解释）⇒ 没有加回来。
    right.append(presenceMark(draft === null ? 0 : 1));
    row.append(left, middle, right);
    // **导航认得这一块**（本刀）： 跳过去落在**这一块自己**的头上（`anchorOf()` ——
    //    2026-09-30 晚订正，落点从"块里第一行 `#+ `"改回"这一块自己"，那一处的账在
    //    `anchorOf()` 上），而"这一段还没有文件"／"整份只读"那两种照旧落在它自己身上
    //    （它一个字都没有 ⇒ 落到那一块的顶上，效果一样）。
    //    ⚠️ **这一格只打一个记号**：`data-navrow` ＝"这一块在导航里"。
    //       ⚠️⚠️ **这一个属性从前差点是两个意思**（早先踩过的一脚）：那时行上还打着一个
    //       "跳过去落在哪"的记号（`data-navat`），而下面这一句当时写的是"这一块里**有没有**
    //       落点" ⇒ `querySelector('[data-nav]')` 命中的是**行上那个**，于是八块有正文的
    // **一块都没进导航**（实测：导航里只剩 4 项）。⇒ 一个属性只许有一个意思；
    //       而"落在哪"这件事今天**不再需要记号**（落点就是这一块自己）。
    row.dataset.navrow = 'true';
    return row;
  };

  /** **第二张清单里的一块**（本波改的： **它与那十段是同一条形状了**）。
   *
   *  ⚠️⚠️ **作者这一刀那两句**：「**把它们升成正经的块**」——
   *     「它们应该长得和别的块一样：**旗子**（**自述** ／ **世界认知**，实心薄荷那块带箭头的
   *      页签）、块顶那条 **2px 的薄荷线**、还有 **正文**（那个文件的那几行，带行号那一列）。」
   * ⚠️ 那面旗上写的是**块的名字**（`THE_READABLE_TITLE`）—— 左边那一面第一版
   *     写的是「我」，作者后来改成 **`自述`**（见那一张表上那一段账）。
   * ⇒ 于是这一处与 `segmentRow()` 那一处**三样并一样**：
   * · **左列**： **那一面块旗子**（`persona-tag` —— 从 `quiet` 那一档**回来**了：
   *         它与别的块**同一支笔、同一个宽度、同一个右沿**）；
   *       · **顶上那 2px**：那一条**本来就在**（`.persona-segment::after` 是通用规则，
   * 这十二块**每块一条**）—— 本波**一个字节都没动它**（见 `style.css` 那一处）；
   * · **正文**： **本波新加的那一列**（`besideLines()` ⇒ `fixedLine()`）——
   *         那个文件的那几行 ＋ 行号那一列，与那十段里只读那一份**同一支笔**。
   *
   * **而"它们只读"这条硬轴一个字都没松**（作者定的：**不要编辑面**）：
   *     · **一个 `contenteditable` 都没有**（`fixedLine()` 那一支画的每一行便是如此）；
   * · **`attachSurface()` 一次都不调**（`segmentRow()` 只对 `writable` 的那几段调）
   * ⇒ **点它们不会有光标、也打不进字** —— 那是"点不开"，不是"点了没反应"。
   *     ⚠️ **"没有编辑面"这件事在页上读得出来**：正文那一格里那几行带 `data-fixed="true"`
   *       （那一格的正身就是它），而 `.persona-body` 上**没有** `contenteditable` 那一个
   * 属性 —— 那一格的判据钉的就是这一对（`test_..._two_read_only_blocks_...`）。
   *
   *  ⚠️ **那几格都不是本页算的**：`label` 从 `unplaced` 来，文件与能不能改从 `rows` 来
   *     —— 两条都在**同一份** `report()` 里（`label` 就是配对的键）。
   *  ⚠️ **`data-readonly` 照旧打着**（"这一份整个不能改"是**事实**）：它决定那一块的底
   * —— 与那十段里只读的几段同一条规矩。
   *     ⚠️⚠️ **而那一枚牌子（「这一块人不能改」）本波走了**（作者 2026-10-01：
   *     「**图2也去掉，已经是不可输入光标状态就不需要再提醒一句了。**」）——
   * "这一块不能改"这件事今天仍**读得到**（那层 4% 的墨色底 ＋ 正文那一格
   * 根本不是一个编辑面），只是**不再写一句话**； 不许拿别的说法补回来。
   *  ⚠️ **`data-owned` 照旧打着**（"不归你"那一档 —— 与 `语气示例` 那几段同一个方向）：
   * 于是那面旗子走的是**中性那一档**（`--color-flag-neutral`），与"归属读不到"
   *     的那几段**同一档**。⚠️⚠️ **与"能不能改"是两条轴**（本刀那条硬轴）：软的那条
   *     说"这是谁的"，硬的那条说"动了会坏" —— 4% 那层底在这一页上仍然只有
   *     `[data-readonly="true"]` 那一处（`style.css` 里量得到）。
   */
  const besideRow = (label: string, info: PersonaRow | undefined): HTMLElement => {
    const row = node('section', 'persona-segment persona-besiderow');
    row.dataset.beside = label;
    row.dataset.owned = 'other'; // 与那十段里"不归你"的那几段同一档
    // **那一条硬轴**：这几块今天**全都不能改** —— 判断照旧从**服务**那一份来
    //    （`file_infos` 里每一份的 `writable`），而它**不是**写死在这一页上的。
    //    ⚠️ 读的是 **`every`**（十二块逐块都要"每一份都改不动"才打那一格）。
    // **本波走掉的 `cannotWrite`**（它读的是 `some`，当年只有那一枚牌子一个下家）：
    //      那一枚牌子一走，这一格没有消费者了 ⇒ 一起收（留着就是一个没人读的判断）。
    const infos = info?.file_infos ?? [];
    if (infos.length > 0 && infos.every((one) => one.writable === false)) {
      row.dataset.readonly = 'true';
    }
    const left = node('div', 'persona-name');
    // **本波：那一面旗子回来了**（本刀那一笔 `quiet` 整个走掉）——
    //    它是**块旗子**那一支笔（实心底 ＋ 右边一枚 10px 的箭头，`clip-path` 在
    //    `.persona-tag` 上）、与别的块**同档字**（10.5px ／ 600 ／ 0.02em）。
    //    ⚠️ 那个 `quiet` 那一档的底（`--color-wash` 4% ＋ `--color-dim`）**不再有**
    //     **下家**（本波是全页唯一一处用它）⇒ 它跟着走（见 `style.css`）。
    left.append(node('span', 'persona-tag', THE_READABLE_TITLE[label] ?? label));
    const middle = node('div', 'persona-body');
    // ⚠️⚠️ **那一句"为什么不能改"今天不摆了**（作者 2026-09-30：这种解释不要）——
    //    服务那一份里的 `why_read_only` 照旧交回来，这一页**不读它**。
    // **而那枚牌子（「这一块人不能改」／「这一块只读」）本波也走了**
    //    （作者 2026-10-01：「**图2也去掉，已经是不可输入光标状态就不需要再提醒
    // 一句了。**」）—— 这一句是他给的**理由**，页上本来就成立：
    //    · 这一块**点不开**（正文那一格上没有 `contenteditable`、`attachSurface()`
    //      一次都不调 ⇒ 点它不出光标、也打不进字）；
    // · ⇒ **这一块上只剩三样**：**旗子**（左列）／**顶上那条 2px 的线**／
    //      **正文那一列**（摆的是那一份文件自己的行 —— 一行都没有就一行都不摆）；
    // **这一页自己加的话，一个字都没有**。
    // · **不许补**：另写一句 ／ 另一枚记号 ／ `title` ／ 提示 —— "去掉"就是不要。
    // 那一枚牌子那两种说法（`cannotWrite` 挑出来的那两种）跟着一起走：
    //      `cannotWrite` 那一格今天**没有消费者了**（见上面那一处）。
    //    ⚠️ **"这一份不能改"这件事一个字都没少**：`data-readonly="true"`（那一层 4%
    //      的墨色底）＋ 正文那一格根本不是一个编辑面（`fixedLine()` 那一条路）。
    // **本波那一列正文**（作者要的那三样里的第三样）：那个文件的那几行 ＋ 行号。
    //     ⚠️ **`fixedLine()` 就是那一支笔**（那十段里只读那一份同一条路）：每一行
    //     一个 `data-fixed="true"`、**一个 `contenteditable` 都没有**。
    //     ⚠️ **一份正文都读不到**（`body` 是 `null` ／ **那一份就是 0 字节** ／
    //     那几份抬头写着"占位"）⇒ 这一块**一行都不摆**，而它**照旧立着**
    //     （旗子 ／ 顶上那条 2px 一个都不动，见 `besideLines()`）。
    const draft = besideLines(info);
    if (draft !== null) {
      draft.lines.forEach((_one, index) => { middle.append(fixedLine(draft, index)); });
    }
    // **本波（作者 2026-10-01）删掉的那一句** —— 它从前在这儿：
    //     `middle.append(node('p', 'persona-placeholder', '这一份还是占位（正文在别处，别拿它当正文看）'))`。
    //     ⚠️⚠️ **作者那一句**：「**就是空着，源文件是空的那就该是空的。**」——
    // ⇒ **这一页摆的 ＝ 那一份文件里有什么，仅此而已**；一句"这一份还是占位"
    //     是这一页自己**多加的话**（它说的那件事，文件里一个字都没有）。
    //     ⚠️ **`placeholder` 那一格照旧有人读**：它现在只判**那一份的行摊不摊**
    //     （见 `besideLines()` —— 一张"东西在别处"的施工说明不是正文，别当正文画）。
    //     服务那一侧照旧交它（`file_info()`，那一份是**有主的**、本仓改不动它）。
    // 与 `.persona-shapewhy` ／ `.persona-nofile` 同一条规矩：作者划掉的形状，
    //     这一页一个字都不许留（那一档样式跟着走，见 `style.css`）。
    const right = node('div', 'persona-home');
    // **本波**：这一格那几条**路径**（`她写的/自述.md` 那些）**整个去掉** ——
    //    与十段同一条规矩（见 `presenceMark()`）：只留**一枚点**，而**份数**记在
    //    `data-files` 上（那一块底下是**两条**路径 ⇒ 那个数得有人记得住）。
    //    ⚠️ **名字照旧在服务那一份里**（`info.files`）—— 去掉的是**这一页画它**这件事。
    // **右边这一列照旧是"归属 ＋ 那一枚落点记号"两格**（与那十段同一支笔）：
    //    归属那一格在这两块上是**空的**（它们没有归属 —— "不归你"那几段也是这一档）。
    //    ⚠️ **它不占地方**（`presenceMark()` 两个分支都不画东西 ⇒ 这一格的高由
    //    左列那面旗子说得算）—— 与那十段里"归属读不到"的那几段**同一个数**。
    right.append(node('span', ownerClass(null), ownerText(null)));
    right.append(presenceMark((info?.files ?? []).length));
    row.append(left, middle, right);
    // **导航那一跳的落点**（本刀）：这一块落在**它自己**上（见 `segmentRow()`）。
    row.dataset.navrow = 'true';
    return row;
  };

  const draw = (report: PersonaReport): void => {
    // The page edits user-authored persona only; memory and channel files
    // stay stored and assembled, but are not duplicated in this navigation.
    segments = editableSegments(report);
    drafts.clear();
    activeHint = null;
    dirty.length = 0;
    // **每一段先立一份草稿**（按**文件**存）—— 于是"正文那一列怎么画"与"保存时写
    //    什么"读的是**同一份**草稿。⚠️ 不先立：`segmentRow()` 会另建一份**临时**的，
    //    而那一格里的字改在临时那一份上 ⇒ **保存写的是原文**（本刀在产物那一趟上量到过）。
    for (const segment of segments) {
      const draft = draftOf(segment);
      if (draft !== null) drafts.set(draft.name, draft);
    }
    list.replaceChildren();
    // **2026-10-07（作者拍的）：把那两条小标题删了** ——
    //    就是「宪法（怎么说话 ／ 怎么待人 ／ 在这套机制里的位置）」与
    //    「自己那一本（那扇门写的那几份）」那两条。
    //    ⚠️ **理由有两条，都是他点的**：
    //      · ① **跳转行要一一对应**：那两条是**小标题、不是段**，却跟着进了左栏那一列
    // ⇒ "列出来的"与"真有几段"就对不上了 （下面 `refreshNav()` 列的正是这一串）；
    //      · ② **那两块的分法不再是今天的分法**：`宪法` 那句点到的"**在这套机制里的位置**"
    //        已经搬去**环境那一层**；而"怎么写"那一族已经搬去**通道那一层** ⇒
    // 留在这一页上，会把另外两层的东西说成 persona 的 。
    // ⇒ 删掉之后：**左栏列出来的就是那几段本身**（一一对应 ），
    // 而层次照旧由段序那张表说话（ 这一页不另立一套分组）。
    // ⚠️⚠️ **2026-10-12（接手那一刀）**：这一处从前是
    //     `for (const segment of segments.slice(0, 6))`，上面还压着一句英文注释
    //     （`Chapters 7–10 are omitted from the editor; statistics retain the complete
    //     report.`）—— 那是重画那一批（`d084bad`）原样带进来的一处**掉段**：
    //     段序第 7–10 段（真树里是 `她写的那两份` ／ `世界认知` ／ `习惯` ／ `示例对话`）
    //     **一个元素都不画**，而这一页抬头照旧按 `segments.length` 写"10 段"
    //     ⇒ 页上那个数与画出来的块数对不上（量到的是 10 段 ／ 6 块）。
    // ⚠️ 那四段**不是**"只读"那一档：只读的段照旧画出来（见 `segmentRow()` 里
    //     `if (!writable)` 那一条路）；这四段是**压根没有块** ⇒ 连"这一段在不在"
    //     都读不到（真树里 `unplaced()` 是空的 ⇒ 第二张清单那条路也不画它们）。
    //     ★ 其中 `她写的那两份` 就是 `她写的/自述.md` 那一份 —— 它从页上消失，
    //     与"她写不了自己的自述"那件事是两笔账，而摆在一页上都读不到。
    // ⇒ **恢复成整份段序都画**（`tests/test_web.py` 里那一条判的就是"十段，一位不挪"）。
    for (const segment of segments) {
      list.append(segmentRow(segment));
    }
    otherList.replaceChildren();
    // **第二张清单**：`unplaced` 交的是**名字**，而那两块的**文件与能不能改**在
    //    `rows` 里（同一份 `report()`）—— `label` 就是配对的键。
    const beside = report.persona_scope === 'user-authored' ? [] : report.unplaced ?? [];
    const byLabel = new Map((report.rows ?? []).map((one) => [one.label, one]));
    for (const label of beside) otherList.append(besideRow(label, byLabel.get(label)));
    otherBox.hidden = beside.length === 0;
    // ⚠️⚠️ **这一处从前写着那一句抬头**（`otherHead.textContent = \`这张表里还有这
    //     ${beside.length} 行 —— 它们不在段序里\``）—— 作者 2026-10-01 把它圈掉了
    //     （那笔账记在上面建 `otherBox` 那一处）。
    // 而 `beside.length` 照旧是**算出来的**、照旧有人读：它决定 `otherBox.hidden`。
    // ⚠️ **2026-10-03 订正（整理态那一刀）**：上面那句"而 `load()` 那一句状态照旧
    //     按它写『表里另多出 N 行』"**不再是那样了** —— 那两行补进段序之后
    //     `unplaced` **就是空的**，而"另多出 0 行"是一句**没有内容的话**（它说的是
    //     "两张清单对得上"，而那句话今天该由**不说**来表达）。⇒ `load()` 那一句
    //     从此**只在 N > 0 时才写那一截**（见那里）。
    refreshHead();
    // **导航就铺在这一次之后**（ 它列的正是**刚刚铺上去的那几块** —— 十段 ＋ 那两块）
    // ＋ 那一枚"现在这一块"也在这里现算一次（`scroll` 那一颗钉子挂在下面）。
    refreshNav();
    trackActive();
    // **那一枚"现在这一块"跟着滚动动**（本刀加的）—— 钉子挂一次就够（`load()` 会被调
    // 好几次，每调一次挂一颗，滚一下就算十遍）。 `passive: true`：这一颗**从不**
    //    `preventDefault`（它只读位置）。
    scroller.removeEventListener('scroll', trackActive);
    scroller.addEventListener('scroll', trackActive, { passive: true });
    trackActive();
  };

  /** **读一次**（`GET /persona` ⇒ 把那一份铺开）—— 打开时走的就是它，"重载"也是它。 */
  let readEpoch = 0;
  let pageStopped = false;
  const readHint = pageLoad(column, '人设', () => { void load(); });
  const load = async (discard = false): Promise<void> => {
    flushFocused();
    if (saving || (dirty.length > 0 && !discard)) return;
    const edits = editEpoch;
    const epoch = ++readEpoch; readHint.start(); saveButton.disabled = true;
    say('正在读那十段…', 'busy');
    try {
      const response = await send(REPORT_ROUTE, { headers: { Accept: 'application/json' } });
      if (!response.ok) {
        readHint.fail();
        say(`读取人设失败（HTTP ${response.status}）`, 'bad');
        return;
      }
      const report = (await response.json()) as PersonaReport;
      if (pageStopped || epoch !== readEpoch || editEpoch !== edits) return;
      draw(report); conflictBox.hidden = true; readHint.ready(); saveButton.disabled = false;
      say('', 'idle');
    } catch (exc) {
      if (pageStopped || epoch !== readEpoch) return;
      readHint.fail();
      say(`读取人设失败（${(exc as Error).message}）`, 'bad');
    } finally {
      if (!pageStopped && epoch === readEpoch) saveButton.disabled = false;
    }
  };

  // Explicit three-way workspace: base and latest are read-only; accepting the
  // merge changes only this draft and its precondition, never writes automatically.
  const offerMerge = (name: string): void => {
    conflictBox.replaceChildren(); conflictBox.hidden = false;
    const read = node('button', 'btn persona-merge-read', '读取新版供手动合并');
    read.setAttribute('type', 'button');
    conflictBox.append(node('p', undefined, name + '：草稿保留，未自动刷新。'), read);
    read.addEventListener('click', () => { void (async () => {
      read.setAttribute('disabled', 'true');
      try {
        const response = await send(REPORT_ROUTE, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(`服务回 ${response.status}`);
        const report = await response.json() as PersonaReport;
        if (pageStopped) return;
        const latest = report.segments.flatMap(one => one.files ?? [])
          .find(one => one.name === name);
        const segment = editableSegments(report).find(one => one.file === name);
        const revision = latest?.revision ?? segment?.revision;
        const remote = (latest as { body?: string | null } | undefined)?.body
          ?? segment?.body ?? '';
        if (typeof revision !== 'string') throw new Error('新版没有可用 revision，请检查服务版本');
        flushFocused();
        const draft = drafts.get(name);
        if (!draft) return;
        const generation = draft.generation;
        const area = (cls: string, title: string, value: string,
                      readonly: boolean): HTMLTextAreaElement => {
          conflictBox.append(node('p', undefined, title));
          const cell = document.createElement('textarea'); cell.className = cls;
          cell.value = value; cell.readOnly = readonly; conflictBox.append(cell); return cell;
        };
        conflictBox.replaceChildren();
        area('persona-merge-base', '读时正文', draft.base ?? '', true);
        area('persona-merge-remote', '盘上新版', remote, true);
        const merged = area('persona-merge-text', '手动合并稿（请对照新版编辑）', draft.lines.join('\n'), false);
        const apply = node('button', 'btn persona-merge-apply', '使用合并稿（尚未保存）');
        apply.setAttribute('type', 'button'); conflictBox.append(apply);
          const cancel = node('button', 'btn persona-merge-cancel', '取消合并（保留原草稿）');
          cancel.setAttribute('type', 'button'); conflictBox.append(cancel);
          cancel.addEventListener('click', () => {
            conflictBox.hidden = true;
            say('已取消合并；原草稿和读时版本未变，尚未保存。', 'warn');
          });
        apply.addEventListener('click', () => {
          flushFocused();
          if (saving || draft.generation !== generation) {
            say('原草稿又有新输入，未覆盖。请重新读取新版后合并。', 'warn'); return;
          }
          draft.lines.splice(0, draft.lines.length, ...linesOf(merged.value));
          draft.revision = revision; draft.base = remote; markDirty(name);
          const scroll = column.scrollTop;
          list.replaceChildren(...segments.map(segmentRow));
          column.scrollTop = scroll;
          markDirty(name); refreshHead(); refreshNav(); conflictBox.hidden = true;
          say('合并稿已放回编辑区，尚未保存。请核对后再保存。', 'warn');
        });
      } catch (exc) {
        say(`读取合并依据失败：${(exc as Error).message}。草稿仍保留。`, 'bad');
        read.removeAttribute('disabled');
      }
    })(); });
  };

  /** **保存一段** ⇒ `POST /persona`（ 那串时间戳**由这一页**挑）。 */
  const saveText = async (name: string, text: string,
                          expect: string): Promise<SaveOutcome | null> => {
    const stamp = now();
    if (!isAStamp(stamp)) {
      say(`这一页自己挑出来的那串时间戳不成形状：${stamp}`, 'bad');
      return null;
    }
    const target = /^(宪法\.md|角色设定\.md)#([a-z][a-z-]{0,63})$/.exec(name);
    const ask: SaveAsk = target
      ? { name: target[1], section: target[2], text, stamp, slot: 'section-' + target[2], expect }
      : { name, text, stamp, slot: DEFAULT_SLOT, expect };
    let response: Response;
    try {
      response = await send(SAVE_ROUTE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ask),
      });
    } catch (exc) {
      say(`没递出去：${(exc as Error).message}`, 'bad');
      return null;
    }
    const body = await response.text();
    const outcome = outcomeOf(response.status, body);
    if (outcome.kind !== 'saved') {
      say(outcome.line, outcome.kind === 'collision' ? 'warn' : 'bad');
      if (outcome.kind === 'conflict') offerMerge(name);
      return outcome;
    }
    // ⚠️⚠️ **从那一份正文里解，不要再 `await response.json()`** —— 一个响应体只读得了一
    //     次（`text()` 已经把那条流读干了）⇒ 第二次读**必失败**，于是页上那句回话会退回
    //     缺省值（"字节数"成了正文长度、"版本"成了空串、而"旧版留在哪儿"说成了"没有旧版"）。
    // 本刀在真浏览器上量到过这一处（第一次保存那一行字就是错的）—— 修的是**读法**。
    let got: { bytes?: number; version?: string; history?: string | null; revision?: string } = {};
    try {
      got = JSON.parse(body) as typeof got;
    } catch {
      got = {};
    }
    const draft = drafts.get(name);
    if (draft) {
      draft.revision = got.revision ?? null;
      draft.base = text;
    }
    if (target && got.revision) {
      for (const other of drafts.values()) {
        if (other.name.startsWith(target[1] + '#') && other.revision === expect)
          other.revision = got.revision;
      }
    }
    const kept = got.history;
    const version = got.version ?? '';
    const where = kept === null || kept === undefined
      ? ' · 盘上原来没有这一份 ⇒ 没留下旧版' : ` · 旧版留在 ${kept}`;
    // **那一句边界就挂在"写进去了"这一句后面**（第二轮：它从抬头挪到了这儿）——
    // **它只在真的写下去的那一次说**（"没改动" ／ "撞名" 那两趟都不说它：
    //     那两趟没碰盘，而这一句是"这一条口子能改她是谁"那一句）。
    const detail = `存了 ${got.bytes ?? text.length} 字节`
      + (version === '' ? '' : ` · 这一版：${version}`) + where + ' · ' + BOUNDARY_LINE_SHORT;
    say(kept === null || kept === undefined ? '已保存' : '已保存 · 历史版本已保留', 'ok', detail);
    return { kind: 'saved', bytes: got.bytes ?? text.length, version,
             history: kept ?? null, left: kept !== null && kept !== undefined };
  };

  /** 保存按钮那一件事：**只写改动过的那几段**（没动过的，一次都不碰）。
   *
   * ⚠️⚠️ **写完之后那个数、那个状态都要留在页上** —— 所以**不**在这里再读一次
   *     `GET /persona`：那一次会把"存了 N 字节 · 这一版…"**盖掉**（本刀在产物那一趟
   *     上量到过：存完页上写着"读到了：10 段…"，而人看不到自己那一版存到哪儿了）。
   *     ⇒ 盘上现在是什么样，由**那一次保存的答案**说；要再读一遍盘，那一下是"重载"。
   * ⚠️ **一段写成了就从"改过的"那一列里划掉**（ 划掉的是**它**，不是整张表）：
   *     一段写成了，下一次保存不该再写一遍；而**没写成的那些照旧留在那一列里**
   * （它们还是改过、还没落盘）。 三样都要在两处说清（`dirty` 那一列 ／ 那个数
   *     ／ 页上那一行状态）—— 只做一样，人就会在页上看到一件与盘不符的事。
   */
  const saveDirty = async (): Promise<void> => {
    if (saving || pageStopped) return;
    flushFocused();
    const selected = activeHint?.name ?? (dirty.length === 1 ? dirty[0] : null);
    const names = selected && dirty.includes(selected) ? [selected] : [];
    if (names.length === 0 && dirty.length > 0) {
      say('请选中需要保存的未保存章节；其他草稿会保留。', 'warn'); return;
    }
    if (names.length === 0) {
      say('没有改动 —— 这一下没碰盘（要读盘上现在那一份，按「重载」）', 'warn'); return;
    }
    saving = true; saveButton.disabled = true; reloadButton.disabled = true; refreshHead();
    try {
      for (const name of names) {
        flushFocused();
        const draft = drafts.get(name);
        if (!draft) continue;
        if (typeof draft.revision !== 'string') {
          say('缺少读时 revision，未保存。请保留草稿并重新加载新版后合并。', 'bad');
          offerMerge(name); return;
        }
        const text = draft.lines.join('\n');
        const generation = draft.generation;
        const outcome = await saveText(name, text, draft.revision);
        flushFocused();
        if (outcome === null || outcome.kind !== 'saved' || pageStopped) return;
        if (draft.generation === generation && draft.lines.join('\n') === text) {
          const at = dirty.indexOf(name);
          if (at >= 0) dirty.splice(at, 1);
        } else {
          markDirty(name); // Compare with the just-acknowledged baseline, including an in-flight undo.
          say('提交的那一版已保存；在途新输入仍是未保存草稿。', 'warn');
        }
        markAllSaved(); refreshHead();
      }
    } finally {
      saving = false; saveButton.disabled = false; reloadButton.disabled = false;
      markAllSaved(); refreshHead();
    }
  };

  saveButton.addEventListener('click', () => { void saveDirty(); });
  let confirmingReload = false;
  reloadButton.addEventListener('click', () => { void (async () => {
    flushFocused();
    if (saving || confirmingReload) return;
    if (dirty.length > 0) {
      confirmingReload = true;
      let accepted = false;
      try {
        accepted = await confirmAction('放弃未保存草稿？',
          '重载会放弃所有未保存草稿，并读取盘上新版。', '放弃草稿并重载');
      } finally { confirmingReload = false; }
      if (!accepted || saving || pageStopped) return;
    }
    void load(true);
  })(); });

  /** **Ctrl+S 就是那一下保存**（ 把关的人不必去找那个按钮）。
   *
   *  ⚠️⚠️ **它挂在窗口上**（不是挂在这一块上）：光标在正文那一格里时，是**那一格**
   *     在收键盘 ⇒ 挂在容器上的监听收不到 `Ctrl+S`（只有窗口级那一个收得到）。
   *  ⚠️ **换页时 `stop()` 把它拔掉**：这一页走了之后，`Ctrl+S` 照旧是浏览器那一份
   *     "保存这一页"，**不许**被一个已经不在屏幕上的页面吞掉。
   *  ⚠️⚠️ **本波补的那一下：先把光标那一格的字收回来**（`flushFocused()`）——
   * 少了它，**正在打的那一行只在 DOM 里**，而这一下会老实回答"没有改动"
   * （理由逐字写在 `flushFocused()` 上）。 这一颗钉子**只走键盘那条路**，
   *     所以那条判据也必须**不派 `blur`** 地判它。 */
  const onKey = (ev: Event): void => {
    const key = ev as KeyboardEvent;
    if (key.ctrlKey && !key.altKey && (key.key === 's' || key.key === 'S')) {
      ev.preventDefault();
      if (saving) return;
      flushFocused(); // 先收字，再保存（次序是承重的）
      void saveDirty();
    }
  };
  window.addEventListener('keydown', onKey);

  void load();

  return {
    root: column,
    show: (): void => { column.hidden = false; shell.bottom.hidden = true; },
    /** **离开这一页**（路由器换页时叫的这一下）—— 本波补的那两条：
     *
     *  ⚠️⚠️ **两条都不许省**（那一条 Minor 说的是"它悄悄把这一页藏起来就走了"）：
     * · **先把光标那一格的字收回来**（`flushFocused()`）—— 与 Ctrl+S 走**同一处
     *      判据**；少了它，屏幕上最后那几笔连草稿都没有进去过；
     * · **有没保存的就说出来**（不许静默）：这一页的把手会被丢掉
     *      （`pages.ts::route()` 每次换页都重新 `build()` 一份）⇒ 那几段改过的字
     *      **真的会没** —— 而"真的会没"这件事**必须在页上说出来**。
     * 说在哪儿：那一页的状态行（回来时照旧看得见）＋ **左栏那一项的 `title`**
     *      （那是这一页走了之后**唯一还留在屏幕上**的地方 —— 不写它，那句话就白说了）。
     *  ⚠️ 换页那一颗 Ctrl+S 的钉子照旧要拔（少了它，浏览器那一份保存会被吞掉）。 */
    stop: (): void => {
      pageStopped = true; readEpoch++;
      flushFocused();
      if (dirty.length > 0) {
        const line = `这一页有 ${dirty.length} 段改过、没保存 —— 离开这一页就丢了`;
        say(line, 'warn');
        const item = shell.railNav.querySelector('.rail-item[data-page="persona"]');
        if (item !== null) item.setAttribute('title', line);
      }
      column.hidden = true;
      shell.bottom.hidden = false;
      window.removeEventListener('keydown', onKey);
      // **那一颗跟着滚动动的钉子也要拔**（本刀）：这一页走了之后，那一格就换人了
      //   —— 留着它，下一处滚动会去动一份已经不在屏幕上的导航。
      scroller.removeEventListener('scroll', trackActive);
    },
  };
}
