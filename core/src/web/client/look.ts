import { corePhrase, coreSetUIText } from './ui-language';
import { readDisplayLocal, writeDisplayLocal, onDisplayScopeReady } from './display-storage';
import { assetUrl } from './asset-url';
import { pageFetch as fetch } from './status-json';
// **外观那几样共用的东西**（ 2026-10-10 ）。
//
// ⚠ **为什么它在这里、不在 `shell.ts`** ： 一度是
// `shell` ⇄ `pages` ⇄ `settings-page` ⇄ `shell` **循环导入** ⇒
// ES module 里初始化顺序会变成 `undefined` ⇒ 而在**那一刻**
// 用它就抛 —— 而那一页就**画不出来** 。
// 一个**中立模块**就把那个环断开了 ： `shell` 读它 ／
// `settings-page` 读它 ／ 而它**不读任何一页** 。
//
// ⚠ **七样都得导出** —— 上一回就是 `loadLook` 忘了
// （ 它在 `shell.ts` 里**本来就没有** `export` ）。

export const THE_LOOK_ROWS: readonly (readonly [string, string])[] = [
  ['--color-ink', '正文'],
  ['--color-dim', '次要的字'],
  ['--color-canvas', '底色'],
  ['--color-line', '分隔线'],
  ['--color-mint', '强调'],
  ['--radius-card', '圆角'],
];

export const theLook: Record<string, string> = {};

/** Immutable theme assets are independent of pending mutable API saves. */
export function readLookAsset(file: string): Promise<Response> {
  return globalThis.fetch(assetUrl(file), { signal: AbortSignal.timeout(8000) });
}

const lookListeners = new Set<() => void>();
/** Resolved theme updates include presets, system mode and late profile loading. */
export function onLookChanged(listener: () => void): () => void {
  lookListeners.add(listener);
  return () => { lookListeners.delete(listener); };
}

let lookEpoch = 0;
let resolvedBase = '';
let displayEdited = false;
export function writeTheLook(automatic = false, editedName = ''): void {
  if (!automatic) displayEdited = true;
  // Explicit colour edits pin the current scheme; automatic mode owns colours.
  if (!automatic && editedName.startsWith('--color-') && theMode() === 'auto') {
    writeDisplayLocal(MODE_KEY, window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }
  lookEpoch++;
  for (const [name, value] of Object.entries(theLook)) {
    document.documentElement.style.setProperty(name, value);
  }
  // Cache only the resolved visual palette, for synchronous first paint next time.
  // Backend /look remains authoritative and refreshes this snapshot after loading.
  try {
    writeDisplayLocal('telyincore-look-resolved-v1', JSON.stringify({
      version: 1, mode: readDisplayLocal('telyincore-look-mode') || 'auto',
      pick: readDisplayLocal('telyincore-look-preset') || '',
      dark: window.matchMedia?.('(prefers-color-scheme: dark)').matches === true,
      base: resolvedBase,
      values: theLook,
    }));
    const mode = readDisplayLocal('telyincore-look-mode') || 'auto';
    document.documentElement.style.colorScheme = mode === 'dark'
      || (mode === 'auto' && window.matchMedia?.('(prefers-color-scheme: dark)').matches)
      ? 'dark' : 'light';
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && theLook['--color-canvas']) meta.setAttribute('content', theLook['--color-canvas']);
  } catch { /* Optional local cache; saving to /look is unchanged. */ }
  for (const listener of lookListeners) listener();
}

/** **2026-10-10：三种取用方式**（ 作者贴的那张图 ）。
 *
 * ⚠ **它为什么住在浏览器里** ： 它不是一个外观变量
 * （ 它不进 `:root` ）—— 它是“**挑哪一套**”这个偏好
 * ⇒ 存在本机 ， 与 `data/外观.json` 那份自定义色值**两回事** 。
 * ⚠ **而它挑的是哪一类** ： 浅色 ⇒ 浅底那一套 ；
 * 黑夜 ⇒ 深底那一套 ； 跟随系统 ⇒ 按那个媒体查询自己切 。
 * ⚠ **每一套自己声明明暗** （ `__scheme` ）——
 * 而不是把名字写在这里 。
 */
