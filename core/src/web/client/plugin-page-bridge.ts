/** Explicit operator calls from one opaque-origin plugin frame, never model tools. */
import { pageFetch as fetch } from './status-json';
export interface PageConfig {
  revision: string;
  pageActions?: Record<string, {mutates: boolean; label: string}>;
}
export function pluginPageBridge(options: {
  name: string; frame: HTMLIFrameElement; current: () => PageConfig | null;
  active: () => boolean; epoch: () => number;
}): (event: MessageEvent) => void {
  let busy = false;
  return event => {
    if (!options.active() || event.source !== options.frame.contentWindow) return;
    const request = event.data;
    if (!request || typeof request !== 'object') return;
    if (request.type === 'telyincore:page:overview') { location.hash = '#/infinity'; return; }
    if (request.type !== 'telyincore:page:request') return;
    const state = options.current();
    if (!state || typeof request.id !== 'string' || !/^[0-9]{1,12}$/.test(request.id)) return;
    const frame = options.frame.contentWindow;
    const epoch = options.epoch();
    const live = (): boolean => options.active() && options.current() === state
      && options.epoch() === epoch && options.frame.contentWindow === frame;
    const reply = (value: object): void => {
      if (live()) frame?.postMessage({type:'telyincore:page:result', id:request.id, ...value}, '*');
    };
    const rule = state.pageActions && Object.hasOwn(state.pageActions, request.action)
      ? state.pageActions[request.action] : null;
    if (!rule || request.revision !== state.revision || !request.payload
        || typeof request.payload !== 'object' || Array.isArray(request.payload) || busy) {
      reply({ok:false,message:'页面请求无效、配置已变化或已有操作进行中；未执行新操作。'}); return;
    }
    // Confirmation is in trusted host chrome, not sandbox modal or an iframe flag.
    if (rule.mutates && !window.confirm(`确认执行“${rule.label}”？\n仅影响当前 Bot 的本机记录，不注销游戏账号。操作后请核对结果。`)) {
      reply({ok:false,message:'已取消，没有执行操作。'}); return;
    }
    busy = true;
    void (async () => {
      try {
        const response = await fetch('/infinity/plugin-page/' + encodeURIComponent(options.name), {
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({action:request.action,payload:request.payload,
            revision:state.revision,confirmed:rule.mutates}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error('页面操作失败，结果未确认；请刷新核对，不要自动重试。');
        reply({ok:true,result});
      } catch { reply({ok:false,message:'页面操作失败，结果未确认；请刷新核对，不要自动重试。'}); }
      finally { busy = false; }
    })();
  };
}
