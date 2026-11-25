import { qqToolbox } from './qq-toolbox';
import {qqStickers} from './qq-stickers';
import { voiceSetup } from './qq-voice-setup';
import {readyPluginDisplay, readPluginTab, writePluginTab} from './plugin-display-cache';
import { uiSource } from './ui-language';
import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { createQQTerminal, createQQEnvironment } from './qq-diagnostics';
import { voiceCorpusPanel } from './voice-corpus-panel';
import { configPresentation } from './config-presentation';

import { confirmAction } from './confirm-dialog';

import type { Chrome, Panel } from './shell';

import type { InstalledPlugin, PluginsAnswer } from './plugins-page';

import { qqSignals, freshSnowSample, freshProtocolSample } from './qq-signals';

import type { QQHealthSnapshot } from './qq-signals';


import { chooseASRSource } from './download-dialog';

import { getStatusJson, invalidateStatus, invalidateStatuses } from './status-json';

import { infinityField as choice, infinityToggle as toggle,

  infinityModelPicker as modelPicker, infinityVoiceHeading } from './infinity-controls';



interface VoiceState {
  readonly autoStart?: boolean;

  readonly install?: { readonly busy: boolean; readonly message: string;

    readonly downloaded: number; readonly total?: number; readonly controls?: boolean; readonly runtimeReady?: boolean; readonly bytesPerSecond?: number; readonly route?: string;

    readonly source?: string; readonly file?: string; readonly phase?: string };

  readonly configured?: boolean;

  readonly setupReady?: boolean;

  readonly enabled: boolean;

  readonly model: string;

  readonly models: readonly string[];

  readonly running?: boolean;

  readonly managed?: boolean | null;

}



interface ASRState extends VoiceState {
  readonly startError?: string;

  readonly runtimeIssue?: string;

  readonly install?: { readonly busy: boolean; readonly message: string;

    readonly downloaded: number; readonly total?: number; readonly controls?: boolean; readonly runtimeReady?: boolean; readonly bytesPerSecond?: number; readonly route?: string;

    readonly source?: string; readonly file?: string; readonly phase?: string };

  readonly language?: string;

  readonly useItn?: boolean;

  readonly threads?: number;

}



interface TTSState extends VoiceState {
  readonly port?: number;
  readonly provider?: string;
  readonly service?: string;

  readonly voice?: string;

  readonly voices?: readonly { readonly id: string; readonly label: string }[];

  readonly speed?: number;

  readonly sendMode?: string;

  readonly previewText?: string;
  readonly voiceFolderAvailable?: boolean;
  readonly pluginEnabled?: boolean;
  readonly startError?: string;
  readonly runtimeIssue?: string;

}



interface QQState extends QQHealthSnapshot {

  readonly environment?: { readonly status: string; readonly text: string;

    readonly source: string; readonly message: string; readonly readOnly: boolean };

  readonly wired: boolean;

  readonly routing: { readonly privates: readonly string[]; readonly groups: readonly string[];

    readonly proactiveTarget: string; readonly sharingOwner?: string;
    readonly ownerRoute?: { readonly accounts: string[]; readonly target: string; readonly unavailable?: boolean };
      readonly sharingEnabled?: boolean; readonly historyBackfillMinutes: number;

    readonly reactionWindowSeconds?: number };

  readonly asr: ASRState;

  readonly tts: TTSState;

  readonly snowluma: NonNullable<QQHealthSnapshot["snowluma"]> & {
    readonly managementUrl?: string;

    readonly configured?: boolean;

    readonly installation?: { readonly nodeAvailable: boolean; readonly nodeVersion: string;

      readonly entryPresent: boolean; readonly ready: boolean };

    readonly installed?: boolean;
    readonly isolatedInstance?: boolean;
    readonly managementPasswordChanged?: boolean | null;

    readonly running: boolean; readonly webuiRunning: boolean;

    readonly managed: boolean | null; readonly log: string;
    readonly controlAvailable?: boolean; readonly controlMessage?: string;

    readonly canStopInstallation?: boolean; readonly sharedService?: boolean;

    readonly recovery?: { readonly busy: boolean; readonly message: string;

      readonly phase?: string; readonly accountOnline?: boolean;

      readonly managementAuthenticated?: boolean; readonly loginCompleted?: boolean;

      readonly qqPath: string; readonly username: string; readonly targetUin: string;

      readonly hasCredentials: boolean } };


}



function node<K extends keyof HTMLElementTagNameMap>(tag: K, className: string,
  text?: string): HTMLElementTagNameMap[K] {

  const element = document.createElement(tag);

  element.className = className;

  if (text !== undefined) element.textContent = text;

  return element;

}



function stateOf(plugin: InstalledPlugin): string {

  if (plugin.installed === false) return '未安装';

  if (plugin.contract) return '接口不兼容';

  if (plugin.loaded) return '运行中';

  if (plugin.enabled) return '已启用 · 未加载';

  return '已停用';

}



/** INFINITY 总览；传入安装名时复用插件详情的配置控件。 */

