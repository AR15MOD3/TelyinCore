// 那一页的构建 —— **两条步骤**，而对外仍是一条命令：`pnpm run build:web`（从仓根跑）。
//
//   ① **esbuild**（那一步一直在）：`main.ts` ⇒ `app.js`（bundle ／ `charset: 'utf8'`）；
// ② **Tailwind 的 CLI**（本刀加上的那一步）：`style.css` ⇒ `style.css` —— 产物**只有
//      一份 CSS**：Tailwind 打出来的那一份（它里面既有它自己的 preflight ／ utilities，
//      也有我们写在 `@theme` 里的那六条词汇）；
//   ③ 把壳（`index.html`）原样拷过去，并把落下的文件连大小一起打出来（一份当场看得见的收据）。
//
// ⚠️⚠️ **第 ② 步那三件事是读了实物写下来的**（不是按名字猜的 —— 本支为这个栽过两次）：
//    · **bin／入口**：`@tailwindcss/cli` 那个包（`package.json` 的 `bin` 是 `tailwindcss`）
//      的交回值是 `dist/index.mjs` —— 这里用 `createRequire(...).resolve()` 找到它，
//      再用**当下这个 node**（`process.execPath`）跑它 ⇒ 不依赖 PATH 上有没有那个 shim，
//      也不依赖 Windows 特有的 `.cmd`；
//    · **两个开关**：`-i/--input` 与 `-o/--output`（`--help` 里那一行：
//      `tailwindcss [--input input.css] [--output output.css] [--watch] [--poll=ms]`）；
//      `-w/--watch` 我们**不用**（这条构建是一次性的），`--silent` 是为了让收据干净；
// · **没有 config 文件**：v4 是 CSS 优先 —— 主题写在 `style.css` 的 `@theme` 里
//      （树里没有 `tailwind.config.*`，也不需要）。
// ⚠️⚠️ **这个文件里零绝对路径** —— DSH 那份运行时目录那一条路径一个字都不许出现：
//    工具链来自 PATH 上系统装的 node ／ pnpm（本机实测 node v24.21.0 ／ pnpm 12.3.4）
//    ⇒ "在别处 clone 也建得出来"这句真话靠的就是这一条。
// 而第 ② 步那个 CLI 的路径**是从本仓的 `node_modules/` 里算出来的**（相对本文件），
//      所以它照样不是一条写死的绝对路径。
// ⚠️ **`charset: 'utf8'` 是承重的**：esbuild 的缺省是 ascii ⇒ 中文会被转义成 \uXXXX，
//    而那一页上那句"写入那条路的边界"就会变成一串转义码（人读不了）。
// ⚠️ **依赖只有三个**（`package.json` 的 devDependencies：`esbuild` ／ `tailwindcss` ／
//    `@tailwindcss/cli`）—— 每多一个就是多一层要背的。
// ⚠️ `--outdir=<dir>` 是给测试用的（把产物落到临时目录 ⇒ 跑测试不往仓里写东西）；
//    不给就落 `dist/web/`（那是 `pnpm run build:web` 的那一条路）。
// 两条步骤**都认这个参数**：① 的 `outdir` 与 ② 的 `-o` 都从它算出来。

import { spawnSync } from 'node:child_process';
import { copyFile, mkdir, readFile, readdir, lstat, mkdtemp, rename, stat,
  writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve, relative, isAbsolute, parse } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const CLIENT = join(ROOT, 'src', 'web', 'client');

