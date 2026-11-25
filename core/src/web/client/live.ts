import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { hasEngineDraft } from './engine-page';
import { wireComposerPopovers, positionComposerPopover } from './composer-popovers';
import { pageFetch as fetch } from './status-json';
import { cachePresentation, latestCacheRead } from './cache-view';
// **页一：聊天**（`#/live`，左栏那一组 `CORE` 里的"我在这一页"）—— 设计稿 §三 那条：
//   正文一列（**圆形头像 ＋ 名字**的气泡 ／ 浅灰卡片）＋ 底部那一行（**大圆角输入框**
//   ＋ 左边"导出诊断"＋ 右边一个小图标 ＋ 一个圆 ＋ **圆的绿色发送键**）。
//
// ⚠️ **这一页建两处**：正文那一列（`chat.ts` 管它的形状）与**底部那一行**（这儿）。
//    壳（左栏 ／ 顶栏）不是它的事（`shell.ts` 管那两块）。
// ⚠️ **零框架**：全是 `document.createElement`；没有 React ／ 没有组件树。

import { mountConsoleIntroduction } from './console-introduction';
import { mountChat, stickToTheBottom, type ChatView } from './chat';
import type { Chrome, Panel } from './shell';

/** 底下那一行那几个字。 左边那个按钮**今天没有下家** —— 见 `composer()` 里那句 `title`。 */
const EXPORT_LABEL = '导出诊断';
const SEND_LABEL = '▶';
/** **2026-10-10：交接那一下的名字** —— 按下它 ＝ **交接 ＋ 起整理态**（两件一起 ）。
 *
 * ⚠️ **它为什么非有不可**：整理态**只有交接那一个触发口**（`10-整理态.md` §触发）——
 *     而自动那两条要 **64000 ／ 245760 token**（她的窗口 262144）⇒ 正常使用里几乎到不了
 * ⇒ **实际只剩"手动"这一条** 。而这一条**服务端 2026-10-03 就建好了**
 * （`web.py::_ask_for_handoff`），**前端一处都没调用它**
 * ⇒ 那一条路只有 `curl` 按得到 —— 那正是这一刀要堵的洞 。
 * ⚠️ **页上写的字照 `web.py::ask_for_handoff` 的说明**：「按这一下 ＝ 交接 ＋ 起整理态」。 */
const HANDOFF_LABEL = '交接';
/* 载入图片按钮；回车发送提示保留在输入框 title 中。 */
/** **那个"仪表"图标**（2026-10-05 作者："要做成图2这样"）—— 细环 ＋ 左上一段弧。
 *
 *  ⚠️⚠️ **这是照着图 2 观察到的形状画的**（细环 ＋ 约四分之一圈的一段弧，弧在左上 ——
 * 也就是"仪表刻度"那个样子） —— **不是**从参考那份产物里抄到的 path：
 * 那个图标的定义在另一个包里，产物里搜不到它 （我搜过 `IconGaugeOutlineRegular`
 * ／ `GaugeOutlineRegular` ／ `gaugeOutline`，只搜到**用法**、搜不到**形状** ）。
 * ⇒ 这一点写在这里，别让下一个人以为它是抄来的 。
 * ⚠️ 用 `createElementNS` 造（这一页的规矩：不把标记当字符串塞进去 ）。
 */
function gaugeIcon(): SVGSVGElement {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  const trace = document.createElementNS(ns, 'circle');
  trace.setAttribute('cx', '8'); trace.setAttribute('cy', '8'); trace.setAttribute('r', '5.3');
  trace.setAttribute('fill', 'none');
  trace.setAttribute('stroke', 'currentColor'); trace.setAttribute('stroke-width', '1.5');
  trace.setAttribute('opacity', '0.35'); // 环是淡的那一圈
  const mark = document.createElementNS(ns, 'path');
  // 左上那一段（从圆的顶点逆时针走到左上约十点钟方向 —— 图 2 里深色的就是它）
  mark.setAttribute('d', 'M8 2.7A5.3 5.3 0 0 0 3.3 6.1');
  mark.setAttribute('fill', 'none');
  mark.setAttribute('stroke', 'currentColor'); mark.setAttribute('stroke-width', '1.8');
  mark.setAttribute('stroke-linecap', 'round');
  svg.append(trace, mark);
  return svg;
}


/** **那个"载入图片"的图标**（2026-10-05 作者："这个图标显示有问题"）——
 *  ⚠️ 两条边界都踩过，别退回去：
 * ① 它原来是 emoji（`🖼`）⇒ **作者那台机器上没有那个字形** ⇒ 渲染成一个方框 ；
 *    ② 想用「把一段标记当字符串塞进去」那一套 ⇒ **那一页的规矩是一律 `textContent`**
 * （判据当场红 —— 而且它管的是**整份源码**，连注释里提到那个词也算 ）。
 * ⇒ 所以用 DOM 那套造节点（`createElementNS`）：**不依赖字体、也不碰那一套** 。 */
function imageIcon(): SVGSVGElement {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('width', '16');
  svg.setAttribute('height', '16');
  svg.setAttribute('aria-hidden', 'true');
  const frame = document.createElementNS(ns, 'rect');
  frame.setAttribute('x', '2.6'); frame.setAttribute('y', '3.6');
  frame.setAttribute('width', '14.8'); frame.setAttribute('height', '12.8');
  frame.setAttribute('rx', '2.4'); frame.setAttribute('fill', 'none');
  frame.setAttribute('stroke', 'currentColor'); frame.setAttribute('stroke-width', '1.5');
  const dot = document.createElementNS(ns, 'circle');
  dot.setAttribute('cx', '7.2'); dot.setAttribute('cy', '8.2'); dot.setAttribute('r', '1.4');
  dot.setAttribute('fill', 'currentColor');
  const hill = document.createElementNS(ns, 'path');
  hill.setAttribute('d', 'M3.6 15.4l4.2-4.2 2.5 2.5 2.5-2.5 3.6 3.6');
  hill.setAttribute('fill', 'none'); hill.setAttribute('stroke', 'currentColor');
  hill.setAttribute('stroke-width', '1.5');
  hill.setAttribute('stroke-linecap', 'round'); hill.setAttribute('stroke-linejoin', 'round');
  svg.append(frame, dot, hill);
  return svg;
}

