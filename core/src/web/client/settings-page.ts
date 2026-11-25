import { packageUpdateCard } from './package-updates';
import {pluginStorageSettings, snowlumaSessionSettings} from './plugin-storage-settings';
import { uiLanguage, setUILanguage, onUILanguage, uiLabel } from './ui-language';
import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { appendFrame } from './chat';
import { pageLoad } from './page-load';
import { modelProxySettings } from './model-proxy-settings';
// 设置页提供主题编辑与实时预览。
import type { Chrome, Panel } from './shell';
import { THE_LOOK_ROWS, keepTheLook, setTheMode, theLook, theMode,
         thePick, thePresets, putOn, writeTheLook, onLookChanged, readLookAsset } from './look';

/** **2026-10-10（2）：调色盘的语义分组** 。
 *
 * ⚠ **为什么分组而不是一堆** ： 作者那张图上就是这么摆的 ，
 * 而“背景层级”与“交互与状态”在人眼里是**两回事** 。
 * ⚠ 规则写在**明处** ； 而没归到的一律进「其它」
 * —— 那样**不会漏** （ 而漏了才是这一页最糟的失败 ）。
 */
const GROUPS: readonly (readonly [string, readonly (readonly [string, string])[]])[] = [
  ['背景层级', [
    ['--color-canvas', '页面背景'], ['--color-rail', '左栏背景'],
    ['--color-card', '主卡片背景'], ['--color-bubble', '聊天气泡'],
    ['--color-focus', '聚焦那一格'], ['--color-white', '纯白'],
    ['--color-wash', '淡底'], ['--color-scrim', '遮罩']]],
  ['文字与边界', [
    ['--color-ink', '主要文字'], ['--color-dim', '次级文字'],
    ['--color-faint', '弱化文字'], ['--color-line', '细边界']]],
  ['交互与状态', [
    ['--color-mint', '主强调'], ['--color-mint-text', '强调色上文字'],
    ['--color-mint-wash', '强调淡底'], ['--color-mint-pick', '选中淡底'],
    ['--color-mint-select', '选中边'], ['--color-soft-red', '危险／失败'],
    ['--color-flag-neutral', '中性小标'], ['--color-flag-ink', '小标上的字'],
    ['--color-flag-mint', '强调小标']]],
  ['圆角', [
    ['--radius-chip', '小标'], ['--radius-row', '一行'],
    ['--radius-card', '卡片'], ['--radius-pill', '胶囊'],
    ['--radius-dot', '小点'], ['--radius-bubble', '气泡']]],
  ['字体与尺寸', [
    ['--font-sans', '正文字体'], ['--font-mono', '等宽字体'],
    ['--sans', '正文（旧名）'], ['--mono', '等宽（旧名）'],
    ['--icon', '图标'], ['--avatar-msg', '聊天头像'],
    ['--avatar-rail', '左栏头像'], ['--nav-veil-blur', '毛玻璃'],
    ['--color-nav-veil', '导航遮罩']]],
];

/** 它归哪一组（ 没归到的一律「其它」 —— 不会漏 ）。 */
function groupOf(name: string): string {
  for (const [title, rows] of GROUPS) {
    if (rows.some((one) => one[0] === name)) return title;
  }
  return '其它';
}

/** 那一格在页上的中文名（ 没有就用变量名本身 —— 不编 ）。 */
function labelOf(name: string): string {
  for (const [, rows] of GROUPS) {
    for (const [key, label] of rows) if (key === name) return label;
  }
  return name;
}

/** **2026-10-10：三种取用方式那一排** （ 照作者那张图 ）。 */
const MODES: readonly (readonly [string, string, string])[] = [
  ['auto', '跟随系统', '◐'],
  ['light', '浅色', '☀'],
  ['dark', '黑夜', '☾'],
];

/** 把当前那一档点亮 。 */
function markTheMode(): void {
  const now = theMode();
  for (const one of document.querySelectorAll<HTMLElement>('.set-mode')) {
    one.classList.toggle('on', one.dataset.mode === now);
  }
}

/** 那一排本身 。 */
function theModeRow(): HTMLElement {
  const row = document.createElement('div');
  row.className = 'set-modes';
  for (const [key, label, glyph] of MODES) {
    const bit = document.createElement('button');
    bit.className = 'set-mode';
    bit.dataset.mode = key;
    bit.textContent = glyph + ' ' + label;
    bit.addEventListener('click', () => {
      void setTheMode(key).then(() => {
        markTheMode();
        markThePick();
      });
    });
    row.append(bit);
  }
  return row;
}