// Boundary helpers are side-effect free until explicitly called.
export async function checkedOutput(root, raw) {
  if (!raw || raw.split(/[\\/]/).includes('..')) throw new Error('Unsafe output path');
  const out = resolve(raw);
  const rel = relative(out, resolve(root));
  if (out === parse(out).root || rel === '' || (!rel.startsWith('..') && !isAbsolute(rel)))
    throw new Error('Output is a root or repository ancestor');
  // Only the fixed repository output may replace an existing directory.
  if (out !== join(resolve(root), 'dist', 'web')) {
    const inside = relative(resolve(root), out);
    if (!inside.startsWith('..') && !isAbsolute(inside))
      throw new Error('Custom output must be outside repository');
    try { await lstat(out); throw new Error('Custom output must not exist'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  for (let part = out;; part = dirname(part)) {
    try { if ((await lstat(part)).isSymbolicLink()) throw new Error('Output link refused'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (part === dirname(part)) break;
  }
  return out;
}

export async function publishOutput(root, raw, stage) {
  const out = await checkedOutput(root, raw);
  if (dirname(resolve(stage)) !== dirname(out) ||
      !relative(dirname(out), resolve(stage)).startsWith('.web-stage-') ||
      (await lstat(stage)).isSymbolicLink() || !(await lstat(stage)).isDirectory())
    throw new Error('Invalid staging directory');
  let backup;
  const failure = (phase, primary, rollback) => {
    const error = rollback === undefined
      ? new Error(`Output ${phase} failed`, { cause: primary })
      : new AggregateError([primary, rollback], 'Output publish and rollback failed',
                           { cause: primary });
    // Preserve recovery locations without deleting either candidate or backup.
    return Object.assign(error, { phase, output: out, stage: resolve(stage), backup });
  };
  let present = false;
  try { await lstat(out); present = true; }
  catch (error) { if (error.code !== 'ENOENT') throw failure('inspect', error); }
  if (present) {
    try {
      // Reserve a unique backup name; preserve old output, never recursively delete.
      backup = await mkdtemp(join(dirname(out), '.web-backup-'));
      await rename(out, join(backup, 'previous'));
    } catch (error) {
      // ENOENT here is a failed move, not evidence that output was absent.
      throw failure('backup', error);
    }
  }
  try { await rename(stage, out); }
  catch (error) {
    if (backup) {
      try { await rename(join(backup, 'previous'), out); }
      catch (rollback) { throw failure('rollback', error, rollback); }
    }
    throw failure('publish', error);
  }
  return backup;
}

async function main() {
const arg = process.argv.find((a) => a.startsWith('--outdir='));
const requested = arg === undefined ? join(ROOT, 'dist', 'web') : arg.slice('--outdir='.length);
const FINAL = await checkedOutput(ROOT, requested);

let esbuild;
try {
  esbuild = await import('esbuild');
} catch {
  console.error('没装 esbuild —— 先跑 `pnpm install`（见 src/web/README.md）。');
  process.exit(2);
}

// 第 ② 步那一支：Tailwind 的 CLI（它自己的包只在 `exports` 里露出 `./package.json`，
//   于是这里就从那一份算到它旁边那份 `dist/index.mjs` —— 不猜 bin 的名字，读的是实物）。
let cli;
try {
  cli = resolve(dirname(createRequire(import.meta.url).resolve('@tailwindcss/cli/package.json')),
                'dist', 'index.mjs');
} catch {
  console.error('没装 @tailwindcss/cli —— 先跑 `pnpm install`（见 src/web/README.md）。');
  process.exit(2);
}

await mkdir(dirname(FINAL), { recursive: true });
const OUT = await mkdtemp(join(dirname(FINAL), '.web-stage-'));

// ── ① esbuild：那一页的脚本 ⇒ `app.js` ─────────────────────────────────────
// 2026-10-10（外观那一页 ①）：**把预设拷进产物**
// ⚠️ 为什么是拷、不是 esbuild 打进去： 作者那句"**可以使用这些预设**" ——
// 一套预设 ＝ **一份 json** （ 别人加一套就是加一份文件 ， 不改代码 ）
// ⇒ 那就得是**真文件** ， 不能被 bundle 吞掉 。
// ⚠️ **为什么**平铺**、不建子目录** ： 服务那条发产物的路只有**一层**
// （`web.py::_PAGE_FILE` —— `presets/默认.json` **带斜杠 ⇒ 404** ）。
// 而那条边界是**有意**的 ： 它靠"只有一层"把 `..` ／ `a/b` ／ `.gitignore`
// 全挡在外面 ⇒ **不许**为了这一份文件去放宽它 （ 放宽＝打开那些口子 ）。
// ⇒ 预设平铺在产物根 （ 一套一份 —— 名字就是它那一套的名字 ）。
// 2026-10-10（外观那一页 ④）：**连清单一起生成**
// ⚠️ 为什么要有清单： 那一页得**知道有哪几套**才能让人挑 ——
// 而"有哪几套"**必须自动来** （ 加一份文件就多一套 ，
// 把名字写进代码＝第二个答案 ）。 清单里那一格 `name` 取自
// 每一份自己的 `__name` （ 没有就退它的文件名 —— 不编 ）。
const thePresets = [];
for (const one of await readdir(join(ROOT, 'presets'))) {
  if (!one.endsWith('.json')) continue;
  await copyFile(join(ROOT, 'presets', one), join(OUT, one));
  let shown = one.replace(/\.json$/, '');
  let rank = 999;                       // 没写 __order 的排后面
  let scheme = 'light';                 // 没写的当浅底
  try {
    const box = JSON.parse(await readFile(join(ROOT, 'presets', one), 'utf8'));
    if (typeof box.__name === 'string' && box.__name !== '') shown = box.__name;
    if (typeof box.__order === 'number') rank = box.__order;
    if (typeof box.__scheme === 'string') scheme = box.__scheme;
  } catch { /* 读不动就退文件名 （ 不许因为这份坏了就不出清单 ） */ }
  thePresets.push({ file: one, name: shown, order: rank, scheme });
}
// 2026-10-10（作者）： **默认放最前** —— 其余照文件名排 。
// 2026-10-10（作者要的次序）： **次序由每一份自己写**（`__order`） ——
// 没写的那几份排在有写的后面、再照文件名
// （ 这样加一份 json 就多一套，代码一个字不动 ）。
thePresets.sort((a, b) => (a.order - b.order) || a.file.localeCompare(b.file))
await writeFile(join(OUT, 'presets.json'),
                JSON.stringify(thePresets, null, 2) + '\n', 'utf8');
const guideFiles = (await readdir(join(ROOT, 'docs')))
  .filter(name => /^Infinity插件开发规范-v\d+(?:\.\d+)*\.md$/.test(name))
  .sort((a, b) => a.localeCompare(b, 'en', { numeric: true }));
if (!guideFiles.length) throw new Error('Plugin developer guide missing');
const guideFile = guideFiles.at(-1);
const guideVersion = guideFile.match(/-(v[\d.]+)\.md$/)[1];
await esbuild.build({
  entryPoints: { app: join(CLIENT, 'main.ts') },
  define: { __PLUGIN_GUIDE_VERSION__: JSON.stringify(guideVersion) },
  outdir: OUT,
  bundle: true,
  format: 'esm',
  target: ['es2022'],
  charset: 'utf8',
  logLevel: 'warning',
});

// ── ② Tailwind 的 CLI：那一页的词汇表 ⇒ `style.css`（ 产物只有这一份 CSS） ──
const css = spawnSync(process.execPath, [
  cli,
  '--input', join(CLIENT, 'style.css'),
  '--output', join(OUT, 'style.css'),
  '--silent',
], { cwd: ROOT, stdio: 'inherit' });
if (css.error !== undefined || css.status !== 0) {
  console.error(`Tailwind 那一步没跑成（exit=${css.status ?? css.error?.code}）——`
    + '先跑 `pnpm install`；还不行就看它上面那几行。');
  process.exit(1);
}

const { renderThemeDocument, defaultThemeCss } = await import('./theme-document.mjs');
const palettes = await Promise.all(thePresets.map(async one => ({
  file: one.file, values: JSON.parse(await readFile(join(ROOT, 'presets', one.file), 'utf8')),
})));
const defaultPalette = palettes.find(one => one.file === 'default.json');
if (!defaultPalette) throw new Error('Missing default.json palette');
const orderedPalettes = [defaultPalette, ...palettes.filter(one => one !== defaultPalette)];
const sheet = await readFile(join(OUT, 'style.css'), 'utf8');
await writeFile(join(OUT, 'style.css'), sheet + defaultThemeCss(defaultPalette.values), 'utf8');
const html = await readFile(join(CLIENT, 'index.html'), 'utf8');
await writeFile(join(OUT, 'index.html'), renderThemeDocument(html, orderedPalettes), 'utf8');

// Offline reference page; Markdown source remains the single content authority.
const { renderPluginSpec } = await import('./render-plugin-spec.mjs');
const guide = await readFile(join(ROOT, 'docs', guideFile), 'utf8');
await writeFile(join(OUT, 'infinity-plugin-spec.html'), renderPluginSpec(guide), 'utf8');


await copyFile(join(ROOT, 'scripts', 'voice', 'asr-self-check.wav'), join(OUT, 'asr-self-check.wav'));
const names = (await readdir(OUT)).sort();
const sizes = [];
for (const name of names) sizes.push(`${name} ${(await stat(join(OUT, name))).size}B`);
let backup;
if (FINAL === join(ROOT, 'dist', 'web')) {
  const { publishGeneration } = await import('./publish-web-generation.mjs');
  await publishGeneration(FINAL, OUT);
} else {
  backup = await publishOutput(ROOT, requested, OUT);
}
console.log(`那一页在 ${FINAL}`);
if (backup) console.log(`旧产物备份：${backup}`);
console.log(`产物：${sizes.join(' ／ ')}`);

}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
