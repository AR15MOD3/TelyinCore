import { uiLabel } from './ui-language';
import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { initializeDisplayScope, readDisplaySession, writeDisplaySession } from './display-storage';
import { pageLoad } from './page-load';
// **「模型」那一页**（`#/engine`，刀 4 重做的那一页：**看 ＋ 选**两件事挤在一页上）。
//
// ⚠️ **一页两栏**（设计稿 裁定 5）：左列是**卡片清单**（一条档案一张），右列是**那一条的四段**。
// 不新增导航项 —— 它从前担一件事（看），现在担两件（看 ＋ 选），所以用左右两栏分开。
//
// ⚠️ **这一页自己发请求**（与从前那句"这一页不发任何请求"相反）：它读 `GET /engine`、
//    写 `POST /engine` ／ `/engine/secret` ／ `/engine/probe`（那四句话全在 `./engine` 里）。
// ⚠️ **那一句边界由服务交出来**（`report.boundary`）：本文件**一个字都不写**它
//    —— "一处"这条规矩就是靠这个落地的（改一次，几处一起动）。
// ⚠️ **零框架**：全是 `document.createElement`，**没有一处 `innerHTML`**（那条判据钉着）。
// ⚠️ **这一页不读时钟**：那串时间戳由服务那一侧管（与那一页的成法一致）。

import {
  engineCommand, engineReport, probeEngine, saveEngineDraft,
  type ModelSpec, type Preset, type ProbeResult, type Profile, type Reply,
} from './engine';
import type { Chrome, Panel } from './shell';
import { combo, selectControl, refreshControls, resetControls } from './engine-controls';
import type { ComboItem } from './engine-controls';
import { chatgptAuth } from './chatgpt-auth';
import { isPlanProvider, providerChoices, nextProviderName } from './engine-providers';

type EngineDraft = {
  values: Record<string, string>;
  modelId: string;
  vision: boolean;
};
type EngineDrafts = { fresh?: EngineDraft; selected?: string | null; profiles: Record<string, EngineDraft> };

function readDrafts(): EngineDrafts {
  try {
    const stored = JSON.parse(readDisplaySession('engine-drafts') ?? '{}');
    return { fresh: stored.fresh, selected: stored.selected, profiles: Object.assign(Object.create(null), stored.profiles) };
  } catch {
    return { profiles: Object.create(null) };
  }
}

export function hasEngineDraft(): boolean {
  const drafts = readDrafts();
  return !!drafts.fresh || Object.keys(drafts.profiles).length > 0;
}

/** 那几格的名字（ 与 `engine.KEYS` 同一套字；`base_url` 与 `model` 在上面画过了）。 */
type Field = 'model' | 'thinking' | 'reasoning_effort' | 'temperature' | 'max_tokens'
  | 'context_window' | 'base_url' | 'reasoning_budget_tokens';


/** 那几段的名字（ 编号一起去掉：只留 `③` 的话看着像掉了两段）。
 * 顺序就是它们落进两列网格的顺序：基本信息 ｜ API 设置 ／ 模型设置 ｜ 高级。 */
const SECTIONS = ['API 设置', '模型设置', '高级'] as const;

function el(tag: string, cls?: string, text?: string | ReturnType<typeof corePhrase>): HTMLElement {
  const one = document.createElement(tag);
  if (cls !== undefined) one.className = cls;
  if (text !== undefined) { if (typeof text === 'string') one.textContent = text; else coreSetUIText(one, 'textContent', text); }
  return one;
}

/** **给一颗按钮挂上"抛了要说话"**（2026-10-05）。

 *  ⚠️⚠️ 这一页连着三次"点了没反应"，而三次都是**处理函数里抛了**：
 *    `nameIn is not defined`（草稿卡）／ `wanted is not defined`（保存）…
 *    —— `draw()` 那条守卫只管**画**，管不到**点**。⇒ 点的那一下也要把话说出来，
 * 而不是让人对着一个按键猜。 抛完照旧往上抛（控制台里也要有）。
 */

function button(label: string, cls = 'btn'): HTMLButtonElement {
  const one = document.createElement('button');
  one.type = 'button';
  one.className = cls;
  uiLabel(one, label);
  return one;
}

let fieldId = 0;

/** 字段名在上、控件在下；组合输入框的标签也关联到实际输入。 */
function row(label: string, cell: HTMLElement, hint?: string, required = false): HTMLElement {
  const box = el('div', 'engine-row');
  const tag = el('label', 'engine-label', label);
  const control = cell.tagName === 'DIV' ? cell.querySelector('input') : cell;
  if (control) {
    control.id = `engine-field-${++fieldId}`;
    control.setAttribute('aria-label', label);
    tag.setAttribute('for', cell.tagName === 'SELECT' ? `${control.id}-button` : control.id);
  }
  if (required) {
    // **必填那个记号**（柔红）：它是给眼睛看的，而"真拦"在下面保存那一下。
    const mark = el('span', 'engine-required', '*');
    tag.append(mark);
    // **把记号交给那一格拿着**（2026-10-05 作者：「用 local 的时候这个密钥就不用写必填了」）——
    // ⚠️ 「API 密钥」那一格是**按地址现判**的（`applyKeyRow()`）⇒ 记号也得能跟着收起来
    // （`hidden` 而不是删掉：地址改回非本机时要能露回来 ）。
    (cell as HTMLElement & { requiredMark?: HTMLElement }).requiredMark = mark;
  }
  box.append(tag, cell.tagName === 'SELECT'
    ? selectControl(cell as HTMLSelectElement, label) : cell);
  // **提示那一位永远在**（没有提示也放一个空的）—— 作者要的"预留好那一行、
  //    钉死板块尺寸"：不然某一格有提示、另一格没有，**行高就不一样**，
  //    几张卡的高度跟着变 ⇒ 整页的版式被内容牵着走。
  //    ⚠️ 空着那一个不占字，而 CSS 给了固定行高（见 `.engine-hint`）。
  box.append(el('span', 'engine-hint', hint ?? ''));
  return box;
}

function input(kind: string): HTMLInputElement {
  const one = document.createElement('input');
  one.type = kind;
  one.autocomplete = kind === 'password' ? 'new-password' : 'off';
  one.setAttribute('autocomplete', one.autocomplete);
  one.className = 'engine-input';
  return one;
}

function pick<T extends string>(choices: readonly T[]): HTMLSelectElement {
  const one = document.createElement('select');
  one.className = 'engine-input';
  for (const choice of choices) {
    const opt = document.createElement('option');
    opt.value = choice;
    coreSetUIText(opt, 'textContent', (choice === '' ? corePhrase("上游默认") : choice));
    one.append(opt);
  }
  return one;
}

