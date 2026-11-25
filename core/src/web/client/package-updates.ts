interface PackageStatus {
  platform: string;
  channel?: 'stable' | 'acceptance';
  current: string | null;
  latest?: string;
  releaseUrl?: string;
  downloadUrl?: string;
  installable?: boolean;
  localPreview?: boolean;
  checkRoute?: string;
  checkedAt?: number;
  nextCheckAt?: number;
  required?: boolean;
  pendingVersion?: string;
  installProgress?: string;
  lastInstall?: {version: string; base?: string; source: 'mirror' | 'official' | 'cache' | 'offline' | 'preview'; route: 'direct' | 'proxy' | 'local'};
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
  card.dataset.updateState = state.status;
  const platform = ({win: 'Windows', linux: 'Linux'} as Record<string, string>)[state.platform] ?? '';
  const version = (value: string) => value.replace(/^beta\./, '');
  const target = state.pendingVersion || (state.status === 'available' ? state.latest : undefined);
  card.querySelector<HTMLElement>('h3')!.textContent = state.pendingVersion ? '更新已就绪'
    : state.status === 'available' ? '发现新版本'
    : state.status === 'current' ? '已是最新版本'
    : state.status === 'unavailable' ? '检查更新失败' : '版本更新';
  current.textContent = target && state.current
    ? version(state.current) + ' → ' + version(target)
    : state.current ? 'v' + version(state.current) : '版本未识别';
  card.querySelector<HTMLElement>('[data-update-platform]')!.textContent =
    [platform, state.current, state.localPreview ? '本机验收源' : state.channel === 'acceptance' ? '验收更新' : ''].filter(Boolean).join(' · ');
  const links = card.querySelector<HTMLElement>('[data-update-links]')!;
  links.replaceChildren();
  const history = card.querySelector<HTMLElement>('[data-update-history]')!;
  const receipt = state.lastInstall;
  history.hidden = !receipt;
  if (receipt) {
    const source = {mirror: 'GitHub 镜像', official: 'GitHub 官方源', cache: '本地缓存', offline: '离线更新包', preview: '本机验收源'}[receipt.source];
    const route = receipt.route === 'proxy' ? ' · 代理' : receipt.route === 'direct' ? ' · 直连' : '';
    const previous = receipt.base ? receipt.base.replace(/^beta\./, '') + ' → ' + receipt.version.replace(/^beta\./, '') + '　' : '';
    history.textContent = previous + '上次安装来源　' + source + route;
  }
  const install = card.querySelector<HTMLButtonElement>('[data-install]')!;
  install.hidden = !(state.pendingVersion || (state.installable && state.status === 'available'));
  install.textContent = state.pendingVersion ? '安装并重启' : '更新并重启';
  if (state.pendingVersion) {
    notice.textContent = '已下载并校验。安装会重启 Core，短暂中断会话。';
  } else if (state.status === 'available') {
    const tag = state.platform + (state.channel === 'acceptance' ? '-acceptance-' : '-') + state.latest;
    const file = `Telyincore_${state.platform}_${state.current}_to_${state.latest}_update.zip`;
    const base = state.releaseUrl?.split('/releases/tag/')[0];
    const direct = base?.startsWith('https://github.com/')
      && state.releaseUrl === base + '/releases/tag/' + tag
      && state.downloadUrl === base + '/releases/download/' + tag + '/' + file;
    notice.textContent = state.required ? '请先更新再唤醒 Bot。安装会重启 Core。'
      : state.installable ? '安装会重启 Core，短暂中断会话。' : '此版本需要手动下载安装。';
    if (direct && state.downloadUrl && !state.installable) {
      const download = document.createElement('a');
      download.href = state.downloadUrl;
      download.download = file;
      download.className = 'package-download';
      download.textContent = '下载更新包';
      links.append(download);
    } else if (!state.installable) {
      notice.textContent = state.required ? '请先更新再唤醒 Bot。请从发布说明获取更新包。' : '请从发布说明获取适用的更新包。';
    }
    if (state.releaseUrl?.startsWith('https://github.com/')) {
      const notes = document.createElement('a');
      notes.href = state.releaseUrl;
      notes.target = '_blank';
      notes.rel = 'noopener noreferrer';
      notes.textContent = '查看发布说明';
      links.append(notes);
    }
  } else if (state.status === 'current') {
    notice.textContent = '当前无需更新。';
  } else if (state.status === 'ahead') {
    notice.textContent = '本机版本高于当前公开版本 ' + state.latest + '。';
  } else {
    notice.textContent = state.status === 'unavailable'
      ? '暂时无法确认新版本，请稍后重试。'
      : state.message || '暂时无法检查更新。';
  }
  const details = card.querySelector<HTMLElement>('[data-update-details]')!;
  const detailText = state.status === 'unavailable' ? state.message : state.checkRoute;
  details.querySelector<HTMLElement>('[data-update-connection]')!.textContent = detailText || '';
  links.hidden = !links.childElementCount;
  const timing = card.querySelector<HTMLElement>('[data-update-timing]')!;
  const time = (stamp: number) => new Date(stamp * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  timing.textContent = [state.checkedAt ? '最近检查 ' + time(state.checkedAt) : '',
    state.status === 'unavailable' ? (state.nextCheckAt
      ? '服务运行期间将于 ' + time(state.nextCheckAt) + ' 左右自动重试，也可立即重试。'
      : '可立即重试；服务运行期间也会自动检查。') : ''].filter(Boolean).join(' · ');
  timing.hidden = !timing.textContent;
  card.querySelector<HTMLButtonElement>('[data-check-update]')!.textContent =
    state.status === 'unavailable' ? '重新检查' : '检查更新';
}

function paintTopbar(checking = false) {
  const button = document.querySelector<HTMLButtonElement>('.topbar-update');
  if (!button) return;
  const available = last?.status === 'available' && !!last.latest;
  const failed = last?.status === 'unavailable';
  const version = last?.current ? 'v' + last.current.replace(/^beta\./, '') : '版本';
  button.classList.toggle('has-update', !checking && available);
  button.classList.toggle('check-failed', !checking && failed);
  button.setAttribute('aria-busy', String(checking));
  button.querySelector<HTMLElement>('.topbar-version-label')!.textContent = checking
    ? version + ' · 检查中' : available ? '新版 ' + last!.latest!.replace(/^beta\./, '')
    : failed ? version + ' · 检查失败'
    : last?.status === 'unconfigured' ? version + ' · 未配置更新源'
    : last?.status === 'unknown' ? '版本未识别' : version;
  button.title = checking ? '正在检查更新'
    : failed ? '暂时无法确认是否有新版本，点击查看原因并重试'
    : available ? '发现新版本 ' + last!.latest + '，查看版本更新'
    : last?.status === 'current' ? '已确认是最新发布版本，点击查看详情'
    : '版本更新 · 查看详情';
  button.setAttribute('aria-label', button.title);
}

async function check(force = false) {
  if (pending) {
    if (!force) return pending;
    await pending;
    if (pending) return pending;
  }
  if (force || !last) paintTopbar(true);
  pending = (async () => {
    let state: PackageStatus;
    try {
      const reply = await fetch('/package-update' + (force ? '?refresh=1' : ''),
        {cache: 'no-store', signal: AbortSignal.timeout(20000)});
      if (!reply.ok) throw new Error();
      state = await reply.json();
    } catch {
      state = {platform: last?.platform ?? '', current: last?.current ?? null,
               status: 'unavailable', message: '暂时无法连接 Core 检查更新。'};
    }
    last = state;
    document.querySelectorAll<HTMLElement>('.package-update-card').forEach(card => paint(card, state));
    paintTopbar();
  })().finally(() => { pending = undefined; });
  return pending;
}

export function openCoreUpdateDialog(opener: HTMLButtonElement) {
  const dialog = document.createElement('dialog');
  dialog.className = 'avatar-crop-dialog package-update-dialog';
  dialog.setAttribute('aria-label', '版本更新');
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = '关闭';
  close.addEventListener('click', () => dialog.close());
  close.className = 'package-update-close';
  close.textContent = '×';
  close.setAttribute('aria-label', '关闭');
  close.title = '关闭';
  dialog.append(close, packageUpdateCard());
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
    const mark = document.createElement('span');
    mark.className = 'topbar-version-mark';
    mark.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.className = 'topbar-version-label';
    label.textContent = '版本';
    button.append(mark, label);
    button.title = '版本更新 · 检查更新';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-label', button.title);
    button.addEventListener('click', () => openCoreUpdateDialog(button));
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
  button.dataset.checkUpdate = '';
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
  const history = document.createElement('p');
  history.dataset.updateHistory = '';
  history.hidden = true;
  const timing = document.createElement('p');
  timing.dataset.updateTiming = '';
  timing.hidden = true;
  const details = document.createElement('details');
  details.dataset.updateDetails = '';
  details.hidden = true;
  const summary = document.createElement('summary');
  summary.textContent = '更新详情';
  const platform = document.createElement('p');
  platform.dataset.updatePlatform = '';
  const connection = document.createElement('p');
  connection.dataset.updateConnection = '';
  details.append(summary, platform, connection, timing, history);
  details.hidden = false;
  const links = document.createElement('div');
  links.dataset.updateLinks = '';
  const actions = document.createElement('div');
  actions.className = 'package-update-actions';
  actions.append(button, install);
  card.append(title, current, notice, links, details, actions);
  if (last) paint(card, last);
  else notice.textContent = '正在读取版本信息…';
  void check();
  return card;
}
