interface PackageStatus {
  platform: string;
  channel?: 'stable' | 'acceptance';
  current: string | null;
  latest?: string;
  releaseUrl?: string;
  downloadUrl?: string;
  installable?: boolean;
  pendingVersion?: string;
  installProgress?: string;
  status: 'available' | 'current' | 'ahead' | 'unavailable' | 'unconfigured' | 'unknown';
  message?: string;
}

let last: PackageStatus | undefined;
let pending: Promise<void> | undefined;
let installing = false;

function paint(card: HTMLElement, state: PackageStatus) {
  if (installing) return;
  const current = card.querySelector<HTMLElement>('[data-version]')!;
  const notice = card.querySelector<HTMLElement>('[data-update-message]')!;
  const platform = ({win: 'Windows', linux: 'Linux'} as Record<string, string>)[state.platform] ?? '';
  current.textContent = [platform, state.channel === 'acceptance' ? '验收更新' : '', state.current ? '已安装 ' + state.current : '未识别安装版本'].filter(Boolean).join(' · ');
  const install = card.querySelector<HTMLButtonElement>('[data-install]')!;
  install.hidden = !(state.pendingVersion || (state.installable && state.status === 'available'));
  install.textContent = state.pendingVersion ? '继续安装并重启' : '立即更新并重启';
  if (state.pendingVersion) {
    notice.textContent = state.pendingVersion + ' 已下载并校验。重启会短暂中断会话，也可等下次启动时安装。';
  } else if (state.status === 'available') {
    const tag = state.platform + (state.channel === 'acceptance' ? '-acceptance-' : '-') + state.latest;
    const file = `Telyincore_${state.platform}_${state.current}_to_${state.latest}_update.zip`;
    const base = state.releaseUrl?.split('/releases/tag/')[0];
    const direct = base?.startsWith('https://github.com/')
      && state.releaseUrl === base + '/releases/tag/' + tag
      && state.downloadUrl === base + '/releases/download/' + tag + '/' + file;
    notice.textContent = '发现新版本 ' + state.latest + (state.installable ? '。更新完成后 Core 会自动重启。' : '。');
    if (direct && state.downloadUrl && !state.installable) {
      const download = document.createElement('a');
      download.href = state.downloadUrl;
      download.download = file;
      download.className = 'package-download';
      download.textContent = '下载更新包';
      notice.append(' ', download);
    } else if (!state.installable) {
      notice.append('请在发布说明或分发 QQ 群中获取适用的更新包。');
    }
    if (state.releaseUrl?.startsWith('https://github.com/')) {
      const notes = document.createElement('a');
      notes.href = state.releaseUrl;
      notes.target = '_blank';
      notes.rel = 'noopener noreferrer';
      notes.textContent = '查看发布说明';
      notice.append(' ', notes);
    }
  } else if (state.status === 'current') {
    notice.textContent = '当前已是最新发布版本。';
  } else if (state.status === 'ahead') {
    notice.textContent = '本机版本高于当前公开版本 ' + state.latest + '。';
  } else {
    notice.textContent = state.message || '暂时无法检查更新。';
  }
}

async function check(force = false) {
  if (pending) {
    if (!force) return pending;
    await pending;
    if (pending) return pending;
  }
  pending = (async () => {
    let state: PackageStatus;
    try {
      const reply = await fetch('/package-update' + (force ? '?refresh=1' : ''), {cache: 'no-store'});
      if (!reply.ok) throw new Error();
      state = await reply.json();
    } catch {
      state = {platform: last?.platform ?? '', current: last?.current ?? null,
               status: 'unavailable', message: '暂时无法连接 Core 检查更新。'};
    }
    last = state;
    document.querySelectorAll<HTMLElement>('.package-update-card').forEach(card => paint(card, state));
    const button = document.querySelector<HTMLButtonElement>('.topbar-update');
    if (button) {
      const available = state.status === 'available' && !!state.latest;
      button.classList.toggle('has-update', available);
      button.textContent = available ? '新版 ' + state.latest!.replace(/^beta\./, '') : '更新';
      button.title = available ? '发现新版本 ' + state.latest + '，查看版本更新' : '版本更新 · 检查更新';
      button.setAttribute('aria-label', button.title);
    }
  })().finally(() => { pending = undefined; });
  return pending;
}