export function enginePage(shell: Chrome): Panel {
  const column = el('div', 'page-engine');
  const left = el('div', 'engine-left');
  const right = el('div', 'engine-right');
  const notice = el('p', 'engine-notice');
  notice.setAttribute('role', 'status');
  // 那一句边界（柔红）—— **内容由服务交出来**（这一句是这一页的正文之一，不是脚注）。
  // ⚠️ 这里**从前挂着两行红字**（这一页那句边界 ＋ `/say` 那条边界的说明）。
  // **2026-10-04 作者看过实物之后说去掉** —— 而"那句话的住处"照旧在
  //    `web.py`（`ENGINE_BOUNDARY_LINE` ／ 模块抬头）与 `src/web/README.md`：
  //    页上不显示 ＝ 不等于那条话不成立。
  // **2026-10-09 作者：「给模型页加个标题 叫 模型 · Model」** ——
  // 它是**通栏**的第一件（占满 1120 那两栏 ）， 样式与另外几页**同一档**
  // （`.page-engine > h1` 那一条 ）。⚠️ 英文照作者**写的原样**：`Model`
  // （ 2026-10-09 作者：「**用全大写。我是笔误了。**」 —— 于是与另外几页同一个写法 ）。
  const title = el("h1", "page-title", corePhrase("模型 · MODEL"));
  const workspace = el('div', 'engine-workspace');
  const editor = el('div', 'engine-editor');
  const actionsHost = el('div', 'engine-page-actions');
  actionsHost.append(notice);
  editor.append(actionsHost, right);
  workspace.append(left, editor);
  column.append(title, workspace);
  shell.content.replaceChildren(column);

  let report: Reply<Awaited<ReturnType<typeof engineReport>>['data']> | null = null;
  let picked: string | null = null;
  // **"新建中"那一个状态**（2026-10-04 作者点出来的 bug：按「+ 新建一条」没反应）。
  //    ⚠️ 从前那一下只是把 `picked` 清成 `null`，而 `chosen()` 会**回落到"当前那一条"**
  //    ⇒ 等于把同一条重画一遍（名字 ／ 地址 ／ 模型全在）⇒ 看着什么都没有发生。
  let fresh = false;
  // **还没保存的那把钥匙**（`2026-10-04` 实测出来的真原因）：那一格每次重画都会
  //    **被重建** ⇒ 作者填了它、一按别的按钮（换协议 ／ 获取模型列表 ／ 新建 …）就**丢了**
  //    ⇒ 探活于是**不带钥匙**发出去 ⇒ 上游回 401。
  //    ⚠️ "只写不回显"说的是**服务那一侧永不回传**它（那条照旧）；而**页自己记着**这条
  //    还没落盘的字是必需的 —— 不然"填了又没了"就成了这一页的常态。
  let typedKey = '';
  let editEpoch = 0;
  let saving = false;
  let captureDraft: (() => void) | undefined;
  // A typed key can survive route changes in this tab, but is never serialized.
  const draftKeys = new Map<string, string>();
  const draftKey = (name: string | undefined) => name === undefined ? 'new' : 'saved:' + name;
  const updateDraft = (): void => { editEpoch++; captureDraft?.(); };
  right.addEventListener('input', updateDraft);
  right.addEventListener('change', updateDraft);
  function rememberSelection(name: string | null): void {
    writeDisplaySession('engine-drafts', JSON.stringify({ ...readDrafts(), selected: name }));
  }
  function changeDraft(name: string | undefined, draft?: EngineDraft): void {
    const drafts = readDrafts();
    if (name === undefined) drafts.fresh = draft;
    else if (draft) drafts.profiles[name] = draft;
    else delete drafts.profiles[name];
    writeDisplaySession('engine-drafts', JSON.stringify(drafts));
    if (!draft) draftKeys.delete(draftKey(name));
  }
  let stopAuth: (() => void) | undefined;
  // **模型那一格"显示的是名字、发出去的是 id"**（作者点过：取回的与已填的看着不一致）：
  //    · `modelItems` 是当前那一份候选（`{value: id, label: 人话名字}`）；
  //    · `modelValue` 是**选中的那个 id**（手打的名字它会落空 ⇒ 那时就用输入框里的原文）。
  let modelItems: { value: string; label: string; field?: string }[] = [];
  let modelValue = '';
  // **上游对每个模型报的那几格**（窗口 ／ 温度 ／ 推理强度）——
  //    "选一个模型就自动填那三格"要它（作者要的；没标注的用默认值）。
  let modelSpecs: ModelSpec[] = [];
  //: **上游报的那几档推理强度**（实测 DeepSeek 报 `["low","high","max"]`）——
  //  「推理强度」那个下拉照它画；没报就用默认那三档（作者要的"从模型的上游获取"）。
  let modelEffortLevels: string[] = [];
  // Explicit saved values and user edits outrank asynchronous catalog defaults.
  const specOverrides = new Set<Field>();
  let rememberedEffort = '';
  //: 「图片输入」那一行的落点（取回列表之后填"能收图 ／ 只看文本"）。
  let cellsVision: HTMLElement | null = null;
  let visionHint: HTMLElement | null = null;
  const cells: Partial<Record<Field | "name", HTMLInputElement | HTMLSelectElement>> = {};

  /** **按模型 id 把那三格填上**：上游报了就用它（并写明"上游默认"），
   * 没报就**留空**（＝"还没人说过"⇒ 不发送 ⇒ 用引擎自己的）—— 温度与推理强度**一并** 。
   *  ⚠️ 它是"**选一个模型就自动填**"那条要求的落点（探活那一趟也调它）。 */
  function applySpecOf(id: string): void {
    // **这一页唯一决定"那几格显示什么"的地方**（照参考项目 `ListedModel` 那四格）。
    //    "picking the model sets the endpoint's `multimodal` to this" —— 选谁就照谁设。
    //    ⚠️ 上游没说的（`null`）**不许落回本机的默认**（那一类漏过来过三次）：
    //       · 上下文上限 ⇒ **空着**（空着就要说清它在等谁）；
    //       · 最大输出 ⇒ 留我们那个出站额度（16384），只在**上游的上限更小**时压到它；
    //       · 图片输入 ⇒ 落"关"，而提示写明"上游没说 —— 可以自己扳"；
    //       · 推理强度 ⇒ 上游报了用上游那几档；没报**留空**（档位表第一个那个空档，
    // 显示"（不设）"⇒ 一样不发）；
    // · 温度 ⇒ 上游报了就填（并写明"上游默认"）；没报**留空**（2026-10-05 作者）
    const spec = modelSpecs.find((each) => each.id === id);
    modelEffortLevels = spec?.effort_levels ?? [];
    fillEfforts();
    const window = cells.context_window as HTMLInputElement | undefined;
    const temp = cells.temperature as HTMLInputElement | undefined;
    const effort = cells.reasoning_effort as HTMLSelectElement | undefined;
    const maxOut = cells.max_tokens as HTMLInputElement | undefined;
    if (window && !specOverrides.has('context_window')) {
      window.value = ''; // Blank means follow upstream, not a copied manual cap.
      coreSetUIText(window, 'placeholder', (spec?.context_window ? [corePhrase("自动跟随上游（当前 "), spec.context_window, corePhrase("）")] : corePhrase("自动查询；未知时使用 128K（131072）")));
    }
    if (maxOut && spec?.max_output_tokens && !specOverrides.has('max_tokens')) {
      // 上游那一格是"**它最多接受多少**" ⇒ 只在我们的额度**更大**时压下来（并说清为什么）。
      const ours = Number(maxOut.value || 0);
      if (ours > spec.max_output_tokens) {
        maxOut.value = String(spec.max_output_tokens);
        coreSetUIText(maxOut, 'title', [corePhrase("该模型的输出上限为 "), spec.max_output_tokens, corePhrase(" token，已自动调整")]);
      }
    }
    if (temp && !specOverrides.has('temperature')) {
      // **上游没报就留空**（2026-10-05 作者："先做温度吧"）——与地址／模型名／上下文上限
      // 同一条原则：那个 `0.8` 原是**照着 DeepSeek 的实物**抄的 ⇒ 不该当缺省 。
      // 留空 ⇒ **不发送**（`LiveConfig.values()` 读的是档案里那一格本身 ）
      // ⇒ 用**引擎自己**的默认（本机那台是 `--temperature 0.7`，见启动器 ）。
      temp.value = spec?.temperature != null ? String(spec.temperature) : '';
      coreSetUIText(temp, 'placeholder', (temp.dataset.subscription === 'yes' ? corePhrase("订阅接口不支持调整") : (spec?.temperature != null ? corePhrase("已配置——上游默认") : corePhrase("上游默认"))));
    }
    if (effort && !specOverrides.has('reasoning_effort')) {
      // **上游没报就留空**（2026-10-05 作者："一并"）—— 与温度那格同一条原则：
      // 那个 `'high'` 是**照着参考那边抄的** 。 留空 ＝ 选到档位表第一个那个**空档**
      // （显示"（不设）"）⇒ 保存时不写进档案 ⇒ **不发这个参数** ⇒ 用引擎自己的默认 。
      effort.value = spec?.effort_default ?? '';
      rememberedEffort = effort.value;
    }
    if (effort) {
      coreSetUIText(effort, 'title', (rememberedEffort && !modelEffortLevels.includes(rememberedEffort) ? corePhrase("保留手动推理强度；上游尚未确认此档位，实际支持以上游为准") : corePhrase("")));
      refreshControls();
    }
    // **图片输入**（2026-10-05 又改一次 —— 作者："保存后图片输入自动关闭了"）：
    // ⚠️ 从前这里**无条件**按上游压成 'yes' ／ 'no' ⇒ 人扳过、也存进档案的那一格
    // 每次"取回列表"都被压回去（与下面"人也能自己扳（那是一份偏好）"**互相打架** ）。
    // ⇒ **档案表过态**（`multimodal` 是 true ／ false）⇒ **那是人的偏好** ⇒ 谁也不许压 ；
    // 只有**没表过态**（`null` ／ 没有那一格）⇒ 才让上游那份 `input_images` 说了算 。
    if (visionHint) {
      coreSetUIText(visionHint, 'textContent', (spec?.input_images === true ? corePhrase("模型支持图片输入") : (spec?.input_images === false ? corePhrase("模型仅支持文本") : corePhrase("上游未报告图片能力"))));
    }
    if (cellsVision) {
      const said = (chosen(report) as Profile & { multimodal?: boolean | null } | null)?.multimodal;
      if (said === undefined || said === null) {
        cellsVision.dataset.on = spec?.input_images === true ? 'yes' : 'no';
      }
      coreSetUIText(cellsVision, 'title', (spec?.input_images === true ? corePhrase("服务商或模型目录声明支持图片输入") : (spec?.input_images === false ? corePhrase("服务商声明仅支持文本输入") : corePhrase("尚未确认图片能力，可手动设置"))));
    }
  }


  /** **推理强度那几档**：上游报了就用它报的（`effort_levels`），未报时允许手动选择。 */
  function effortLevels(): string[] {
    // **取值顺序**（2026-10-05 第 2 步，照参考那份 `reasoningTiers` ＋ `effortSuggestions`）：
    //    ① **上游自己报的**（`/models` 里 `effort.supported_levels` —— 最硬）；
    // ② **这一条预设声明的**（`preset.efforts`； 空 = 开放 ⇒ 落到 ③）；
    //    ③ 预设的**备选值**（`preset.effort_suggestions`，默认可空 ⇒ 再落 ④）；
    //    ④ 最后那三档（兜底 —— 页上总得画点什么，而它是**备选值**不是"这一家认的"）。
    const withSaved = (levels: string[]): string[] =>
      [...new Set(['', ...levels, ...(rememberedEffort ? [rememberedEffort] : [])])];
    if (modelEffortLevels.length > 0) { return withSaved(modelEffortLevels); }
    const declared = (currentPreset()?.efforts ?? []).filter(Boolean);
    if (declared.length > 0) { return withSaved(declared); }
    // Missing metadata is not a prohibition: these are explicit manual values, not verified model capabilities.
    const endpoint = cells.base_url?.value ?? '';
    if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(endpoint.trim())) {
      return withSaved(['low', 'medium', 'high', 'xhigh']);
    }
    if (isDeepSeekEndpoint(endpoint)) { return withSaved(['low', 'high', 'max']); }
    const suggests = (currentPreset()?.effort_suggestions ?? []).filter(Boolean);
    return withSaved(suggests.length > 0 ? suggests : ['low', 'medium', 'high']);
  }

  /** **对得上哪一条预设**（页级 —— 两栏都要用）。
   *  ⚠️ 从前它是 `drawRight` 里的一个局部量，而"新建"那张草稿卡（在 `drawLeft` 里）也要用
   *  ⇒ 我一引用就抛 `nameIn is not defined`。⇒ 提上来，两栏读**同一份**。
   * 查的时候**忽略末尾那个 `-数字`**（`DeepSeek-2` 也要命中 `DeepSeek`）。 */
  function presetOf(who: string): Preset | undefined {
    const want = who.trim().toLowerCase();
    const original = report?.data?.presets ?? [];
    const rows = [...original, ...providerChoices(original, true)];
    return rows.find((each) => each.name.toLowerCase() === want)
      ?? rows.find((each) => each.name.toLowerCase() === want.replace(/-\d+$/, ''));
  }

  /** **正在编的这一条打算叫什么**（新建时挑一个还没被用过的名字）。
   *  ⚠️ 与"查预设"同一条理由提上来：右列要填它、左列那张草稿卡要显示它。 */
  function spareName(): string {
    const taken = new Set((report?.data?.profiles ?? []).map((each) => each.name));
    let spare = 'DeepSeek';
    for (let n = 2; taken.has(spare); n += 1) { spare = `DeepSeek-${n}`; }
    return spare;
  }

  /** 现在这一条档案对得上的那条预设（`applySpecOf` 与档位那两处都要用）。 */
  function currentPreset(): Preset | undefined {
    const name = (cells.provider as HTMLInputElement | undefined)?.value;
    return name ? presetOf(name) : undefined;
  }

  /** **把那个下拉重画一遍**（档位是会变的：取回列表之后 ／ 换了供应商之后）。
   *  ⚠️ 少了这一下，"上游报了哪几档"就只影响**下一次**建元素时画出来的那一份 ⇒
   *  这一趟取回来的档位看不见（作者："依旧没法自动开启或关闭"那类直觉就是这么来的）。 */
  function fillEfforts(): void {
    const sel = cells.reasoning_effort as HTMLSelectElement | undefined;
    if (!sel) { return; }
    const keep = specOverrides.has('reasoning_effort') ? sel.value : rememberedEffort;
    const levels = effortLevels();
    sel.replaceChildren();
    for (const level of levels) {
      const opt = document.createElement('option');
      opt.value = level;
      coreSetUIText(opt, 'textContent', (level === '' ? corePhrase("上游默认") : level));
      sel.append(opt);
    }
    sel.value = levels.includes(keep) ? keep : '';
    refreshControls();
  }

  /** 探活／保存时**发出去的那个模型名**：选了候选里的那一个就用它的 `id`；
   *  手打进去的（没选过）就用输入框里的原文。 */
  function modelValueOf(): string {
    return modelValue || (cells.model as HTMLInputElement | undefined)?.value || '';
  }

  /** **截短**（播报与报错**一条都不许长到换行** —— 作者两次点过这一处）。

   *  ⚠️ 上游那类原话经常是一整段 JSON（实测那条 401 有 190 多个字符）⇒ 塞进页上就会
   *  把几个板块已经排好的版式撑开。⇒ 一律先过这里；**完整那句**挂到 `title` 上
   *  （鼠标停能看到，一点信息都不丢）。
   */
