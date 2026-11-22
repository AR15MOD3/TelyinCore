import { corePhrase, coreSetUIAttribute, coreSetUIText, coreUITextNode } from './ui-language';
/** A local operator review surface, not an Agent tool or QQ chat command. */
export function voiceCorpusPanel(): HTMLElement & { start(): void; stop(): void } {
  const root = document.createElement('details');
  root.className = 'voice-corpus';
  const title = document.createElement('summary');
  coreSetUIText(title, 'textContent', corePhrase("认可素材库 · 当前 Bot"));
  const hint = document.createElement('p');
  hint.className = 'infinity-hint';
  coreSetUIText(hint, 'textContent', corePhrase("开启后，收藏当前 Bot 经 QQ 确认发送的合成语音，供你试听和评审。试听样本和收到的录音不会入库。"));
  const enableLabel = document.createElement('label');
  const enabled = document.createElement('input');
  enabled.type = 'checkbox'; enabled.disabled = true;
  enableLabel.append(enabled, coreUITextNode(corePhrase(" 收集合成语音")));
  const toolbar = document.createElement('div');
  toolbar.className = 'voice-corpus-toolbar';
  const filter = document.createElement('select');
  coreSetUIAttribute(filter, 'aria-label', corePhrase("素材评审状态"));
  for (const [value, label] of [['pending', '待评审'], ['approved', '已认可'], ['rejected', '已排除']]) {
    filter.append(new Option(label, value));
  }
  const button = (text: string): HTMLButtonElement => {
    const b = document.createElement('button'); b.type = 'button';
    b.className = 'infinity-action'; b.textContent = text; return b;
  };
  const refresh = button('刷新');
  const previous = button('上一页'); const next = button('下一页');
  const notice = document.createElement('p');
  notice.className = 'infinity-hint'; notice.setAttribute('role', 'status');
  const counts = document.createElement('p');
  counts.className = 'infinity-hint';
  const list = document.createElement('div'); list.className = 'voice-corpus-list';
  toolbar.append(filter, refresh, previous, next);
  root.append(title, hint, enableLabel, toolbar, counts, notice, list);
  let busy = false, offset = 0, knownEnabled = false, ready = false;
  let active = true, epoch = 0;
  const requests = new Set<AbortController>();
  const api = async (body: object): Promise<any> => {
    if (!active) throw new Error('素材页已离开');
    const generation = epoch;
    const controller = new AbortController(); requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
    const response = await fetch('/infinity/qq/voice-corpus', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    });
    const result = await response.json();
    if (!active || generation !== epoch) throw new Error('素材页响应已过期');
    if (!response.ok || result.ok !== true) throw new Error(result.said || result.error ||
      '素材库不可用；更新后需重启 Core。');
    return result;
    } finally { clearTimeout(timeout); requests.delete(controller); }
  };
  const lock = (on: boolean): void => {
    busy = on;
    for (const item of root.querySelectorAll('button,select,input')) {
      (item as HTMLButtonElement).disabled = on;
    }
  };
  let hasNext = false;
  const show = async (): Promise<void> => {
    const state = filter.value;
    let data = await api({ action: 'list', state, offset });
    if (!data.samples.length && offset > 0) {
      offset = Math.max(0, Math.floor((data.counts[state] - 1) / 20) * 20);
      data = await api({ action: 'list', state, offset });
    }
    ready = true; knownEnabled = data.enabled; enabled.checked = knownEnabled;
    coreSetUIText(counts, 'textContent', [
      corePhrase("待评审 "), data.counts.pending, corePhrase(" · 已认可 "), data.counts.approved,
      corePhrase(" · 已排除 "), data.counts.rejected, " · ",
      (data.bytes / 1048576).toFixed(1), " / ", (data.maxBytes / 1048576).toFixed(0), " MiB",
      data.full ? corePhrase(" · 库已满，暂停收集") : '',
    ]);
    for (const audio of list.querySelectorAll('audio')) { audio.pause();
      audio.removeAttribute('src'); }
    list.replaceChildren(); hasNext = offset + data.samples.length < data.counts[state];
    if (!data.samples.length) {
      const empty = document.createElement('p'); empty.className = 'infinity-hint';
      coreSetUIText(empty, 'textContent', corePhrase("当前筛选下没有素材。")); list.append(empty);
    }
    for (const sample of data.samples) {
      const card = document.createElement('article'); card.className = 'voice-corpus-item';
      const text = document.createElement('p'); text.textContent = sample.text;
      const meta = document.createElement('small');
      meta.textContent = `${sample.voice} · ${new Date(sample.created * 1000).toLocaleString()}`;
      const audio = document.createElement('audio'); audio.controls = true;
        audio.preload = 'none'; audio.hidden = true;
      const actions = document.createElement('div'); actions.className = 'voice-corpus-toolbar';
      const play = button('加载试听');
      play.addEventListener('click', () => { void run(async () => {
        const data = await api({ action: 'audio', id: sample.id });
        audio.src = `data:audio/wav;base64,${data.audio}`; audio.hidden = false;
        coreSetUIText(notice, 'textContent', corePhrase("音频已加载，请点击播放。"));
      }); });
      actions.append(play);
      for (const [state, label] of [['approved', '认可'], ['rejected', '排除'], ['pending', '重新评审']]) {
        if (state === sample.state) continue;
        const review = button(label);
        review.addEventListener('click', () => { void run(async () => {
          await api({ action: 'review', id: sample.id, state }); await show();
        }); });
        actions.append(review);
      }
      card.append(text, meta, audio, actions); list.append(card);
    }
  };
  const run = async (work: () => Promise<void>): Promise<void> => {
    if (busy || !active) return;
    const generation = epoch;
    lock(true);
    notice.textContent = '';
    try { await work(); }
    catch (error) {
      if (!active || generation !== epoch) return;
      enabled.checked = knownEnabled;
      coreSetUIText(notice, 'textContent', (error instanceof Error ? error.message : corePhrase("素材库操作失败")));
    } finally { if (active && generation === epoch) { lock(false); enabled.disabled = !ready;
      previous.disabled = offset === 0; next.disabled = !hasNext; } }
  };
  enabled.addEventListener('change', () => {
    const desired = enabled.checked;
    void run(async () => { const result = await api({ action: 'configure', enabled: desired });
      knownEnabled = result.enabled;
      await show(); });
  });
  filter.addEventListener('change', () => { offset = 0; void run(show); });
  refresh.addEventListener('click', () => { void run(show); });
  previous.addEventListener('click', () => { offset = Math.max(0, offset - 20); void run(show); });
  next.addEventListener('click', () => { offset += 20; void run(show); });
  root.addEventListener('toggle', () => {
    if (root.open) void run(show);
    else for (const audio of list.querySelectorAll('audio')) audio.pause();
  });
  return Object.assign(root, {
    start(): void { active = true; if (root.open) void run(show); },
    stop(): void {
      active = false; epoch += 1; busy = false;
      for (const controller of requests) controller.abort();
      for (const audio of list.querySelectorAll('audio')) { audio.pause();
        audio.removeAttribute('src'); }
    },
  });
}