function openUpdateDialog(opener: HTMLButtonElement) {
  const dialog = document.createElement('dialog');
  dialog.className = 'avatar-crop-dialog package-update-dialog';
  dialog.setAttribute('aria-label', '版本更新');
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = '关闭';
  close.addEventListener('click', () => dialog.close());
  const actions = document.createElement('div');
  actions.className = 'avatar-crop-actions';
  actions.append(close);
  dialog.append(packageUpdateCard(), actions);
  dialog.addEventListener('close', () => { dialog.remove(); opener.focus(); }, {once: true});
  document.body.append(dialog);
  dialog.showModal();
}

export function startPackageUpdates() {
  const row = document.querySelector('.topbar-status');
  if (row && !row.querySelector('.topbar-update')) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'topbar-update';
    button.textContent = '更新';
    button.title = '版本更新 · 检查更新';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-label', button.title);
    button.addEventListener('click', () => openUpdateDialog(button));
    row.append(button);
  }
  void check();
  window.setInterval(() => { if (!document.hidden) void check(); }, 60 * 1000);
  window.addEventListener('core-control-refresh', () => { if (!document.hidden) void check(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void check(); });
}

export function packageUpdateCard() {
  const card = document.createElement('section');
  card.className = 'settings-card package-update-card';
  const title = document.createElement('h3');
  title.textContent = '版本更新';
  const current = document.createElement('p');
  current.dataset.version = '';
  const notice = document.createElement('p');
  notice.dataset.updateMessage = '';
  notice.setAttribute('role', 'status');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'settings-language-button';
  button.textContent = '检查更新';
  button.addEventListener('click', async () => {
    button.disabled = true;
    notice.textContent = '正在检查…';
    try { await check(true); } finally { button.disabled = false; }
  });
  const install = document.createElement('button');
  install.type = 'button';
  install.className = 'settings-language-button';
  install.dataset.install = '';
  install.hidden = true;
  install.addEventListener('click', async () => {
    if (installing) return;
    const targetVersion = last?.pendingVersion || last?.latest;
    if (!targetVersion) return;
    installing = true;
    install.disabled = true;
    notice.textContent = '正在下载并校验，安装完成后会自动重启…';
    let waitingForTransfer = true;
    const showProgress = window.setInterval(async () => {
      try {
        const response = await fetch('/package-update', {cache: 'no-store', signal: AbortSignal.timeout(2000)});
        if (!response.ok) return;
        const state: PackageStatus = await response.json();
        if (waitingForTransfer && state.installProgress) notice.textContent = state.installProgress;
      } catch { /* The installer closes the listener on successful handoff. */ }
    }, 2000);
    try {
      const response = await fetch('/package-update/install', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.error || '更新未完成，请重试');
      }
      waitingForTransfer = false;
      window.clearInterval(showProgress);
      notice.textContent = '正在重启，等待新版 Core 就绪…';
      const deadline = Date.now() + 120000;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        try {
          const state = await fetch('/package-update', {cache: 'no-store', signal: AbortSignal.timeout(4000)});
          if (state.ok && (await state.json()).current === targetVersion) {
            window.location.reload();
            return;
          }
        } catch { /* The local listener is offline during replacement. */ }
      }
      notice.textContent = '尚未收到新版 Core 的就绪回应。请查看启动器状态。';
    } catch (error) {
      notice.textContent = error instanceof Error ? error.message : '更新失败，请重试。';
    } finally {
      waitingForTransfer = false;
      window.clearInterval(showProgress);
      installing = false;
      install.disabled = false;
    }
  });
  card.append(title, current, notice, install, button);
  if (last) paint(card, last);
  else notice.textContent = '正在读取版本信息…';
  void check();
  return card;
}