/** **一次 `POST /say` 最多多少字节** —— **与服务那一侧那个 `MAX_BODY` 对齐**
 *  （`telyincore/web.py`；2026-10-05 从 64 KiB 提到 16 MiB，就是因为图 → data URL 太大）。
 *  ⚠️ **为什么页上还要卡一道**：服务回 413 时**回完就关连接** ⇒ 浏览器那一下**是抛**的，
 * 拿不到那句"太大了" ⇒ 只会画成"这条路断了"（作者实测报的那一处）
 * ⇒ 所以在这里先算一遍、先说清、不发 。 */
const MAX_SAY_BYTES = 16 * 1024 * 1024;

/* 一次最多 8 张，每张上限 6 MiB。超长边缩至 2048，仍超字节上限则转 JPEG，再超则拒收。QQ 入口使用相应归一化规则。 */
const MAX_IMAGES = 8;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
/* 图片长边上限；只缩不放。 */
const MAX_IMAGE_EDGE = 2048;
/* 允许的图片 MIME 类型白名单。 */
const IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

/* 只缩不放：长边未超过 maxEdge 时保持原尺寸。 */
function fitWithin(width: number, height: number,
                   maxEdge: number): { width: number; height: number } {
  const edge = Math.max(width, height);
  if (!(edge > maxEdge) || maxEdge <= 0) { return { width, height }; }
  const scale = maxEdge / edge;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** 把一个 blob 读成 **data URL**（协议层那一格收的就是它 —— 字符串那一头 ）。 */
function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('图片读取失败'));
    reader.onload = () => { resolve(String(reader.result ?? '')); };
    reader.readAsDataURL(blob);
  });
}

/* 通过画布缩放或转码，再编码为 blob。 */
function encodeTo(bitmap: ImageBitmap, width: number, height: number,
                  mime: string, quality?: number): Promise<Blob> {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) { throw new Error('当前浏览器无法处理图片'); }
  ctx.drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('图片转换失败'))),
                  mime, quality);
  });
}

/* 归一化图片并返回 data URL：够小则保留原字节；超长边则缩放；仍超字节上限则转 JPEG 0.85，再超限或解码失败则拒收。 */
export async function normalizeImage(file: File): Promise<string> {
  if (!IMAGE_MIMES.includes(file.type)) {
    throw new Error('请选择 JPEG、PNG、WebP 或 GIF 图片');
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error('无法打开图片，文件格式不支持或已损坏');
  }
  try {
    const fitted = fitWithin(bitmap.width, bitmap.height, MAX_IMAGE_EDGE);
    const untouched = fitted.width === bitmap.width && fitted.height === bitmap.height
      && file.size <= MAX_IMAGE_BYTES;
    if (untouched) { return await readAsDataUrl(file); }
    // GIF 无画布编码器，转为 PNG。
    const preferred = file.type === 'image/gif' ? 'image/png' : file.type;
    let blob = await encodeTo(bitmap, fitted.width, fitted.height, preferred);
    if (blob.size > MAX_IMAGE_BYTES && preferred !== 'image/jpeg') {
      blob = await encodeTo(bitmap, fitted.width, fitted.height, 'image/jpeg', 0.85);
    }
    if (blob.size > MAX_IMAGE_BYTES) {
      throw new Error('图片压缩后仍超过 6 MB，请先缩小图片');
    }
    return await readAsDataUrl(blob);
  } finally {
    bitmap.close(); // 别漏：解出来的位图占的是真内存
  }
}

/** 那一行输入框的说明（占位字）。
 * ⚠️ 措辞（2026-10-05 作者，第二次）：他要的是**一句招呼**——
 * 从前那句在说"这条路不校验来源"（那是**给做的人看**的一句话 ）⇒ 换掉 。
 * ⚠️ 更早的一版写的是「**本机**上的任何一页」 —— 「本机」在**开源**项目里容易歧义
 * （别人会以为是他自己那台）⇒ 那一版就已经去掉了它 。
 */
const PLACEHOLDER = '输入消息…';

/** `composer()` 交回的两样：那一行本身 ／ 那个计数的那一格。 */
interface Composer {
  form: HTMLFormElement;
  /** 那一下"刷一次"的出口（`live` 那一页拿它接上"她说完一句就刷"）。 */
  refresh(): void;
  setProcessing(processing: boolean, stopping?: boolean): void;
}

/**
 * 底部那一行：**大圆角输入框** ＋ 左边"导出诊断" ／ 右边**小图标 ＋ 一个圆 ＋ 绿色发送键**。
 *
 * ⚠️ **发送键真的会送**（`onSend` 就是那条 `POST /say`）；**圆里那个数真的会动**
 *    （数的是你正在打的这一行有几个字 —— 这一页上一个写死的数都不要）。
 * ⚠️ **那个小图标照旧是今天的边界**（与左栏那三个小图标同一条口径）：
 *    按钮在、`title` 说清它今天不做什么 —— 而**不假装**有一个下家。
 * 这条边界要写在报告里（"借了形状、而那个按钮没有下家"），别让它只活在代码里。
 * **而"导出诊断"在 2026-10-05 接上了下家**（作者："这个导出诊断是做什么的" ⇒
 * 听清它是一个空壳之后 ⇒ "做一下吧" ）：它把那四个**已经有的**读数存成一条 JSON
 * （见下面 `exportButton` 那一处 ）。⚠️ 从此那一句"照旧是今天的边界"**只对小图标成立** 。
 */
