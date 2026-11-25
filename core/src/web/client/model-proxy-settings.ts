import { corePhrase, coreSetUIAttribute, coreSetUIText, coreUITextNode } from './ui-language';
import { pageFetch } from './status-json';
import { configPresentation } from './config-presentation';

interface ProxySettings {
  enabled: boolean;
  market_enabled?: boolean;
  downloads_enabled?: boolean;
  downloads_source?: 'mirror' | 'official';
  market_source?: 'mirror' | 'official';
  downloads_fallback?: boolean;
  market_fallback?: boolean;
  proxy_url: string;
  providers: string[];
  revision: string;
  system_proxy: string;
}
const PROVIDERS = [
  ['openai', 'OpenAI / ChatGPT'],
  ['google', 'Google Gemini'],
  ['anthropic', 'Anthropic Claude'],
  ['xai', 'xAI Grok'],
] as const;

/** One backend setting, not browser localStorage and not a machine-wide proxy. */
export function modelProxySettings(): HTMLElement {
  const root = document.createElement('section');
  root.className = 'settings-card model-proxy-settings';
  configPresentation(root, 'loading');
  const h = document.createElement('h3');
  coreSetUIText(h, 'textContent', corePhrase('网络与下载'));
  const note = document.createElement('p');
  note.className = 'set-note';
  coreSetUIText(note, 'textContent',
    corePhrase('先选择下载源，再决定官方连接方式。镜像始终直连，模型 API 单独控制。'));
  const enabledLabel = document.createElement('label');
  enabledLabel.className = 'model-proxy-toggle';
  const enabled = document.createElement('input');
  enabled.type = 'checkbox';
  coreSetUIAttribute(enabled, 'aria-label', corePhrase('启用模型代理'));
  enabledLabel.append(enabled, coreUITextNode(corePhrase('模型 API')));
  const label = document.createElement('label');
  label.className = 'model-proxy-address';
  coreSetUIText(label, 'textContent', corePhrase('代理地址'));
  const address = document.createElement('input');
  address.type = 'url';
  address.className = 'engine-input engine-input-wide';
  coreSetUIAttribute(address, 'aria-label', corePhrase('共用代理地址'));
  address.placeholder = 'http://127.0.0.1:7891';
  address.autocomplete = 'off';
  address.spellcheck = false;
  label.append(address);
  const help = document.createElement('p');
  help.className = 'set-note';
  coreSetUIText(help, 'textContent', corePhrase('支持 HTTP(S) 代理，不支持带账号密码的地址。'));
  const targets = document.createElement('fieldset');
  targets.className = 'model-proxy-targets';
  const legend = document.createElement('legend');
  coreSetUIText(legend, 'textContent', corePhrase('官方模型接口'));
  targets.append(legend);
  const boxes = PROVIDERS.map(([id, name]) => {
    const row = document.createElement('label');
    row.className = 'model-proxy-provider';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('aria-label', name);
    input.checked = true;
    row.append(input, name);
    targets.append(row);
    return { id, input };
  });
  const uses = document.createElement('div');
  uses.className = 'public-network-policies';
  const select = (label: string, entries: [string, string][]) => {
    const box = document.createElement('label');
    box.className = 'public-network-field';
    box.append(label);
    const input = document.createElement('select');
    input.setAttribute('aria-label', label);
    for (const [value, text] of entries) input.add(new Option(text, value));
    box.append(input);
    return {box, input};
  };
  const policies = (['downloads', 'market'] as const).map(id => {
    const row = document.createElement('section');
    row.className = 'public-network-policy';
    const title = document.createElement('h4');
    title.textContent = id === 'downloads' ? '公共下载 · Core 更新、模型与运行环境' : '插件市场 · 搜索与安装';
    const source = select(title.textContent + '：来源顺序', [['mirror', '镜像优先'], ['official', '官方优先']]);
    const transport = select(title.textContent + '：官方连接', [
      ['direct', '直接连接'], ['fallback', '代理优先，失败后直连'], ['required', '仅使用代理'],
    ]);
    // Visual field labels stay short; accessible names retain the purpose.
    source.box.firstChild!.textContent = '来源顺序';
    transport.box.firstChild!.textContent = '官方连接';
    const flow = document.createElement('p');
    flow.className = 'public-network-flow';
    const paintFlow = () => {
      const official = transport.input.value === 'direct' ? '官方直连'
        : transport.input.value === 'fallback' ? '官方代理 → 官方直连' : '官方代理';
      flow.textContent = source.input.value === 'mirror' ? '镜像直连 → ' + official : official + ' → 镜像直连';
    };
    source.input.addEventListener('change', paintFlow);
    transport.input.addEventListener('change', paintFlow);
    row.append(title, source.box, transport.box, flow);
    uses.append(row);
    return {id, source: source.input, transport: transport.input, paintFlow};
  });
  const policyNote = document.createElement('p');
  policyNote.className = 'set-note';
  policyNote.textContent = '箭头表示失败后的下一条路径。更新摘要与插件文件清单只向官方核验，遵守上面的官方连接设置。下载页明确指定来源时，以该次选择为准。';
  uses.append(policyNote);
  const scopeInputs = boxes.map(box => box.input);
  const policyInputs = policies.flatMap(p => [p.source, p.transport]);
  const hint = document.createElement('p');
  hint.className = 'set-note';
  coreSetUIText(
    hint,
    'textContent',
    corePhrase('OpenAI 包含 ChatGPT 登录和模型调用。代理失败时会报错，不会自动改走直连。'),
  );
  const status = document.createElement('p');
  status.className = 'model-proxy-status';
  status.setAttribute('role', 'status');
  const actions = document.createElement('div');
  actions.className = 'model-proxy-actions';
  const save = document.createElement('button');
  save.className = 'btn';
  coreSetUIText(save, 'textContent', corePhrase('保存'));
  coreSetUIAttribute(save, 'aria-label', corePhrase('保存代理设置'));
  const system = document.createElement('button');
  system.className = 'btn ghost';
  coreSetUIText(system, 'textContent', corePhrase('填入系统代理'));
  const reload = document.createElement('button');
  reload.className = 'btn ghost';
  coreSetUIText(reload, 'textContent', corePhrase('重新读取'));
  const header = document.createElement('div');
  header.className = 'model-proxy-header';
  header.append(h);
  targets.insertBefore(enabledLabel, legend.nextSibling);
  const details = document.createElement('details');
  details.className = 'model-proxy-details';
  const summary = document.createElement('summary');
  coreSetUIText(summary, 'textContent', corePhrase('模型 API 代理'));
  details.append(summary, targets, help, hint, reload);
  actions.append(save, system);
  root.append(header, note, label, uses, details, actions, status);
  let revision: string | null = null,
    systemProxy = '',
    busy = false;
  let baseline: string | null = null,
    saving = false,
    guarding = false;
  const draft = (): string =>
    JSON.stringify([enabled.checked, address.value, scopeInputs.map(input => input.checked), policyInputs.map(input => input.value)]);
  const dirty = (): boolean => baseline !== null && draft() !== baseline;
  const beforeUnload = (event: BeforeUnloadEvent): void => {
    if (!saving && !dirty()) return;
    event.preventDefault();
    event.returnValue = '';
  };
  const guardDraft = (): void => {
    const needed = saving || dirty();
    if (needed === guarding) return;
    guarding = needed;
    if (needed) window.addEventListener('beforeunload', beforeUnload);
    else window.removeEventListener('beforeunload', beforeUnload);
  };
  for (const input of [enabled, address, ...scopeInputs, ...policyInputs]) {
    input.addEventListener('input', guardDraft);
    input.addEventListener('change', guardDraft);
  }
  const lock = (pending: boolean): void => {
    busy = pending;
    for (const control of [enabled, address, ...scopeInputs, ...policyInputs, save]) {
      control.disabled = pending || revision === null;
    }
    reload.disabled = pending;
    system.disabled = pending || revision === null || !systemProxy;
  };
  const apply = (s: ProxySettings): void => {
    revision = s.revision;
    systemProxy = s.system_proxy ?? '';
    enabled.checked = s.enabled;
    address.value = s.proxy_url;
    for (const box of boxes) box.input.checked = s.providers.includes(box.id);
    for (const p of policies) {
      p.source.value = s[`${p.id}_source`] || 'mirror';
      p.transport.value = !s[`${p.id}_enabled`] ? 'direct' : s[`${p.id}_fallback`] ? 'fallback' : 'required';
      p.paintFlow();
    }
    baseline = draft();
    guardDraft();
    configPresentation(root, 'ready');
  };
  const read = async (): Promise<void> => {
    lock(true);
    coreSetUIText(status, 'textContent', corePhrase('正在读取网络代理设置…'));
    try {
      const r = await pageFetch('/settings/model-proxy');
      if (!r.ok) throw new Error('无法读取代理设置；请确认后端版本并重试。未用默认值覆盖。');
      const value = await r.json();
      if (!value.ok || !value.settings || typeof value.settings.revision !== 'string') {
        throw new Error('代理设置响应不完整，未覆盖。');
      }
      apply(value.settings);
      status.textContent = '已读取 · 各用途独立生效';
    } catch (error) {
      revision = null;
      configPresentation(root, 'error');
      coreSetUIText(
        status,
        'textContent',
        error instanceof Error ? error.message : corePhrase('读取失败，未覆盖。'),
      );
    } finally {
      lock(false);
    }
  };
  save.addEventListener('click', () => {
    void (async () => {
      if (busy || revision === null) return;
      if (enabled.checked && (!address.value.trim() || !boxes.some((x) => x.input.checked))) {
        coreSetUIText(status, 'textContent', corePhrase('启用前请填写代理地址并选择供应商。'));
        return;
      }
      if (policies.some(p => p.transport.value !== 'direct') && !address.value.trim()) {
        coreSetUIText(status, 'textContent', corePhrase('启用代理前请填写代理地址。'));
        return;
      }
      saving = true;
      guardDraft();
      lock(true);
      coreSetUIText(status, 'textContent', corePhrase('正在保存…'));
      try {
        const r = await pageFetch('/settings/model-proxy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: enabled.checked,
            proxy_url: address.value.trim(),
            providers: boxes.filter((x) => x.input.checked).map((x) => x.id),
            ...Object.fromEntries(policies.flatMap(p => [
              [`${p.id}_enabled`, p.transport.value !== 'direct'],
              [`${p.id}_fallback`, p.transport.value === 'fallback'],
              [`${p.id}_source`, p.source.value],
            ])),
            revision,
          }),
        });
        const value = await r.json().catch(() => ({}));
        if (!r.ok || !value.ok || !value.settings) {
          throw new Error(value.error || '保存未确认，请重新读取核对；未自动重试。');
        }
        apply(value.settings);
        coreSetUIText(status, 'textContent', corePhrase('已保存，下次请求生效。'));
      } catch (error) {
        coreSetUIText(
          status,
          'textContent',
          error instanceof Error ? error.message : corePhrase('保存未确认，请重新读取核对。'),
        );
      } finally {
        saving = false;
        guardDraft();
        lock(false);
      }
    })();
  });
  reload.addEventListener('click', () => {
    if (busy) return;
    if (dirty() && !window.confirm('重新读取将放弃尚未保存的代理草稿，继续吗？')) return;
    void read();
  });
  system.addEventListener('click', () => {
    if (busy || !systemProxy) return;
    address.value = systemProxy;
    coreSetUIText(status, 'textContent', corePhrase('已填入，尚未保存。'));
    guardDraft();
  });
  void read();
  return root;
}
