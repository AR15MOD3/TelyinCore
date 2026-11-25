import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
/** Read-only diagnostic views. Text stays text, never terminal escape codes or HTML. */
export interface QQEnvironmentDeclaration {
  readonly status: string; readonly text: string; readonly source: string;
  readonly message: string; readonly readOnly: boolean;
}
type Stream = 'stdout' | 'stderr' | 'all';
export interface SnowLogLine { readonly text: string; readonly stream: 'stdout' | 'stderr'; }
function element<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string,
  text?: string): HTMLElementTagNameMap[K] {
  const item = document.createElement(tag); item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
}
export function splitSnowLog(content: string): SnowLogLine[] {
  // Legacy API combines stdout and stderr, which are independent historical tails.
  // Do not sort time-only stamps or imply that the appended stderr is newer.
  const clean = content.slice(-131072)
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\r\n?/g, '\n').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '');
  let stream: 'stdout' | 'stderr' = 'stdout';
  const lines: SnowLogLine[] = [];
  for (const text of clean.split('\n')) {
    if (text.trim() === '── stderr · 错误输出 ──') { stream = 'stderr'; continue; }
    lines.push({ text, stream });
  }
  // Keep stack indentation and internal blank lines, but not separator padding.
  return (['stdout', 'stderr'] as const).flatMap(kind => {
    const rows = lines.filter(row => row.stream === kind);
    while (rows.length && !rows[0].text.trim()) rows.shift();
    while (rows.length && !rows[rows.length - 1].text.trim()) rows.pop();
    return rows.slice(-160);
  });
}
export function snowLogLevel(line: string): string {
  if (/\b(error|fatal|failed|unauthorized)\b/i.test(line) || /异常|失败|错误/.test(line)) return 'error';
  if (/\b(warn|warning|retry)\b/i.test(line) || /重试|警告/.test(line)) return 'warn';
  if (/\b(ready|connected|online|listening)\b/i.test(line) || /成功|在线/.test(line)) return 'ok';
  return 'normal';
}
function heading(title: string, badge: HTMLElement): HTMLElement {
  const head = element('div', 'qq-diagnostic-heading');
  head.append(element('h3', '', title), badge); return head;
}
export function createQQTerminal(refresh: () => void) {
  const root = element('section', 'infinity-voice-card qq-diagnostic-card qq-terminal-card');
  const badge = element('span', 'qq-diagnostic-badge', '只读日志');
  root.append(heading('SnowLuma · 运行日志', badge),
    element('p', 'qq-diagnostic-description', '查看 SnowLuma 最近的运行日志。'));
  const toolbar = element('div', 'qq-diagnostic-toolbar');
  const sourceLabel = element('label', 'qq-log-source');
  const source = element('select', '');
  coreSetUIAttribute(source, 'aria-label', corePhrase("日志来源"));
  source.append(new Option('标准输出', 'stdout'), new Option('错误输出', 'stderr'),
    new Option('全部输出 · 分流显示', 'all'));
  source.value = 'stdout'; sourceLabel.append(element('span', '', '日志来源'), source);
  const controls = element('div', 'qq-diagnostic-actions');
  const liveLabel = element('label', 'infinity-log-follow');
  const live = element('input', ''); live.type = 'checkbox'; live.checked = true;
  liveLabel.append(live, element('span', '', '实时更新'));
  const latestButton = element('button', 'infinity-action', '回到最新'); latestButton.type = 'button';
  const refreshButton = element('button', 'infinity-action', '刷新日志'); refreshButton.type = 'button';
  controls.append(liveLabel, latestButton, refreshButton); toolbar.append(sourceLabel, controls);
  const log = element('div', 'infinity-log qq-log-viewport');
  log.setAttribute('role', 'log'); coreSetUIAttribute(log, 'aria-label', corePhrase("SnowLuma 运行日志"));
  log.setAttribute('aria-live', 'off'); log.setAttribute('tabindex', '0');
  const footer = element('div', 'qq-diagnostic-footer');
  const status = element('span', 'qq-log-status', '等待读取日志');
  status.setAttribute('role', 'status');
  const count = element('span', 'qq-log-count'); footer.append(status, count);
  root.append(toolbar, log, footer,
    element('p', 'qq-diagnostic-note', '向上滚动可暂停跟随。日志可能包含上一次运行的记录。'));
  let snapshot: string | undefined;
  let shown = '';
  let renderedKey: string | undefined;
  let visible = false;
  let following = true;
  let failed = false;
  let busy = false;
  const sync = (): void => {
    coreSetUIText(status, 'textContent', (busy ? corePhrase("正在读取日志…") : (failed ? corePhrase("读取失败 · 保留上次内容") : (!live.checked ? corePhrase("已暂停显示 · 可手动刷新") : (!following ? ((snapshot !== shown ? corePhrase("阅读历史 · 有新日志") : corePhrase("阅读历史 · 已暂停跟随"))) : (snapshot === undefined ? corePhrase("等待读取日志") : corePhrase("跟随最新输出")))))));
    coreSetUIText(badge, 'textContent', (failed ? corePhrase("读取暂不可用") : (!live.checked || !following ? corePhrase("已暂停跟随") : corePhrase("只读日志"))));
    badge.dataset.state = failed ? 'warn' : 'neutral';
    latestButton.disabled = snapshot === undefined || (following && live.checked &&
      snapshot === shown);
    refreshButton.disabled = busy;
  };
  const render = (scrollToLatest = false): void => {
    if (snapshot === undefined) { sync(); return; }
    const key = source.value + '\n' + snapshot;
    const top = log.scrollTop;
    if (key !== renderedKey) {
      renderedKey = key; shown = snapshot;
      const lines = splitSnowLog(snapshot).filter(row => source.value === 'all' ||
        row.stream === source.value as Stream);
      log.replaceChildren();
      if (!lines.length) {
        log.append(element('p', 'qq-log-empty', source.value === 'stderr'
          ? '最近片段中没有错误流输出。' : '暂无日志；服务启动并产生输出后会显示在这里。'));
      }
      lines.forEach((line, index) => {
        const row = element('div', 'qq-log-row'); row.dataset.level = snowLogLevel(line.text);
        row.append(element('span', 'qq-log-number', String(index + 1)),
          element('span', 'qq-log-stream', line.stream === 'stderr' ? 'ERR' : 'OUT'),
          element('span', 'qq-log-text', line.text || ' '));
        log.append(row);
      });
      coreSetUIText(count, 'textContent', [corePhrase(""), lines.length, corePhrase(" 行 · "), source.value === 'all' ? '标准流 → 错误流，非时间排序'
          : source.value === 'stderr' ? 'stderr' : 'stdout', corePhrase("")]);
    }
    if (scrollToLatest || following) log.scrollTop = log.scrollHeight;
    else log.scrollTop = top;
    sync();
  };
  log.addEventListener('scroll', () => {
    if (!visible || log.clientHeight <= 0) return;
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 32;
    if (nearBottom === following) return;
    following = nearBottom;
    if (following && live.checked) render(true); else sync();
  });
  live.addEventListener('change', () => {
    if (live.checked) { following = true; render(true); } else sync();
  });
  latestButton.addEventListener('click', () => {
    following = true; live.checked = true; render(true);
  });
  source.addEventListener('change', () => { following = true; render(true); });
  refreshButton.addEventListener('click', () => {
    if (busy || refreshButton.disabled) return;
    busy = true; sync(); refresh();
  });
  return {
    root,
    activate(): void { visible = true; if (live.checked && following) render(true); else sync(); },
    deactivate(): void { visible = false; },
    update(content: string, manual = false): void {
      snapshot = content; failed = false; busy = false;
      if (visible && (manual || (live.checked && following))) render(); else sync();
    },
    unavailable(): void { failed = true; busy = false; sync(); },
  };
}
export function createQQEnvironment() {
  const root = element('section', 'infinity-voice-card qq-diagnostic-card qq-environment-card');
  const badge = element('span', 'qq-diagnostic-badge', '只读声明');
  const source = element('span', 'qq-environment-source');
  const sourceRow = element('div', 'qq-environment-meta');
  sourceRow.append(element('span', 'qq-diagnostic-caption', '声明来源'), source);
  const text = element('div', 'qq-environment-document');
  coreSetUIAttribute(text, 'aria-label', corePhrase("QQ 插件环境声明文本"));
  const hint = element('p', 'qq-diagnostic-note', '正在读取环境声明…');
  const description = element('p', 'qq-diagnostic-description', '插件声明 QQ 消息的来源和范围。工具能力随启用状态提供，运行状态和工具结果按轮追加。');
  const header = heading('QQ 通道 · 插件声明', badge);
  let previousText: string | undefined;
  let editing = false;
  let revision = '';
  let busy = false;
  const editor = element('textarea', 'qq-environment-document');
  coreSetUIAttribute(editor, 'aria-label', corePhrase("当前 Bot 的 QQ 环境提示词"));
  editor.rows = 8;
  const save = element('button', 'btn', '保存');
  const reload = element('button', 'btn ghost', '重新读取');
  save.type = reload.type = 'button';
  const status = element('p', 'qq-diagnostic-note');
  const buttons = element('div', 'qq-diagnostic-actions');
  buttons.append(reload, save);
  buttons.hidden = editor.hidden = true;
  const sceneHeader = heading('当前 Bot · QQ 场景提示词', element('span', 'qq-diagnostic-badge', '可编辑'));
  sceneHeader.append(buttons);
  sceneHeader.hidden = true;
  status.setAttribute('role', 'status');
  root.append(header, description, sourceRow, text, hint, sceneHeader, editor, status);
  let lastSaved = '';

  async function loadEditor(): Promise<void> {
    if (busy) return;
    busy = save.disabled = reload.disabled = editor.disabled = true;
    try {
      const response = await fetch('/persona/qq-environment');
      if (response.status === 404 && !editing) return;
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const info = await response.json() as { text: string; revision: string };
      editor.value = lastSaved = info.text;
      revision = info.revision;
      editing = true;
      sceneHeader.hidden = buttons.hidden = editor.hidden = false;
      status.textContent = '保存到当前 Bot 的 环境/QQ.md，下一轮匹配的 QQ 会话读取。';
    } catch (error) {
      coreSetUIText(status, 'textContent', [corePhrase("读取失败："), (error as Error).message]);
    } finally {
      busy = save.disabled = reload.disabled = editor.disabled = false;
    }
  }

  async function saveEditor(): Promise<void> {
    if (busy || !revision) return;
    busy = save.disabled = reload.disabled = editor.disabled = true;
    const submitted = editor.value;
    try {
      const response = await fetch('/persona', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: '环境/QQ.md', text: submitted, expect: revision,
          stamp: new Date().toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-') }),
      });
      if (response.status === 409) {
        coreSetUIText(status, 'textContent', corePhrase("文件版本冲突或格式校验失败，草稿已保留。"));
        return;
      }
      if (!response.ok) throw new Error((await response.text()).trim() || 'HTTP ' + response.status);
      revision = (await response.json() as { revision: string }).revision;
      lastSaved = submitted;
      coreSetUIText(status, 'textContent', corePhrase("已保存，下一轮匹配的 QQ 场景会读取新版。"));
    } catch (error) {
      coreSetUIText(status, 'textContent', [corePhrase("保存失败："), (error as Error).message]);
    } finally {
      busy = save.disabled = reload.disabled = editor.disabled = false;
    }
  }
  save.addEventListener('click', () => { void saveEditor(); });
  reload.addEventListener('click', () => {
    if (editor.value === lastSaved || window.confirm('重新读取会丢弃未保存的修改，继续吗？')) {
      void loadEditor();
    }
  });
  editor.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      void saveEditor();
    }
  });
  return {
    root,
    async open() { if (!editing) await loadEditor(); },
    update(value?: QQEnvironmentDeclaration): void {
      source.textContent = value?.source || 'QQ 插件 · channel/qq.md';
      const available = value?.status === 'available';
      coreSetUIText(badge, 'textContent', (available ? corePhrase("只读声明") : (value?.status === 'unconfigured' ? corePhrase("尚未声明") : (value ? corePhrase("不可读取") : corePhrase("接口未提供")))));
      badge.dataset.state = available ? 'neutral' : 'warn';
      const content = available ? value.text
        : value ? '当前没有可显示的 QQ 通道环境声明。' : '当前 Core 未提供环境声明读取接口。';
      if (content !== previousText) {
        text.replaceChildren();
        for (const paragraph of content.split(/\n\s*\n/)) text.append(element('p', '', paragraph));
        previousText = content;
      }
      hint.textContent = value?.message || '仅显示已安装 QQ 插件的声明；不会读取其他插件或终端环境作为替代。';
    },
    unavailable(): void {
      coreSetUIText(badge, 'textContent', corePhrase("读取暂不可用")); badge.dataset.state = 'warn';
      coreSetUIText(hint, 'textContent', corePhrase("状态更新失败；保留上次读取内容，不代表当前进程已经加载此声明。"));
    },
  };
}