/** **把“当前那一套”点亮** —— 照模型页那个 `picked`
 * （ `.engine-card.picked`：一圈强调色边框 ＋ 一层 1px 的内圈 ）。
 *
 * ⚠ **为什么不能只看模式** ： 浅底有**两张**卡（默认 ／ 白橙 ）
 * —— 而模式只分得出“浅底”这一档 ⇒ 两张都会亮 。
 */
function markThePick(): void {
  const now = thePick();
  for (const one of document.querySelectorAll<HTMLElement>('.set-card')) {
    one.classList.toggle('picked', one.dataset.file === now);
  }
}

/** 左栏那两项（ 照那张图的次序 ）。 */
const TABS: readonly (readonly [string, string])[] = [
  ['general', '通用'],
  ['look', '外观'],
  ['storage', '存储'],
];

/** 一块小标题（ 与那一页上其它地方同一个样子 ）。 */
function head(text: string, note: string): HTMLElement {
  const box = document.createElement('div');
  box.className = 'set-head';
  const h = document.createElement('h2');
  uiLabel(h, text);
  const p = document.createElement('p');
  p.className = 'set-note';
  uiLabel(p, note);
  box.append(h, p);
  return box;
}


/* 实时试色复用消息与工具组件的 class；颜色、圆角和字体从主题变量读取。 */
/** **那一格是什么「值」** —— 控件按它来选（不是一律取色器）。 */
function kindOf(value: string): 'color' | 'alpha' | 'length' | 'font' | 'token' {
  const v = value.trim();
  if (/^var\(/i.test(v)) return 'token';
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return 'color';
  if (/^rgb\(/i.test(v)) return 'alpha';
  // 多个数也算尺寸 （ `4px 15px 15px` 那种 ——
  // 上一回它落进了「字体」那一类成了下拉 ）。
  if (/^([0-9.]+px)([ \t]+[0-9.]+px)*$/.test(v)) return 'length';
  return 'font';
}

/** **滑杆的上下界按变量给** —— 从前一律 0–40 ⇒ `999px` 被夹到 40。 */
function rangeOf(name: string, seen: number): readonly [number, number] {
  if (name === '--radius-pill') return [0, 1000];
  if (name.startsWith('--radius')) return [0, 32];
  if (name === '--icon') return [8, 40];
  if (name.startsWith('--avatar')) return [16, 96];
  if (name === '--nav-veil-blur') return [0, 40];
  return [0, Math.max(32, Math.ceil(seen * 2))];
}

/** 字体那一串的**头一个字族**（下拉里显示这个就够了）。 */
function shortFamily(value: string): string {
  const first = value.split(',')[0].trim();
  return first.replace(/^["']|["']$/g, '');
}

/** 把 `rgb(r g b / p%)` 拆成一个色 ＋ 一个百分比。 */
function rgbOf(value: string): { hex: string; pct: number } {
  const shape = /^rgb\(\s*([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*\/\s*([0-9.]+)%\s*\)$/i;
  const m = shape.exec(value.trim());
  if (m === null) return { hex: '#000000', pct: 100 };
  const hex = '#' + [m[1], m[2], m[3]]
    .map((bit) => Number(bit).toString(16).padStart(2, '0')).join('');
  return { hex, pct: Number(m[4]) };
}

/** 反过来：一个色 ＋ 一个百分比 → `rgb(r g b / p%)`。 */
function rgbText(hex: string, pct: number): string {
  const at = (n: number): number => parseInt(hex.slice(n, n + 2), 16);
  return 'rgb(' + at(1) + ' ' + at(3) + ' ' + at(5) + ' / ' + pct + '%)';
}

/** **一格** —— 控件的形式跟着它的**交互逻辑**走。
 *
 * ⚠️ **这一条是作者纠正过的**： 该拖的是滑杆 、该点的是按钮 ——
 * 而一度我把尺寸做成了数字框、把颜色做成了取色器 ＋ 数字框
 * ⇒ 那**还不如之前** 。
 * ⚠️ **三列固定**：控件（22px）／名字／值 —— 值那一格超长截断。
 */
function oneItem(name: string,
                 put: (n: string, v: string, code?: HTMLElement) => void,
                 pool: Map<string, string[]>): HTMLElement {
  const item = document.createElement('div');
  item.className = 'set-item';
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const words = document.createElement('span');
  words.className = 'set-item-words';
  uiLabel(words, labelOf(name));
  words.title = labelOf(name);
  const cell = document.createElement('span');
  cell.className = 'set-swatch';
  const slot = document.createElement('span');
  slot.className = 'set-value';
  const kind = kindOf(value);

  if (kind === 'font') {
    // 字体是**离散的** ⇒ 一个按钮（下拉）。
    const pick = document.createElement('select');
    pick.className = 'set-select';
    pick.setAttribute('aria-label', labelOf(name));
    for (const one of new Set<string>([value, ...(pool.get(name) ?? [])])) {
      const opt = document.createElement('option');
      opt.value = one;
      opt.textContent = shortFamily(one);
      opt.selected = one === value;
      pick.append(opt);
    }
    pick.addEventListener('change', () => { put(name, pick.value); });
    slot.append(pick);
    item.append(cell, words, slot);
    return item;
  }

  if (kind === 'token') {
    // 它是个**引用** ⇒ 不给控件。
    cell.classList.add('token');
    cell.title = value;
    const code = document.createElement('code');
    code.className = 'set-code';
    code.textContent = value;
    code.title = name;
    slot.append(code);
    item.append(cell, words, slot);
    return item;
  }

  const code = document.createElement('code');
  code.className = 'set-code';
  code.textContent = value;
  code.title = name;

  if (kind === 'color') {
    // 颜色 ⇒ **色块按钮**（点开取色器）。
    const pick = document.createElement('input');
    pick.type = 'color';
    pick.className = 'set-pick';
    coreSetUIAttribute(pick, 'aria-label', [labelOf(name), corePhrase("颜色")]);
    pick.value = value;
    pick.addEventListener('input', () => { put(name, pick.value, code); });
    cell.append(pick);
    slot.append(code);
  } else if (kind === 'alpha') {
    // 色块按钮 ＋ **透明度滑杆**。
    const bits = rgbOf(value);
    const pick = document.createElement('input');
    pick.type = 'color';
    pick.className = 'set-pick';
    coreSetUIAttribute(pick, 'aria-label', [labelOf(name), corePhrase("颜色")]);
    pick.value = bits.hex;
    const pct = document.createElement('input');
    pct.type = 'range';
    pct.className = 'set-range';
    coreSetUIAttribute(pct, 'aria-label', [labelOf(name), corePhrase("透明度")]);
    pct.min = '0';
    pct.max = '100';
    pct.step = '1';
    pct.value = String(bits.pct);
    code.textContent = String(bits.pct) + '%';
    const both = (): void => {
      code.textContent = String(pct.value) + '%';
      put(name, rgbText(pick.value, Number(pct.value)));
    };
    pick.addEventListener('input', both);
    pct.addEventListener('input', both);
    cell.append(pick);
    slot.append(pct, code);
  } else {
    // 尺寸 ⇒ **滑杆**（该拖的就给拖）。
    const seen = parseFloat(value) || 0;
    const bounds = rangeOf(name, seen);
    const num = document.createElement('input');
    num.type = 'range';
    num.className = 'set-range';
    coreSetUIAttribute(num, 'aria-label', [labelOf(name), corePhrase("尺寸")]);
    num.min = String(bounds[0]);
    num.max = String(bounds[1]);
    num.step = '1';
    num.value = String(seen);
    num.addEventListener('input', () => {
      const unit = value.replace(/^[0-9.\s-]+/, '') || 'px';
      put(name, num.value + unit, code);
    });
    cell.classList.add('plain');
    slot.append(num, code);
  }
  item.append(cell, words, slot);
  return item;
}


function specimen(): HTMLElement {
  const box = document.createElement('details');
  box.className = 'set-spec settings-card';

  const head = document.createElement('summary');
  head.className = 'set-spec-head';
  const title = document.createElement('span');
  coreSetUIText(title, 'textContent', corePhrase("聊天预览"));
  const note = document.createElement('span');
  note.className = 'set-spec-note';
  coreSetUIText(note, 'textContent', corePhrase("展开查看 · 配色实时同步"));
  box.addEventListener('toggle', () => {
    coreSetUIText(note, 'textContent', box.open
      ? corePhrase('配色实时同步') : corePhrase('展开查看 · 配色实时同步'));
  });
  head.append(title, note);
  box.append(head);

  const demo = document.createElement('div');
  demo.className = 'set-spec-demo';

  // Use the console renderer so receipt placement, controls and message markup stay aligned.
  appendFrame(demo, { kind: 'msg', who: 'user', at: '14:52', text: '帮我查一下之前的相关记录。' });
  appendFrame(demo, { kind: 'tool', who: null, at: '14:53', tool: 'memory_search',
    stage: 'call', display_id: 'preview-memory:function_call', text: '{"query":"相关记录"}' });
  appendFrame(demo, { kind: 'tool', who: null, at: '14:53', tool: 'memory_search',
    stage: 'result', display_id: 'preview-memory:function_result', text: '已找到 3 条相关记录。' });
  appendFrame(demo, { kind: 'msg', who: 'bot', at: '14:53',
    text: '找到了 3 条相关记录，我来帮你整理。' }, 'BOT');

  box.append(demo);
  return box;
}

export function settingsPage(shell: Chrome): Panel {
  const root = document.createElement('section');
  root.className = 'page-settings page-world settings-unified';

  const bar = document.createElement('div');
  bar.className = 'set-bar';
  const title = document.createElement('h1');
  coreSetUIText(title, 'textContent', corePhrase("设置 · SETTINGS"));
  bar.append(title);
  root.append(bar);

  const book = document.createElement('div');
  book.className = 'set-book';
  const tabs = document.createElement('nav');
  tabs.className = 'set-tabs';
  const body = document.createElement('div');
  body.className = 'set-body';
  book.append(tabs, body);
  root.append(book);

  onUILanguage(value => {
    for (const button of root.querySelectorAll<HTMLButtonElement>('.settings-language-button')) {
      button.setAttribute('aria-pressed', String(button.dataset.language === value));
    }
  });
  let viewEpoch = 0;
  let refreshPalette: (() => void) | undefined;
  let unsubscribeLook: (() => void) | undefined;
  let editingPalette = false;
  const refreshLook = (): void => {
    if (root.hidden || editingPalette) return;
    markTheMode();
    markThePick();
    refreshPalette?.();
  };
  // Keep the same editor and its unsaved draft when switching settings tabs.
  let proxyPanel: HTMLElement | undefined;
  const readHint = pageLoad(root, '外观预设', () => { void drawLook(); });
  const drawGeneral = (): void => {
    viewEpoch++; refreshPalette = undefined; readHint.ready();
    body.replaceChildren();
    const language = document.createElement('section');
    language.className = 'settings-card settings-language';
    const languageTitle = document.createElement('h3');
    coreSetUIText(languageTitle, 'textContent', corePhrase("界面语言"));
    const languageOptions = document.createElement('div');
    languageOptions.className = 'settings-language-options';
    languageOptions.setAttribute('role', 'group');
    coreSetUIAttribute(languageOptions, 'aria-label', corePhrase('界面语言'));
    for (const [value, label] of [['zh-Hans', '简体中文'], ['zh-Hant', '繁體中文']] as const) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'settings-language-button';
      button.dataset.language = value; button.textContent = label;
      button.setAttribute('aria-pressed', String(uiLanguage() === value));
      button.addEventListener('click', () => setUILanguage(value));
      languageOptions.append(button);
    }
    language.append(languageTitle, languageOptions);
    body.append(language);
    body.append(packageUpdateCard());
    body.append(proxyPanel ??= modelProxySettings());
  };

  const drawLook = async (): Promise<void> => {
    const epoch = ++viewEpoch; refreshPalette = undefined; readHint.start();
    body.replaceChildren();

    // Ⅰ 颜色方案（ 一套 ＝ 一张卡 —— 点一下就换 ）
    // Ⅲ **右边那一块：实时试色** （ 照作者那张图 ）。
    const two = document.createElement('div');
    two.className = 'set-two';
    const left = document.createElement('div');
    left.className = 'set-left settings-card';
    const right = document.createElement('div');
    right.className = 'set-right';
    const controls = document.createElement('div');
    controls.className = 'set-controls';
    controls.append(left);
    two.append(controls, right);
    const preview = specimen() as HTMLDetailsElement;
    right.append(preview); body.append(two);

    const appearanceTitle = document.createElement('h3');
    coreSetUIText(appearanceTitle, 'textContent', corePhrase("主题与配色"));
    left.append(appearanceTitle, theModeRow());
    const modeHint = document.createElement('p');
    modeHint.className = 'set-note';
    coreSetUIText(modeHint, 'textContent', corePhrase('跟随系统自动切换明暗配色；选择预设或手动调色后，使用固定配色。'));
    left.append(modeHint);
    markTheMode();

    const scheme = document.createElement('div');
    scheme.className = 'set-scheme';
    const title = document.createElement('div');
    title.className = 'set-scheme-title';
    coreSetUIText(title, 'textContent', corePhrase("颜色方案"));
    scheme.append(title);
    const cards = document.createElement('div');
    cards.className = 'set-cards';
    scheme.append(cards);
    left.append(scheme);

    const presets = await thePresets();
    let incomplete = presets.length === 0;
    if (epoch !== viewEpoch || root.hidden) return;
    const presetReads = new Map(presets.map(one => [one.file,
      readLookAsset(one.file).then(r => r.ok ? r.json() : null).catch(() => null)]));
    for (const one of presets) {
      const card = document.createElement('button');
      card.className = 'set-card';
      card.dataset.file = one.file;
      const name = document.createElement('span');
      name.className = 'set-card-name';
      name.textContent = one.name;
      const seen = document.createElement('span');
      seen.className = 'set-card-swatch';
      try {
        const box = await presetReads.get(one.file) as Record<string, unknown> | null;
        if (epoch !== viewEpoch || root.hidden) return;
        if (!box) incomplete = true;
        if (box) {
          for (const key of ['--color-canvas', '--color-rail', '--color-card',
                             '--color-mint', '--color-ink']) {
            const bit = document.createElement('i');
            if (typeof box[key] === 'string') bit.style.background = box[key] as string;
            seen.append(bit);
          }
        }
      } catch (why) {
        console.error('那一套读不到：', why);
      }
      const mark = document.createElement('span');
      mark.className = 'set-card-mark';
      coreSetUIText(mark, 'textContent', corePhrase("内置"));
      card.append(name, mark, seen);
      // 点了哪一张 ⇒ 那一排的灯也跟着切 。
      card.addEventListener('click', () => {
        void putOn(one.file).then(() => {
          markTheMode();
          markThePick();
        });
      });
      cards.append(card);
    }

    // Ⅱ 调色盘（ 按语义分组 —— 而没归到的进「其它」）
    const palette = document.createElement('div');
    palette.className = 'set-palette';
    const ptitle = document.createElement('div');
    ptitle.className = 'set-scheme-title';
    coreSetUIText(ptitle, 'textContent', corePhrase("细节调整"));
    palette.append(ptitle);

    // **名单从那一份预设来** （ 它才是**全部** ——
    // 而 `THE_LOOK_ROWS` 只是旧浮层那六行 ）。
    let names: string[] = THE_LOOK_ROWS.map((one) => one[0]);
    try {
      const got = await readLookAsset('default.json');
      if (got.ok) {
        const box = await got.json() as Record<string, unknown>;
        const plenty = Object.keys(box).filter((k) => !k.startsWith('__'));
        if (plenty.length > 0) names = plenty;
      }
    } catch (why) {
      console.error('调色盘的名单读不到，退回那六行：', why);
    }
    if (epoch !== viewEpoch || root.hidden) return;

    /** 写一格 （ 三件事一起做 ：当场变 ／ 存下去 ／ 写回格里 ）。 */
    const put = (name: string, value: string, code?: HTMLElement): void => {
      theLook[name] = value;
      // Keep the active native picker/slider mounted while it emits input.
      editingPalette = true;
      try { writeTheLook(false, name); } finally { editingPalette = false; }
      markTheMode();
      void keepTheLook();
      if (code !== undefined) code.textContent = value;
    };

    // 字体那一类要一个**可选值**的单子（ 下拉用 ）—— 从各预设里收
    // （ 只收真出现过的值 —— 不编 ）。
    const pool = new Map<string, string[]>();
    for (const one of presets) {
      try {
        const box = await presetReads.get(one.file) as Record<string, unknown> | null;
        if (epoch !== viewEpoch || root.hidden) return;
        if (!box) continue;
        for (const [name, cell] of Object.entries(box)) {
          if (name.startsWith('__') || typeof cell !== 'string') continue;
          const seen = pool.get(name) ?? [];
          if (!seen.includes(cell)) pool.set(name, [...seen, cell]);
        }
      } catch (why) {
        console.error('收可选值时那一份读不到：', one.file, why);
      }
    }

    // 2026-10-10（作者）：「正文字体 和 等宽字体 这俩删掉好了。」
    // 理由： 每一档里它**只有一种** ⇒ 摆在那儿也调不动 。
    // ⚠ 它们**还在预设里** （ 预设该覆盖外观那一半 ）
    // —— 只是**不摆进调色盘** 。
    names = names.filter((one) => one !== '--font-sans' && one !== '--font-mono'
                              && one !== '--sans' && one !== '--mono');

    const byGroup = new Map<string, string[]>();
    for (const name of names) {
      const key = groupOf(name);
      byGroup.set(key, [...(byGroup.get(key) ?? []), name]);
    }
    const order = [...GROUPS.map((one) => one[0]), '其它'];
    let activePaletteGroup = order.find(key => byGroup.has(key)) ?? '';
    refreshPalette = (): void => {
      palette.replaceChildren(ptitle);
      for (const key of order) {
        const mine = byGroup.get(key);
        if (mine === undefined || mine.length === 0) continue;
        const box = document.createElement('details');
        box.className = 'set-group';
        box.open = key === activePaletteGroup;
        box.addEventListener('toggle', () => {
          if (!box.isConnected || !box.open) return;
          activePaletteGroup = key;
          for (const other of palette.querySelectorAll<HTMLDetailsElement>('.set-group')) {
            if (other !== box) other.open = false;
          }
        });
        const h = document.createElement('summary');
        h.className = 'set-group-title';
        uiLabel(h, key);
        box.append(h);
        const grid = document.createElement('div');
        grid.className = 'set-grid';
        for (const name of mine) grid.append(oneItem(name, put, pool));
        box.append(grid);
        palette.append(box);
      }
    };
    refreshPalette();
    const advanced = document.createElement('details');
    advanced.className = 'settings-card set-advanced';
    const advancedTitle = document.createElement('summary');
    coreSetUIText(advancedTitle, 'textContent', corePhrase("高级外观"));
    advanced.append(advancedTitle, palette);
    advanced.addEventListener('toggle', () => {
      two.classList.toggle('is-editing', advanced.open);
      if (advanced.open) preview.open = true;
    });
    two.append(advanced);

    // 右边那一列就是试色 （ 而 `two` 到这一刻才挂上 ）
    if (epoch !== viewEpoch || root.hidden) return;

    // 点亮得在**那一块真正挂上去之后**
    // （ `querySelectorAll` 只找得到**已挂上的** ——
    // 而 `body.append(two)` 在这一句之前 ）。
    markThePick();
    if (incomplete) readHint.fail(); else readHint.ready();
  };

  const draws: Record<string, () => void> = {
    general: drawGeneral,
    storage: () => { viewEpoch++; refreshPalette = undefined; readHint.ready(); body.replaceChildren(pluginStorageSettings(), snowlumaSessionSettings()); },
    look: () => { void drawLook(); },
  };
  for (const [key, label] of TABS) {
    const bit = document.createElement('button');
    bit.className = 'set-tab';
    uiLabel(bit, label);
    bit.addEventListener('click', () => { draws[key](); markTheTab(key); });
    bit.dataset.tab = key;
    tabs.append(bit);
  }
  const markTheTab = (key: string): void => {
    for (const one of tabs.querySelectorAll<HTMLElement>('.set-tab')) {
      one.classList.toggle('on', one.dataset.tab === key);
    }
  };

  // **2026-10-10：把自己挂上去** —— 这一步**每一页自己做**
  // （ 成法见 `live.ts:746`：`mountChat(shell.content)` ）。
  // ⚠ 一度写成 `void shell;` —— 那等于**没挂** ，
  // 而那一页就**不出现** （ 页表里有、导航也点得动 ）。
  shell.content.replaceChildren(root);

  return {
    root,
    show(): void {
      root.hidden = false;
      unsubscribeLook ??= onLookChanged(refreshLook);
      drawGeneral();
      markTheTab('general');
    },
    stop(): void {
      viewEpoch++; root.hidden = true;
      refreshPalette = undefined;
      unsubscribeLook?.(); unsubscribeLook = undefined;
    },
  };
}