const MODE_KEY = 'telyincore-look-mode';
// **当前是哪一套** （ 而它与“哪一档”**不是一回事** ：
// 浅底有**两张**（默认 ／ 白橙 ）而模式分不出它们 ）。
const PICK_KEY = 'telyincore-look-preset';

export function theMode(): 'light' | 'dark' | 'auto' {
  const raw = readDisplayLocal(MODE_KEY);
  return raw === 'light' || raw === 'dark' ? raw : 'auto';
}

/** **换一档** —— 而它不只是改一个记录 ：
 *
 * ⚠ **为什么非得把那一套重新落一遍** ： 自定义那份
 * （`data/外观.json`）**永远压在上面** —— 只改模式、不重落
 * ⇒ 页面**看不出变化** （ 真页上量到过 ）。
 * ⚠ `跟随系统`那一档**不覆盖模式本身** （ 否则下一次就不跟了 ）。
 */
/** **当前是哪一份** （ 没选过 ⇒ 空串 ）。 */
export function thePick(): string {
  return readDisplayLocal(PICK_KEY) ?? '';
}

export async function setTheMode(mode: string): Promise<void> {
  displayEdited = true;
  const epoch = ++lookEpoch;
  const fine = mode === 'light' || mode === 'dark' ? mode : 'auto';
  writeDisplayLocal(MODE_KEY, fine);
  if (fine === 'auto') { await loadLook(); return; }
  const file = await theBaseFor(fine);
  if (epoch === lookEpoch) await putOn(file);
}

/** 跟随系统： 系统那一头变了就重挑一遍 （ 只在那一档管用 ）。 */
let watching = false;

function watchTheSystem(): void {
  if (watching) return;
  watching = true;
  const ask = window.matchMedia('(prefers-color-scheme: dark)');
  ask.addEventListener('change', () => {
    if (theMode() === 'auto') void loadLook();
  });
}

/** 这一档该用**哪一份**（ 没挑到 ⇒ 退回第一份 ）。 */
async function theBaseFor(mode: string): Promise<string> {
  const want = mode === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : mode;
  const all = await thePresets();
  const pick = all.find((one) => one.scheme === want);
  return pick === undefined ? (all[0]?.file ?? 'default.json') : pick.file;
}

export async function loadLook(): Promise<void> {
  const epoch = ++lookEpoch;
  const next = { ...theLook };
  try {
    watchTheSystem();
    // 按**这一档**挑那一套 （ 而不是死用第一份 ）。
    const mode = theMode();
    const want = thePick();
    // 选过就用选的那一套 （ 只有“跟随系统”那一档不看它 ）。
    const base = mode === 'auto' ? await theBaseFor(mode)
                                : (want !== '' ? want : await theBaseFor(mode));
    // 它落了哪一套就记下哪一套 （ 那一排卡点亮的就是它 ）。

    const got = await readLookAsset(base);
    if (!got.ok) return;
    const box = await got.json() as Record<string, unknown>;
    const baseSnapshot = JSON.stringify(box);
    for (const [name, value] of Object.entries(box)) {
      // 带两个下划线那几格是**给人看的** （ 不是 CSS 变量 ）
      if (name.startsWith('__')) continue;
      if (typeof value === 'string' && value !== '') next[name] = value;
    }
    // **2026-10-10（③）**：预设打底之后，**自定义压上来** 。
    // ⚠ **没有就照旧** （ 503 也是“还没接上” ）。
    await saveTail;
    if (epoch !== lookEpoch) return;
    const mine = await fetch('/look');
    if (mine.ok) {
      const box = await mine.json() as Record<string, unknown>;
      for (const [name, value] of Object.entries(box)) {
        if (name.startsWith('__')) continue;
        // /look contains a full saved palette, not only user overrides. Reusing
        // its colours would undo a system scheme change after first paint.
        if (mode === 'auto' && name.startsWith('--color-')) continue;
        if (typeof value === 'string' && value !== '') next[name] = value;
      }
    }
    if (epoch !== lookEpoch) return;
    resolvedBase = baseSnapshot;
    writeDisplayLocal(PICK_KEY, base);
    Object.assign(theLook, next);
    writeTheLook(true);
  } catch (why) {
    // ⚠️ **这一行必须留着**（ 裸 `catch` 会把真错吞成一句"读不到" ）。
    console.error('预设读不到（照旧用表里那些）：', why);
  }
}