//: **那两个浮窗的"点外面收起"**（2026-10-05 作者："做一个点击浮窗外任意地方就能把浮窗
//: 收起来的功能，现在必须点一下才能关闭太繁琐了"）—— **只挂一次** （页面重建时不该越挂越多）。
function composer(onSend: (text: string, images: readonly string[]) => void,
                  onNote: (text: string, action?: HTMLElement) => void, onStop?: () => void,
                  canSend: () => boolean = () => true): Composer {
  const form = document.createElement('form');
  form.className = 'composer';
  form.autocomplete = 'off';
  coreSetUIAttribute(form, 'aria-label', corePhrase("消息输入"));
  const box = document.createElement('div');
  box.className = 'composer-box';
  const input = document.createElement('textarea');
  input.id = 'line';
  input.name = 'line';
  input.rows = 1;
  input.placeholder = PLACEHOLDER;
  coreSetUIAttribute(input, 'aria-label', corePhrase("说一句"));

  const row = document.createElement('div');
  row.className = 'composer-row';
  const exportButton = document.createElement('button');
  exportButton.type = 'button';
  exportButton.className = 'btn ghost composer-export';
  exportButton.textContent = EXPORT_LABEL;
  coreSetUIText(exportButton, 'title', corePhrase("下载诊断 ZIP，包含最近对话和工具回执，转发前请核对内容。"));
  const diagnosticDownload = document.createElement('a');
  diagnosticDownload.className = 'composer-diagnostic-download';
  coreSetUIText(diagnosticDownload, 'textContent', corePhrase("下载诊断包")); diagnosticDownload.hidden = true;
  let diagnosticUrl: string | null = null;
  exportButton.addEventListener('click', async () => {
    exportButton.disabled = true;
    coreSetUIText(exportButton, 'textContent', corePhrase("正在生成…"));
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch('/diagnostics/export', { method: 'POST', signal: controller.signal });
      if (!response.ok) throw new Error('diagnostic export failed');
      const blob = await response.blob();
      if (diagnosticUrl) URL.revokeObjectURL(diagnosticUrl);
      diagnosticUrl = URL.createObjectURL(blob);
      diagnosticDownload.href = diagnosticUrl;
      diagnosticDownload.download = `telyincore-diagnose-${new Date().toISOString()
        .replace(/[:.]/g, '-').slice(0, 19)}.zip`;
      diagnosticDownload.hidden = false;
      onNote('诊断包已生成；未自动下载可点击 ', diagnosticDownload);
      diagnosticDownload.click();
    } catch {
      onNote(controller.signal.aborted ? '诊断生成超时，请重试。' : '导出诊断失败，请重试。');
    } finally {
      window.clearTimeout(timeout);
      exportButton.disabled = false;
      exportButton.textContent = EXPORT_LABEL;
    }
  });

  // **2026-10-10：交接那一下** —— **它是整理态唯一"能按得到"的触发口** 。
  //    ⚠️⚠️ **为什么非要它**：整理态挂在 `on_handoff` 上，而自动那两条路要
  //       **64000 ／ 245760 token**（她的窗口 262144）⇒ 正常使用里几乎到不了
  // ⇒ **实际只剩"手动"这一条** 。 而 `POST /handoff` 服务端**早就有了**
  // ⇒ 前端一处不调用它 ＝ 那一条路**只有 `curl` 按得到** 。
  // ⚠️ **它说的话要照实**：那一路回的是**四个记号**、不是一个布尔值
  // （`console.ask_for_handoff`：「照实说清那一刻是哪一种情形」）——
  //       · **503** ⇒ 现在叫不动（还没起来 ／ 正在收摊）；
  // · `answered === false` ⇒ **递出去了，还没等到回话**（ 不许说成"做完了" ）；
  //       · `asked === false` ⇒ **已经有一次在路上**（单飞挡住）—— 这一下没有另起一次；
  // · 其余 ⇒ 交接做了（ 整理态是它的**副产品**，它跑到哪一步**不在这条回话里** ）。
  const handoffButton = document.createElement('button');
  handoffButton.type = 'button';
  handoffButton.className = 'btn ghost composer-handoff';
  handoffButton.textContent = HANDOFF_LABEL;
  coreSetUIText(handoffButton, 'title', [corePhrase("整理当前对话摘要，并在新的上下文中继续。"), corePhrase("相关记忆会在后台整理。")]);
  handoffButton.addEventListener('click', () => {
    void fetch('/handoff', { method: 'POST' }).then(async (res) => {
      if (!res.ok) {
        onNote(res.status === 500 ? '交接失败，请导出诊断' : `交接暂不可用（${res.status}）`);
        return;
      }
      const got = (await res.json()) as { asked?: unknown; answered?: unknown };
      if (got.answered === false) {
        onNote('交接进行中，请稍候');
        return;
      }
      if (got.asked === false) {
        onNote('交接已在进行中');
        return;
      }
      onNote('交接已完成，整理任务已安排');
    }).catch(() => { onNote('交接状态未确认，请检查连接'); });
  });
  const grow = document.createElement('span');
  grow.className = 'grow';

  // 那个"载入图片"（2026-10-05）：**按钮 ＋ 一个藏起来的 file input**
  // ⚠️ 张数写在按钮上（`🖼 2`）—— 那是这一页唯一的"带了几张"的读数 。
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = 'image/*';
  picker.multiple = true;
  picker.hidden = true;

  let attached: string[] = [];
  // **那一行缩略图**（2026-10-05 作者："太不明显了" ／ "按照 dsh 这样"）——
  // 铺在输入框**上方**，每张右上角一个 ×（点了就撤掉这一张 ）。
  // **上下文占用那一块**（2026-10-05 作者："关于上下文，你要做一个点击显示"）——
  // ⚠️ 它**必须声明在下面那句 `box.append` 之前**（`const` 的死时间区 —— 那个错真发生过 ）。
  const usagePanel = document.createElement('div');
  usagePanel.className = 'composer-usage';
  usagePanel.hidden = true;

  /** 那四块的**页上名字** —— **次序照作者给的那一份** （服务端只给 key ＋ 数 ）。
   * ⚠️ 名字本身就是那一句话（作者 2026-10-05："非常简洁，但是能知道这几项说的是什么"）
   * —— 所以面板底下**不再挂那五句底注** 。 */
  const USAGE_LABEL: Record<string, string> = {
    framing: '结构 · 基座',
    prefix: '系统消息 · 人设',
    tools: '工具声明 · 插件',
    dialogue: '对话 · 往来',
  };

  const usageLine = (text: string, cls: string): HTMLElement => {
    const one = document.createElement('div');
    one.className = cls;
    one.textContent = text;
    return one;
  };

  /** **把那一块画出来** —— 逐格 `createElement` ＋ `textContent`（这一页的规矩 ）。
   * 那些数来自 `GET /usage`（服务端按真对象算的三块 ）。 */
  /** **底部那一行的两格**（缓存 ／ 上一轮用时）—— 它们都在浮层外面 ⇒ **每次都要画**
   * （浮层那一行详细只在**点开**时才画 ）。 */
  const drawCache = (got: Record<string, unknown>): void => {
    const batch = (typeof got.batch_timing === 'object' && got.batch_timing !== null
      ? got.batch_timing : null) as Record<string, number | null> | null;
    const t = batch ?? (typeof got.timing === 'object' && got.timing !== null
      ? got.timing : null) as Record<string, number | null> | null;
    const total = batch?.total ?? t?.total;
    const title = batch ? '上一批用时' : '最近一次模型请求';
    coreSetUIText(whenLabel, 'textContent', (typeof total === 'number' ? [corePhrase(""), batch ? '共' : '请求', corePhrase(" "), total.toFixed(1), corePhrase("s")] : corePhrase("共 —")));
    coreSetUIText(whenCell, 'title', [corePhrase(""), title, corePhrase("（点一下看明细）")]);
    whenBody.replaceChildren();
    const whenHead = document.createElement('div');
    whenHead.className = 'usage-head';
    whenHead.append(usageLine(title, 'usage-title'));
    whenHead.append(usageLine(typeof total === 'number' ? `${total.toFixed(1)}s` : '—', 'usage-num'));
    whenBody.append(whenHead);
    whenBody.append(usageLine(batch
      ? `与消息结束提示使用同一计时。已记录 ${batch.requests ?? 0} 次模型请求；分段不含工具执行等间隔。`
      : '单次请求时间；首段可能是思考或正文，等待包含网络与服务端处理。', 'usage-note'));
    const stages: [string, number | null | undefined][] = [
      ['等待首段输出', t?.thinking], ['流式生成', t?.output],
      ...(batch ? [['模型请求合计', batch.model_total]] as [string, number | null | undefined][]
        : [['ASR', t?.asr], ['TTS', t?.tts]] as [string, number | null | undefined][]),
    ];
    for (let i = 0; i < stages.length; i++) {
      const [name, value] = stages[i];
      const row = document.createElement('div');
      row.className = 'usage-row';
      const dot = document.createElement('span');
      dot.className = `usage-dot usage-stage-${i}`;
      row.append(dot);
      row.append(usageLine(name, 'usage-name'));
      row.append(usageLine('', 'usage-pct'));
      row.append(usageLine(secs(value), 'usage-tok'));
      whenBody.append(row);
    }
    const cache = cachePresentation(got);
    cacheLabel.textContent = cache.label;
    cacheCell.title = cache.title;
    cacheBody.replaceChildren();
    const cacheHead = document.createElement('div');
    cacheHead.className = 'cache-summary-head';
    cacheHead.append(usageLine(cache.heading, ''), usageLine(cache.amount, ''));
    cacheBody.append(cacheHead);
    for (const [name, value] of cache.rows) {
      const row = document.createElement('div');
      row.className = 'cache-summary-row';
      row.append(usageLine(name, ''), usageLine(value, ''));
      cacheBody.append(row);
    }
    cacheBody.append(usageLine(cache.note, 'cache-summary-note'));
    const diagnostic = document.createElement('section');
    diagnostic.className = 'cache-diagnostics';
    const summary = document.createElement('h3');
    coreSetUIText(summary, 'textContent', corePhrase("诊断"));
    diagnostic.append(summary);
    for (const line of cache.diagnostics) {
      diagnostic.append(usageLine(line, 'usage-note'));
    }
    cacheBody.append(diagnostic);
    // cache_note is the deprecated legacy accumulator note; do not mix scopes.
  const woke = (typeof got.next_wake === 'object' && got.next_wake !== null
    ? got.next_wake as { at?: string; what?: string } : null);
  if (woke !== null && typeof woke.at === 'string') {
    const what = typeof woke.what === 'string' && woke.what !== '' ? woke.what : '主动唤醒';
    coreSetUIText(cacheCell, 'title', [corePhrase(""), cacheCell.title, corePhrase("\n下一次"), what, corePhrase("："), woke.at.replace('T', ' ').slice(0, 16), corePhrase("")]);
  }
  };

  const drawUsage = (got: Record<string, unknown>): void => {
    const window = typeof got.window === 'number' ? got.window : null;
    const total = typeof got.total === 'number' ? got.total : null;
    // ⚠️ 那一格里可能是 `null`（"还没有可以算的东西" 与 0 不是一回事 ）
    const parts = (typeof got.parts === 'object' && got.parts !== null
      ? got.parts : {}) as Record<string, number | null>;
    const share = (n: number): number => (total && total > 0 ? Math.round(n / total * 100) : 0);
    const num = (n: number): string => n.toLocaleString('en-US');
  // 照图那个 `64.0k`（大数用 k ／ M —— 不然 `262,144` 会把标题右挤满 ）
  const kfmt = (n: number): string =>
    (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));

    usagePanel.replaceChildren();
    const head = document.createElement('div');
    head.className = 'usage-head';
    head.append(usageLine(got.estimate_source === 'completed_batch_snapshot' ? '上一批输入估算 / 当前配置窗口' : got.estimate_source === 'standby_archive_preview' ? '待机基础上下文估算' : '上下文估算', 'usage-title'));
    // 照图：`~818 / 64.0k tok`（`~` 前缀 ＋ k 缩写 ）
    head.append(usageLine(total === null ? '—'
      : `~${num(total)}${window === null ? '' : ` / ${kfmt(window)}`} tok`, 'usage-num'));
    // ⚠️ **`ofWindow` 必须先算**（2026-10-05 崩过一次：`usage-sub` 那一行就要用它，而声明在下面
    // ⇒ 死时间区 ⇒ `ReferenceError` ⇒ **面板只剩标题行** （作者那张截图就是它 ）。
    const ofWindow = total !== null && window ? Math.round(total / window * 100) : null;
    usagePanel.append(head);
    // ⚠️ **这一行除的是窗口**（`share()` 除的是**总数** —— 拿它算会恒等于 100% ，
    // 2026-10-05 实测：面板上写着"占窗口 100%"而圆里是 8% ）⇒ `ofWindow` 在上面算好了 。
    usagePanel.append(usageLine(ofWindow === null ? '预算未知' : `占预算 ${ofWindow}%`,
                                'usage-sub'));
    if (got.estimate_source === 'completed_batch_snapshot' && typeof got.estimate_captured_at_ms === 'number') {
      usagePanel.append(usageLine(`快照 ${new Date(got.estimate_captured_at_ms).toLocaleTimeString()}`, 'usage-sub'));
    }
    if (got.estimate_source === 'standby_archive_preview') {
      usagePanel.append(usageLine('静候预览，不含本轮动态信息', 'usage-sub'));
    }
    // 底注**全删**（2026-10-05 作者："字太多了。这些都删了。要求是非常简洁"）

    // ⚠️ **整条代表窗口**（2026-10-05 作者："占窗口5%，怎么这个条已经用掉了这么多"）——
    // 从前那条是按**块占总数**画的（加起来恒 100% ⇒ **永远铺满** ）⇒ 与"占窗口 5%"
    // 那句**对不上** 。现在：外壳 ＝ 窗口；里面那一段 ＝ 用掉的部分（按窗口算 ）；
    // **段内**再按各块分色（段内加起来 100% ）。
    // ⚠️ `ofWindow` 在**上面**算好了（这一处曾经把它写在后面 ⇒ 崩 ）。
    const bar = document.createElement('div');
    bar.className = 'usage-bar';
    const usedBar = document.createElement('div');
    usedBar.className = 'usage-used';
    usedBar.style.width = `${ofWindow === null ? 0 : ofWindow}%`;
    for (const key of Object.keys(USAGE_LABEL)) {
      const slice = document.createElement('span');
      slice.className = `usage-slice usage-${key}`;
      slice.style.width = `${share(parts[key] ?? 0)}%`;
      usedBar.append(slice);
    }
    bar.append(usedBar);
    usagePanel.append(bar);

    // **"缓存命中"不在这儿**（2026-10-05 作者："缓存命中不要放进上下文那个里面"）——
    // 它是**上一次请求**的读数，与"占在哪几块"不是一类 ⇒ 搬到下面底部那一行去了
    // （照参考那一张的样子：一格纯文字 ＋ 数 ）。

    // **这里只讲上下文**（2026-10-05 作者："上下文是上下文，时间是时间，两个功能，
    // 别混用。分开的。"）—— 上一笔我把"上一轮各阶段"塞进了这个浮窗 ⇒ 已挪走
    // （时间那一格有自己的浮窗：`whenPanel` ）。

    // 一行 = 名字 ／ 百分比 ／ 数。**没有数的那一格画 `—`**（不画 0 —— 两件事不一样 ）
    for (const key of Object.keys(USAGE_LABEL)) {
      const size = parts[key];
      const row = document.createElement('div');
      row.className = 'usage-row';
      // 照图：每行最前面一个**色块**（色跟那一块走 —— 复用进度条那批类 ）
      const dot = document.createElement('span');
      dot.className = `usage-dot usage-${key}`;
      row.append(dot);
      row.append(usageLine(USAGE_LABEL[key], 'usage-name'));
      row.append(usageLine(size === null || size === undefined ? '—' : `${share(size)}%`,
                           'usage-pct'));
      row.append(usageLine(size === null || size === undefined ? '—' : num(size), 'usage-tok'));
      usagePanel.append(row);
    }
  };

  const thumbs = document.createElement('div');
  thumbs.className = 'composer-thumbs';
  thumbs.hidden = true;

  const glyph = document.createElement('button');
  glyph.type = 'button';
  glyph.className = 'composer-glyph';
  // 计数另起一个 span（图标与数字分开 ⇒ 换图标不必动计数那一段 ）
  const glyphCount = document.createElement('span');
  glyphCount.className = 'composer-count';
  glyph.append(imageIcon(), glyphCount);

  // 顺序：这一句必须在 `thumbs` **声明之后**（它是 `const` ⇒ 在它前面碰它就是死时间区
  //   那正是判据当场抓住的那一处：`ReferenceError: Cannot access thumbs before initialization`）。
  box.append(thumbs, usagePanel, input);
  coreSetUIText(glyph, 'title', corePhrase("载入图片（也可以拖进来 ／ 粘贴）—— 随这一行一起发；回车是发送，Shift+回车换行"));
  const drawAttached = (): void => {
    glyphCount.textContent = attached.length === 0 ? '' : String(attached.length);
    // 有图 ⇒ 那一颗按钮**亮起来**（照"图片输入"那一格 `data-on` 的手法 ）
    glyph.dataset.on = attached.length === 0 ? 'no' : 'yes';
    thumbs.replaceChildren();
    attached.forEach((url: string, index: number): void => {
      const cell = document.createElement('span');
      cell.className = 'composer-thumb';
      const picture = document.createElement('img');
      picture.src = url;
      picture.alt = '待发送图片';
      const drop = document.createElement('button');
      drop.type = 'button';
      drop.className = 'composer-thumb-x';
      drop.textContent = '×';
      coreSetUIText(drop, 'title', corePhrase("移除图片"));
      drop.addEventListener('click', () => { attached.splice(index, 1); drawAttached(); });
      cell.append(picture, drop);
      thumbs.append(cell);
    });
    thumbs.hidden = attached.length === 0;
  };
  const addFiles = async (files: Iterable<File>): Promise<void> => {
    for (const file of files) {
      if (attached.length >= MAX_IMAGES) { onNote('一次最多 8 张'); break; }
      // **归一化**（缩放 ／ 转码 ／ 拒收）全在 `normalizeImage` 那一处 ——
      // 这里只负责"把话说给用户"（它抛出来的都是能直接给人看的话 ）。
      try {
        attached.push(await normalizeImage(file));
      } catch (exc) {
        onNote(exc instanceof Error ? exc.message : '图片读取失败');
      }
    }
    drawAttached();
  };
  glyph.addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => {
    void addFiles(picker.files ?? []);
    picker.value = ''; // 同一个文件再选一次也要能触发
  });

  // 那个圆 —— **上下文使用**（2026-10-05 作者点的那一处）：
  // ⚠️ 从前它数的是"**你正在打的这一行有几个字**" （与那台机器的计数不是一回事）；
  // 作者要的是"载入图片右边那个**上下文使用情况的监控**" 。
  // 分子取 `/state` 的 `tokens`（＝那一份请求的 token 数 ）、分母取同一份里的
  // `context_window`（上一格刚加的 ）—— **一次请求拿两样** ，不多问一趟。
  // ⚠️ 缺哪一样就画"—"（**不冒充一个数** —— 与那一页"不许把不知道画成零"同一条口径 ）。
  // **那个圆 ⇒ 按钮**（2026-10-05 作者："关于上下文，你要做一个点击显示，
  // 目前 core 上的那个还没发点。"）—— 点一下展开上面那一块明细（再点收起 ）。
  // **"图 1 ⇒ 图 2"**（2026-10-05 作者："◍ 这个去掉 … 要做成图2这样"）——
  // 从前那一格是一个**CSS 画的圆圈**、而 "8%" **写在圈里面** ；
  // 参考那一格是「**仪表图标 ＋ 文字**」（文字在图标**外面** ，与缓存那一格同一套 ）。
  const ring = document.createElement('button');
  ring.type = 'button';
  ring.className = 'composer-ring';
  const ringLabel = document.createElement('span');
  ringLabel.textContent = '—';
  ring.append(gaugeIcon(), ringLabel); // 图标在前、数字在后（照那一份 ）
  coreSetUIText(ring, 'title', corePhrase("查看上下文用量明细"));
  const readCache = latestCacheRead(fetch);
  const showUsage = async (): Promise<void> => {
    await readCache((got) => {
      const used = typeof got.total === 'number' ? got.total : null;
      const window = typeof got.window === 'number' ? got.window : null;
      const percent = used !== null && window !== null && window > 0
        ? Math.round(used / window * 100) : null;
      ringLabel.textContent = percent === null ? '—' : `${percent}%`;
      coreSetUIText(ring, 'title', (percent === null ? [corePhrase("上下文估算暂不可用"), window !== null ? `；当前模型配置窗口 ${window} token` : '', corePhrase("")] : [corePhrase(""), got.estimate_source === 'completed_batch_snapshot' ? '上一批输入估算 / 当前配置窗口' : got.estimate_source === 'standby_archive_preview' ? '待机基础上下文估算' : '上下文估算', corePhrase(" "), used, corePhrase(" ／ "), window, corePhrase(" token（"), percent, corePhrase("%）")]));
      drawCache(got);
      if (!usagePanel.hidden) { drawUsage(got); }
      positionComposerPopover();
    });
  };

  // **点一下展开／收起**（作者要的那一下）—— 展开的**那一刻**才去问 `/usage`
  // 2026-10-05 第二次改：**「共 Xs」那一格也叫它**（作者："在点击图1的时候显示出来
  // 详细的每个阶段所花的时间" ）—— 两处**同一个动作**，所以抽成一个函数 。

  /** **那个"数据库"图标**（内联 SVG，**14×14**）—— 2026-10-05 照参考那一份做的。
   *
   * ⚠️ 参考那一格是「**图标 ＋ 文字**」：
   *      `<span class=pill><IconDatabaseOutlineRegular />{t("stats.cacheHit", …)}</span>`
   * —— 而我第一版**只有一个 `span` 纯文字** （作者："你没有用 dsh 的样式啊" ）。
   * ⚠️ 尺寸与间距都照那一份的 CSS：`svg { width:14px; height:14px }` ／ `gap: 6px` 。
   * ⚠️ 用 `createElementNS` 一格格造（这一页的规矩：不把标记当字符串塞进去 ）。
   */
  const databaseIcon = (): SVGSVGElement => {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    const lid = document.createElementNS(ns, 'ellipse');
    lid.setAttribute('cx', '8'); lid.setAttribute('cy', '4');
    lid.setAttribute('rx', '5.2'); lid.setAttribute('ry', '2.1');
    lid.setAttribute('fill', 'none');
    lid.setAttribute('stroke', 'currentColor'); lid.setAttribute('stroke-width', '1.3');
    const body = document.createElementNS(ns, 'path');
    body.setAttribute('d', 'M2.8 4v8c0 1.15 2.35 2.1 5.2 2.1s5.2-.95 5.2-2.1V4');
    body.setAttribute('fill', 'none');
    body.setAttribute('stroke', 'currentColor'); body.setAttribute('stroke-width', '1.3');
    const band = document.createElementNS(ns, 'path');
    band.setAttribute('d', 'M2.8 8c0 1.15 2.35 2.1 5.2 2.1s5.2-.95 5.2-2.1');
    band.setAttribute('fill', 'none');
    band.setAttribute('stroke', 'currentColor'); band.setAttribute('stroke-width', '1.3');
    svg.append(lid, body, band);
    return svg;
  };

  // **底部那一行的"缓存命中"**（照上面那一份的样子：**图标 ＋ 文字** ）——
  // 当前控制台／模型／上下文分区累计；零命中与未知严格区分。
  const cacheCell = document.createElement('button');
  cacheCell.type = 'button';
  cacheCell.className = 'composer-cache';
  const cacheLabel = document.createElement('span');
  coreSetUIText(cacheLabel, 'textContent', corePhrase("缓存命中 —"));
  cacheCell.append(databaseIcon(), cacheLabel); // 图标在前、文字在后（照那一份 ）
  coreSetUIText(cacheCell, 'title', corePhrase("当前控制台 · 当前模型累计输入用量与缓存命中"));
  cacheCell.setAttribute('aria-expanded', 'false');
  const cachePanel = document.createElement('div');
  cachePanel.className = 'composer-stages composer-cache-details';
  cachePanel.hidden = true;
  const cacheBody = document.createElement('div');
  cachePanel.append(cacheBody);


  // **上一轮花了多久**（2026-10-05 作者："我怎么看不到上一轮的思考输出时间"）——
  // ⚠️ 那一行原来画在**浮层里面** ⇒ 要点开那个圆才看得见 （而缓存那一格在外面
  // ⇒ 于是"看得见缓存、看不见时间" ）。
  // 参考那一份的做法正是**两格都放外面**（它那一行是"速度"与"缓存命中"两个 pill ）
  // ⇒ 这一格用**已经有的** `gaugeIcon()`（就是为参考那一格画的 ）。
  const whenCell = document.createElement('button');
  whenCell.type = 'button';
  whenCell.className = 'composer-when';
  const secs = (n: number | null | undefined): string =>
    (typeof n === 'number' ? `${n.toFixed(1)}s` : '—');
  const whenLabel = document.createElement('span');
  coreSetUIText(whenLabel, 'textContent', corePhrase("共 —"));
  whenCell.append(whenLabel); // 不要图标（作者 2026-10-05："前面的图标就不用了"）
  coreSetUIText(whenCell, 'title', corePhrase("上一轮：思考 ／ 输出各花了多久（点一下看明细）"));

  // **时间自己的浮窗**（2026-10-05 作者："不要和上下文做成一个浮窗"）——
  // 它只讲那几段，与上下文那个 `usagePanel` **毫无关系** 。
  // ⚠️ `asr` ／ `tts` 今天没有下家（那两段在链的**两端** 见 `keep_stage`）⇒ 画 `—` 。
  const whenPanel = document.createElement('div');
  whenPanel.className = 'composer-stages composer-time-details';
  whenPanel.hidden = true;
  const whenBody = document.createElement('div');
  whenPanel.append(whenBody);
  // 点它开的是**它自己的**浮窗（2026-10-05 作者："不要和上下文做成一个浮窗" ）——
  // ⚠️ 这一句**必须在 `whenCell` 声明之后**（死时间区 —— 烟测当场抓到过一次 ）
  wireComposerPopovers([
    { trigger: ring, panel: usagePanel, label: '上下文占用' },
    { trigger: whenCell, panel: whenPanel, label: '回复用时' },
    { trigger: cacheCell, panel: cachePanel, label: '输入用量与缓存命中' },
  ], () => { void showUsage(); });

  const send = document.createElement('button');
  send.type = 'submit';
  send.className = 'composer-send';
  send.textContent = SEND_LABEL;
  coreSetUIText(send, 'title', corePhrase("发送消息 · Enter（Shift+Enter 换行）"));
  coreSetUIAttribute(send, 'aria-label', corePhrase("发送消息"));
  let processing = false;
  let interrupting = false;
  const setProcessing = (active: boolean, stopping = false): void => {
    processing = active; interrupting = stopping;
    send.dataset.processing = String(active);
    send.textContent = active ? '■' : SEND_LABEL;
    coreSetUIText(send, 'title', (stopping ? corePhrase("正在停止，请稍候") : (active ? corePhrase("停止当前处理") : corePhrase("发送消息 · Enter（Shift+Enter 换行）"))));
    coreSetUIAttribute(send, 'aria-label', (stopping ? corePhrase("正在停止") : (active ? corePhrase("停止当前处理") : corePhrase("发送消息"))));
    send.disabled = stopping;
  };
  send.addEventListener('click', (event) => {
    if (processing) { event.preventDefault(); if (!interrupting) onStop?.(); }
  });

  // **左边那一组**：导出诊断 ／ 那个圆（上下文占用 —— 作者："挪到左边来" ）／ 缓存命中；
  // **右边那一组**：图片 ／ 发送 ；`grow` 夹在中间（它才是把两边推开的那一格 ）。
  row.append(exportButton, handoffButton, ring, whenCell, cacheCell, grow, glyph, send);
  // 时间那个浮窗挂在那一格旁边（`composerBox` 是定位父级 ）
  box.append(cachePanel);
  box.append(whenPanel); // 那个定位父级叫 `box`（`.composer-box` ）
  form.append(box, row);

  const refresh = (): void => {
    void showUsage(); // 那一格现在是"上下文用了多少"（不是这一行几个字）
  };
  input.addEventListener('input', refresh);
  void showUsage(); // 画出来就先问一次
  input.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', (event: SubmitEvent) => {
    event.preventDefault();
    if (!canSend()) return;
    const text = input.value;
    // 现在**图也算内容**（2026-10-05）：只有文字那一格为空、而手上带着图 ⇒ 照样发
    if (text.trim() === '' && attached.length === 0) { void showUsage(); return; }
    const images = attached;
    attached = [];
    drawAttached();
    input.value = '';
    onSend(text, images);
    void showUsage(); // 发完再问一次（这一份请求多半变长了）
  });
  return { form, refresh, setProcessing };
}