function onClick(target: HTMLElement, run: () => void): void {
  target.addEventListener('click', () => {
    try {
      run();
    } catch (exc) {
      say(`操作失败：${short(exc, 60)}`, true);
      throw exc;
    }
  });
}

  function short(text: unknown, n = 42): string {
    const one = String(text ?? '').replace(/\s+/g, ' ').trim();
    return one.length > n ? `${one.slice(0, n)}…` : one;
  }

  function say(text: string, bad = false): void {
    notice.textContent = text;
    notice.dataset.bad = bad ? '1' : '';
  }

  /** 这一条档案（选了那一条就用它，没选就用"当前"那一条）。 */
  function chosen(one: Reply<{ profiles: Profile[]; current: string | null }> | null)
      : Profile | null {
    // 正在"新建一条" ⇒ **没有档案**（那几格就照默认值填 —— 那正是新建该有的样子）。
    if (fresh) { return null; }
    const rows = one?.data?.profiles ?? [];
    // ⚠️ **没有"当前"也没有选过**时不许回落到 `rows[0]`：那会让"新建"看起来像"选了第一张卡"。
    const want = picked ?? one?.data?.current ?? '';
    return rows.find((r) => r.name === want) ?? null;
  }

  const isLocalService = (url: string): boolean =>
    /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(url.trim());

  function drawLeft(one: NonNullable<typeof report>): void {
    // ⚠️ 顶上那个「供应商」标题**删掉了**（2026-10-05 作者），而「+ 新建实例」挪到**最上面**
    //    （顶部对齐 —— 从前它排在那一列卡片底下）。
    left.replaceChildren();
    const list = el('div', 'engine-cards');
    for (const rowOne of one.data?.profiles ?? []) {
      const cardEl = el('div', 'engine-card');
      cardEl.setAttribute('role', 'button');
      cardEl.setAttribute('tabindex', '0');
      coreSetUIAttribute(cardEl, 'aria-label', [corePhrase("编辑 "), rowOne.name, corePhrase("")]);
      cardEl.setAttribute('aria-pressed', String(
        !fresh && rowOne.name === (picked ?? one.data?.current)));
      if (!fresh && rowOne.name === (picked ?? one.data?.current)) {
        cardEl.classList.add('picked');
      }
      cardEl.title = rowOne.name; // Stable credential identity, not the editable label.
      cardEl.append(el('strong', 'engine-card-name', rowOne.display_name || rowOne.name));
      const pills = el('div', 'engine-pills');
      // **三处统一到同一个名字**（2026-10-05 作者："三者不一致，以获取的模型名称为准"）：
      //    上游那份里 `id` 与 `name` 是两样 ⇒ 卡片上这一枚也显示**人看的那个**
      //    （档案里存着的 `model_name`；没有就退回 `model`）。
      const shownModel = (one.data?.profiles ?? []).find((p) => p.name === rowOne.name)
        ?.model_name || rowOne.model;
      for (const text of [rowOne.kind, shownModel || '未选择模型', rowOne.base_url]) {
        pills.append(el('span', 'pill', text));
      }
      cardEl.append(pills);
      const marks = el('div', 'engine-card-marks');
      if (rowOne.name === one.data?.current) marks.append(el('span', 'dot-mark', corePhrase('● 当前')));
      const hasModel = one.data?.availability?.[rowOne.name];
      const missingKey = rowOne.auth !== 'chatgpt' && !isLocalService(rowOne.base_url)
        && one.data?.secret?.[rowOne.secret] === 'missing';
      marks.append(el('span', hasModel && !missingKey ? 'ok-mark' : 'warn-mark',
        missingKey ? '待配置 API 密钥' : hasModel ? '模型已配置' : '未选择模型'));
      cardEl.append(marks);
      cardEl.addEventListener('click', () => {
        fresh = false; // 点了某一页/某一张卡 ⇒ 就不是"新建中"了
        picked = rowOne.name;
        rememberSelection(picked);
        typedKey = '';
        modelValue = '';
        say('');
        draw();
      });
      cardEl.addEventListener('keydown', (ev: KeyboardEvent) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          cardEl.click();
        }
      });
      list.append(cardEl);
    }
    const make = button('+ 新建实例', 'btn ghost');
    // **它排在最上面**（作者要的"放到上面来、顶部对齐"）—— 落在那一列**头一项**。
    list.append(make);
    // 新建草稿立即显示为卡片，右侧编辑当前草稿。
    if (fresh || readDrafts().fresh) {
      const draft = el('div', fresh ? 'engine-card draft picked' : 'engine-card draft');
      if (fresh) draft.setAttribute('aria-current', 'true');
      // 名字与协议都从**页级帮手**拿（⚠️ 从前这里引用了 `drawRight` 的局部量 ⇒ 一按就抛）。
      const planned = readDrafts().fresh?.values?.displayName || spareName();
      const maybe = presetOf(planned);
      draft.append(el('strong', 'engine-card-name', planned || '未命名实例'));
      const pills = el('div', 'engine-pills');
      for (const text of [maybe?.kind || 'kind', maybe?.model || 'model —',
                          maybe?.base_url || 'baseUrl —']) {
        pills.append(el('span', 'pill', text));
      }
      draft.append(pills);
      const draftFoot = el('div', 'engine-draft-foot');
      draftFoot.append(el('span', 'engine-draftmark', corePhrase('✎ 草稿')));
      const giveUp = el('button', 'engine-card-drop', '🗑');
      coreSetUIText(giveUp, 'title', corePhrase("删除草稿"));
      giveUp.addEventListener('click', (ev) => {
        ev.stopPropagation();
        changeDraft(undefined);
        fresh = false;
        typedKey = '';
        say('');
        draw();
      });
      draftFoot.append(giveUp); draft.append(draftFoot);
      draft.addEventListener('click', () => { fresh = true; rememberSelection(null); say('草稿尚未保存'); draw(); });
      list.append(draft);
    }
    make.addEventListener('click', () => {
      fresh = true; // 真的"新建中"（见上面 `fresh` 那一段）
      picked = null;
      rememberSelection(null);
      // **新建要把那两格清掉**（作者点出来的）：密钥与模型名都是**那一条**的东西
      //    —— 新建意味着"还没有那一条"，留着上一条的钥匙与模型名就错了。
      typedKey = '';
      modelValue = '';
      say('已填入默认值，请检查后保存。');
      draw();
    });
    left.append(list);
  }


  function drawRight(one: NonNullable<typeof report>): void {
    editEpoch++;
    captureDraft = undefined;
    stopAuth?.(); stopAuth = undefined; resetControls();
    modelSpecs = []; modelEffortLevels = [];
    cellsVision = null; visionHint = null;
    const profile = chosen(one);
    const draftName = fresh ? undefined : profile?.name;
    const drafts = readDrafts();
    const restored = fresh ? drafts.fresh : profile && drafts.profiles[profile.name];
    typedKey = draftKeys.get(draftKey(draftName)) ?? '';
    specOverrides.clear();
    const specFields = ['context_window', 'temperature', 'reasoning_effort', 'max_tokens'] as const;
    for (const field of specFields) {
      const value = profile?.[field];
      if (value !== null && value !== undefined && String(value) !== '') specOverrides.add(field);
    }
    rememberedEffort = profile?.reasoning_effort ?? '';
    let modelsEpoch = 0;
    // **两个竖排栏**（作者定的排法）：左栏＝基本信息 ＋ API 设置；右栏＝模型设置 ＋ 高级。
    //    ⚠️ 从前是一整片 `auto-fit` 网格（"一行两张"）⇒ API 设置会被甩到基本信息的**右边**。
    const colLeft = el('div', 'engine-col engine-connection');
    const colRight = el('div', 'engine-col');
    right.replaceChildren(colLeft, colRight);
    // ⚠️ **一条档案都没有时不留空标题**（从前那句「还没有一条档案」占着一行，
    //    而它下面那三段照旧是空的）—— 空的是**真的没有**，不用一句话去说它。

    const basic = el('div', 'engine-section');
    basic.append(el('h3', 'engine-sub', corePhrase('基本信息')));
    const preset = profile
      ? (one.data?.presets ?? []).find(p => p.base_url === profile.base_url
        && p.kind === profile.kind && (p.auth ?? 'api_key') === (profile.auth ?? 'api_key'))
      : presetOf(spareName());
    const authIn = pick(['api_key', 'chatgpt']);
    coreSetUIText(authIn.options[0], 'textContent', corePhrase("API 密钥认证"));
    coreSetUIText(authIn.options[1], 'textContent', corePhrase("订阅套餐"));
    authIn.value = profile?.auth === 'chatgpt' || (preset && isPlanProvider(preset))
      || (profile && /\/api\/coding\/paas\/v4\/?$/.test(profile.base_url))
      ? 'chatgpt' : 'api_key';
    const isPlan = (): boolean => authIn.value === 'chatgpt';
    const chosenPreset = (): Preset | undefined => presetOf(providerIn.value);
    const isSubscription = (): boolean => isPlan() && (chosenPreset()?.auth === 'chatgpt'
      || (profile?.auth === 'chatgpt' && providerIn.value === (preset?.name || 'Custom')));
    basic.append(row('认证方式', authIn));
    // Internal identity owns the saved credential slot and survives label edits.
    const nameIn = input('text');
    nameIn.value = profile?.name || spareName();
    const providerIn = input('text');
    providerIn.classList.add('engine-input-wide');
    providerIn.value = preset?.name || 'Custom';
    cells.provider = providerIn;
    const providerBox = combo(providerIn,
      (): ComboItem[] => providerChoices(one.data?.presets ?? [], isPlan()).map(each => ({
        value: each.name, label: each.name })));
    basic.append(row('模型供应商', providerBox, undefined, true));
    const instanceNameIn = input('text');
    instanceNameIn.value = profile?.display_name || profile?.name || nameIn.value;
    coreSetUIText(instanceNameIn, 'placeholder', corePhrase("例如：工作账号、备用API（可随时改名）"));
    instanceNameIn.maxLength = 80;
    basic.append(row('实例名称', instanceNameIn));
    // **协议可以改**（2026-10-03 作者定的）。它指的是"**这一条说的是哪一套话**"：
    //    · `responses`   —— OpenAI 那一套（**本机那个引擎也走它**：实测 `/v1/responses`）；
    //    · `chat`        —— `chat/completions` 那一套（DeepSeek ／ OpenRouter 等）。
    //    ⚠️ **本仓只实现这两套**（`protocol.KINDS`）⇒ 这一格就是两选一，**不编第三样**；
    //       "本地服务"不是第三种协议 —— 它是**一个地址**（本机那个引擎两套都会说）。
    //    ⚠️ 改它 ＝ **换一条路**：请求体怎么拼、回来的字节怎么读，都跟着换（`protocol.shape`）。
    // **那份预设要先算好**（2026-10-04 修）：它下面几处都要用（协议 ／ 地址 ／ 模型 ／
    //    上下文上限），而"协议"那一行**最早**用 ⇒ 先前把它声明在下面 ⇒
    //    `ReferenceError: Cannot access 'preset' before initialization`（**那一页整个画不出来**，
    //    因为 `draw()` 抛在右列建完之前；按「新建一条」也走它 ⇒ 看起来"又没用了"）。
    // **查预设时要忽略末尾那个数字后缀**（`2026-10-05` 作者圈的三个红框）：
    //    "新建实例"会给一个**还没被用过**的名字（`DeepSeek-2` ／ `DeepSeek-3` …，
    //    为的是别同名覆盖掉手上那一条）—— 而 `DeepSeek-2` 与预设 `DeepSeek` **对不上**
    //    ⇒ 供应商那一层整层失效 ⇒ 地址 ／ 模型 ／ 上下文上限**全落回"本机那台"**。
    //    ⚠️ 只剥**末尾的 `-数字`**（那是我们自己加的那种后缀），别的一律照原名对。

    // **对得上一条预设 ⇒ 那几格照预设走**（不许把"本机那台"的数塞给别的供应商 ——
    //    作者问过一次："为什么模型名称里的是 qwen3.8-27b"）。
    const vendor = preset !== undefined;

    const kindIn = pick(['chat', 'responses']);
    kindIn.replaceChildren();
    // 顺序与默认都按作者说的：**`chat`（DeepSeek 那套）在先、且是默认**，
    //   `Responses`（OpenAI 等）排在它**下面**。
    // **两行标签写清楚**（2026-10-05 作者第一次说"不够清晰"；当日**第二次**更正 ）：
    // ⚠️⚠️ **第一版我写错了**：我把「 本机 NInfer」写进 `responses` 那一行的标签里
    // ⇒ 读起来像"**本地服务 = responses**" —— 而作者当场指出：
    //        「**本机服务有 v／o／l 三种**（`Local-vLLM` ／ `Local-Ollama` ／ `Local-LMStudio`）
    //          **还有一种就是我们目前在用**（`Local` ＝ NInfer，第四类），**其他的 n**（还有别的）」
    // 页上那条老注释本来就写着相反的话（本文件下面那句：「**"本地服务"不是第三种协议**
    // —— 它是**一个地址**（本机那个引擎**两套都会说**）」）⇒ 是我没照它写 。
    // 而当天**量过**（不是听谁说的）：本机那条 NInfer 线
    // `/chat/completions` 与 `/responses` **都 200** （都 0.2 秒）⇒ 两套都会说 ；
    //      而 `Local-vLLM` ／ `Local-Ollama` ／ `Local-LMStudio` 三条预设（`engine.py:289-294`）
    // 选的都是 `chat` —— 那是**各家的默认**，不是"本地的规矩" 。
    //    ⇒ 下拉只显示协议名；使用场景和完整接口路径放在 title 中。
    for (const [value, label] of [
      ['chat', 'Chat Completions'],
      ['responses', 'Responses'],
    ] as const) {
      const opt = document.createElement('option');
      opt.value = value;
      uiLabel(opt, label);
      kindIn.append(opt);
    }
    kindIn.value = profile?.kind ?? preset?.kind ?? 'chat';
    // **措辞要通用**（2026-10-05 作者第二次更正）：「**本机**」这种词在**开源**项目里
    // 容易歧义 —— 别人读到"本机"会以为是他自己那台 ⇒ 改成「**本地服务**」
    // （＝"和客户端在同一台机器上跑的服务" ／ 英文 `local server` —— 这是**通用**说法 ）。
    // 那一行的小字**删掉了**（2026-10-05 作者：「删掉……」）—— 信息挪进 `title`：
    // 不占地方，鼠标停上去还在 （与「API 密钥」那一行用 `title` 说清是同一手法 ）。
    coreSetUIText(kindIn, 'title', corePhrase('选择服务支持的 API 协议，地址与协议需匹配。'));
    basic.append(row('协议', kindIn));
    // ⚠️ 这里从前挂着一个"换协议 ⇒ 那两格跟着灰/亮"的监听器 —— **2026-10-04 拿掉了**：
    //    那两格现在**永远可编辑**（作者要的），也就没有"跟着变"这回事了。
    colLeft.append(basic);

    const kind = kindIn.value;
    const flow = el('div', 'engine-section engine-api');
    flow.append(el('h3', 'engine-sub', SECTIONS[0]));
    // **API 地址**（必填）：选 `chat` 时**默认填 DeepSeek 那个地址**（作者说的），
    //    选 `responses` 时落回**生效值**（默认那一份 —— 本机那个引擎就是它）。
    //    ⚠️ 与这一页那条规矩一致：**显示的那个地址 ＝ 按下去会去打的那个地址**。
    const urlIn = input('url');
    // **地址不写死在页里**：从**服务交出来的那张预设表**里按名字拿（唯一那一份在
    //    `engine.PRESETS`，而它七条地址**每一条都量过**）。对不上名字 ⇒ 落回生效值那一份。
    // ⚠️ `presetFor` ／ `preset` ／ `vendor` 已经在**上面**算好了（那份预设要先算好：
    //    「协议」那一行最早用它 —— 见那一段的注释）。
    urlIn.value = profile?.base_url || preset?.base_url
      || String(one.data?.keys?.base_url?.value ?? '');
    cells.base_url = urlIn;
    const urlRow = row('API 地址', urlIn, undefined, true);
    flow.append(urlRow);

    // 名字换了 ⇒ **地址与协议跟着换**（只在那两格"还没被手改过"的那一种是空的时候换：
    //   地址空 ／ 或者正是**别的一条预设**的地址 —— 手打进去的地址一律不动）。
    providerIn.addEventListener('change', () => {
      const maybe = chosenPreset();
      const allowed = providerChoices(one.data?.presets ?? [], isPlan());
      if (!maybe || !allowed.some((p) => p.name === maybe.name)) {
        if (isPlan()) {
          providerIn.value = allowed[0]?.name ?? 'ChatGPT-Subscription';
          selectProvider();
          say('订阅供应商请从套餐列表选择；未实现的账号登录不会冒充支持', true);
        } else {
          modelItems = []; modelSpecs = []; modelValue = ''; modelEffortLevels = [];
          rebindSecret(); applyAuth();
        }
        syncDraft();
        return;
      }
      selectProvider();
      say(`已带出「${maybe.name}」的认证、地址与协议`);
    });

    // **API 密钥**：占位就是作者要的那一句； 而输入之后照旧**不显示**（`password` 没动）。
    // **变量名按预设走**（2026-10-05）—— ⚠️ 从前一律落回那个共用的 `TELYIN_API_KEY`
    //    ⇒ **所有档案共用一把钥匙**：存过 DeepSeek 那把之后，"已配置"在**别的档案、连
    //    刚新建的那种**上也亮着（作者："我并没有保存新的示例，为什么是已配置"）。
    // 预设表里每家本来就带着自己的变量名（`DEEPSEEK_API_KEY` ／ `ZHIPU_API_KEY` …）——
    //    这里用它；档案自己写过就以它为准。
    // **兜底不再共用一把**（2026-10-05 作者：「保存了就存对应的。不要混用」）——
    // ⚠️ 从前对不上预设时一律落回共用的 `TELYIN_API_KEY` ⇒ 两条自定义档案会**顶着
    //      同一个变量名**（今天量到：盘上 `DEEPSEEK_API_KEY` 与 `TELYIN_API_KEY` 是**同一把
    // 钥匙** —— 那是"混用"自我记录痕迹）。
    // ⇒ 按**档案自己那个名字**生成一个专属变量名 ：大写、非字母数字一律换成 `_`。
    // （名字那一格在这一步之前已经有值 —— 见上面 `nameIn.value = …` ）
    const ownName = (nameIn.value.trim() || 'CUSTOM').toUpperCase().replace(/[^A-Z0-9]+/g, '_');
    let secretName = profile?.secret || `TELYIN_KEY_${ownName}`;
    // Probes bind to the saved slot (including an empty slot); saving may create a new slot.
    const probeSecretName = (): string => profile?.name === nameIn.value.trim()
      ? profile.secret : secretName;
    const secretIn = input('password');
    // 已经配过（盘上有它 ／ 或者这一趟刚敲过）⇒ 占位说"已配置，输入新值可替换"；
    // 没配过 ⇒ "输入 API Key"。 而**输入之后照旧不显示**（`password` 没动）。
    // ⚠️ **"新建中"不许去问盘上**（2026-10-05 作者："我让你改的是已经保存的，怎么新建的
    //    也改掉了"）：新建意味着"还没有那一条"，而盘上那把变量名（`TELYIN_API_KEY`）
    //    可能正是**原来那一条**用的 ⇒ 一问就错报"已配置"。
    //    ⇒ 只有"已保存的那一条"才看盘；新建的只看这一趟敲过没有。
    let configured = typedKey !== ''
      || (!fresh && (one.data?.secret?.[secretName] ?? 'missing') !== 'missing');
    coreSetUIText(secretIn, 'placeholder', (configured ? corePhrase("已配置——输入新 API 密钥可替换") : corePhrase("输入 API Key")));
    // 提示行按作者的话删了 ⇒ 那"是哪一把钥匙"就挂在悬停上（一个字都不多占）。
    coreSetUIText(secretIn, 'title', (configured ? [corePhrase(""), secretName, corePhrase(" 已保存密钥，输入新值可替换")] : [corePhrase("保存时写进 data/engine.env（变量名 "), secretName, corePhrase("）")]));
    // 把**还没保存的那把**填回去（见 `typedKey` 那一段）；打字时记下来。
    secretIn.value = typedKey;
    secretIn.addEventListener('input', () => { typedKey = secretIn.value; });
    // **本地服务不用密钥**（2026-10-05 作者："关于这几个 local，是本地的，就不用输入 api 密钥了。
    //    **直接把这一条黑掉，不用输入**"）——
    // 判据用**通用规则**（不点名任何预设 ）：地址就是本机 ⇒ 本地服务
    // （`127.0.0.1` ／ `localhost` ／ `[::1]` —— 开源项目里别写"本机"，见当天那次更正 ）。
    // ⚠️ **地址是可以改的**（手打 ／ 选预设都会变 ）⇒ 这一行得**跟着地址走**：
    // 挂一个 `input` 监听现算 ，而不是画的时候判一次 。
    // 黑掉的样子**不用自造样式**：`style.css:2226` 那一条就是它 ——
    //      `.engine-input:disabled { background: var(--color-wash); color: var(--color-faint); }`
    const applyKeyRow = (): void => {
      const local = isLocalService(urlIn.value);
      secretIn.disabled = local;
      if (isSubscription()) secretIn.disabled = true;
      // **本机时那个必填星号也收起来**（作者：「用 local 的时候这个密钥就不用写必填了」）
      const mark = (secretIn as HTMLInputElement & { requiredMark?: HTMLElement }).requiredMark;
      if (mark) { mark.hidden = local || isSubscription(); }
      if (local) {
        secretIn.value = '';
        coreSetUIText(secretIn, 'placeholder', corePhrase("无需 API 密钥"));
        coreSetUIText(secretIn, 'title', corePhrase("当前本地服务无需 API 密钥"));
      } else {
        coreSetUIText(secretIn, 'placeholder', (configured ? corePhrase("已配置——输入新 API 密钥可替换") : corePhrase("输入 API Key")));
        coreSetUIText(secretIn, 'title', (configured ? [corePhrase(""), secretName, corePhrase(" 已保存密钥，输入新值可替换")] : [corePhrase("保存时写进 data/engine.env（变量名 "), secretName, corePhrase("）")]));
      }
    };
    urlIn.addEventListener('input', applyKeyRow);
    // 那一行的小字**删掉了**（同上）； 它的 `title`（上面那一处）照旧说清"本机不校验"。
    // 必填记号（2026-10-05 作者：「在 API 密钥后也添加一个 * 和其他几个必填一致」）——
    // ⚠️ 本机那几家**不校验**密钥（那一格会自动黑掉），所以它是"**非本机时必填**" 。
    const keyRow = row('API 密钥', secretIn, undefined, true);
    flow.append(keyRow);
    const authSlot = el('div', 'engine-chatgpt-slot');
    flow.append(authSlot);
    const planNote = el('p', 'engine-chatgpt-note');
    flow.append(planNote);
    let oauthIdentity = '';
    const syncDraft = (): void => {
      if (!fresh) return;
      const draft = left.querySelector<HTMLElement>('.engine-card.draft');
      const title = draft?.querySelector<HTMLElement>('.engine-card-name');
      const pills = draft?.querySelector<HTMLElement>('.engine-pills');
      if (title) title.textContent = instanceNameIn.value.trim() || nameIn.value.trim() || '未命名实例';
      if (pills) {
        const model = (cells.model as HTMLInputElement | undefined)?.value || '未选择模型';
        pills.replaceChildren(...[kindIn.value, model, urlIn.value || '（未填地址）']
          .map((value) => el('span', 'pill', value)));
      }
    };
    const rebindSecret = (): void => {
      typedKey = ''; secretIn.value = '';
      const sameSaved = profile?.name === nameIn.value.trim();
      const keyBase = nameIn.value.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_');
      secretName = sameSaved && profile.secret ? profile.secret : `TELYIN_KEY_${keyBase}`;
      configured = sameSaved && (one.data?.secret?.[secretName] ?? 'missing') !== 'missing';
    };
    const selectProvider = (): void => {
      const selected = chosenPreset();
      if (!selected) return;
      if (fresh) {
        const previousName = nameIn.value;
        nameIn.value = nextProviderName(selected.name, (one.data?.profiles ?? []).map(p => p.name));
        if (!instanceNameIn.value.trim() || instanceNameIn.value === previousName) {
          instanceNameIn.value = nameIn.value;
        }
      }
      urlIn.value = selected.base_url; kindIn.value = selected.kind;
      modelItems = []; modelSpecs = []; modelValue = ''; modelEffortLevels = [];
      const model = cells.model as HTMLInputElement | undefined;
      if (model) model.value = selected.model || '';
      // Never carry a draft key to another vendor or reuse a saved vendor's secret slot.
      rebindSecret();
      specOverrides.clear(); rememberedEffort = '';
      applySpecOf(''); fillEfforts(); applyAuth(); syncDraft();
    };
    function applyAuth(): void {
      const subscribed = isSubscription();
      keyRow.hidden = false; authSlot.hidden = !subscribed;
      // Both OAuth and keyed plans have a fixed, automatically selected endpoint.
      urlIn.readOnly = isPlan(); urlIn.disabled = isPlan(); kindIn.disabled = isPlan();
      const mark = (urlIn as HTMLInputElement & { requiredMark?: HTMLElement }).requiredMark;
      if (mark) mark.hidden = isPlan();
      if (isPlan()) {
        const selected = subscribed
          ? providerChoices(one.data?.presets ?? [], true).find((p) => p.auth === 'chatgpt')
          : chosenPreset();
        urlIn.value = selected?.base_url ?? ''; kindIn.value = selected?.kind ?? 'responses';
      }
      applyKeyRow();
      if (subscribed) {
        coreSetUIText(secretIn, 'placeholder', corePhrase("订阅登录无需 API Key"));
        coreSetUIText(secretIn, 'title', corePhrase("请通过下方账号授权登录；不使用 API Key"));
      } else if (isPlan()) {
        coreSetUIText(secretIn, 'placeholder', corePhrase("输入此套餐的 API Key"));
        coreSetUIText(secretIn, 'title', corePhrase("GLM / Z.ai Coding Plan 仍需套餐密钥；不是 ChatGPT 账号登录"));
      }
      planNote.hidden = !isPlan() || subscribed;
      coreSetUIText(planNote, 'textContent', (isPlan() && !subscribed ? corePhrase("此套餐仍需 API Key；已锁定专用地址。仅限厂商允许的编码场景，不是通用聊天订阅。") : corePhrase("")));
      const identity = subscribed ? nameIn.value.trim() : '';
      if (identity !== oauthIdentity) {
        stopAuth?.(); stopAuth = undefined; authSlot.replaceChildren();
        oauthIdentity = identity;
        if (subscribed) {
          const backendReady = (one.data?.presets ?? []).some((p) => p.auth === 'chatgpt');
          const auth = chatgptAuth(() => nameIn.value.trim(), backendReady);
          authSlot.append(auth.root); stopAuth = auth.stop; auth.refresh();
        }
      }
      const temperature = cells.temperature as HTMLInputElement | undefined;
      if (temperature) {
        temperature.dataset.subscription = subscribed ? 'yes' : 'no';
        if (subscribed) coreSetUIText(temperature, 'placeholder', corePhrase("订阅接口不支持调整"));
      }
      const thinking = cells.thinking as HTMLSelectElement | undefined;
      if (thinking) {
        for (const option of Array.from(thinking.options)) {
          coreSetUIText(option, 'textContent', (subscribed ? corePhrase("由模型决定") : option.value));
        }
      }
      for (const field of ['temperature', 'max_tokens', 'thinking'] as const) {
        const cell = cells[field];
        if (cell) {
          cell.disabled = subscribed;
          coreSetUIText(cell, 'title', (subscribed ? ((field === 'max_tokens' ? corePhrase("订阅接口不发送输出上限；此值仍作为 Core 本地输出保护预算") : corePhrase("订阅接口不发送此参数"))) : corePhrase("")));
        }
      }
      refreshControls();
    }
    authIn.addEventListener('change', () => {
      const allowed = providerChoices(one.data?.presets ?? [], isPlan());
      const selected = chosenPreset();
      if (!selected || !allowed.some((p) => p.name === selected.name)) {
        const target = isPlan() ? allowed.find((p) => p.auth === 'chatgpt') : allowed[0];
        if (target) providerIn.value = target.name;
      }
      selectProvider();
    });
    for (const cell of [providerIn, instanceNameIn, kindIn, urlIn]) {
      cell.addEventListener('input', syncDraft); cell.addEventListener('change', syncDraft);
    }
    // **首次调用必须在那一行建好之后**（2026-10-05 作者圈的那一处）——
    //    ⚠️ 从前它在 `row(...)` **之前** ⇒ 那一刻星号还没挂到 `secretIn` 上
    // ⇒ 本地地址下**首次画出来照样带星号** （改地址时才收起来）。
    applyKeyRow();

    // **测试连通**：标签删了、只剩这一颗按钮；点它 ＝ **真发一次最小请求**。
    const tester = button('测试连通', 'btn ghost');
    const probeOut = el('p', 'engine-hint', '');
    probeOut.dataset.state = '';
    // Probe the current draft; saving is a separate user action.
    const admitProbeTarget = (): boolean => {
      if (isSubscription() && profile && (profile.auth !== 'chatgpt'
          || profile.kind !== kindIn.value || profile.base_url !== urlIn.value.trim())) {
        say('此实例已有不同的认证方式或地址。请新建 ChatGPT 订阅实例，再在其中登录。', true);
        return false;
      }
      const changedEndpoint = profile && ((profile.auth || 'api_key') !== 'api_key'
        || profile.kind !== kindIn.value
        || profile.base_url !== urlIn.value.trim());
      if (!isSubscription() && changedEndpoint && !secretIn.value.trim()) {
        say('API 地址或协议已变更，请填入该服务的 API 密钥后测试。', true);
        return false;
      }
      return true;
    };
    const probeTarget = () => ({
      name: nameIn.value.trim(), kind: kindIn.value, base_url: urlIn.value.trim(),
      auth: isSubscription() ? 'chatgpt' : 'api_key',
      api_key: isSubscription() ? '' : secretIn.value,
      secret_name: isSubscription() ? '' : probeSecretName(),
    });
    onClick(tester, () => {
      if (!admitProbeTarget()) return;
      const epoch = editEpoch;
      probeOut.title = '';
      coreSetUIText(probeOut, 'textContent', corePhrase("正在测试连通性…"));
      tester.disabled = true;
      probeOut.dataset.state = '';
      void probeEngine({ mode: 'connection', ...probeTarget() }).then((got) => {
        if (epoch !== editEpoch || column.hidden) return;
        if (got.ok && got.data?.ok) {
          // **成了**：绿色那一句（作者要的"测试成功"）。
          coreSetUIText(probeOut, 'textContent', corePhrase("测试成功"));
          probeOut.dataset.state = 'ok';
          return;
        }
        const why = got.data?.error ?? `服务未返回有效响应（HTTP ${got.status}）`;
        // detail 仅含服务整理的安全摘要，不回显上游原始错误正文。
        const said = String(got.data?.detail ?? '');
        const detail = said ? `（上游：${short(said)}）` : '';
        coreSetUIText(probeOut, 'textContent', [corePhrase("测试失败: "), why, corePhrase(""), detail, corePhrase("")]);
        probeOut.title = said || why; // 完整那句在这儿（鼠标停看得到）
        probeOut.dataset.state = 'bad';
      }).finally(() => { tester.disabled = false; });
    });
    flow.append(tester, probeOut);
    colLeft.append(flow);

    const gen = el('div', 'engine-section');
    gen.append(el('h3', 'engine-sub', SECTIONS[1]));
    for (const [field, label] of FIELD_ROWS) {
      // **地址也要传**（2026-10-05 作者：「改成『要重启引擎』的旋钮」）——
      // 「推理强度」对**本地**那条线是**不收**的 （实测 8 个值全 400 ）⇒ 那一格要灰 。
      const supported = accepts(kind, field, urlIn.value);
      // ⚠️ 那两句"这一条协议（chat）多半忽略或报错…"与每格下面的「来源：…」
      //    2026-10-04 按作者的话**都去掉了**（页上不留这类解释与来源小字）。
      const hint = undefined;
      if (field === 'model') {
        const modelIn = input('text');
        // **这一格拉满**（与「协议」同宽 ）； 上一段也带同一个类 —— 两格一致
        modelIn.classList.add('engine-input-wide');
        // **对得上预设 ⇒ 用预设里的模型**（多半是空的 ⇒ 留给「获取模型列表」问上游要）；
        // 对不上（自定义）才落回生效值那一份 —— 那时"本机那台"才是对的。
        //    ⚠️ 从前的写法一律落回生效值 ⇒ 一条 DeepSeek 档案被填上 `qwen3.8-27b`
        //      （本机那台引擎的模型名）—— 作者一眼就看出来了。
        // **显示用 `model_name`（存下来的那个名字），而 `modelValue` 是发出去的 id**
        //    —— 作者点过："点击后又变成 flash 了"（少了这一格，名字就活不过一次刷新）。
        cells.model = modelIn;
        // **没预设就空着**（作者："这里的模型名称还是没有用空值。修一下"）：
        //    ⚠️ 从前"对不上预设"那一支会落回 `keys.model.value`（＝**本机那台**的模型名）
        //      ⇒ 一格看不出属于谁的默认值就那么漏过来了（反复出过三次）。
        //    ⇒ 取值只有三个来源：档案自己存的（`model_name` ／ `model`）、预设、**空**。
        // 本机那条路不吃亏 —— 它的预设（`Local`）本来就带着本机那个模型名。
        modelIn.value = profile?.model_name || profile?.model || preset?.model || '';
        coreSetUIText(modelIn, 'placeholder', corePhrase("选择或输入模型"));
        // **一个与「协议」同一种样子的下拉**：取回列表之后可换选
        // （ 选它 = 填那一格的 `id`；而**菜单上显示的是上游的人话名字**）。
        modelItems = [];
        modelValue = profile?.model ?? ''; // 发出去的那个 id（显示的是 name）
        modelIn.addEventListener('input', () => { modelValue = ''; });
        const modelBox = combo(modelIn, (): ComboItem[] => modelItems,
          (picked) => {
            const changed = modelValue !== picked.value;
            modelValue = picked.value;
            if (changed) { specOverrides.clear(); rememberedEffort = ''; }
            // **选一个模型 ⇒ 自动填那三格**（作者要的）：上游报了就用它，
            //    **没标注就用默认值**（那三格的默认值：温度留空 ／ 推理强度 high ／
            //    上下文上限 = 生效值那一份）。
            applySpecOf(picked.value);
          });
        // ⚠️ **这一格与上面那个「模型供应商名称」不是一回事** —— 作者上一次就是在这儿
        //    卡住的（填了供应商名称、按测试，回话说"模型名不能为空"）：探活发出去的
        //    是**模型名**（`deepseek-chat` 那一种），不是这一条档案叫什么。
        // 保存档案或测试模型回答时需要模型名。
        const line = row(label, modelBox, undefined, true);
        const grab = button('获取模型列表', 'btn ghost');
        const loadModels = () => {
          if (!admitProbeTarget()) return;
          if (grab.getAttribute('aria-busy') === 'true') return;
          const epoch = editEpoch, request = ++modelsEpoch;
          grab.setAttribute('aria-busy', 'true');
          say('正在获取模型列表…');
          void probeEngine({ mode: 'models', ...probeTarget() }).then((got) => {
            if (column.hidden || request !== modelsEpoch || epoch !== editEpoch
                || cells.model !== modelIn) return;
            const names = got.ok && got.data?.ok ? got.data.models : null;
            if (!names || names.length === 0) {
              const why = got.data?.error ?? (got.data?.ok ? '上游没有返回可选模型' : `HTTP ${got.status}`);
              // 上游原话**截短**（播报不许长到换行 —— 作者要的）。
              const said = String(got.data?.detail ?? '');
              say(`无法获取模型列表：${why}${said ? `（上游：${short(said)}）` : ''}`, true);
              notice.title = said; // 完整那句挂在 title 上
              return;
            }
            // 「取回的模型」要**显示人话名字、发送 id**（作者：应该是 v4.1-flash，
            //    而页上只显示 flash —— 因为 `flash` 是**接口的 id**，名字在 `name` 里）。
            const specs = got.data?.specs ?? [];
            // **三件事**（⚠️ 第 1 步重写这一段时漏了第二件 ⇒ 菜单里没有可选项，
            //    作者点出来的那一次）：
            //    ① 记住这一份（`modelSpecs`）；
            //    ② **重建菜单那一份**（`modelItems`：`display_name` 给人看、值是 `id`）
            //       ＋ 把第一个填进那一格；
            //    ③ 逐格落地交给 `applySpecOf()`（那是唯一决定"这几格显示什么"的地方）
            //       ＋ `fillEfforts()`（档位变了就当场重画那个下拉）。
            const keep = modelValueOf();
            modelSpecs = specs;
            modelItems = names.map((id) => {
              const spec = specs.find((each) => each.id === id);
              const shown = spec?.display_name || id;
              return { value: id, field: shown, label: `${shown}（${id}）` };
            });
            modelValue = keep || names[0];
            const selected = modelItems.find((item) => item.value === modelValue);
            modelIn.value = selected?.field ?? modelValue;
            coreSetUIText(modelIn, 'placeholder', corePhrase("选择或搜索模型"));
            applySpecOf(modelValue);
            fillEfforts();
            captureDraft?.();

            // 那几格**就在页上看得见**（填进去了）⇒ 播报只说个数与选了谁。
            say(`上游 ${names.length} 个模型 · 当前 ${modelValue}`);
            if (document.activeElement === grab || document.activeElement === modelIn) {
              modelIn.focus();
              (modelIn as HTMLInputElement & { openAll(): void }).openAll();
            }
          }).finally(() => {
            grab.removeAttribute('aria-busy');
          });
        };
        onClick(grab, loadModels);
        line.append(grab);
        gen.append(line);
        continue;
      }
      // 那两格"不是每条协议都认"：**灰掉并写清为什么**（不是画成能用的样子）。
      const cell: HTMLInputElement | HTMLSelectElement =
        field === 'thinking' ? pick(['开', '关'])
          // **推理等级从上游拿**（作者要的）：上游报了哪几档就用哪几档
          //   （实测 DeepSeek 报 `["low","high","max"]`）；没报用默认那三档。
          : field === 'reasoning_effort' ? pick(effortLevels())
            : input('text');
      if (field === 'thinking') {
        (cell as HTMLSelectElement).value = (profile?.thinking ?? true) ? '开' : '关';
      } else if (field === 'reasoning_effort') {
        const effort = cell as HTMLSelectElement;
        const saved = profile?.reasoning_effort ?? '';
        effort.value = Array.from(effort.options).some(o => o.value === saved) ? saved : '';
      } else {
        const now = (profile as unknown as Record<string, unknown> | null)?.[field];
        const wrote = now !== null && now !== undefined && String(now) !== '';
        // **没写下过的那几格不是空的**：里面**填的就是生效值**（默认那一份）——
        //    与地址那一格同一条规矩：**显示的 ＝ 实际用的**；而保存时会把它**写进档案**。
        // 于是"默认值"这件事只有**一处**：`config.load_config` ＋ `engine.EXTRA_DEFAULTS`
        //      （页不自己写一套数 —— 两处各答一个数就是本仓反复栽的那一类）。
        const live = fresh ? one.data?.defaults?.[field] : one.data?.keys?.[field]?.value;
        // **对得上预设的那两格不许拿本机的数冒充**：上下文上限那一格在"别的供应商"下
        //    **空着**（上游报多少就是多少 —— 按「获取模型列表」问它要），
        //    而"最大输出 token"照旧用我们的出站额度（那是**我们的选择**，不是上游的窗口）。
        const ours = field !== 'context_window' && field !== 'reasoning_budget_tokens'
          && (field === 'max_tokens' || !vendor);
        const fallback = ours && live !== null && live !== undefined && live !== ''
          ? String(live) : '';
        (cell as HTMLInputElement).value = wrote ? String(now) : fallback;
        if (!wrote && !ours) {
          // ⚠️ 空着就要说清"空着等谁"：不然它看起来像"这一格没法填"。
          const capacity = profile ? one.data?.capacities?.[profile.name] : undefined;
          const value = capacity?.value ?? (profile?.name === one.data?.current ? live : null);
          const status = capacity?.status ?? 'not_checked';
          const capacityText = capacity?.source === 'fallback' ? '回退容量：128K（131072）'
            : typeof value === 'number' ? `自动容量：${value}`
            : status === 'not_reported' ? '容量未知：上游未提供'
            : status.startsWith('lookup_failed') ? `容量查询失败${capacity?.http_status ? ' · HTTP ' + capacity.http_status : ''}`
            : '容量尚未查询';
          coreSetUIText((cell as HTMLInputElement), 'placeholder', (field === 'context_window' ? capacityText : corePhrase("上游默认")));
          if (field === 'context_window') coreSetUIText(cell, 'title', corePhrase("优先使用上游容量；未知或查询失败时按 128K（131072）规划交接。回退值不代表上游承诺支持，可手动填写已确认的容量。"));
        }
      }
      // ⚠️ **2026-10-04：不再锁着**（作者："都是锁着的，没法编辑"）。
      // 灰掉本来是照设计稿 §6.2 那条"chat 系多半忽略或报错"—— 而**能不能编辑**
      //    与"上游认不认"是两件事：认不认由`上游`说了算，页不该替它把人挡在外面。
      //    ⇒ 两格都**可编辑**，而"这一条协议多半不认它"那句话留在提示里。
      cells[field] = cell;
      if (['context_window', 'temperature', 'reasoning_effort', 'max_tokens'].includes(field)) {
        const preserve = (): void => {
          specOverrides.add(field);
          if (field === 'reasoning_effort') rememberedEffort = cell.value;
        };
        cell.addEventListener('input', preserve);
        cell.addEventListener('change', preserve);
      }
      gen.append(row(label, cell, hint));
      if (field === 'max_tokens') {
        // **这里本该是"最大图片输出 token" —— 而上游没有那一格**（2026-10-05 实测：
        //    DeepSeek `output_modalities: ["text"]` ／ 本机那台 `modalities: {vision: true}`
        //    ⇒ 两家都**只出文本**，图片 token 也没有独立上限 —— 它算在同一个上下文里）。
        //    ⇒ 页上画**量得到**的那一样：**能不能收图**（取回列表之后填上）。
        // **一个滑块**（作者要的）：上游报有视觉 ⇒ 滑到"开"；没有 ⇒ "关"。
        //    ⚠️ 它是**只读**的（照上游报的显示）—— 页上不假装自己能改上游的能力。
        const sees = el('button', 'engine-switch');
        // **先照档案自己那一格画**（存得住的那一格，照参考 `multimodal`）；
        // ⚠️ 取回列表之后 `applySpecOf()` 会按上游报的那一格覆盖它 。
        sees.dataset.on = profile?.multimodal ? 'yes' : 'no';
        coreSetUIText(sees, 'title', corePhrase("根据模型信息自动设置，也可手动调整"));
        // **它能点**（2026-10-05 作者："选择了支持多模态的模型，图片输入依旧点不动"）：
        //    上游报了 **自动开**，而人也能自己扳（那是一份偏好 —— 页上不该只有一个死开关）。
        sees.addEventListener('click', () => {
          sees.dataset.on = sees.dataset.on === 'yes' ? 'no' : 'yes';
          updateDraft();
          say(sees.dataset.on === 'yes' ? '图片输入：开' : '图片输入：关');
        });
        cellsVision = sees;
        const visionRow = row('图片输入', sees);
        visionHint = el('p', 'engine-hint', '');
        visionRow.append(visionHint); gen.append(visionRow);
      }
    }
    // **灰掉的那一格要能说清为什么**（2026-10-05）：
    // `WHY_NOT` 从前是**死代码** （作者让去掉页上小字时，那句解释没了落点 ）——
    // 现在把它挂到那一格的 **`title`（悬停）** 上 ：不占地方、又点得到
    // （与「API 密钥」那一行用 `title` 说清变量名是同一手法 ）。
    for (const [field] of FIELD_ROWS) {
      const one = cells[field] as HTMLInputElement | HTMLSelectElement | undefined;
      if (one && !accepts(kind, field, urlIn.value)) {
        one.disabled = true;
        one.title = WHY_NOT[field] ?? '';
      }
    }
    // **那两格的联合提醒**（2026-10-05，见 `handoffNote` 的抬头）：
    const subscriptionNote = el('p', 'engine-hint',
      corePhrase('温度由上游控制；输出值为本地预算。'));
    gen.append(subscriptionNote);
    const refreshSubscriptionNote = (): void => { subscriptionNote.hidden = !isSubscription(); };
    authIn.addEventListener('change', refreshSubscriptionNote);
    providerIn.addEventListener('change', refreshSubscriptionNote);
    fillEfforts(); applyAuth();
    const fields = { identity: nameIn, auth: authIn, provider: providerIn,
      displayName: instanceNameIn, protocol: kindIn, url: urlIn,
      ...Object.fromEntries(FIELD_ROWS.map(([field]) => [field, cells[field]])) };
    const snapshot = (): EngineDraft => ({
      values: Object.fromEntries(Object.entries(fields).map(([key, cell]) => [key, cell?.value ?? ''])),
      modelId: modelValueOf(),
      vision: cellsVision?.dataset.on === 'yes',
    });
    const baseline = JSON.stringify(snapshot());
    if (restored?.values && typeof restored.values === 'object') {
      for (const [key, cell] of Object.entries(fields)) {
        const value = restored.values[key];
        if (!cell || typeof value !== 'string') continue;
        if (cell === nameIn && (!fresh || one.data?.profiles.some(row => row.name === value))) continue;
        if (cell === cells.reasoning_effort && value && cell instanceof HTMLSelectElement
            && !Array.from(cell.options).some(option => option.value === value)) {
          cell.add(new Option(value, value));
        }
        cell.value = value;
      }
      modelValue = typeof restored.modelId === 'string' ? restored.modelId : '';
      if (cellsVision) cellsVision.dataset.on = restored.vision ? 'yes' : 'no';
      rebindSecret();
      typedKey = draftKeys.get(draftKey(draftName)) ?? '';
      secretIn.value = typedKey;
      applyAuth();
      for (const field of ['thinking', 'reasoning_effort'] as const) {
        const cell = cells[field];
        if (cell) cell.disabled = (field === 'thinking' && isSubscription())
          || !accepts(kindIn.value, field, urlIn.value);
      }
      refreshControls();
      for (const field of specFields) specOverrides.add(field);
    }
    captureDraft = () => {
      if (!fresh && !profile) return;
      const next = snapshot();
      if (!fresh && !typedKey && JSON.stringify(next) === baseline) {
        changeDraft(draftName);
      } else {
        if (typedKey) draftKeys.set(draftKey(draftName), typedKey);
        else draftKeys.delete(draftKey(draftName));
        changeDraft(draftName, next);
      }
    };
    if (fresh && !restored) captureDraft();
    refreshSubscriptionNote();
    // ⚠️ 它是**两格联合**的判据 ⇒ 单格 `title` 挂不住（改一格会让另一格的说明过期 ）
    // ⇒ 两格各挂一个 `input` 监听，值一变就重算 。
    // ⚠️ 灰掉的格**不动它的 `title`**（那一句 `WHY_NOT` 更该被看到 ）。
    const noteCells = [cells.context_window, cells.max_tokens];
    const refreshHandoffNote = (): void => {
      const said = handoffNote(
        numberOrNull(cells.context_window), numberOrNull(cells.max_tokens),
      );
      for (const one of noteCells) {
        if (one && !one.disabled) { one.title = said; }
      }
    };
    refreshHandoffNote();
    for (const one of noteCells) {
      one?.addEventListener('input', refreshHandoffNote);
    }
    colRight.append(gen);

    // Keep actions beside the selected configuration and outside its scrolling form.
    const act = el('div', 'engine-actions');
    const actionTitle = el('strong', 'engine-action-title', profile?.display_name || profile?.name || corePhrase('新建模型配置'));
    act.append(actionTitle);
    const actions = el('div', 'engine-foot');
    act.append(actions, notice);
    actionsHost.replaceChildren(act);

    // ⚠️ **复制那一颗删掉了**（作者要的）；删除还在 —— 它是这一页唯一那条删路。
    // **「高级」那张卡删掉了**（2026-10-04 作者：这个高级不用了）。
    //    ⚠️ 而里面那两颗**不能一起丢**（"删掉一条档案"是这一页唯一那条删路）⇒
    //    挪到底部那排去：保存 ／ 设为当前 ／ 复制 ／ 删除。
    // **「删除」两步走**（2026-10-05 作者要的）：点一下先变成**红框「🗑 确认删除」**，
    //    再点一下才真删；⚠️ 而**不再禁用** —— 连"当前那一条 ／ 唯一那一条"也删得掉
    //    （作者："点击删除没法删除自身，只能有多个 API 的时候才能删除"）。
    const drop = button('删除', 'btn ghost');
    drop.disabled = profile === null;
    let armed = false;
    onClick(drop, () => {
      if (profile === null) { return; }
      if (!armed) {
        armed = true;
        coreSetUIText(drop, 'textContent', corePhrase("🗑 确认删除"));
        drop.className = 'btn danger';
        say('再点一下确认删除');
        window.setTimeout(() => {          // ⚠️ 过一会儿自己退回去（不然它一直红着像坏了）
          armed = false;
          coreSetUIText(drop, 'textContent', corePhrase("删除"));
          drop.className = 'btn ghost';
        }, 5000);
        return;
      }
      void engineCommand({ action: 'delete', name: profile.name }).then((result) => {
        if (result.ok) changeDraft(profile.name);
        after(result);
      });
    });

    const save = button('保存');
    onClick(save, () => {
      if (saving) return;
      if (isSubscription() && !(one.data?.presets ?? []).some((p) => p.auth === 'chatgpt')) {
        say('后端尚未加载订阅登录功能，请先重启 Core，再保存', true);
        return;
      }
      // **两道校验**（2026-10-05 作者定的，原话照抄 ）—— 都**在发请求之前**拦下 。
      //    ⚠️ 为什么放在这儿（而服务那一处也有一套必填／范围校验）：这两条是**两格之间**
      // 的关系与**本页认得的东西**（"是不是本机"），服务那一处看不到"页上刚敲的那把钥匙" 。
      const winNow = numberOrNull(cells.context_window);
      const outNow = numberOrNull(cells.max_tokens);
      // ⚠️ **两格都填了才判**：空着 ＝ "还没人说过"（那是正常状态 —— 上游没报就是空 ）
      // ⇒ 空着时不拦，"填得不合适"才拦 。
      if (winNow !== null && outNow !== null && winNow <= outNow) {
        say('总输出上限必须小于上下文容量', true);
        return;
      }
      // 非本地那几家上游：**必须有一把钥匙**（本机那几家不校验，照旧放行 ）。
      // 判"有没有"看两处：这一趟刚敲的（`secretIn.value`）／盘上已有的（`configured`）。
      if (!isSubscription() && !isLocalService(urlIn.value)
          && secretIn.value.trim() === '' && !configured) {
        say('有必填项未填，请检查', true);
        return;
      }
      // ⚠️ **这一处判断删掉了**（2026-10-05 第 3 步）：必填／范围／正整数**收在服务的
      //    `check_a_profile()` 一处**（照参考 `validateSpec`）⇒ 页上不再各判一半
      //    （"页上过得去、盘上拒收"那种左右脑互搏就是这么来的）。
      // 错由服务说清、原样回到页上那一行（见 `after()`）；`*` 那个记号照旧是给人看的。
      const one2: Record<string, unknown> = {
        action: 'save', set_current: true, create_only: fresh,
        profile: {
          // ⚠️ **读那一格当时的值**（不是画这一页时捕获的那个常量）——
          //    协议现在**可以改**，读常量的话"改了协议、存下来还是旧的"。
          // ⚠️ 这里从前写的是 `wanted` —— 那是第 3 步**删掉**的那句必填判断里的局部量
          //    ⇒ 点保存当场 `ReferenceError` ⇒ **按键没反应**（作者点到的那一次）。
          //    ⇒ 直接读那一格（服务那一处会判必填，错由它说清）。
          name: nameIn.value.trim(), display_name: instanceNameIn.value.trim(), kind: kindIn.value,
          auth: isSubscription() ? 'chatgpt' : 'api_key',
          base_url: (cells.base_url as HTMLInputElement | undefined)?.value ?? urlIn.value,
          model: modelValueOf(),
          // 一起把"给人看的名字"写进去（`model_name`）—— 不然刷新之后那一格只剩 id。
          model_name: (cells.model as HTMLInputElement | undefined)?.value ?? '',
          // **空着的那几个数就不发**（2026-10-05）：`numberOrNull('')` 交回 `null`，
          //    而服务那一处只认正整数 ⇒ 会**误拒**；而"上游没报就空着"是**正常**状态
          //    （第 1 步那条规矩）⇒ 缺省 ＝ **用默认那一份**（`config.load_config`）。
          ...(numberOrNull(cells.max_tokens) === null
            ? {} : { max_tokens: numberOrNull(cells.max_tokens) }),
          reasoning_budget_tokens: numberOrNull(cells.reasoning_budget_tokens),
          thinking: String((cells.thinking as HTMLSelectElement | undefined)?.value) === '开',
          ...(numberOrNull(cells.context_window) === null
            ? {} : { context_window: numberOrNull(cells.context_window) }),
          ...(numberOrNull(cells.temperature) === null
            ? {} : { temperature: numberOrNull(cells.temperature) }),
          ...((cells.reasoning_effort as HTMLSelectElement | undefined)?.value
            ? { reasoning_effort:
                (cells.reasoning_effort as HTMLSelectElement).value } : {}),
          // **本地服务这一条不写变量名**（2026-10-05 作者："本地服务不用密钥"）——
          // 地址是本机 ⇒ 存空串 （＝"这一条没有钥匙" 与那四条预设的 `secret=""` 一致 ）。
          // ⚠️ 从前这里一律写 `secretName` ⇒ 一条本地档案会顶着别人的变量名（今天量到
          // `Local` 就写着 `DEEPSEEK_API_KEY` ）⇒ 现在它保存一次就自己清干净 。
          secret: isSubscription() || isLocalService(urlIn.value) ? '' : secretName,
          // **那个开关的值也要存**（照参考 `multimodal`）：不存它 ⇒ 保存时开着、
          //    刷新回来就关（作者点出来的那一处）。
          multimodal: cellsVision?.dataset.on === 'yes',
        },
      };
      // 「写进去」那颗按钮删了 ⇒ **密钥由保存这一下一起写**（一次动作）。
      //    ⚠️ 那一格空着 = "这一次不改密钥"（**不是**清空它 —— 清空是另一件事）。
      const typed = secretIn.value;
      const submitted = JSON.stringify(snapshot());
      const epoch = editEpoch;
      saving = true;
      void saveEngineDraft(one2, typed).then((result) => {
        const stored = readDrafts();
        const currentDraft = draftName === undefined ? stored.fresh : stored.profiles[draftName];
        if (result.profile.ok && !result.error && (!result.secret || result.secret.ok)
            && JSON.stringify(currentDraft) === submitted
            && (draftKeys.get(draftKey(draftName)) ?? '') === typed) {
          changeDraft(draftName);
        }
        if (epoch !== editEpoch) {
          say(result.error || (!result.profile.ok ? `档案未确认：${result.profile.note}`
            : result.secret && !result.secret.ok ? `档案已保存，密钥未确认：${result.secret.note}`
            : '原档案保存已完成；当前编辑保留，请刷新核对。'),
            !!result.error || !result.profile.ok || (!!result.secret && !result.secret.ok));
          return;
        }
        if (result.error || (result.secret && !result.secret.ok)) {
          say(result.error || `档案已保存，密钥未确认：${result.secret!.note}`, true);
          return;
        }
        if (result.secret?.ok) { secretIn.value = ''; typedKey = ''; }
        if (result.profile.ok) changeDraft(draftName);
        after(result.profile);
      }).catch(() => {
        say('保存结果未确认；请刷新核对后再决定，未自动重试。', true);
      }).finally(() => { saving = false; });
    });
    const use = button('设为当前', 'btn ghost');
    use.disabled = !(profile && profile.model.trim());
    coreSetUIText(use, 'title', (use.disabled ? corePhrase("请先选择模型，再设为当前") : corePhrase("")));
    onClick(use, () => {
      void engineCommand({ action: 'use', name: profile?.name ?? '' }).then(after);
    });
    // 那三颗进**新板块**那一排（顺序：保存 ／ 设为当前 ／ 删除；复制删掉了）。
    actions.append(save, use, drop);
    const modelCell = cells.model;
    modelCell?.addEventListener('input', syncDraft);
    modelCell?.addEventListener('change', syncDraft);
    syncDraft();
  }

  function after(got: Reply<unknown>): void {
    say(got.ok ? '已保存' : `操作失败（HTTP ${got.status}）：${short(got.note)}`, !got.ok);
    if (got.ok) {
      fresh = false; // 存下了 ⇒ 就不是"新建中"了（它自己会高亮）
      const epoch = editEpoch;
      void engineReport().then((again) => {
        if (epoch !== editEpoch) return;
        typedKey = '';
        report = again as NonNullable<typeof report>;
        if (again.ok) { picked = again.data?.current ?? null; rememberSelection(picked); }
        draw();
      });
    }
  }

  function draw(): void {
    if (report === null) { return; }
    try {
      drawLeft(report);
      drawRight(report);
    } catch (exc) {
      // **一个异常不许把整页留白**（那正是"点了没反应"最难查的那种形状：
      //    右列停在 `replaceChildren()` 那一步，页上**一个字的线索都没有**）。
      //    ⇒ 把话说出来（人看得见、我也看得见），再把异常抛上去（控制台里也要有）。
      say(`页面加载失败：${short(exc, 60)}`, true);
      throw exc;
    }
  }

  function sourceOf(one: NonNullable<typeof report>, field: string): string {
    const got = one.data?.keys?.[field];
    return got ? `来源：${got.source}` : '';
  }

  /** **先立骨架，再去取数**（`show()` 里**同步**跑）。

   *  ⚠️ **它不是装饰**：判据在"切到这一页那一下"**之后同步**读 `.card` ——
   *     一个"等 `fetch` 回来才建元素"的实现，在那一刻页上**什么都没有**
   *     （读不到那一格，而人眼看到的是一下白屏）。⇒ 形状先立住，数回来再填。
   */
  function skeleton(): void {
    // ⚠️ 那个「供应商」标题**删掉了**（2026-10-05 作者）—— 它在这里**也**写过一遍
    //    （骨架那一份），只改画完那一份是不够的。
    left.replaceChildren();
    // 骨架照**新形状**立（两个竖排栏）：一栏两张卡 ⇒ "骨架"与"画完"一个样，
    //   而判据在切页那一下同步读得到的也还是那几张 `.engine-section`。
    const bone = (): HTMLElement => {
      const col = el('div', 'engine-col');
      col.append(el('div', 'engine-section'), el('div', 'engine-section'));
      return col;
    };
    right.replaceChildren(el('h2', 'card-title', corePhrase('加载中…')), bone(), bone());
  }

  let readEpoch = 0;
  const readHint = pageLoad(column, '模型档案', () => { load(); });
  function load(): void {
    editEpoch++; typedKey = '';
    const epoch = ++readEpoch; readHint.start();
    void Promise.all([engineReport(), initializeDisplayScope()]).then(([got]) => {
      if (epoch !== readEpoch || column.hidden) return;
      report = got as NonNullable<typeof report>;
      if (!got.ok) {
        // ⚠️ **读不到要说**（那是一件真出事了的事）；读到了**不必报数**（作者：用处不大）。
        say(`读不到（HTTP ${got.status}）：${short(got.note)}`, true);
        readHint.fail();
        return;
      }
      if (picked === null) {
        const drafts = readDrafts();
        const selected = got.data?.profiles.find(row => row.name === drafts.selected);
        if (selected) picked = selected.name;
        else if (drafts.fresh) fresh = true;
        else picked = got.data?.profiles.find(row => drafts.profiles[row.name])?.name ?? null;
      }
      draw(); readHint.ready();
    });
  }

  return {
    root: column,
    show: (): void => {
      column.hidden = false; shell.bottom.hidden = true;
      skeleton();
      load();
    },
    stop: (): void => {
      resetControls();
      captureDraft = undefined;
      editEpoch++; typedKey = '';
      readEpoch++; stopAuth?.(); stopAuth = undefined;
      column.hidden = true; shell.bottom.hidden = false;
    },
  };
}