export function infinityPage(shell: Chrome, pluginName = ''): Panel {

  const knownConfig = pluginName === 'core-infinity-qq';

  const root = node('section', 'page-infinity page-world');
  if (pluginName === 'core-infinity-qq') root.classList.add('page-qq-settings');

  const head = knownConfig ? node('header', 'core-page-head') : node('div', 'head');

  const heading = node('h1', knownConfig ? 'plugins-title' : '',
    knownConfig ? 'QQ 连接 · 配置' : pluginName ? '插件配置' : '插件总览 · INFINITY');

  head.append(heading);
  if (knownConfig) {
    const intro = node('p', 'qq-page-intro');
    coreSetUIText(intro, 'textContent',
      corePhrase("通过 OneBot v11 协议端收发 QQ 私聊、群聊文字、表情和语音。"));
    head.append(intro);
  }

  const liveNotice = knownConfig ? node('p', 'plugins-note')
    : node('span', 'infinity-hint', '正在读取状态…');
  liveNotice.setAttribute('role', 'status');

  head.append(liveNotice);


  const summary = node('div', 'infinity-summary');

  const cards = node('div', 'infinity-cards');

  const message = node('p', 'infinity-message');

  const qqPanel = node('section', 'infinity-qq-panel');
  const panelScroll = node('div', 'page-scroll qq-panel-scroll');


  const health = node('div', 'infinity-health');

  const tabs = node('div', 'infinity-tabs');

  tabs.setAttribute('role', 'tablist');

  const voiceGrid = node('div', 'infinity-voice-grid');

  const environmentPane = node('div', 'infinity-pane infinity-environment-pane');

  const environmentView = createQQEnvironment();
  const toolbox = qqToolbox();
  environmentPane.append(environmentView.root);

  const routePane = node('div', 'infinity-pane');

  const voicePane = node('div', 'infinity-pane');

  const snowPane = node('div', 'infinity-pane');

  const terminalPane = node('div', 'infinity-pane infinity-terminal-pane');

  const routingCard = node('div', 'infinity-voice-card infinity-routing-card');

  const asrCard = node('div', 'infinity-voice-card');

  const ttsCard = node('div', 'infinity-voice-card');

  const ttsDownload = node('button', 'infinity-text-link asr-download-link',

    '下载推荐模型 ↓') as HTMLButtonElement;

  ttsDownload.type = 'button';

  const ttsDownloadState = node('p', 'infinity-hint');

  const ttsProgress = document.createElement('progress');

  ttsProgress.className = 'infinity-download-progress'; ttsProgress.max = 1;

  coreSetUIAttribute(ttsProgress, 'aria-label', corePhrase("TTS 模型下载进度")); ttsProgress.hidden = true;

  const snowCard = node('div', 'infinity-voice-card infinity-snow-card');


  const asrSwitch = document.createElement('input');

  asrSwitch.type = 'checkbox';

  const asrModel = document.createElement('select');

  const asrDownload = node('button', 'infinity-text-link asr-download-link',

    '下载推荐模型 ↓') as HTMLButtonElement;

  asrDownload.type = 'button';

  const asrDownloadState = node('p', 'infinity-hint');
  const asrState = node('p', 'infinity-service-state');

  const asrProgress = document.createElement('progress');

  asrProgress.className = 'infinity-download-progress'; asrProgress.max = 1;

  coreSetUIAttribute(asrProgress, 'aria-label', corePhrase("ASR 模型下载进度")); asrProgress.hidden = true;

  const asrLanguage = document.createElement('select');

  asrLanguage.append(new Option('自动识别', 'auto'), new Option('中文', 'zh'),

    new Option('英语', 'en'));

  const asrItn = document.createElement('input');

  asrItn.type = 'checkbox';

  const asrThreads = document.createElement('select');

  asrThreads.append(new Option('均衡 · CPU 2 线程', '2'),

    new Option('性能 · CPU 3 线程', '3'));

  const asrFolder = node('button', 'infinity-action', '打开文件夹') as HTMLButtonElement;

  asrFolder.type = 'button';

  coreSetUIAttribute(asrFolder, 'aria-label', corePhrase("打开 ASR 本地模型文件夹"));

  coreSetUIText(asrFolder, 'title', corePhrase("未选模型时打开 ASR 模型根目录"));

  const ttsSwitch = document.createElement('input');

  ttsSwitch.type = 'checkbox';

  const ttsModel = document.createElement('select');

  const ttsVoice = document.createElement('select');
  const ttsVoiceFolder = node('button', 'infinity-action', '打开文件夹') as HTMLButtonElement;
  ttsVoiceFolder.type = 'button';
  coreSetUIAttribute(ttsVoiceFolder, 'aria-label', corePhrase("打开 TTS 音色文件夹"));
  const voiceFolderTitle = '打开本机自定义音色目录（references），用于管理参考音频';
  ttsVoiceFolder.title = voiceFolderTitle;
  const defaultPreviewText = '你好，这是一段语音试听。让我们听听当前音色的效果。';

  const ttsSendMode = document.createElement('select');

  ttsSendMode.append(new Option('按需发送 · 仅工具调用', 'on_demand'),

    new Option('普通 QQ 回复附带语音', 'reply_with_voice'));

  const ttsSpeed = document.createElement('select');

  ttsSpeed.disabled = true;

  ttsSpeed.append(new Option('1.0 · 当前固定', '1.0'));

  const ttsPreview = node('button', 'infinity-action', '试听当前音色') as HTMLButtonElement;

  ttsPreview.type = 'button';

  const ttsPreviewText = document.createElement('textarea');

  ttsPreviewText.rows = 2;

  ttsPreviewText.maxLength = 200;

  coreSetUIText(ttsPreviewText, 'placeholder', corePhrase("输入 1—200 字试听文字"));

  const ttsHint = node('p', 'infinity-hint');

  const ttsAudio = document.createElement('audio');

  ttsAudio.controls = true;

  ttsAudio.className = 'infinity-voice-preview';

  ttsAudio.hidden = true;

  const ttsFolder = node('button', 'infinity-action', '打开文件夹') as HTMLButtonElement;

  ttsFolder.type = 'button';

  coreSetUIAttribute(ttsFolder, 'aria-label', corePhrase("打开 TTS 本地模型文件夹"));

  coreSetUIText(ttsFolder, 'title', corePhrase("未选模型时打开 TTS 模型根目录"));

  const save = node('button', 'infinity-action', '保存语音设置') as HTMLButtonElement;

  save.type = 'button';

  const asrAuto = document.createElement('input');
  const ttsAuto = document.createElement('input');
  asrAuto.type = ttsAuto.type = 'checkbox';
  const asrProcess = node('button', 'infinity-action qq-primary-action', '启动 ASR') as HTMLButtonElement;
  asrProcess.type = 'button';
  const ttsProvider = document.createElement('select');
  ttsProvider.append(new Option('内置 CosyVoice3', 'builtin'), new Option('外部兼容 TTS', 'external'));
  const servicePort = document.createElement('input');
  servicePort.type = 'number';
  servicePort.min = '1024';
  servicePort.max = '65535';
  const serviceName = document.createElement('input');
  serviceName.placeholder = '可选，用于启动和停止外部服务';
  const serviceField = choice('系统服务名（Linux 用户服务 / Windows 服务）', serviceName);
  ttsProvider.addEventListener('change', () => { serviceField.hidden = ttsProvider.value !== 'external'; });
  const ttsProcess = node('button', 'infinity-action', '启动 TTS') as HTMLButtonElement;

  ttsProcess.type = 'button';

  const snowProcess = node('button', 'infinity-action', '启动 SnowLuma') as HTMLButtonElement;

  snowProcess.type = 'button';

  const ttsState = node('span', 'infinity-service-state');
  const ttsRuntimeIssue = node('p', 'infinity-hint');
  ttsRuntimeIssue.hidden = true;

  const snowUsername = document.createElement('input');

  snowUsername.autocomplete = 'off';

  const snowPassword = document.createElement('input');

  snowPassword.type = 'password'; snowPassword.autocomplete = 'new-password';

  coreSetUIText(snowPassword, 'placeholder', corePhrase("请输入 SnowLuma 管理密码"));

  const snowTarget = document.createElement('input');

  snowTarget.inputMode = 'numeric';

  coreSetUIText(snowTarget, 'placeholder', corePhrase("建议填写；多个 QQ 进程时只连接此账号"));

  const snowSave = node('button', 'infinity-action', '保存设置') as HTMLButtonElement;

  snowSave.type = 'button';

  const snowForget = node('button', 'infinity-action', '移除 Core 保存的管理密码') as HTMLButtonElement;

  snowForget.type = 'button';

  const snowCredential = node('span', 'snow-service-badge');
  const passwordField = choice('SnowLuma 管理密码（用于协议同步）', snowPassword);
  passwordField.append(snowCredential);

  const checkConnection = node('button', 'infinity-action', '刷新连接状态') as HTMLButtonElement;

  checkConnection.type = 'button';

  const importConnection = node('button', 'infinity-action',

    '同步协议配置') as HTMLButtonElement;

  importConnection.type = 'button';

  coreSetUIText(importConnection, 'title', corePhrase("读取此 QQ 的 HTTP / WebSocket 地址与令牌，保存到当前 Bot；不改变会话名单。"));

  const checkResult = node('p', 'infinity-hint');

  checkResult.setAttribute('role', 'status');

  const snowManager = node('a', 'infinity-manage',

    '打开 SnowLuma 管理页 →') as HTMLAnchorElement;

  snowManager.href = '#';

  snowManager.target = '_blank';

  snowManager.rel = 'noopener noreferrer';

  const feedback = node('p', 'infinity-message');
  feedback.setAttribute('role', 'status');
  let feedbackTimer: number | undefined;
  const clearFeedback = (): void => {
    if (feedbackTimer !== undefined) window.clearTimeout(feedbackTimer);
    feedbackTimer = undefined;
    feedback.textContent = '';
  };
  const showFeedback = (message: unknown, transient = true): void => {
    clearFeedback();
    coreSetUIText(feedback, 'textContent', message);
    if (transient && typeof window.setTimeout === 'function') {
      feedbackTimer = window.setTimeout(clearFeedback, 6500);
    }
  };

  const terminalView = createQQTerminal(() => pollLog(true));

  const privateIds = document.createElement('textarea');

  const groupIds = document.createElement('textarea');

  const proactiveTarget = document.createElement('input');

  const backfillMinutes = document.createElement('input');

  const reactionWindow = document.createElement('input');

  reactionWindow.type = 'number';

  reactionWindow.min = '0';

  reactionWindow.max = '10';

  reactionWindow.step = '1';

  const saveRoutes = node('button', 'infinity-action', '保存会话路由') as HTMLButtonElement;

  saveRoutes.type = 'button';

  privateIds.rows = 2;

  groupIds.rows = 2;

  coreSetUIText(privateIds, 'placeholder', corePhrase("每行一个 QQ 号，也可用逗号分隔"));

  coreSetUIText(groupIds, 'placeholder', corePhrase("每行一个群号，也可用逗号分隔"));

  coreSetUIText(proactiveTarget, 'placeholder', corePhrase("主动消息仅发送给主人"));

  backfillMinutes.type = 'number';

  backfillMinutes.min = '0';

  backfillMinutes.max = '30';

  backfillMinutes.step = '1';

  const routeAccount = node('p', 'infinity-hint');

  const routeDestinations = node('section', 'qq-route-group');
  const destinationFields = node('div', 'qq-route-destinations');
  destinationFields.append(choice('允许的私聊 QQ 号', privateIds), choice('允许的群号', groupIds));
  routeDestinations.append(node('h4', '', '允许接收哪些消息'), destinationFields,
    node('p', 'infinity-hint', '填写允许 Bot 接收消息的联系人和群。'));
  const ownerHint = node('p', 'infinity-hint');
  const ownerChoices = document.createElement('datalist');
  ownerChoices.id = 'qq-confirmed-owner-accounts';
  proactiveTarget.setAttribute('list', ownerChoices.id);
  let ownerRouteKey = '';
  const drawOwner = (state: QQState): void => {
    const route = state.routing.ownerRoute;
    if (!route) return;
    const key = JSON.stringify([route, state.routing.proactiveTarget]);
    if (ownerRouteKey !== key) {
      ownerRouteKey = key;
      ownerChoices.replaceChildren(...route.accounts.map(account => {
        const option = document.createElement('option');
        option.value = account;
        return option;
      }));
      proactiveTarget.value = route.target;
      proactiveTarget.readOnly = route.accounts.length <= 1;
    }
    const hint = route.unavailable ? '暂时无法读取身份绑定，主动投递已暂停。'
      : !route.accounts.length ? '在人物画像里，把你的 QQ 画像与本机控制台画像确认为同一个人。'
      : !route.target ? '你已关联多个 QQ，请选择用于接收主动消息的账号并保存会话路由。'
      : !state.routing.privates.includes(route.target) ? '已确认主人的 QQ。将它加入上方允许的私聊名单后即可投递。'
      : '已关联主人的 QQ。是否主动开口、聊什么由 Bot 自己决定。';
    coreSetUIText(ownerHint, 'textContent', corePhrase(hint));
    coreSetUIText(proactiveTarget, 'title', corePhrase(hint));
  };
  const routeBehavior = node('section', 'qq-route-group');
  const behaviorFields = node('div', 'qq-route-behavior');
  behaviorFields.append(choice('主人接收主动消息的 QQ', proactiveTarget),
    choice('断线补读 · 0—30 分钟', backfillMinutes),
    choice('消息反应窗口 · 0—10 秒', reactionWindow));
  routeBehavior.append(node('h4', '', '回复与主动发送'), behaviorFields, ownerChoices, ownerHint,
    node('p', 'infinity-hint', '默认 3 秒，按首条消息计时；窗口内撤回则取消，不叠加聚合等待。'));
  const routeColumns = node('div', 'qq-route-columns');
  routeColumns.append(routeDestinations, routeBehavior);
  const routeBack = node('a', 'infinity-manage', '查看 QQ 连接状态 →') as HTMLAnchorElement;

  routeBack.href = '#/infinity/core-infinity-qq';

  routeBack.addEventListener('click', event => {

    event.preventDefault(); (tabs.children[0] as HTMLButtonElement | undefined)?.click();

  });

  saveRoutes.classList.add('qq-primary-action');
  const routeActions = node('div', 'snow-action-row');routeActions.append(saveRoutes, routeBack);

  routingCard.append(node('h3', '', '会话路由'), routeAccount, routeColumns,


    routeActions);

  const asrSetup = voiceSetup('asr', () => ({ model: asrModel.value }));
  const ttsSetup = voiceSetup('tts', () => ({ model: ttsModel.value, voice: ttsVoice.value }));
  function transferButtons() {
    const group = node('div', 'snow-action-row');
    const pause = node('button', 'infinity-action', '暂停下载') as HTMLButtonElement;
    const cancel = node('button', 'infinity-action', '取消下载') as HTMLButtonElement;
    pause.type = cancel.type = 'button';
    group.append(pause, cancel);
    group.hidden = true;
    return { group, pause, cancel };
  }
  const transfer = { asr: transferButtons(), tts: transferButtons() };
  const asrHeader = infinityVoiceHeading('ASR · 语音识别', asrDownload);

  asrCard.append(asrHeader,

    toggle('接收语音时转写', asrSwitch), toggle('随 Bot 唤醒自动启动 ASR', asrAuto), modelPicker('识别模型', asrModel, asrFolder),

    choice('识别语言', asrLanguage), toggle('数字与标点规范化', asrItn),

    choice('性能档', asrThreads),

    asrState, asrProcess, asrDownloadState, asrProgress, transfer.asr.group, asrSetup.section);

  const ttsHeader = infinityVoiceHeading('TTS · 语音合成', ttsDownload);
  ttsProcess.classList.add('qq-primary-action');

  ttsCard.append(ttsHeader,

    toggle('启用语音发送', ttsSwitch), toggle('随 Bot 唤醒自动启动 TTS', ttsAuto),
    choice('TTS 服务', ttsProvider), choice('本机 TTS 端口', servicePort), serviceField, modelPicker('合成模型', ttsModel, ttsFolder),

    modelPicker('音色', ttsVoice, ttsVoiceFolder), choice('语音发送方式', ttsSendMode),

    choice('语速', ttsSpeed), choice('试听文本', ttsPreviewText),

    ttsHint,

    ttsRuntimeIssue, ttsState, ttsProcess, ttsPreview, ttsAudio, ttsDownloadState, ttsProgress, transfer.tts.group, ttsSetup.section);

  const corpusPanel = voiceCorpusPanel();
  ttsCard.append(corpusPanel);

  const accountFields = node('div', 'snow-fields');

  accountFields.append(choice('目标 QQ 号', snowTarget),

    choice('SnowLuma 管理用户名', snowUsername),

    passwordField);

  const accountActions = node('div', 'snow-action-row');

  accountActions.append(snowSave, snowForget,
    node('p', 'infinity-hint', '更换密码后，在这里输入新密码并保存。移除密码会同时清除 Core 保存的管理登录会话。'));

  const routeLink = node('a', 'infinity-manage', '前往会话路由填写允许名单 →') as HTMLAnchorElement;

  routeLink.href = '#/infinity/core-infinity-qq';

  routeLink.addEventListener('click', event => {

    event.preventDefault(); (tabs.children[1] as HTMLButtonElement | undefined)?.click();

  });

  const commonActions = node('section', 'snow-common-actions');

  coreSetUIAttribute(commonActions, 'aria-label', corePhrase("SnowLuma 常用操作"));

  const serviceActions = node('div', 'snow-service-strip');

  const serviceIdentity = node('div', 'snow-service-identity');

  const serviceStatus = node('span', 'snow-service-badge');

  const controlStatus = node('span', 'snow-service-badge', '控制通道不可用');
  controlStatus.dataset.state = 'off';
  controlStatus.hidden = true;
  serviceIdentity.append(node('span', 'snow-service-name', '协议服务'), serviceStatus, controlStatus);

  const serviceUtilities = node('div', 'snow-service-utilities');

  snowProcess.className += ' snow-service-control';

  const initialPassword = node('button', 'infinity-action', '查看初始管理密码') as HTMLButtonElement;
  initialPassword.type = 'button';
  const passwordResult = node('p', 'infinity-hint');
  passwordResult.setAttribute('role', 'status');
  let passwordTimer: number | undefined;
  let passwordRequest = 0;
  let passwordReadPending = false;
  let qqStatusPending: symbol | undefined;
  function clearInitialPassword() {
    passwordRequest += 1;
    if (passwordTimer !== undefined) window.clearTimeout(passwordTimer);
    passwordTimer = undefined;
    passwordResult.textContent = '';
  }
  initialPassword.addEventListener('click', async () => {
    clearInitialPassword();
    const current = passwordRequest;
    initialPassword.disabled = true;
    passwordReadPending = true;
    try {
      const response = await fetch('/infinity/qq/initial-password', {
        method: 'POST', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-Core-Local-Password': '1' },
        body: '{}',
      });
      const result = await response.json();
      if (root.hidden || current !== passwordRequest) return;
      passwordResult.textContent = response.ok && result.ok
        ? `用户名 admin · 初始密码 ${result.password} · 请登录管理页并修改密码`
        : result.said || '暂时无法读取初始密码。';
      passwordTimer = window.setTimeout(clearInitialPassword, 60000);
    } catch {
      if (!root.hidden && current === passwordRequest) passwordResult.textContent = '暂时无法读取初始密码。';
    } finally {
      passwordReadPending = false;
      initialPassword.disabled = false;
    }
  });
  serviceUtilities.append(snowManager, snowProcess);

  const firstSetup = node('section', 'snow-first-setup');
  const firstSetupToggle = node('button', 'infinity-action', '查看首次部署步骤') as HTMLButtonElement;
  firstSetupToggle.type = 'button';
  firstSetup.hidden = true;
  firstSetupToggle.setAttribute('aria-expanded', 'false');

  const setupActions = node('div', 'snow-setup-actions');
  setupActions.append(initialPassword, passwordResult);
  const checklist = node('ol', 'snow-first-checklist');
  const checks = [
    '登录 SnowLuma 管理页并修改初始密码',
    '在 SnowLuma 管理页加载 QQ，确认绑定账号在线',
    '在会话路由保存至少一个允许的私聊 QQ 号',
  ].map(label => {
    const row = node('li', 'snow-first-step');
    const status = node('span', 'snow-service-badge', '待完成');
    row.append(node('span', '', label), status);
    checklist.append(row);
    return status;
  });
  checklist.children[0].append(setupActions);
  const syncStep = node('li', 'snow-first-step');
  syncStep.append(node('span', '', '确认 Core 已接入 QQ'),
    node('p', 'infinity-hint',
      '如果 SnowLuma 已显示 QQ 在线，但 Core 仍未连接，先在 SnowLuma 确认 OneBot 设置，再回到本页点击「同步协议配置」。已经能正常收发时可跳过。'));
  checklist.append(syncStep);
  firstSetup.append(checklist);
  firstSetup.id = 'snow-first-setup';
  firstSetupToggle.setAttribute('aria-controls', firstSetup.id);
  firstSetup.prepend(node('h3', '', '首次部署步骤'));
  firstSetupToggle.addEventListener('click', () => {
    firstSetup.hidden = !firstSetup.hidden;
    firstSetupToggle.setAttribute('aria-expanded', String(!firstSetup.hidden));
    firstSetupToggle.textContent = firstSetup.hidden ? '查看首次部署步骤' : '收起首次部署步骤';
    if (!firstSetup.hidden) firstSetup.scrollIntoView({block: 'nearest'});
  });


  serviceActions.append(serviceIdentity, serviceUtilities);

  const connectionActions = node('div', 'snow-secondary-actions');

  importConnection.classList.add('qq-primary-action');
  checkConnection.classList.add('qq-quiet-action');
  firstSetupToggle.classList.add('qq-quiet-action');
  initialPassword.classList.add('qq-quiet-action');
  connectionActions.append(importConnection, checkConnection, routeLink);
  const connectionTools = node('div', 'snow-connection-tools');
  connectionTools.append(connectionActions, firstSetupToggle);
  commonActions.append(serviceActions, connectionTools);

  const setupDetails = node('details', 'snow-onboarding compact-disclosure');



  setupDetails.append(node('summary', '', '账号与管理凭据'), accountFields, accountActions);


  const setupHint = node('p', 'infinity-hint');
  setupHint.setAttribute('role', 'status');
  setupHint.hidden = true;
  snowCard.append(commonActions, setupHint, checkResult, firstSetup, setupDetails);

  if (pluginName === 'core-infinity-qq') {

    const stickers = qqStickers();
    // Keep persisted tab IDs stable across display order changes.
    const sections = [
      { label: 'SnowLuma', pane: snowPane, id: 0 },
      { label: '会话路由', pane: routePane, id: 1 },
      { label: '表情包', pane: stickers.root, id: 5 },
      { label: '语音模型', pane: voicePane, id: 2 },
      { label: '终端', pane: terminalPane, id: 3 },
      { label: '工具集', pane: toolbox.root, id: 6 },
      { label: '环境', pane: environmentPane, id: 4 },
    ];
    let tabTouched = false;
    sections.forEach((section, index) => {
      const tab = node('button', 'infinity-tab', section.label) as HTMLButtonElement;
      tab.type = 'button';
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', String(index === 0));
      tab.addEventListener('click', () => {
        clearFeedback();
        tabTouched = true;
        panelScroll.scrollTop = 0;
        writePluginTab('core-infinity-qq', section.id);
        if (section.pane !== snowPane) clearInitialPassword();
        for (const [position, item] of sections.entries()) {
          item.pane.hidden = section !== item;
          tabs.children[position].setAttribute('aria-selected', String(section === item));
        }
        if (section.pane === terminalPane) terminalView.activate(); else terminalView.deactivate();
        if (section.pane === stickers.root) stickers.open();
        if (section.pane === environmentPane) void environmentView.open();
        if (section.pane === toolbox.root) void toolbox.refresh();
      });
      tabs.append(tab);
      section.pane.hidden = index !== 0;
    });
    void readyPluginDisplay().then(() => {
      const saved = readPluginTab('core-infinity-qq');
      const position = sections.findIndex(section => section.id === saved);
      if (!root.hidden && !tabTouched && position >= 0) (tabs.children[position] as HTMLButtonElement).click();
    });

    routePane.append(routingCard);

    save.classList.add('qq-primary-action');
    const voiceActions = node('div', 'qq-voice-save');
    voiceActions.append(save);
    voicePane.append(ttsCard, asrCard, voiceActions);

    snowPane.append(snowCard);

    terminalPane.append(terminalView.root);

    voiceGrid.append(...sections.filter(section => section.pane !== toolbox.root && section.pane !== environmentPane)
      .map(section => section.pane));

  }


  // Native fieldset semantics also prevent editing defaults before the first read.

  const configFields = document.createElement('fieldset');

  configFields.style.display = 'contents';

  configFields.disabled = true;

  configFields.append(voiceGrid);
  if (knownConfig) configPresentation(voiceGrid, 'loading');

  panelScroll.append(configFields, ...(knownConfig ? [toolbox.root, environmentPane] : []));
  qqPanel.append(node('h2', 'qq-section-title', 'QQ 接入与消息设置'),
    health, tabs, panelScroll, feedback);

  qqPanel.hidden = !knownConfig;

  summary.hidden = pluginName !== '';

  cards.hidden = pluginName !== '';

  save.hidden = pluginName !== 'core-infinity-qq';


  if (knownConfig) root.append(head, message, qqPanel);
  else root.append(head, summary, cards, message, qqPanel);

  const pageFooter = node('footer', 'page-footer qq-page-footer');
  const footerScope = node('span', 'page-footer-summary', '正在读取接收范围…');
  pageFooter.append(node('span', 'page-footer-label', 'QQ 连接 · OneBot v11'), footerScope);
  if (knownConfig) root.append(pageFooter);
  shell.content.replaceChildren(root);



  let requestId = 0;

  let currentQQ: QQState | null = null;
  let displayedTarget = '';

  let hasPlugin = false;

  let pluginLoaded = false;

  let pollTimer: number | undefined;

  let mutationPending = false;

  let previewPending = false;
  let previewController: AbortController | undefined;



  let pluginsDrawn = false;

  let pluginsFailed = false;

  let pluginMissing = false;

  let qqLoaded = false;

  let qqFailed = false;
  let formFilled = false;
  let checkPending = false;
  let importPending = false;

  let asrDownloadPending = false;

  let ttsDownloadPending = false;
  const downloadErrors = { asr: '', tts: '' };

  const commandButtons: HTMLButtonElement[] = [save, saveRoutes, asrProcess, ttsProcess, ttsPreview,

    snowProcess, snowSave, snowForget, importConnection, checkConnection,

    asrDownload, ttsDownload, asrFolder, ttsFolder, ttsVoiceFolder];

  const lockCommands = (): void => {

    configFields.disabled = true;

    for (const item of commandButtons) item.disabled = true;

  };

  const renderWaitingHealth = (failed: boolean): void => {

    if (currentQQ !== null) return;

    health.replaceChildren();

    const badge = node('span', 'infinity-health-item',

      failed ? 'QQ 状态 · 读取失败 · 自动重试' : 'QQ 状态 · 读取中…');

    badge.dataset.state = 'warn';

    health.append(badge);

  };

  const syncNotice = (): void => {

    liveNotice.hidden = false;

    if (mutationPending) { coreSetUIText(liveNotice, 'textContent', corePhrase("正在处理…")); return; }

    if (qqLoaded && qqFailed) {

      coreSetUIText(liveNotice, 'textContent', corePhrase("QQ 状态更新暂不可用 · 自动重试"));

      return;

    }

    if (!qqLoaded && knownConfig) {

      coreSetUIText(liveNotice, 'textContent', (qqFailed ? corePhrase("QQ 状态读取失败 · 自动重试") : corePhrase("正在读取 QQ 状态…")));

      return;

    }

    if (pluginsDrawn && pluginsFailed) {

      coreSetUIText(liveNotice, 'textContent', corePhrase("插件列表刷新失败 · 自动重试"));

      return;

    }

    if (!pluginsDrawn) {

      coreSetUIText(liveNotice, 'textContent', (pluginsFailed ? corePhrase("插件列表读取失败 · 自动重试") : corePhrase("正在读取插件列表…")));

      return;

    }

    liveNotice.textContent = '';

    liveNotice.hidden = true;

  };

  if (knownConfig) {

    lockCommands();

    renderWaitingHealth(false);

    showFeedback(corePhrase("正在读取 QQ 配置；数据就绪前不能保存、试听或启停进程。"));

    coreSetUIText(message, 'textContent', corePhrase("正在读取插件状态…"));

  } else {

    coreSetUIText(message, 'textContent', corePhrase("正在读取插件列表…"));

  }

  const updateLive = (state: QQState): void => {
    footerScope.textContent = `接收范围：私聊 ${state.routing.privates.length} 个 · 群聊 ${state.routing.groups.length} 个`;


    configFields.disabled = mutationPending || pluginMissing;

    currentQQ = state;

    // Commands stay disabled until the first successful read has filled the

    // form (and while a mutation is in flight).

    const commandsLocked = mutationPending || !formFilled ||
      state.snowluma.controlAvailable === false;
    // Protocol identity/WS readiness and recovery-worker retirement are distinct.
    // Keep mutation locks, but never label a verified live connection as connecting.
    const connectionReady = freshProtocolSample(state.snowluma)
      && state.snowluma.protocolHealth?.pluginConnected === true;
    const recoveryBusy = !!state.snowluma.recovery?.busy;
    if (!recoveryBusy && connectionReady && !mutationPending
      && /^(恢复检测已提交；|加载确认已提交；)/.test(feedback.textContent || '')) {
      showFeedback(corePhrase("接入已完成：目标 QQ 在线、插件 WS 已连接。真实消息收发仍待验收。"));
    }

    for (const kind of ['tts', 'asr'] as const) {
      const isTts = kind === 'tts';
      const button = isTts ? ttsDownload : asrDownload;
      const status = isTts ? ttsDownloadState : asrDownloadState;
      const progress = isTts ? ttsProgress : asrProgress;
      const pending = isTts ? ttsDownloadPending : asrDownloadPending;
      const voice = state[kind];
      const install = voice.install;
      const recommended = isTts ? 'Fun-CosyVoice3-0.5B-2512' : 'SenseVoiceSmall-onnx';
      const installed = voice.models.includes(recommended);
      const failed = install?.phase === 'error' || install?.phase === 'cleanup_failed';
      const busy = install?.busy === true;
      const complete = installed && install?.runtimeReady !== false && !voice.runtimeIssue;
      button.hidden = complete && !busy;
      button.disabled = mutationPending || !formFilled || pending || !install || busy || complete;
      const paused = install?.phase === 'paused';
      const partial = !complete && (paused || (failed && (install?.downloaded || 0) > 0));
      const changing = ['pausing', 'cancelling', 'publishing'].includes(install?.phase || '');
      const controls = transfer[kind];
      controls.group.hidden = !install?.controls || !(busy || partial);
      controls.pause.hidden = !busy;
      controls.pause.disabled = controls.cancel.disabled = pending || changing || mutationPending || !formFilled;
      button.textContent = partial ? '继续安装' : installed ? '补全运行环境' : '下载推荐模型 ↓';
      progress.hidden = !(busy || partial);
      progress.value = Math.min(1, (install?.downloaded || 0) / (install?.total || 1));
      if (busy && ['environment', 'selfcheck'].includes(install?.phase || '')) {
        progress.hidden = true;
        status.textContent = install?.message || '正在准备语音环境…';
      } else if (busy) {
        const phase = ({verify:'正在校验', publishing:'正在安装', pausing:'正在暂停', cancelling:'正在取消'} as Record<string,string>)[install.phase || ''] || '正在下载';
        const source = install.source === 'mirror' ? '国内镜像 · 直连'
          : install.route === 'proxy' ? '官方源 · 代理' : install.route === 'direct' ? '官方源 · 直连' : '官方源';
        const rate = install.bytesPerSecond || 0;
        const speed = rate >= 1048576 ? (rate/1048576).toFixed(2)+' MiB/s' : (rate/1024).toFixed(0)+' KiB/s';
        status.textContent = `${phase} · ${source} · ${(progress.value*100).toFixed(1)}% · ${(install.downloaded/1048576).toFixed(1)} MiB` + (rate ? ' · '+speed : '');
      } else if (paused || install?.phase === 'cancelled') {
        status.textContent = paused ? '已暂停 · 已保留 '+(install.downloaded/1048576).toFixed(1)+' MiB' : '已取消下载';
      } else if (pending) {
        status.textContent = '正在准备下载…';
      } else if (complete) {
        status.textContent = install?.runtimeReady ? '模型与环境已就绪 · 自检通过' : '推荐模型已安装';
      } else if (downloadErrors[kind] || failed) {
        status.textContent = downloadErrors[kind] || install?.message || '下载失败，可以重试。';
      } else {
        status.textContent = installed ? '模型已下载，请补全运行环境。' : !install ? '当前 Core 未提供模型下载。'
          : '推荐模型未安装';
      }
      status.dataset.state = !busy && !pending && !complete && (failed || downloadErrors[kind])
        ? 'warn' : 'neutral';
    }

    const ttsNames = Array.from(ttsModel.options).map(option => option.value).filter(Boolean);

    if (JSON.stringify(ttsNames) !== JSON.stringify(state.tts.models)) {

      const selected = ttsModel.value || state.tts.model;

      fillSelect(ttsModel, state.tts.models, selected, true);

    }

    const asrNames = Array.from(asrModel.options).map(option => option.value).filter(Boolean);
    if (JSON.stringify(asrNames) !== JSON.stringify(state.asr.models)) {
      fillSelect(asrModel, state.asr.models, asrModel.value || state.asr.model, false);
    }

    coreSetUIText(routeAccount, 'textContent', (state.snowluma.recovery?.targetUin ? [corePhrase("当前 Bot 的 QQ："), state.snowluma.recovery.targetUin, corePhrase(" · 名单只作用于这个 Bot。")] : corePhrase("尚未选择登录 QQ；可先保存会话名单，再到 SnowLuma 页完成账号接入。")));

snowProcess.hidden = false;

    const serviceLabel = state.snowluma.managed ? '已启动'
      : state.snowluma.sharedService ? '共用服务'
      : state.snowluma.webuiRunning ? '端口可达' : '未启动';
    if (uiSource(serviceStatus) !== serviceLabel) {
      coreSetUIText(serviceStatus, 'textContent', corePhrase(serviceLabel));
    }
    const serviceColor = state.snowluma.webuiRunning ? 'on' : 'off';
    if (serviceStatus.dataset.state !== serviceColor) serviceStatus.dataset.state = serviceColor;
    controlStatus.hidden = state.snowluma.controlAvailable !== false;
    controlStatus.title = state.snowluma.controlMessage || '';

    snowProcess.dataset.mode = state.snowluma.sharedService ? 'shared'

      : state.snowluma.managed || state.snowluma.canStopInstallation ? 'stop' : 'start';

    const passwordChanged = state.snowluma.managementPasswordChanged;
    const accountOnline = state.snowluma.localAccountOnline === true || freshProtocolSample(state.snowluma) || freshSnowSample(state.snowluma);
    const progress = [passwordChanged, accountOnline,
      state.routing.privates.length > 0];
    checks.forEach((badge, index) => {
      const value = progress[index];
      const label = value === true ? '已完成' : value === false ? '待完成' : '待检查';
      if (badge.textContent !== label) badge.textContent = label;
      badge.dataset.state = value === true ? 'on' : 'off';
    });
    const installation = state.snowluma.installation;
    let guidance = '';
    if (installation?.ready === false) {
      guidance = '请从 Core 启动器完成 SnowLuma 运行环境自检。';
    } else if (state.snowluma.webuiRunning && !connectionReady && !accountOnline
      && state.snowluma.waitingForDesktopExit === true) {
      guidance = '当前 QQ 需要正常退出一次。Core 正在等待桌面 QQ 退出，连接状态会自动更新。';
    } else if (state.snowluma.webuiRunning && !state.snowluma.recovery?.targetUin) {
      guidance = '在 SnowLuma 管理页选择已登录的 QQ 并加载。识别成功后，Core 会保存目标 QQ。';
    } else if (state.snowluma.webuiRunning && !connectionReady) {
      const protocol = state.snowluma.protocolHealth;
      const savedRoutes = state.routing.privates.length > 0 || state.routing.groups.length > 0;
      if (accountOnline && !savedRoutes) {
        guidance = 'QQ 已在线，请先到会话路由保存允许名单，再刷新连接状态。';
      } else if ((accountOnline && !state.plugin?.configured)
        || (protocol?.fresh && protocol.status === 'auth_failed')) {
        guidance = '请确认 SnowLuma 的 OneBot 地址和令牌，再点击「同步协议配置」。';
      } else if (state.snowluma.localAccountOnline !== undefined && state.plugin?.running
        && state.plugin.configured && !protocol?.pluginConnected
        && ['checking', 'untrusted', 'unavailable', 'offline', 'online'].includes(protocol?.status ?? '')) {
        const waiting = 'Core 正在等待 QQ 事件连接。已配置的 OneBot 服务就绪后会自动重连。';
        const hookEvents = (state.snowluma.log ?? '').split('\n').filter(line => line.includes('[Hook]'));
        guidance = !accountOnline && hookEvents.at(-1)?.includes('[COMPONENT_LOAD_FAILED]')
          ? '最近一次组件加载失败。' + waiting : waiting;
      }
    }
    setupHint.hidden = !guidance;
    if (setupHint.textContent !== guidance) setupHint.textContent = guidance;

    snowSave.disabled = commandsLocked || !state.snowluma.recovery

      || !!state.snowluma.recovery.busy;

    snowForget.disabled = commandsLocked || !state.snowluma.recovery?.hasCredentials

      || !!state.snowluma.recovery?.busy;

    const credentialsSaved = state.snowluma.recovery?.hasCredentials === true;
    snowCredential.dataset.state = credentialsSaved || passwordChanged ? 'on' : 'off';
    coreSetUIText(snowCredential, 'textContent', credentialsSaved
      ? corePhrase("管理密码已保存") : (passwordChanged ? corePhrase("SL 已改密 · Core 尚未保存管理凭据") : state.snowluma.localAccountOnline ? corePhrase("本机协议可同步；管理密码按需保存") : corePhrase("管理密码未保存")));
    coreSetUIText(snowPassword, 'placeholder', credentialsSaved
      ? corePhrase("已设置；留空保持原值") : passwordChanged ? corePhrase("SL 已改密，按需填写以保存到 Core") : corePhrase("请输入 SnowLuma 管理密码"));

    snowManager.hidden = false;
    const managerUrl = state.snowluma.managementUrl || '';
    const managerAddress = state.snowluma.webuiRunning && /^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(managerUrl)
      ? `${managerUrl}/processes` : '#';
    if (snowManager.getAttribute('href') !== managerAddress) snowManager.href = managerAddress;
    snowManager.setAttribute('aria-disabled', String(managerAddress === '#'));
    coreSetUIText(snowManager, 'title', corePhrase("打开本机 SnowLuma 管理页"));

    coreSetUIText(snowProcess, 'textContent', (state.snowluma.managed ? corePhrase("停止 SnowLuma") : (state.snowluma.sharedService ? corePhrase("共享 SnowLuma 运行中") : (state.snowluma.canStopInstallation ? corePhrase("停止此安装的 SnowLuma") : corePhrase("启动 SnowLuma")))));

    snowProcess.disabled = commandsLocked || state.snowluma.sharedService === true

      || ((state.snowluma.transportReachable || state.snowluma.webuiRunning)

        && !state.snowluma.managed && !state.snowluma.canStopInstallation);

    coreSetUIText(snowProcess, 'title', (state.snowluma.sharedService ? corePhrase("共享服务由其他实例管理；本页可恢复自己的 QQ，不允许停止其他 Bot 的协议服务。") : (state.snowluma.managed || state.snowluma.canStopInstallation ? corePhrase("停止整个 SnowLuma 服务会断开使用它的所有 QQ 账号。") : corePhrase("启动 SnowLuma 并尝试恢复当前账号；不会跳过扫码或二次验证。"))));

    if (state.snowluma.recovery?.busy) {

      coreSetUIText(snowProcess, 'textContent', (connectionReady ? corePhrase("已连接，正在收尾…") : corePhrase("正在验证并接入…")));

      snowProcess.disabled = true;

    }

    save.disabled = commandsLocked;

    saveRoutes.disabled = commandsLocked;

    importConnection.disabled = commandsLocked || importPending;

    checkConnection.disabled = commandsLocked || checkPending;


    asrFolder.disabled = commandsLocked;

    ttsFolder.disabled = commandsLocked;
    ttsVoiceFolder.disabled = commandsLocked || !state.tts.voiceFolderAvailable;
    coreSetUIText(ttsVoiceFolder, 'title', (state.tts.voiceFolderAvailable ? voiceFolderTitle : (state.tts.voiceFolderAvailable === false ? corePhrase("请先安装本机 TTS 环境") : corePhrase("重启 Core 后可打开本机音色文件夹"))));

    health.replaceChildren();

    const voiceBlocked = state.tts.pluginEnabled === false;
    const markers = qqSignals(state, pluginLoaded);
    const pluginEnabled = state.tts.pluginEnabled;
    if (typeof pluginEnabled === 'boolean') {
      const pluginStatus = node('span', 'qq-plugin-status');
      const badge = node('span', 'infinity-health-item qq-plugin-state',
        pluginEnabled ? 'QQ 插件 · 已启用' : 'QQ 插件 · 未启用');
      badge.dataset.state = pluginEnabled ? 'on' : 'warn';
      pluginStatus.append(badge);
      if (!pluginEnabled) {
        const link = node('a', 'qq-plugin-enable-link', '前往插件市场 →');
        link.href = '#/plugin';
        pluginStatus.append(link);
      }
      health.append(pluginStatus);
      markers.shift();
    }
    for (const { state: status, label } of markers) {
      const badge = node('span', 'infinity-health-item', label);
      badge.dataset.state = status;
      health.append(badge);
    }

    coreSetUIText(asrState, 'textContent', corePhrase(voiceBlocked
      ? 'QQ 插件已停用 · 语音设置已保留'
      : state.asr.startError || (state.asr.running ? 'ASR 模型已加载' : 'ASR 已停止')));
    const ttsStatus = voiceBlocked ? 'QQ 插件已停用 · 语音设置已保留'
      : state.tts.startError || (state.tts.running
        ? (state.tts.managed ? 'TTS 服务已就绪' : '外部 TTS 服务已就绪')
        : state.tts.managed ? '正在加载 TTS 模型…' : 'TTS 服务未启动');
    coreSetUIText(ttsState, 'textContent', corePhrase(ttsStatus));

    asrProcess.textContent = state.asr.running ? '停止 ASR' : '启动 ASR';
    asrProcess.disabled = commandsLocked || (voiceBlocked && !state.asr.running);
    coreSetUIText(ttsProcess, 'textContent', (state.tts.managed || state.tts.running ? corePhrase("停止 TTS") : corePhrase("启动 TTS")));

    asrSetup.status(voiceBlocked ? '启用 QQ 插件后才能运行自检。' : state.asr.runtimeIssue || '', voiceBlocked);
    ttsSetup.status(voiceBlocked ? '启用 QQ 插件后才能运行自检。' : '', voiceBlocked);
    const runtimeIssue = state.tts.running || state.tts.managed ? '' : state.tts.runtimeIssue || '';
    ttsRuntimeIssue.hidden = !runtimeIssue;
    coreSetUIText(ttsRuntimeIssue, 'textContent', corePhrase(runtimeIssue));
    ttsProcess.disabled = commandsLocked || (voiceBlocked && !state.tts.managed)
      || (state.tts.running && !state.tts.managed && !(state.tts.provider === 'external' && state.tts.service));
    ttsProcess.title = state.tts.running && !state.tts.managed && !(state.tts.provider === 'external' && state.tts.service)
      ? '如需从 Core 停止外部 TTS，请选择外部服务并保存其系统服务名' : '';

    ttsPreview.disabled = commandsLocked || voiceBlocked || previewPending || !state.tts.running

      || !state.tts.voices;


  };

  const fillSelect = (select: HTMLSelectElement, names: readonly string[], selected: string,

    blank: boolean): void => {

    select.replaceChildren();

    if (blank) {

      const empty = document.createElement('option');

      empty.value = '';

      coreSetUIText(empty, 'textContent', corePhrase("暂不选择"));

      select.append(empty);

    }

    for (const name of names) {

      const option = document.createElement('option');

      option.value = name;

      option.textContent = name;

      select.append(option);

    }

    select.value = selected;

  };

  /** Authoritative first-success fill. Polls never call this again. */

  const fillQQForm = (state: QQState): void => {

    privateIds.value = state.routing.privates.join('\n');

    groupIds.value = state.routing.groups.join('\n');

    proactiveTarget.value = state.routing.proactiveTarget;
    proactiveTarget.readOnly = Boolean(state.routing.sharingOwner && state.routing.sharingEnabled);
    coreSetUIText(proactiveTarget, 'title', (proactiveTarget.readOnly ? corePhrase("主动分享只发送给主人，收件人不可在这里更改") : corePhrase("未启用主人分享")));

    backfillMinutes.value = String(state.routing.historyBackfillMinutes);

    reactionWindow.value = String(state.routing.reactionWindowSeconds ?? 3);

    snowUsername.value = state.snowluma.recovery?.username || 'admin';

    snowTarget.value = state.snowluma.recovery?.targetUin || '';

    asrSwitch.checked = state.asr.enabled && state.asr.setupReady !== false;

    asrLanguage.value = state.asr.language || 'auto';

    asrLanguage.disabled = !state.asr.language;

    asrItn.checked = state.asr.useItn ?? true;

    asrItn.disabled = state.asr.useItn === undefined;

    asrThreads.value = String(state.asr.threads ?? 3);

    asrThreads.disabled = state.asr.threads === undefined;

    ttsSwitch.checked = state.tts.enabled;
    asrAuto.checked = state.asr.autoStart ?? state.asr.enabled;
    ttsAuto.checked = state.tts.autoStart ?? state.tts.enabled;
    servicePort.value = String(state.tts.port ?? 18084);
    ttsProvider.value = state.tts.provider ?? 'builtin';
    serviceName.value = state.tts.service || '';
    serviceField.hidden = ttsProvider.value !== 'external';

    fillSelect(asrModel, state.asr.models, state.asr.model, false);

    fillSelect(ttsModel, state.tts.models, state.tts.model, true);

    ttsVoice.replaceChildren();

    for (const voice of state.tts.voices || [{ id: 'default', label: '默认音色 · 通用' }]) {

      // Compatibility with a Core process started before filename labels were introduced.
      const label = voice.id === 'telyin' && ['特琳 · 仅本机', '特琳（本机）'].includes(voice.label)
        ? 'Telyin.wav' : voice.label;
      ttsVoice.append(new Option(label, voice.id));

    }

    ttsVoice.value = state.tts.voice || 'default';

    ttsVoice.disabled = !state.tts.voices;

    ttsSendMode.value = state.tts.sendMode || 'on_demand';

    ttsSendMode.disabled = !state.tts.sendMode;

    ttsSpeed.value = '1.0';

    if (!ttsPreviewText.value) {
      const text = state.tts.previewText;
      ttsPreviewText.value = !text || text === '你好，我是特琳。欢迎来听一段语音。'
        ? defaultPreviewText : text;
    }

    ttsPreviewText.disabled = !state.tts.previewText;

    coreSetUIText(ttsHint, 'textContent', corePhrase("按需发送由 Bot 调用。选择回复附语音时，先发文字，再发语音。"));

  };

  /** Status-only redraw: never rewrites an unsaved form. */

  const drawQQ = (state: QQState): void => {
    drawOwner(state);
    environmentView.update(state.environment);
    terminalView.update(state.snowluma.log);

    const boundTarget = state.snowluma.recovery?.targetUin || '';
    if (snowTarget.value === displayedTarget) snowTarget.value = boundTarget;
    displayedTarget = boundTarget;

    currentQQ = state;

    if (knownConfig) qqPanel.hidden = pluginMissing;

    updateLive(state);


  };

  const draw = (answer: PluginsAnswer | null): void => {

    summary.replaceChildren();

    cards.replaceChildren();

    message.textContent = '';

    if (answer === null) {

      hasPlugin = false;

      coreSetUIText(message, 'textContent', (knownConfig ? corePhrase("扩展状态暂时读不到，正在重试…") : corePhrase("暂时读不到扩展状态。")));

      if (knownConfig) qqPanel.hidden = false;

      return;

    }

    if (!answer.wired) {

      hasPlugin = false;

      pluginLoaded = false;

      pluginMissing = true;

      coreSetUIText(message, 'textContent', corePhrase("扩展状态尚未接入。"));

      if (knownConfig) qqPanel.hidden = true;

      return;

    }

    const plugins = answer.plugins ?? [];

    const selected = plugins.find((one) => one.name === pluginName

      && one.installed !== false);

    hasPlugin = selected !== undefined;

    pluginMissing = !hasPlugin;

    pluginLoaded = selected?.loaded === true;

    if (pluginName) {

      qqPanel.hidden = !knownConfig || pluginMissing;

      if (!knownConfig) coreSetUIText(heading, 'textContent', (selected ? [corePhrase(""), selected.displayName || selected.name, corePhrase(" · 配置")] : corePhrase("插件配置")));

      coreSetUIText(message, 'textContent', (selected ? (knownConfig ? '' : selected.description || '') : corePhrase("这份插件尚未安装。")));

      if (selected && !knownConfig) coreSetUIText(message, 'textContent', corePhrase("此插件暂未提供网页配置项。"));

      if (currentQQ) updateLive(currentQQ);

      return;

    }

    qqPanel.hidden = true;

    const installed = plugins.filter((one) => one.installed !== false);

    const metrics: [string, number][] = [

      ['运行中', installed.filter((one) => one.loaded).length],

      ['已安装', installed.length],

      ['可安装', plugins.filter((one) => one.installed === false).length],

    ];

    for (const [label, count] of metrics) {

      const metric = node('div', 'infinity-metric');

      metric.append(node('strong', '', String(count)), node('span', '', label));

      summary.append(metric);

    }

    if (plugins.length === 0) {

      coreSetUIText(message, 'textContent', corePhrase("目前没有扩展。"));

    }

    for (const plugin of plugins) {

      const card = node('article', 'infinity-card');

      const title = node('div', 'infinity-card-title');

      title.append(node('h2', '', plugin.displayName || plugin.name));

      const state = stateOf(plugin);

      const badge = node('span', 'infinity-state', state);

      badge.dataset.state = plugin.loaded ? 'running'

        : plugin.installed === false ? 'available' : 'idle';

      title.append(badge);

      card.append(title);

      const meta = node('p', 'infinity-card-meta',

        [plugin.name, plugin.version ? `v${plugin.version}` : '', plugin.builtin ? '内置' : '']

          .filter(Boolean).join(' · '));

      card.append(meta);

      if (plugin.description) {

        card.append(node('p', 'infinity-card-description', plugin.description));

      }

      if (plugin.contract) card.append(node('p', 'infinity-card-contract', plugin.contract));

      const installedHere = plugin.installed !== false;

      const manage = node('a', 'infinity-manage', installedHere

        ? '打开插件页面 →' : '前往插件市场 →') as HTMLAnchorElement;

      manage.href = installedHere

        ? `#/infinity/${encodeURIComponent(plugin.name)}` : '#/plugin';

      coreSetUIText(manage, 'title', (installedHere ? [corePhrase("打开 "), plugin.displayName || plugin.name, corePhrase(" 的页面")] : [corePhrase("在插件市场查看 "), plugin.displayName || plugin.name, corePhrase("")]));

      card.append(manage);

      cards.append(card);

    }

  };



  let lastPluginsView = "";
  const loadPlugins = async (mine: number): Promise<void> => {

    try {

      const answer = await getStatusJson<PluginsAnswer>('/plugins', 6000);

      if (mine !== requestId || root.hidden || mutationPending) return;

      pluginsFailed = false;

      const view = JSON.stringify(answer);
      if (!pluginsDrawn || view !== lastPluginsView) {
        draw(answer);
        lastPluginsView = view;
      }
      pluginsDrawn = true;

    } catch (error) {

      if (error instanceof Error && error.name === 'StaleStatusError') return;

      if (mine !== requestId || root.hidden || mutationPending) return;

      pluginsFailed = true;

      // Keep an already drawn list on a later failure; report via the notice.

      if (!pluginsDrawn) draw(null);

    } finally {

      if (mine === requestId && !root.hidden) syncNotice();

    }

  };

  const loadQQ = async (mine: number, manual = false): Promise<void> => {

    if (!knownConfig || passwordReadPending || qqStatusPending) return;
    const statusRequest = Symbol();
    qqStatusPending = statusRequest;

    try {

      const state = await getStatusJson<QQState>('/infinity/qq', 8000);

      if (mine !== requestId || root.hidden || mutationPending) return;

      const first = !formFilled;

      currentQQ = state;

      qqLoaded = true;

      qqFailed = false;

      if (knownConfig) qqPanel.hidden = pluginMissing;

      if (first) {

        fillQQForm(state);

        formFilled = true;
        configPresentation(voiceGrid, 'ready');

        if (uiSource(feedback).startsWith('正在读取 QQ 配置')

          || uiSource(feedback).startsWith('QQ 状态读取失败')) feedback.textContent = '';

      }

      drawQQ(state);

      if (manual) terminalView.update(state.snowluma.log, true);

    } catch (error) {

      if (error instanceof Error && error.name === 'StaleStatusError') return;

      if (mine !== requestId || root.hidden) return;

      qqFailed = true;
      terminalView.unavailable();
      environmentView.unavailable();

      if (currentQQ === null) {

        renderWaitingHealth(true);

        if (!formFilled) {
          configPresentation(voiceGrid, 'error');

          showFeedback(corePhrase("QQ 状态读取失败，正在自动重试；数据就绪前不能保存。"));

        }

      }

    } finally {
      if (qqStatusPending === statusRequest) qqStatusPending = undefined;

      if (mine === requestId && !root.hidden) syncNotice();

    }

  };

  const load = async (): Promise<void> => {

    const mine = ++requestId;

    if (knownConfig) {

      qqPanel.hidden = false;

      lockCommands();

      renderWaitingHealth(false);

      syncNotice();

    }

    coreSetUIText(message, 'textContent', (knownConfig ? corePhrase("正在读取插件状态…") : corePhrase("正在读取插件列表…")));

    // Independent reads: a slow /infinity/qq never delays plugin metadata.

    void loadPlugins(mine);

    void loadQQ(mine);

  };

  const pollLog = (manual = false): void => {

    if (mutationPending || root.hidden

      || (!manual && document.visibilityState === 'hidden')) return;

    const mine = requestId;

    void loadPlugins(mine);

    void loadQQ(mine, manual);

  };



  checkConnection.addEventListener('click', () => {

    if (checkConnection.disabled || checkPending || mutationPending

      || currentQQ === null) return;

    const mine = requestId;

    checkPending = true;

    checkConnection.disabled = true;

    coreSetUIText(checkResult, 'textContent', corePhrase("正在检测已保存的接入设置…"));

    void (async () => {

      try {

        const response = await fetch('/infinity/qq/check', {

          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',

        });

        const answer = await response.json() as { message?: string };

        if (mine === requestId && !root.hidden) {

          coreSetUIText(checkResult, 'textContent', (response.ok && answer.message ? answer.message : corePhrase("检测暂不可用。")));

        }

      } catch {

        if (mine === requestId && !root.hidden) {

          coreSetUIText(checkResult, 'textContent', corePhrase("检测请求失败，请稍后重试。"));

        }

      } finally {

        checkPending = false;

        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);

        else checkConnection.disabled = false;

      }

    })();

  });

  importConnection.addEventListener('click', async () => {

    if (importConnection.disabled || importPending || mutationPending

      || currentQQ === null || root.hidden) return;

    if (!await confirmAction('同步协议配置',

      `读取 QQ ${currentQQ.snowluma.recovery?.targetUin || '（未选择）'} 的 HTTP／WebSocket 地址和令牌，保存到当前 Bot。`

      + '会话名单与语音设置保留；已启用的 QQ 插件将重载。', '确认同步')) return;

    if (root.hidden || mutationPending || currentQQ === null) return;

    const mine = requestId;

    importPending = true;

    mutationPending = true;

    lockCommands();

    coreSetUIText(checkResult, 'textContent', corePhrase("正在读取目标账号配置…"));

    try {

      const response = await fetch('/infinity/qq/import-connection', {

        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',

      });

      const answer = await response.json() as { message?: string; said?: string };

      if (mine !== requestId || root.hidden) return;

      checkResult.textContent = response.ok ? answer.message || '导入完成。'

        : answer.said || '导入失败，原配置已保留。';

      invalidateStatuses(['/infinity/qq', '/plugins']);
      qqStatusPending = undefined;

      window.dispatchEvent(new Event('plugins-changed'));

    } catch {

      if (mine === requestId && !root.hidden) {

        coreSetUIText(checkResult, 'textContent', corePhrase("导入结果未确认，请检测接入后再操作。"));

      }

    } finally {

      importPending = false;

      mutationPending = false;

      if (mine === requestId && !root.hidden) {

        if (currentQQ) updateLive(currentQQ);

        void loadPlugins(mine);

        void loadQQ(mine);

      }

    }

  });

  const openModelFolder = async (kind: 'tts' | 'asr', model: string,

    button: HTMLButtonElement): Promise<void> => {

    if (button.disabled || currentQQ === null) return;

    const mine = requestId;

    button.disabled = true;

    showFeedback(corePhrase("正在打开模型文件夹…"));

    try {

      const response = await fetch('/infinity/qq/model-folder', {

        method: 'POST', headers: { 'Content-Type': 'application/json' },

        body: JSON.stringify({ kind, model }),

      });

      const answer = await response.json() as { ok?: boolean; said?: string };

      if (!response.ok || !answer.ok) {

        throw new Error(answer.said || `打开失败（${response.status}）`);

      }

      if (mine === requestId && !root.hidden) {

        showFeedback([corePhrase("已打开 "), kind.toUpperCase(), corePhrase(" 模型文件夹。")]);

      }

    } catch (error) {

      if (mine === requestId && !root.hidden) {

        showFeedback((error instanceof Error ? error.message : corePhrase("无法打开模型文件夹。")));

      }

    } finally {

      if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);

    }

  };

  ttsVoiceFolder.addEventListener('click', () => {
    if (ttsVoiceFolder.disabled || mutationPending || !currentQQ?.tts.voiceFolderAvailable ||
      root.hidden) return;
    const mine = requestId;
    ttsVoiceFolder.disabled = true;
    showFeedback(corePhrase("正在打开音色文件夹…"));
    void (async () => {
      try {
        const response = await fetch('/infinity/qq/voice-folder', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const answer = await response.json() as { ok?: boolean; said?: string };
        if (!response.ok || !answer.ok) throw new Error(answer.said || `打开失败（${response.status}）`);
        if (mine === requestId && !root.hidden) showFeedback(corePhrase("已打开本机音色目录（references）。"));
      } catch (error) {
        if (mine === requestId && !root.hidden) {
          showFeedback((error instanceof Error ? error.message : corePhrase("无法打开音色文件夹。")));
        }
      } finally {
        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
      }
    })();
  });
  ttsFolder.addEventListener('click', () => {

    void openModelFolder('tts', ttsModel.value, ttsFolder);

  });

  for (const kind of ['tts', 'asr'] as const) {
    const isTts = kind === 'tts';
    const button = isTts ? ttsDownload : asrDownload;
    const setPending = (value: boolean): void => {
      if (isTts) ttsDownloadPending = value;
      else asrDownloadPending = value;
    };
    for (const action of ['pause', 'cancel'] as const) {
      const control = transfer[kind][action];
      control.addEventListener('click', async () => {
        if (control.disabled || !currentQQ) return;
        const mine = requestId;
        if (action === 'cancel' && !await confirmAction('取消模型下载', '将停止本次安装并清理未完成的模型下载。已安装的模型和依赖会保留。', '取消下载')) return;
        if (mine !== requestId || root.hidden || !currentQQ) return;
        setPending(true);
        updateLive(currentQQ);
        try {
          const response = await fetch(`/infinity/qq/${kind}-install`, { method:'POST',
            headers:{'Content-Type':'application/json'}, body:JSON.stringify({action}) });
          const install = await response.json();
          if (!response.ok || install.ok === false) throw new Error(install.said || '下载操作失败');
          if (mine === requestId && !root.hidden && currentQQ) {
            currentQQ = {...currentQQ, [kind]: {...currentQQ[kind], install}};
            invalidateStatus('/infinity/qq');
            void loadQQ(mine);
          }
        } catch (error) {
          if (mine === requestId && !root.hidden) showFeedback(error instanceof Error ? error.message : '下载操作失败');
        } finally {
          setPending(false);
          if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
        }
      });
    }
    button.addEventListener('click', async () => {
      if (button.disabled || !currentQQ) return;
      const mine = requestId;
      setPending(true);
      button.disabled = true;
      try {
        const source = await chooseASRSource(isTts ? 'TTS' : 'ASR', currentQQ[kind].install?.source);
        if (!source || mine !== requestId || root.hidden) return;
        downloadErrors[kind] = '';
        updateLive(currentQQ);
        const response = await fetch(`/infinity/qq/${kind}-install`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source }),
        });
        const install = await response.json();
        if (!response.ok || install.ok === false) throw new Error(install.said || '未能开始下载，请重试。');
        if (mine !== requestId || root.hidden || !currentQQ) return;
        currentQQ = { ...currentQQ, [kind]: { ...currentQQ[kind], install } };
        invalidateStatus('/infinity/qq');
        void loadQQ(mine);
      } catch (error) {
        if (mine === requestId && !root.hidden) {
          downloadErrors[kind] = error instanceof Error ? error.message : '未能开始下载，请重试。';
        }
      } finally {
        setPending(false);
        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
      }
    });
  }

  asrFolder.addEventListener('click', () => {

    void openModelFolder('asr', asrModel.value, asrFolder);

  });

  const voicePayload = (): object => ({
    asrAutoStart: asrAuto.checked, ttsAutoStart: ttsAuto.checked,
    ttsPort: Number(servicePort.value), ttsProvider: ttsProvider.value, ttsService: serviceName.value.trim(),

    ...(currentQQ?.tts.voices ? { ttsVoice: ttsVoice.value } : {}),

    ...(currentQQ?.tts.sendMode ? { ttsSendMode: ttsSendMode.value } : {}),

    ...(currentQQ?.asr.language ? { asrLanguage: asrLanguage.value } : {}),

    ...(currentQQ?.asr.useItn !== undefined ? { asrUseItn: asrItn.checked } : {}),

    ...(currentQQ?.asr.threads !== undefined ? { asrThreads: Number(asrThreads.value) } : {}),

  });

  ttsPreview.addEventListener('click', () => {

    if (ttsPreview.disabled || previewPending || mutationPending

      || currentQQ === null || root.hidden) return;

    const previewText = ttsPreviewText.value.trim();

    if (!previewText || previewText.length > 200) {

      showFeedback(corePhrase("请输入 1—200 字试听文字。"));

      return;

    }

    const mine = requestId;

    previewPending = true;
    const controller = new AbortController();
    previewController = controller;

    ttsPreview.disabled = true;

    showFeedback(corePhrase("正在合成试听音频…"), false);

    void (async () => {

      try {

        const response = await fetch('/infinity/qq/tts-preview', {
          signal: controller.signal,

          method: 'POST', headers: { 'Content-Type': 'application/json' },

          body: JSON.stringify({ model: ttsModel.value, voice: ttsVoice.value,

            text: previewText }),

        });

        const result = await response.json() as { ok?: boolean; audio?: string; said?: string };

        if (!response.ok || !result.ok || !result.audio) {

          throw new Error(result.said || '试听失败。');

        }

        if (mine !== requestId || root.hidden) return;

        ttsAudio.pause();

        ttsAudio.src = `data:audio/wav;base64,${result.audio}`;

        ttsAudio.hidden = false;

        showFeedback(corePhrase("试听已生成；可在卡片内播放。"));

        void ttsAudio.play().catch(() => { /* 浏览器阻止自动播放时使用控件。 */ });

      } catch (error) {

        if (mine === requestId && !root.hidden) {

          showFeedback((error instanceof Error ? error.message : corePhrase("试听失败。")));

        }

      } finally {

        if (previewController === controller) {
          previewController = undefined;
          previewPending = false;
        }

        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);

      }

    })();

  });

  const send = async (route: string, body: object,

    before?: { route: string; body: object }): Promise<void> => {

    if (mutationPending || currentQQ === null || root.hidden) {

      if (currentQQ === null && !root.hidden) {

        showFeedback(corePhrase("正在读取配置，请稍候再操作。"));

      }

      return;

    }

    const mine = requestId;



    let prepared = false;

    mutationPending = true;

    showFeedback(corePhrase("正在处理…"), false);

    lockCommands();

    try {

      for (const step of [...(before ? [before] : []), { route, body }]) {

        const response = await fetch(step.route, {

          method: 'POST', headers: { 'Content-Type': 'application/json' },

          body: JSON.stringify(step.body),

        });

        const answer = await response.json() as QQState & { said?: string; ok?: boolean };

        if (mine !== requestId || root.hidden) return;

        if (!response.ok || answer.ok === false) throw new Error(answer.said ||
          `请求失败（${response.status}）`);

        currentQQ = answer;

        if (step === before) prepared = true;

      }

      drawQQ(currentQQ);

      const action = route === '/infinity/qq/process' && 'action' in body ? body.action : undefined;
      if (action === 'tts_start') {
        clearFeedback();
      } else if (action === 'snowluma_start_service') {
        showFeedback(corePhrase("SnowLuma 已启动。管理页就绪后，在那里选择并加载 QQ。"));
      } else if (action === 'snowluma_recover') {
        showFeedback(corePhrase("正在核对 QQ 进程，请查看检测结果。"));
      } else if (typeof action === 'string' && action.startsWith('snowluma_confirm_')) {
        showFeedback(corePhrase("加载确认已提交；正在核验目标进程与实际账号。"));
      } else if (route === '/infinity/qq/routes' && currentQQ.plugin?.configured !== true) {
        showFeedback(corePhrase("会话路由已保存。请在 SnowLuma 管理台接入 QQ，再同步协议配置。"));
      } else {
        showFeedback(corePhrase("已更新。"));
      }

    } catch (error) {

      if (mine !== requestId || root.hidden) return;

      // The first save is committed. Without a server revision-bound CAS,
      // restoring a stale snapshot could overwrite another editor's changes.
      showFeedback([((error instanceof Error ? error.message : corePhrase("操作失败。"))), ((prepared ? corePhrase("；语音设置已保存，后续动作未确认。请刷新检查后再决定；未自动回滚或重试。") : corePhrase("")))]);

      if (currentQQ) drawQQ(currentQQ);

    } finally {

      if (mine === requestId) {

        // A read that started before this write must not land after it.

        invalidateStatuses(['/infinity/qq', '/plugins']);
        qqStatusPending = undefined;

        mutationPending = false;

        if (!root.hidden && currentQQ) {
          updateLive(currentQQ);
        }

      }

    }

    if (mine === requestId && !root.hidden) {

      void loadPlugins(mine);

      void loadQQ(mine);

    }

  };

  const voiceSetupReady = (kind: 'asr' | 'tts'): boolean => {
    if (!currentQQ) return false;
    if (kind === 'tts' && ttsProvider.value === 'external') return true;
    const setup = kind === 'asr' ? asrSetup : ttsSetup;
    const saved = currentQQ[kind];
    const matches = kind === 'asr' ? saved.model === asrModel.value
      : saved.model === ttsModel.value && currentQQ.tts.voice === ttsVoice.value;
    const installedSelection = saved.install?.runtimeReady === true && !saved.runtimeIssue
      && (kind === 'asr' ? asrModel.value === 'SenseVoiceSmall-onnx'
        : ttsModel.value === 'Fun-CosyVoice3-0.5B-2512' && ttsVoice.value === 'default');
    if (saved.runtimeIssue === undefined || (matches && saved.setupReady && !saved.runtimeIssue)
        || installedSelection || setup.ready()) return true;
    setup.require();
    return false;
  };

  asrSwitch.addEventListener('change', () => {
    if (asrSwitch.checked) voiceSetupReady('asr');
  });
  ttsSwitch.addEventListener('change', () => {
    if (ttsSwitch.checked) voiceSetupReady('tts');
  });

  save.addEventListener('click', () => {

    if (save.disabled || mutationPending || currentQQ === null) return;
    if (asrSwitch.checked && !voiceSetupReady('asr')) return;
    if (ttsSwitch.checked && !voiceSetupReady('tts')) return;

    void send('/infinity/qq/settings', {

      asrEnabled: asrSwitch.checked, asrModel: asrModel.value,

      ttsEnabled: ttsSwitch.checked, ttsModel: ttsModel.value,

      ...voicePayload(),

    });

  });

  saveRoutes.addEventListener('click', () => {

    if (saveRoutes.disabled || mutationPending || currentQQ === null) return;

    const ids = (value: string): string[] => value.split(/[\s,，;；]+/).filter(Boolean);

    const privates = ids(privateIds.value);

    const target = proactiveTarget.value.trim();

    if (!proactiveTarget.readOnly && target && target === currentQQ?.routing.proactiveTarget

      && currentQQ.routing.privates.includes(target) && !privates.includes(target)) {

      proactiveTarget.value = '';

      showFeedback(corePhrase("主动目标已移出私聊名单，已取消主动投递；正在保存。"));

    }

    void send('/infinity/qq/routes', {

      privates, groups: ids(groupIds.value),

      proactiveTarget: currentQQ.routing.ownerRoute && currentQQ.routing.ownerRoute.accounts.length <= 1
        ? '' : proactiveTarget.value.trim(),

      historyBackfillMinutes: Number(backfillMinutes.value),

      reactionWindowSeconds: Number(reactionWindow.value),

    });

  });

  asrProcess.addEventListener('click', () => {
    if (asrProcess.disabled || mutationPending || !currentQQ) return;
    if (currentQQ.asr.running) {
      void send('/infinity/qq/process', { action: 'asr_stop' });
    } else if (voiceSetupReady('asr')) {
      void send('/infinity/qq/process', { action: 'asr_start' }, {
        route: '/infinity/qq/settings',
        body: { asrEnabled: true, asrModel: asrModel.value, ttsEnabled: ttsSwitch.checked,
          ttsModel: ttsModel.value, ...voicePayload() },
      });
    }
  });

  ttsProcess.addEventListener('click', () => {

    if (ttsProcess.disabled || mutationPending || currentQQ === null) return;

    if (currentQQ.tts.managed || currentQQ.tts.running) {
      void send('/infinity/qq/process', { action: 'tts_stop' });
      return;
    }

    if (!ttsModel.value && ttsProvider.value !== 'external') {

      showFeedback(corePhrase("请先选择 TTS 合成模型，再点击启动。"));

      return;

    }

    if (!voiceSetupReady('tts') || (asrSwitch.checked && !voiceSetupReady('asr'))) return;

    void send('/infinity/qq/process', { action: 'tts_start' }, {

      route: '/infinity/qq/settings',

      body: { asrEnabled: asrSwitch.checked, asrModel: asrModel.value,

        ttsEnabled: true, ttsModel: ttsModel.value, ...voicePayload() },

    });

  });

  snowManager.addEventListener('click', event => {
    if (snowManager.getAttribute('aria-disabled') === 'true') {
      event.preventDefault();
      showFeedback(corePhrase("请先启动 SnowLuma，再打开管理页。"));
    }
  });

  snowProcess.addEventListener('click', async () => {

    if (snowProcess.disabled || mutationPending || currentQQ === null || root.hidden) return;
    const managed = currentQQ.snowluma.managed;
    const canStopInstallation = currentQQ.snowluma.canStopInstallation;
    const target = currentQQ.snowluma.recovery?.targetUin;
    const unchanged = () => !root.hidden && !mutationPending && currentQQ !== null
      && currentQQ.snowluma.managed === managed
      && currentQQ.snowluma.canStopInstallation === canStopInstallation
      && currentQQ.snowluma.recovery?.targetUin === target;

    if (managed) {

      if (!await confirmAction('停止 SnowLuma 服务',

        currentQQ.snowluma.isolatedInstance
          ? '当前 Bot 的 QQ 接入将断开。'
          : '使用这个协议服务的 QQ 接入将断开。',

        '停止服务')) return;

      if (!unchanged()) return;

    }

    if (!managed && canStopInstallation) {

      if (!await confirmAction('停止 SnowLuma',

        '将停止当前项目安装目录的协议进程，QQ 连接会断开，但 QQ 程序不会关闭。',

        '确认停止')) {

        return;

      }

      if (!unchanged()) return;

      void send('/infinity/qq/process', { action: 'snowluma_stop_installation' });

      return;

    }

    void send('/infinity/qq/process', {

      action: managed ? 'snowluma_stop' : 'snowluma_start_service',

    });

  });

  const recoveryPayload = (): object => ({ qqPath: currentQQ?.snowluma.recovery?.qqPath || '',

    username: snowUsername.value.trim(), targetUin: snowTarget.value.trim() });

  snowSave.addEventListener('click', () => {

    if (snowSave.disabled || mutationPending || currentQQ === null) return;

    void send('/infinity/qq/recovery', {

      ...recoveryPayload(), password: snowPassword.value,

    }).finally(() => { snowPassword.value = ''; });

  });

  snowForget.addEventListener('click', async () => {

    if (snowForget.disabled || mutationPending || currentQQ === null) return;

    const recovery = currentQQ.snowluma.recovery;
    if (!recovery || !await confirmAction('移除 Core 保存的管理密码',
      '同时清除本 Bot 的 SnowLuma 管理登录会话。SnowLuma 管理页仍可使用现有密码。下次从 Core 同步协议配置前，需要重新保存管理密码。',
      '移除密码')) return;
    if (root.hidden || mutationPending || currentQQ?.snowluma.recovery?.targetUin !== recovery.targetUin
      || currentQQ?.snowluma.recovery?.username !== recovery.username) return;
    await send('/infinity/qq/recovery', {
      qqPath: recovery.qqPath, username: recovery.username, targetUin: recovery.targetUin,
      clearCredentials: true,
    });

  });

  return {

    root,

    show(): void {
      corpusPanel.start();

      root.hidden = false;
      if (!terminalPane.hidden) terminalView.activate();
      if (!environmentPane.hidden && knownConfig) void environmentView.open();
      if (!toolbox.root.hidden && knownConfig) void toolbox.refresh();

      void load();

      if (pollTimer !== undefined) window.clearInterval(pollTimer);

      if (typeof window.setInterval === 'function') {

        pollTimer = window.setInterval(() => { pollLog(); }, 1000);

      }

    },

    stop(): void {
      corpusPanel.stop();
      previewController?.abort(); previewController = undefined; previewPending = false;
      ttsAudio.pause(); ttsAudio.removeAttribute('src'); ttsAudio.load(); ttsAudio.hidden = true;

      requestId += 1;
      terminalView.deactivate();
      clearFeedback();
      clearInitialPassword();

      if (pollTimer !== undefined) window.clearInterval(pollTimer);

      pollTimer = undefined;

      for (const audio of root.querySelectorAll('.voice-corpus audio')) {

        (audio as HTMLAudioElement).pause();

      }

      root.hidden = true;

    },

  };

}