/**
 * 把聊天那一页建出来（ 它就是页表里 `live` 那一项的 `mount`）。
 *
 * ⚠️ 交回的把手：正文那一列（`view` 那两下：推送来的帧 ／ 这一页自己的话）＋
 * 路由那两下（`show()` ／ `stop()`）—— 这一页没有自己的订阅，于是那两下只翻
 *    自己那两块的可见性；**留着它们**是为了"换页两下都做得到"这件事不因页而异。
 */
export function chatPage(shell: Chrome): Panel & { view: ChatView } {
  const chat = mountChat(shell.content);
  const page = chat.root.parentElement ?? chat.root;
  const activity = document.createElement('div'); activity.className = 'console-control-note';
  activity.setAttribute('role', 'status'); activity.setAttribute('aria-live', 'polite');
  activity.hidden = true;
  const activityBadge = document.createElement('span');
  const activityIcon = document.createElement('i');
  activityIcon.className = 'console-activity-icon';
  activityIcon.setAttribute('aria-hidden', 'true');
  const activityText = document.createElement('span');
  activityText.className = 'console-activity-text';
  activityBadge.append(activityIcon, activityText);
  activity.append(activityBadge);
  const showActivity = (text: string, busy = false): void => {
    const changed = activityText.textContent !== text || activity.hidden === !!text;
    const atBottom = chat.root.scrollHeight - chat.root.scrollTop - chat.root.clientHeight < 60;
    if (activityText.textContent !== text) activityText.textContent = text;
    activity.dataset.busy = String(busy);
    activity.hidden = !text;
    if (changed && atBottom) requestAnimationFrame(() => stickToTheBottom(activity));
  };
  chat.root.append(activity);
  let roundId: string | null = null;
  let wasProcessing = false;
  let gatePaused = false;
  let hasActiveModel: boolean | null = null;
  let activityRevision = 0;
  const readActivity = (event: Event): void => {
    const state = (event as CustomEvent).detail;
    if (!state || typeof state.processing !== 'boolean') return;
    if ('model' in state) hasActiveModel = typeof state.model === 'string' && !!state.model.trim();
    if (typeof state.stopped_by_hand === 'boolean') gatePaused = state.stopped_by_hand;
    if (wasProcessing !== state.processing || roundId !== (state.round_id ?? null)) activityRevision++;
    roundId = state.round_id ?? null;
    setProcessing(state.processing, state.interrupting === true);
    if (state.processing) showActivity(state.interrupting ? '正在中断并收尾…'
      : state.yielding ? '正在收尾，随后转接…' : '正在处理…', true);
    else if (state.activity?.background_running > 0) showActivity('正在整理…', true);
    else if (state.activity?.local_observations?.uncertain > 0) showActivity('有本地输入的处理结果待核对，不会自动重试。');
    else if (state.activity?.state === 'paused') showActivity('静候中，新事件会保留。');
    else if (wasProcessing) showActivity('');
    wasProcessing = state.processing;
  };
  window.addEventListener('core-console-state', readActivity);
  window.addEventListener('core-control-feedback', (event) => {
    showActivity((event as CustomEvent).detail?.text ?? '');
  });
  const stopCurrent = (): void => {
    const requested = roundId;
    if (!requested) return;
    setProcessing(true, true); showActivity('正在停止，请稍候…', true);
    void fetch('/interrupt', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ round_id: requested }) }).then(async response => {
      const state = await response.json();
      if (roundId === requested && typeof state.processing === 'boolean') {
        readActivity(new CustomEvent('core-console-state', {detail: state}));
      }
      if (!response.ok) showActivity(response.status === 409 ? '本轮已结束，请等待状态更新' : '停止状态未确认，请检查连接');
      window.dispatchEvent(new Event('core-control-refresh'));
    }).catch(() => { showActivity('停止状态未确认，请检查连接'); window.dispatchEvent(new Event('core-control-refresh')); });
  };
 // 正文那一列
  // **她说完一句 ⇒ 这一块当场跟着变**（2026-10-05 作者问"这个是动态的吗"）——
  // `refresh` 就是"刷一次"，而**收着的时候它自己会跳过**（见 `showUsage` 里那一句 ）
  // ⇒ 所以这里不必再判一次（不打开就不做无谓的那一趟 ）。
  // ⚠️ 它必须是 `composer` 交出来的那一个出口 —— 闭包里面那个够不到 。
  const { form, refresh, setProcessing } = composer((text: string, images: readonly string[]): void => {
    // JSON keeps multiline input inside one request envelope.
    // **先把这一句画到那一列上**（2026-10-05 作者："我看不到我有没有发出去"）——
    // ⚠️ 它是**回显**：画在"送出去之前"，所以它只说明"你按下去了、这一页打算送这个" ；
    // 真送没送到由下面那条 `note()` 说（失败时那一句就在它下面 ）。
    const intent = chat.mine(text, images);
    const body = JSON.stringify({ text, images });
    // **先自己量一遍**（那个上限与服务对齐 —— 见 `MAX_SAY_BYTES` 的抬头）：
    // 超了就把话说清、**不发**（服务那一侧回 413 时会关连接 ⇒ 页上只看到"断了" ）。
    const bodyBytes = new TextEncoder().encode(body).byteLength;
    if (bodyBytes > MAX_SAY_BYTES) {
      chat.clearPending(intent);
      const mb = Math.round((bodyBytes / 1024 / 1024) * 10) / 10;
      chat.note(`这一次太大了（约 ${mb} MB，上限 ${MAX_SAY_BYTES / 1024 / 1024} MB）——`
        + '去掉一两张图，或把它们裁小一点。');
      return;
    }
    if (hasEngineDraft()) {
      chat.note(hasActiveModel === false ? '模型配置尚未保存，当前没有可用的模型配置。'
        : '模型配置尚未保存，本次仍使用当前已保存的配置。');
    }
    const revision = activityRevision;
    showActivity('正在发送…', true);
    void fetch('/say', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }).then(async (res) => {
      chat.clearPending(intent);
      if (res.ok) { if (!wasProcessing && revision === activityRevision) showActivity(gatePaused ? '消息已保留，唤醒后再处理。' : '消息已接收，等待处理…', !gatePaused); window.dispatchEvent(new Event('core-control-refresh')); }
      if (!res.ok) {
        showActivity('发送失败');
        const detail = (await res.text()).trim().slice(0, 500);
        chat.note(`发送失败（HTTP ${res.status}）${detail ? '：' + detail : '。服务未返回原因。'}`);
      }
    }).catch(() => {
      chat.clearPending(intent);
      showActivity('发送状态未确认');
      chat.note('发送状态未确认，请先核对记录，避免重复发送。');
    });
  }, (said: string, action?: HTMLElement): void => { chat.note(said, action); }, stopCurrent, () => {
    if (gatePaused) showActivity('静候中，输入会保留，唤醒后再处理。');
    return true;
  });
  chat.watch(refresh); // 她说完一句 ⇒ 那一块当场跟着变（见接口上那条 ）
  // **把已经说过的话重放一遍**（2026-10-05 作者：那一列自己的抬头写着"刷新之后旧话
  // 就不在了"那一句**已经不成立**了 —— 历史重放 2026-10-05 接上了 ）。
  // ⚠️ 拉回来的是**一串帧**（`{kind, who, at, text}` ）—— 与那条推的通道**同一个形状**
  // ⇒ 这里**只用 `chat.append` 那一条路** （不必为历史另开一支 ，也不必多认一种格式 ）。
  // ⚠️ 拉不到 ⇒ 就照旧的空列（**不编** ）；`frames` 不是数组也照空 。
  let stopped = false;
  chat.refreshHistory = () => {
  const ticket = chat.beginHistory!();
  void fetch('/history').then((res) => { if (!res.ok) throw new Error('history unavailable');
    return res.json(); }).then((got) => {
    if (stopped) return;
    const box = got as { frames?: unknown } | null;
    const frames = box !== null && Array.isArray(box.frames) ? box.frames : [];
    chat.finishHistory!(ticket, frames);
  }).catch(() => { if (!stopped) { chat.failHistory!(ticket); chat.note('历史记录暂不可用；重新进入可重试。'); } });
  };
  chat.refreshHistory();
  mountConsoleIntroduction(chat.root, form);
  shell.bottom.replaceChildren(form); // 底部那一行（壳给的那一格）

  return {
    root: page,
    view: chat,
    show: (): void => { const wasStopped = stopped; stopped = false;
      if (wasStopped) chat.refreshHistory!(); chat.listen(true); page.hidden = false; chat.root.hidden = false;
      form.hidden = false; },
    stop: (): void => { stopped = true; chat.listen(false); page.hidden = true; chat.root.hidden = true;
      form.hidden = true; },
  };
}