let saveTail: Promise<void> = Promise.resolve();
let saveEpoch = 0;
export function keepTheLook(): Promise<void> {
  const body = JSON.stringify(theLook);
  const epoch = ++saveEpoch;
  const task = saveTail.then(() => persistLook(body, epoch));
  saveTail = task.catch(() => {});
  return task;
}

async function persistLook(body: string, epoch: number): Promise<void> {
  try {
    const response = await fetch('/look', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (epoch !== saveEpoch) return;
    document.getElementById('look-save-failure')?.remove();
    document.documentElement.dataset.lookSave = 'saved';
  } catch (why) {
    console.error('这一套外观没存下来：', why);
    if (epoch !== saveEpoch) return;
    document.documentElement.dataset.lookSave = 'failed';
    let warning = document.getElementById('look-save-failure');
    if (!warning) {
      warning = document.createElement('p'); warning.id = 'look-save-failure';
      warning.setAttribute('role', 'status'); document.body.append(warning);
    }
    coreSetUIText(warning, 'textContent', corePhrase("外观未保存；当前仅为临时显示，请重新保存。"));
  }
}

export async function thePresets():
    Promise<Array<{ file: string; name: string; scheme?: string }>> {
  try {
    const got = await readLookAsset('presets.json');
    if (!got.ok) return [];
    const box = await got.json() as Array<{ file?: unknown; name?: unknown; scheme?: unknown }>;
    return box.filter((one) => typeof one.file === 'string')
              .map((one) => ({ file: String(one.file),
                               name: String(one.name ?? one.file),
                               scheme: typeof one.scheme === 'string'
                                 ? one.scheme : undefined }));
  } catch (why) {
    console.error('预设清单读不到：', why);
    return [];
  }
}

/** **换上那一套** —— `keepMode` 是给「跟随系统」留的
 * （ 那一档不该被它挑的那一套反过来覆盖 ）。 */
export async function putOn(file: string, keepMode = false): Promise<void> {
  const epoch = ++lookEpoch;
  try {
    const got = await readLookAsset(file);
    if (!got.ok) return;
    const box = await got.json() as Record<string, unknown>;
    if (epoch !== lookEpoch) return;
    resolvedBase = JSON.stringify(box);
    writeDisplayLocal(PICK_KEY, file);
    // 点了哪一张就把模式跟着切 （ 它自己声明了明暗 ）。
    const scheme = typeof box.__scheme === 'string' ? box.__scheme : '';
    if (!keepMode && (scheme === 'light' || scheme === 'dark')) {
        writeDisplayLocal(MODE_KEY, scheme);
    }
    for (const [name, value] of Object.entries(box)) {
      if (name.startsWith('__')) continue;
      if (typeof value === 'string' && value !== '') theLook[name] = value;
    }
    writeTheLook();
    void keepTheLook();
  } catch (why) {
    console.error('那一套没换上来：', why);
  }
}


onDisplayScopeReady(prefix => {
  if (displayEdited) return; // Late identity must not overwrite a user edit.
  const apply = (window as unknown as { __coreApplyScopedTheme?: (prefix: string) =>
    void }).__coreApplyScopedTheme;
  apply?.(prefix);
  void loadLook();
});
