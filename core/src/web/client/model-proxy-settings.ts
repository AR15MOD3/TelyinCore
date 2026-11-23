import { corePhrase, coreSetUIAttribute, coreSetUIText, coreUITextNode } from './ui-language';
import { pageFetch } from './status-json';
import { configPresentation } from './config-presentation';

interface ProxySettings {
  enabled: boolean;
  market_enabled?: boolean;
  downloads_enabled?: boolean;
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
  coreSetUIText(h, 'textContent', corePhrase('网络代理'));
  const note = document.createElement('p');
  note.className = 'set-note';
  coreSetUIText(note, 'textContent',
    corePhrase('国内镜像始终直连。切换到官方源时，按下方用途使用代理。'));
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
  coreSetUIAttribute(address, 'aria-label', corePhrase('模型代理地址'));
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
  const uses = document.createElement('fieldset');
  uses.className = 'model-proxy-targets';
  const usesTitle = document.createElement('legend');
  coreSetUIText(usesTitle, 'textContent', corePhrase('其他用途'));
  uses.append(usesTitle);
  const extraBoxes = ([
    ['market_enabled', '插件市场（GitHub 搜索与下载）'],
    ['downloads_enabled', '下载（模型、运行环境和 Core 更新）'],
  ] as const).map(([id, name]) => {
    const label = document.createElement('label');
    label.className = 'model-proxy-provider';
    const input = document.createElement('input');
    input.type = 'checkbox';
    label.append(input, coreUITextNode(corePhrase(name)));
    uses.append(label);
    return {id, input};
  });
  const scopeInputs = [...boxes, ...extraBoxes].map(box => box.input);
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
  header.append(h, enabledLabel);
  const details = document.createElement('details');
  details.className = 'model-proxy-details';
  const summary = document.createElement('summary');
  coreSetUIText(summary, 'textContent', corePhrase('适用范围'));
  details.append(summary, targets, uses, help, hint, reload);
  actions.append(save, system);
  root.append(header, label, details, actions, note, status);
  let revision: string | null = null,
    systemProxy = '',
    busy = false;
  let baseline: string | null = null,
    saving = false,
    guarding = false;
  const draft = (): string =>
    JSON.stringify([enabled.checked, address.value, scopeInputs.map(input => input.checked)]);
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
  for (const input of [enabled, address, ...scopeInputs]) {
    input.addEventListener('input', guardDraft);
    input.addEventListener('change', guardDraft);
  }
  const lock = (pending: boolean): void => {
    busy = pending;
    for (const control of [enabled, address, ...scopeInputs, save]) {
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
    for (const box of extraBoxes) box.input.checked = s[box.id] === true;
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
      const active = enabled.checked || extraBoxes.some(box => box.input.checked);
      coreSetUIText(status, 'textContent', corePhrase(active ? '已启用' : '未启用'));
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
      if (extraBoxes.some(box => box.input.checked) && !address.value.trim()) {
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
            ...Object.fromEntries(extraBoxes.map(box => [box.id, box.input.checked])),
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
