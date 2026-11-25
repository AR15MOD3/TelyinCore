import { pluginUpdateNotice } from './plugin-updates';
import {readyPluginDisplay, readPluginTab, writePluginTab, protectPluginDisplay, pluginDisplayBridge, displayCacheCapability} from './plugin-display-cache';
import { corePhrase, coreSetUIText } from './ui-language';
import { pluginPageBridge } from './plugin-page-bridge';
import { pageLoad } from './page-load';
import { getStatusJson, StaleStatusError } from './status-json';
import { pageFetch as fetch } from './status-json';
import type { Chrome, Panel } from './shell';

interface Field {
  path: string[]; label: string; description: string; type: string;
  secret: boolean; hasValue: boolean; value: unknown;
  minimum?: number; maximum?: number; enum?: unknown[]; readOnly?: boolean;
}
interface Config {
  name: string; displayName: string; revision: string; fields: Field[]; page?: string; pageActions?: Record<string, {mutates:boolean;label:string}>;
}

function text(tag: string, className: string, value = ''): HTMLElement {
  const node = document.createElement(tag); node.className = className;
  node.textContent = value; return node;
}

/** All installed plugins use the same scoped configuration and revision contract. */
export function pluginConfigPage(shell: Chrome, name: string): Panel {
  const root = text('section', 'page-infinity page-world');
  const title = text('h1', '', '插件配置');
  const info = text('p', 'infinity-hint',
    '配置按当前 Bot 独立保存；保存会重载已启用的插件，停用的插件不会自动启动。');
  const tabs = text('div', 'infinity-tabs');
  const form = text('div', 'generic-plugin-form');
  const custom = document.createElement('iframe'); custom.className = 'plugin-config-frame';
  coreSetUIText(custom, 'title', corePhrase("插件自定义配置页")); custom.setAttribute('sandbox', 'allow-scripts');
  custom.referrerPolicy = 'no-referrer'; custom.hidden = true;
  const message = text('p', 'infinity-message'); message.setAttribute('role', 'status');
  const save = text('button', 'infinity-action', '保存配置') as HTMLButtonElement;
  save.type = 'button'; save.disabled = true;
  const reload = text('button', 'infinity-action', '重新读取配置') as HTMLButtonElement;
  reload.type = 'button';
  const status = text('p', 'infinity-hint');
  const url = '/infinity/plugin-config/' + encodeURIComponent(name);
  let state: Config | null = null; let busy = false; let generation = 0; let dirty = false;
  let editVersion = 0;
  let unprotect: (() => void) | undefined;
  let customOpened = false;
  let integrated = false;
  let tabEdits = 0;
  let values: Array<() => { path: string[]; value: unknown } | null> = [];
  const draw = (answer: Config): void => {
    integrated = false; tabs.hidden = false; reload.hidden = false; status.hidden = false; custom.style.height = '';
    state = answer; dirty = false; values = []; form.replaceChildren(); tabs.replaceChildren();
    coreSetUIText(title, 'textContent', [corePhrase(""), answer.displayName, corePhrase(" · 配置")]);
    for (const field of answer.fields) {
      const row = text('label', 'generic-plugin-field');
      row.append(text('span', '', field.label));
      row.append(text('small', 'infinity-hint', field.path.join(' → ')));
      let input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
      if (field.enum && !field.secret) {
        input = document.createElement('select');
        input.append(...field.enum.map(value => new Option(String(value), JSON.stringify(value))));
        input.value = JSON.stringify(field.value);
      } else if (['array', 'object', 'null'].includes(field.type)) {
        input = document.createElement('textarea'); input.rows = 4;
        input.value = field.secret ? '' : JSON.stringify(field.value, null, 2);
      } else {
        input = document.createElement('input');
        input.type = field.type === 'boolean' ? 'checkbox' : field.secret ? 'password'
          : ['integer', 'number'].includes(field.type) ? 'number' : 'text';
        input.autocomplete = 'off';
        if (field.type === 'boolean') input.checked = field.value === true;
        else if (!field.secret) input.value = field.value == null ? '' : String(field.value);
        if (input.type === 'number') {
          input.step = field.type === 'integer' ? '1' : 'any';
          if (field.minimum !== undefined) input.min = String(field.minimum);
          if (field.maximum !== undefined) input.max = String(field.maximum);
        }
      }
      input.disabled = !!field.readOnly;
      input.setAttribute('aria-label', field.label);
      if (field.secret && !(input instanceof HTMLSelectElement)) {
        coreSetUIText(input, 'placeholder', (field.hasValue ? corePhrase("已设置；留空保持原值") : corePhrase("未设置")));
      }
      row.append(input);
      const clear = document.createElement('input'); clear.type = 'checkbox';
      if (field.secret) {
        const clearLabel = text('span', 'generic-plugin-clear');
        clearLabel.append(clear, text('span', '', '清空此字段'));
        row.append(clearLabel);
      }
      if (field.description) row.append(text('small', 'infinity-hint', field.description));
      let touched = false;
      const mark = (): void => { editVersion++; dirty = true; touched = true;
        coreSetUIText(status, 'textContent', corePhrase("有未保存的修改")); };
      input.addEventListener('input', mark); input.addEventListener('change', mark);
      clear.addEventListener('change', mark);
      values.push(() => {
        if (!touched || field.readOnly || (field.secret && !clear.checked && !input.value)) {
          return null;
        }
        let value: unknown;
        if (clear.checked) value = field.type === 'array' ? [] : field.type === 'object' ? {} : '';
        else if (field.enum && !field.secret) value = JSON.parse(input.value);
        else if (field.type === 'boolean') value = (input as HTMLInputElement).checked;
        else if (['integer', 'number'].includes(field.type)) {
          if (!input.value.trim()) throw new Error(`${field.label} 需要填写数值`);
          value = Number(input.value);
          if (!Number.isFinite(value)) throw new Error(`${field.label} 数值无效`);
        } else if (['array', 'object', 'null'].includes(field.type)) {
          value = JSON.parse(input.value);
        }
        else value = input.value;
        return { path: field.path, value };
      });
      form.append(row);
    }
    coreSetUIText(message, 'textContent', (answer.fields.length ? corePhrase("") : corePhrase("此插件未声明配置字段，也没有 config.json 或 config.example.json；请由作者提供声明。")));
    save.disabled = answer.fields.length === 0;
    custom.hidden = true; form.hidden = false; save.hidden = false;
    if (answer.page) {
      const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        + "img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'";
      custom.srcdoc = `<meta http-equiv="Content-Security-Policy" content="${policy}">`
        + answer.page;
      for (const [label, isCustom] of [['常规配置', false], ['插件页面', true]] as const) {
        const tab = text('button', 'infinity-tab', label) as HTMLButtonElement;
        tab.type = 'button'; tab.setAttribute('aria-selected', String(!isCustom));
        tab.addEventListener('click', () => {
          if (integrated) return;
          tabEdits++; writePluginTab(name, isCustom ? 1 : 0);
          if (isCustom) customOpened = true;
          form.hidden = isCustom; custom.hidden = !isCustom; save.hidden = isCustom;
          for (const item of tabs.children) item.setAttribute('aria-selected', 'false');
          tab.setAttribute('aria-selected', 'true');
          if (isCustom && state) { const {page:_, ...publicState}=state;
            custom.contentWindow?.postMessage({type:'telyincore:config:state',config:publicState,toolCatalog:true, displayCache:displayCacheCapability,active:true}, '*'); }
        });
        tabs.append(tab);
      }
      const tabVersion = tabEdits;
      void readyPluginDisplay().then(() => {
        if (!integrated && !root.hidden && !dirty && state === answer && tabVersion === tabEdits && readPluginTab(name) === 1)
          (tabs.children[1] as HTMLButtonElement).click();
      });
    }
  };
  const readHint = pageLoad(root, '插件配置', () => { void load(); });
  const load = async (): Promise<void> => {
    if (busy || dirty) return;
    readHint.start();
    const mine = ++generation;
    try {
      const response = await fetch(url, { cache: 'no-store' });
      const answer = await response.json() as Config & { said?: string };
      if (!response.ok) throw new Error(answer.said || '无法读取插件配置');
      if (mine === generation && !root.hidden && !dirty) {
        draw(answer); status.textContent = ''; readHint.ready();
      }
    } catch (error) {
      if (mine !== generation || root.hidden) return;
      if (error instanceof StaleStatusError) { void load(); return; }
      readHint.fail();
    }
  };
  const persist = async (changes: unknown[], revision: string): Promise<void> => {
    if (busy) throw new Error('正在保存，请稍候');
    const edits = editVersion;
    const mine = ++generation;
    busy = true; save.disabled = reload.disabled = true;
    try {
      const response = await fetch(url, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes, revision }),
      });
      const answer = await response.json() as { config: Config; message: string; said?: string };
      if (!response.ok) throw new Error(answer.said || '保存失败');
      if (mine !== generation || root.hidden) return;
      if (edits !== editVersion) {
        state = answer.config;
        coreSetUIText(status, 'textContent', corePhrase("本次保存已完成，后续修改仍未保存。"));
      } else {
        if (custom.hidden) draw(answer.config);
        else { state = answer.config; dirty = false; }
        status.textContent = answer.message;
      }
      window.dispatchEvent(new Event('plugins-changed'));
    } finally { busy = false; reload.disabled = false; save.disabled = !state?.fields.length; }
  };
  save.addEventListener('click', () => {
    if (!state || busy) return;
    try {
      const changes = values.map(get => get()).filter(one => one !== null);
      if (!changes.length) { coreSetUIText(status, 'textContent', corePhrase("没有需要保存的修改")); return; }
      void persist(changes, state.revision).catch(error => { status.textContent = String(error); });
    } catch (error) { status.textContent = String(error); }
  });
  reload.addEventListener('click', () => {
    if (dirty) { coreSetUIText(status, 'textContent', corePhrase("有未保存的修改；请先保存，或离开本页后重新进入。")); return; }
    void load();
  });
  // The opaque-origin frame gets only its own redacted fields and save contract.
  const operatorBridge = pluginPageBridge({name, frame:custom, current:()=>state,
    active:()=>!root.hidden && !custom.hidden, epoch:()=>generation});
  const displayBridge = pluginDisplayBridge({name,frame:custom,
    active:()=>!root.hidden && !custom.hidden,epoch:()=>generation});
  const bridge = (event: MessageEvent): void => {
    displayBridge(event);
    operatorBridge(event);
    if (event.source !== custom.contentWindow || !state || root.hidden) return;
    const request = event.data;
    if (!request || typeof request !== 'object') return;
    const reply = (payload: object): void => custom.contentWindow?.postMessage(payload, '*');
    // The package may own a unified editor; this changes presentation only.
    // It retains the same scoped config/revision and opaque-origin bridges.
    if (request.type === 'telyincore:config:layout' && request.mode === 'integrated' && state.page) {
      integrated = true; customOpened = true;
      tabs.hidden = form.hidden = save.hidden = reload.hidden = status.hidden = true;
      custom.hidden = false;
      custom.style.height = 'max(620px, calc(100dvh - 250px))';
      const {page: _, ...publicState} = state;
      reply({type:'telyincore:config:state', config:publicState, active:true,
        toolCatalog:true, displayCache:displayCacheCapability});
    }
    if (request.type === 'telyincore:config:dirty' && integrated && typeof request.dirty === 'boolean') {
      dirty = request.dirty; editVersion++;
    }
    if (request.type === 'telyincore:tools:get' && !custom.hidden && Number.isSafeInteger(request.id)) {
      const epoch = generation;
      const send = (catalog: unknown) => {
        if (epoch === generation && !root.hidden && !custom.hidden)
          reply({type:'telyincore:tools:result', id:request.id, catalog});
      };
      // The host binds the lookup to this iframe's package.
      void getStatusJson('/infinity/plugin-tools/' + encodeURIComponent(name)).then(send, () => send(null));
      return;
    }
    if (request.type === 'telyincore:config:get') {
      const { page: _, ...publicState } = state;
      reply({ type: 'telyincore:config:state', config: publicState, toolCatalog:true, displayCache:displayCacheCapability, active:!custom.hidden });
    }
    if (request.type === 'telyincore:config:save' && !custom.hidden && Array.isArray(request.changes)) {
      void persist(request.changes, request.revision).then(() => {
        reply({ type: 'telyincore:config:saved', ok: true });
      }).catch(error => { reply({ type: 'telyincore:config:saved', ok: false, message: String(error) }); });
    }
  };
  root.append(title, pluginUpdateNotice(name), info, tabs, message, form, custom, save, reload, status);
  shell.content.replaceChildren(root);
  return { root, show(): void {
    root.hidden = false; unprotect ??= protectPluginDisplay(name, () => dirty || busy || customOpened); window.addEventListener('message', bridge); void load();
  }, stop(): void {
    unprotect?.(); unprotect = undefined;
    root.hidden = true; generation++; window.removeEventListener('message', bridge);
  } };
}