/** 那几格的画法（字段 ／ 标签）。 */
const FIELD_ROWS: ReadonlyArray<readonly [Field, string]> = [
  ['model', '模型名称'],
  ['thinking', '思维链'],
  ['reasoning_effort', '推理强度'],
  ['reasoning_budget_tokens', '思考预算'],
  ['temperature', '温度'],
  ['max_tokens', '最大输出 token'],
  ['context_window', '上下文上限'],
];

/** Match the provider-specific Chat capability used by protocol.shape. */
function isDeepSeekEndpoint(url?: string): boolean {
  try { return new URL(url ?? '').hostname === 'api.deepseek.com'; }
  catch { return false; }
}

function accepts(kind: string, field: string, url?: string): boolean {
  if (field === 'thinking' || field === 'reasoning_effort') {
    return kind === 'responses' || (kind === 'chat' && isDeepSeekEndpoint(url));
  }
  return true;
}

/** "为什么这一格是灰的"（ **灰掉并写清为什么** —— 不是画成能用的样子）。 */
const WHY_NOT: Record<string, string> = {
  thinking: '当前协议及服务尚未适配思考开关',
  reasoning_effort: '当前协议及服务尚未适配推理强度'
    + '（实测 7 档全过 ，嵌在 `reasoning.effort` 里发 ）',
};

function numberOrNull(one: HTMLInputElement | HTMLSelectElement | undefined): number | null {
  const text = one?.value ?? '';
  if (text.trim() === '') { return null; }
  const got = Number(text);
  return Number.isFinite(got) ? got : null;
}

/** "软阈占窗口的比例" —— **与 `handoff.HandoffPolicy.soft_ratio` 同值**（`telyincore/handoff.py`）。
 *
 *  ⚠️ 页这一侧**只是照它画**（与上面 `accepts()` 那条纪律一样）：**正身在 Python 那边** ——
 *     改那一个数就得同时改这里 ⇒ 有一条判据盯着（`tests/test_web.py` 里那条）。
 */
const SOFT_RATIO = 0.85;

/** Soft pressure leaves 15% of the input allowance for the next batch. */
function handoffNote(windowTokens: number | null, outTokens: number | null): string {
  if (windowTokens === null || outTokens === null) { return ''; }
  if (windowTokens <= 0 || outTokens <= 0) { return ''; }
  const hard = Math.max(0, windowTokens - outTokens);
  return ` 输入硬阈 ${hard}；软提醒 ${Math.floor(hard * SOFT_RATIO)}，余下 15% 为后续增长预留。`;
}
