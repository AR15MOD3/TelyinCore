import { corePhrase, coreSetUIText } from './ui-language';
/** Model-page OAuth controls. No credentials, browser storage, or HTML injection. */
import { confirmAction } from './confirm-dialog';
import { chatgptCommand, type ChatGPTStatus } from './engine';

export function chatgptAuth(readName: () => string, backendReady = true): {
  root: HTMLElement; refresh: () => void; stop: () => void;
} {
  const root = document.createElement('div');
  root.className = 'engine-chatgpt';
  const heading = document.createElement('div');
  heading.className = 'engine-chatgpt-heading';
  const title = document.createElement('strong');
  coreSetUIText(title, 'textContent', corePhrase("ChatGPT 订阅"));
  const badge = document.createElement('span');
  badge.className = 'pill';
  coreSetUIText(badge, 'textContent', corePhrase("未登录"));
  heading.append(title, badge);
  const status = document.createElement('p');
  status.className = 'engine-chatgpt-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const hint = document.createElement('p');
  hint.className = 'engine-chatgpt-note';
  coreSetUIText(hint, 'textContent', corePhrase("使用符合条件的 ChatGPT 订阅，不需要 API Key。模型和额度以账号授权为准；此入口不会自动切换到付费 API。"));
  const actions = document.createElement('div');
  actions.className = 'engine-chatgpt-actions';
  const make = (text: string, cls = 'btn ghost'): HTMLButtonElement => {
    const node = document.createElement('button');
    node.type = 'button'; node.className = cls; node.textContent = text;
    actions.append(node); return node;
  };
  const login = make('Continue with ChatGPT', 'btn');
  const refresh = make('刷新状态');
  const cancel = make('取消登录');
  const logout = make('退出登录');
  const security = document.createElement('p');
  security.className = 'engine-chatgpt-note';
  root.append(heading, status, hint, actions, security);
  let stopped = false, epoch = 0, timer: ReturnType<typeof setTimeout> | undefined;
  let authWindow: Window | null = null;
  let authName = '';
  const closeAuth = (): void => {
    try { authWindow?.close(); } catch { /* Browser may deny closing. */ }
    authWindow = null; authName = '';
  };
  let busy = false, state: ChatGPTStatus | null = null;
  const clear = (): void => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };
  const paint = (): void => {
    coreSetUIText(badge, 'textContent', (state?.pending ? corePhrase("等待授权") : (state?.connected ? corePhrase("已登录") : corePhrase("未登录"))));
    badge.dataset.state = state?.connected ? 'ok' : '';
    const message = state?.connected && state.message.startsWith('尚未登录')
      ? '已从本机载入登录' : state?.message;
    coreSetUIText(status, 'textContent', (!backendReady ? corePhrase("后端仍是旧版本，请重启 Core 后刷新此页。") : (state ? [state.account, message].filter(Boolean).join(' · ') : corePhrase("正在读取登录状态…"))));
    coreSetUIText(security, 'textContent', (state ? [corePhrase("凭证仅保存在本机 · "), state.storage, corePhrase(" · 不读取 Codex 或浏览器的登录文件")] : corePhrase("")));
    login.disabled = !backendReady || busy || !!state?.pending;
    refresh.disabled = !backendReady || busy; cancel.hidden = !state?.pending;
    cancel.disabled = busy; logout.hidden = !state?.connected;
    logout.disabled = busy;
  };
  const run = async (action: 'status' | 'start' | 'cancel' | 'logout',
                     popup?: Window | null): Promise<void> => {
    if (!backendReady) { popup?.close(); return; }
    clear(); busy = true; paint();
    const current = ++epoch, name = readName();
    try {
      const got = await chatgptCommand(name, action);
      if (stopped || current !== epoch || name !== readName()) {
        popup?.close(); return;
      }
      if (!got.ok || !got.data) {
        popup?.close(); status.textContent = got.note || '无法读取登录状态'; return;
      }
      const next = { ...got.data, pending: got.data.pending || !!got.data.login_in_progress };
      const completed = next.login_completed === true;
      state = next; paint();
      if (authWindow && authName === name && !state.pending
          && (completed || action === 'cancel' || action === 'logout')) {
        const returned = completed && action !== 'cancel' && action !== 'logout';
        closeAuth();
        if (returned) {
          try { window.focus(); } catch { /* Focusing is best-effort. */ }
          coreSetUIText(status, 'textContent', corePhrase("已登录，可以测试连通。"));
        }
      }
      if (action === 'start' && state.authorization_url) {
        const url = new URL(state.authorization_url);
        if (url.origin !== 'https://auth.openai.com'
            || url.pathname !== '/api/accounts/authorize') {
          popup?.close(); coreSetUIText(status, 'textContent', corePhrase("授权地址校验失败，未打开")); return;
        }
        if (popup) {
          authWindow = popup; authName = name;
          popup.opener = null; popup.location.replace(url.href);
        }
        else {
          coreSetUIText(status, 'textContent', corePhrase("浏览器阻止了授权窗口，请允许弹出窗口后重新登录。"));
          await chatgptCommand(name, 'cancel'); state.pending = false; }
      }
      if (state.pending) timer = setTimeout(() => { void run('status'); }, 2000);
    } catch {
      popup?.close();
      if (!stopped && current === epoch) {
        coreSetUIText(status, 'textContent', corePhrase("登录操作未完成，请刷新状态后重试。"));
      }
    } finally {
      if (!stopped && current === epoch) { busy = false;
        login.disabled = !!state?.pending; refresh.disabled = false;
        cancel.disabled = false; logout.disabled = false; }
    }
  };
  login.addEventListener('click', () => {
    if (!backendReady || busy) return;
    // Synchronous user gesture prevents browsers from treating the auth tab as unsolicited.
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    void run('start', popup);
  });
  refresh.addEventListener('click', () => { if (!busy) void run('status'); });
  cancel.addEventListener('click', () => { if (!busy) void run('cancel'); });
  let confirming = false;
  logout.addEventListener('click', () => {
    if (busy || confirming || stopped) return;
    confirming = true;
    const identity = readName(), revision = epoch;
    void confirmAction('退出 ChatGPT 登录',
      '仅退出此模型档案，后续订阅请求将停止。其他模型和当前配置不受影响。', '退出登录')
      .then(accepted => {
        if (accepted && !stopped && !busy && identity === readName() && revision === epoch) {
          void run('logout');
        }
      }).finally(() => { confirming = false; });
  });
  paint();
  return { root, refresh: () => { if (!stopped && !busy) void run('status'); },
    stop: () => { stopped = true; epoch++; clear(); closeAuth(); } };
}
