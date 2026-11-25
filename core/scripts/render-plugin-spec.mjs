import {createHash} from 'node:crypto';
import {defaultThemeCss} from './theme-document.mjs';
import {readFileSync} from 'node:fs';
const sharedScrollbars = readFileSync(new URL('../src/web/client/core-scrollbars.css', import.meta.url), 'utf8');
const palette = JSON.parse(readFileSync(new URL('../presets/default.json', import.meta.url), 'utf8'));
/** Deliberately small Markdown subset: escaped text, headings, lists and fenced code. */
export function renderPluginSpec(source, themeScript = '') {
  const script = themeScript.replace(/<\/script/gi, '<\\/script');
  const digest = createHash('sha256').update(script).digest('base64');
  const esc = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  // Render only explicit inline syntax; all text and attributes remain escaped.
  const inline = value => {
    const tokens = /`([^`]+)`|\[([^\]]+)\]\(([^\s)]+)\)|\*\*([^*]+)\*\*/g;
    let result = ''; let offset = 0;
    for (const match of value.matchAll(tokens)) {
      result += esc(value.slice(offset, match.index));
      if (match[1] !== undefined) result += `<code>${esc(match[1])}</code>`;
      else if (match[2] !== undefined) {
        let allowed = /^#[A-Za-z0-9_-]+$/.test(match[3]);
        try { allowed ||= ['https:', 'http:'].includes(new URL(match[3]).protocol); } catch {}
        result += allowed ? `<a href="${esc(match[3])}" rel="noreferrer">${esc(match[2])}</a>` : esc(match[2]);
      } else result += `<strong>${esc(match[4])}</strong>`;
      offset = match.index + match[0].length;
    }
    return result + esc(value.slice(offset));
  };
  const lines = source.replace(/\r/g, '').split('\n');
  const title = lines.find(line => line.startsWith('# '))?.slice(2) || 'Infinity 开发规范';
  const version = title.match(/v[\d.]+/)?.[0] || '';
  const toc = []; const body = [];
  let paragraph = []; let code = null; let language = ''; let list = '';
  let headingNumber = 0;
  const flush = () => {
    if (paragraph.length) body.push(`<p>${inline(paragraph.join(' '))}</p>`);
    paragraph = [];
    if (list) { body.push(`</${list}>`); list = ''; }
  };
  const cells = line => line.trim().replace(/^\||\|$/g, '').split('|').map(v => v.trim());
  const divider = line => line?.includes('|') && cells(line).every(v => /^:?-{3,}:?$/.test(v));
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line.startsWith('```')) {
      if (code !== null) {
        body.push(`<div class="code"><div class="code-label">${esc(language || 'TEXT')}</div>`
          + `<pre><code>${esc(code.join('\n'))}</code></pre></div>`);
        code = null;
      } else { flush(); code = []; language = line.slice(3).trim(); }
      continue;
    }
    if (code !== null) { code.push(line); continue; }
    if (line.startsWith('# ')) continue;
    if (line.trim().startsWith('|') && divider(lines[index + 1])) {
      flush(); const header = cells(line); const rows = []; index += 1;
      while (index + 1 < lines.length && lines[index + 1].trim().startsWith('|')) {
        rows.push(cells(lines[++index]));
      }
      body.push('<div class="table-wrap"><table><thead><tr>'
        + header.map(v => `<th scope="col">${inline(v)}</th>`).join('') + '</tr></thead><tbody>'
        + rows.map(row => '<tr>' + header.map((_, i) => `<td>${inline(row[i] || '')}</td>`).join('') + '</tr>').join('')
        + '</tbody></table></div>');
      continue;
    }
    const heading = /^(#{2,6}) (.*)/.exec(line);
    if (heading) {
      flush(); const id = `section-${++headingNumber}`;
      if (heading[1].length === 2) {
        const title = heading[2];
        const prefix = title.match(/^\d+\.\s*/)?.[0] || '';
        const short = /^<!-- toc: (.+?) -->$/.exec(lines[index + 1] || '');
        if (short) index++;
        const chars = Array.from(short?.[1] || title.slice(prefix.length));
        const label = prefix + (chars.length > 18 ? chars.slice(0, 17).join('') + '…' : chars.join(''));
        toc.push({ id, title, label });
      }
      body.push(`<h${heading[1].length} id="${id}">${inline(heading[2])}</h${heading[1].length}>`);
    } else if (/^(?:- |\d+[.)] )/.test(line)) {
      if (paragraph.length) { body.push(`<p>${inline(paragraph.join(' '))}</p>`); paragraph = []; }
      const kind = /^\d/.test(line) ? 'ol' : 'ul';
      if (list && list !== kind) { body.push(`</${list}>`); list = ''; }
      if (!list) { body.push(`<${kind}>`); list = kind; }
      body.push(`<li>${inline(line.replace(/^(?:- |\d+[.)] )/, ''))}</li>`);
    } else if (!line.trim()) flush();
    else paragraph.push(line);
  }
  flush();
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-${digest}'; connect-src 'self'">
<meta name="theme-color" content="${palette['--color-canvas']}">
<title>${esc(title)}</title><script>${script}</script><style>
${defaultThemeCss(palette)}
${sharedScrollbars}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:32px}
body{margin:0;background:var(--color-canvas);color:var(--color-ink);font:15px/1.95 var(--font-sans)}
a{color:var(--color-mint-text);text-decoration:none}a:hover{text-decoration:underline}
a:focus-visible{outline:2px solid var(--color-mint);outline-offset:4px;border-radius:3px}
.shell{max-width:1300px;margin:auto;padding:36px 40px 72px}
.top{display:flex;justify-content:space-between;gap:16px;align-items:center;
font-size:12px;letter-spacing:.04em;color:var(--color-dim);padding-bottom:30px}
.brand{font-weight:750;color:var(--color-ink);font-size:15px;letter-spacing:.02em}
.layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:48px}
aside{position:sticky;top:30px;align-self:start;max-height:calc(100vh - 60px);overflow:auto}
.eyebrow{font-size:11px;font-weight:650;letter-spacing:.16em;color:var(--color-dim)}
nav{display:grid;gap:5px;margin-top:18px}nav a{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--color-dim);font-size:12px;line-height:1.7;
padding:7px 10px;border-radius:var(--radius-chip);border-left:2px solid transparent}
nav a:hover{background:var(--color-mint-wash);color:var(--color-mint-text);text-decoration:none;border-color:var(--color-mint)}
main{min-width:0;background:var(--color-white);border:1px solid var(--color-line);border-radius:var(--radius-card);
padding:48px 54px;box-shadow:0 8px 28px var(--color-wash)}
.hero{padding-bottom:28px;border-bottom:1px solid var(--color-line);margin-bottom:26px}
.badge{display:inline-block;background:var(--color-card);border:1px solid var(--color-line);color:var(--color-mint-text);
border-radius:var(--radius-pill);padding:2px 10px;font-size:11px;margin-left:10px;letter-spacing:0}
h1{font-size:32px;line-height:1.4;letter-spacing:-.025em;margin:16px 0 12px;font-weight:730}
.subtitle{font-size:13px;color:var(--color-dim);margin:0}
h2{font-size:20px;line-height:1.55;margin:42px 0 18px;font-weight:700;
padding-top:24px;border-top:1px solid var(--color-line);letter-spacing:-.015em}
h3{font-size:17px;margin:28px 0 14px}p{margin:14px 0;color:var(--color-ink)}
ul,ol{padding-left:22px;color:var(--color-ink)}li{padding-left:4px;margin:8px 0}
.table-wrap{overflow-x:auto;margin:22px 0;border:1px solid var(--color-line);border-radius:var(--radius-row)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;padding:12px 16px;border-bottom:1px solid var(--color-line)}
th{background:var(--color-card);font-weight:650}tr:last-child td{border-bottom:0}
code{font:12px/1.7 var(--font-mono);background:var(--color-bubble);color:var(--color-mint-text);
border-radius:4px;padding:2px 5px;overflow-wrap:anywhere}
.code{margin:22px 0;border:1px solid var(--color-line);border-radius:var(--radius-row);overflow:hidden;background:var(--color-focus)}
.code-label{font:10px/1.4 var(--font-sans);letter-spacing:.13em;color:var(--color-dim);
padding:10px 18px;border-bottom:1px solid var(--color-line);text-transform:uppercase}
pre{margin:0;padding:20px;overflow:auto;tab-size:4}pre code{padding:0;background:none;
color:var(--color-ink);white-space:pre;overflow-wrap:normal;font-size:12px}
footer{margin-top:40px;border-top:1px solid var(--color-line);padding-top:20px;
font-size:12px;color:var(--color-dim);display:flex;justify-content:space-between;gap:16px}
@media(max-width:900px){.shell{padding:22px 20px}.layout{grid-template-columns:1fr;gap:20px}
aside{position:static;max-height:none}nav{grid-template-columns:repeat(2,minmax(0,1fr));gap:2px}
main{padding:30px 24px}h1{font-size:27px}.top{padding-bottom:22px}}
@media(max-width:480px){nav{grid-template-columns:1fr}main{padding:24px 18px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{:root{color-scheme:light!important;--color-ink:#000!important;--color-dim:#333!important;--color-white:#fff!important;--color-line:#bbb!important;--color-card:#eee!important;--color-focus:#fff!important;--color-bubble:#eee!important;--color-mint-text:#222!important}body{background:white}.shell{padding:0}.top,aside{display:none}
.layout{display:block}main{border:0;box-shadow:none;padding:0}pre{white-space:pre-wrap}
h2,h3{break-after:avoid}.code{break-inside:avoid}a{color:inherit}}
</style></head><body><div class="shell" id="top">
<header class="top"><span class="brand">TelyinCore <span class="badge">INFINITY</span></span>
<a href="/#/plugin">← 返回插件市场</a></header><div class="layout">
<aside><div class="eyebrow">DEVELOPER REFERENCE · ${esc(version)}</div><nav aria-label="章节目录">
${toc.map(item => `<a href="#${item.id}" title="${esc(item.title)}" aria-label="${esc(item.title)}">${esc(item.label)}</a>`).join('\n')}
</nav></aside><main><header class="hero"><div class="eyebrow">插件开发参考</div>
<h1>Infinity 插件开发规范</h1><p class="subtitle">${esc(version)} · Python Infinity API 1
 · 本地与 GitHub 统一接入</p></header>${body.join('\n')}
<footer><span>TelyinCore · 以实际实现为准</span><a href="#top">返回顶部 ↑</a></footer>
</main></div></div></body></html>`;
}
