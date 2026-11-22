import { freshSnowSample, freshProtocolSample } from './qq-signals';
import type { QQHealthSnapshot } from './qq-signals';

export interface SetupSnapshot {
  readonly wired?: boolean;
  readonly plugin?: { readonly configured?: boolean; readonly running?: boolean };
  readonly snowluma: NonNullable<QQHealthSnapshot["snowluma"]> & {
    readonly installed?: boolean; readonly running: boolean;
    readonly webuiRunning: boolean; readonly transportReachable?: boolean;
    readonly installation?: { readonly nodeAvailable: boolean };
    readonly recovery?: { readonly hasCredentials: boolean; readonly accountOnline?: boolean;
      readonly managementAuthenticated?: boolean; readonly loginCompleted?: boolean;
      readonly targetUin?: string;
      readonly busy?: boolean; readonly phase?: string; readonly message?: string } };
}

export function snowSetupSteps(state: SetupSnapshot): Array<{ done: boolean; text: string;
  pendingLabel?: string; optional?: boolean }> {
  const protocolOnline = freshProtocolSample(state.snowluma);
  return [
    { done: state.snowluma.installed === true,
      text: '安装文件：需要项目旁的 SnowLuma/index.mjs；未安装时先按项目部署说明安装。' },
    { done: state.snowluma.webuiRunning,
      text: 'SnowLuma 管理页可达。若服务尚未启动，可在此页启动。' },
    { done: state.snowluma.recovery?.loginCompleted === true,
      optional: protocolOnline,
      pendingLabel: protocolOnline ? '可选 · 未验证' : undefined,
      text: protocolOnline
        ? '管理端验证（可选）：QQ 已在线；管理验证记录单独保存，不影响当前 QQ 在线状态。同步协议配置时才需要管理凭据。'
        : '管理登录：在 SnowLuma 管理页完成初始改密，回到本页保存新管理凭据并同步协议配置。'
        + '此项记录历史完成状态，不代表当前鉴权或 QQ 在线。' },
    { done: state.plugin?.configured ?? state.wired === true,
      text: 'OneBot 接入：核对 HTTP、正向 WebSocket 与鉴权配置，并填写允许的会话名单。'
        + '仅保存目标 QQ 不会导入连接；每个 Bot 须使用独立端口和自己的令牌。'
        + '此检查不代表鉴权已验证；不要关闭鉴权或对外开放端口。' },
    { done: freshProtocolSample(state.snowluma) || freshSnowSample(state.snowluma),
      pendingLabel: !freshProtocolSample(state.snowluma)
        && state.snowluma.recovery?.healthSampleExpired === true ? '待复核' : undefined,
      text: freshProtocolSample(state.snowluma)
        ? 'QQ 接入：目标账号在线；协议状态自动只读检查，不需要重复管理登录或注入。'
        : state.snowluma.recovery?.healthSampleExpired === true
        ? 'QQ 接入：管理在线采样已过期（30 秒），不代表 QQ 掉线；可在 SnowLuma 管理台查看 QQ。'
        : 'QQ 接入：在 SnowLuma 管理台选择已登录的 QQ。首次识别成功后，Core 会保存目标 QQ。'
          + '扫码、二次验证和条款确认仍由你完成。' },
    { done: state.plugin?.running === true,
      text: '插件运行：启用 QQ 插件后，用允许名单内的账号发送测试消息。'
        + '收到真实回复才算收发验收完成。' },
  ];
}

/** Optional management history must not reduce online connection readiness. */
export function snowSetupSummary(state: SetupSnapshot): string {
  const steps = snowSetupSteps(state);
  const required = steps.filter(step => !step.optional);
  const pendingOptional = steps.filter(step => step.optional && !step.done).length;
  return '接入配置清单 · '
    + `${required.filter(step => step.done).length} / ${required.length} 项已就绪`
    + (pendingOptional ? ' · 管理端验证可选，不影响当前 QQ 连接' : '');
}

/** Configuration checks are not a substitute for a real message round trip. */
export function snowNextStep(state: SetupSnapshot): string {
  const snow = state.snowluma;
  const recovery = snow.recovery;
  if (!snow.installed) return '下一步：下载官方发行包，解压到安装目录根部，再检查入口文件。';
  if (snow.installation && !snow.installation.nodeAvailable) {
    return '下一步：安装 Node.js，重新打开 Core 启动窗口后再检查。';
  }
  if (freshProtocolSample(snow)) {
    if (snow.protocolHealth?.pluginConnected !== true) {
      return '目标 QQ 在线；插件 WS 尚未连接，请检查插件状态。无需重复管理登录或注入 QQ。';
    }
    return '目标 QQ 在线，插件 WS 已连接；状态自动只读检查，无需重复验证接入。真实消息收发仍需单独验收。';
  }
  if (recovery?.busy) return '正在恢复接入，请等待本页实时结果，不要重复点击。';
  if (recovery?.phase === 'needs_action') {
    return `需要处理：${recovery.message || '请查看接入结果提示。'}`;
  }
  if (!snow.webuiRunning) return 'SnowLuma 管理台尚未就绪。若随 Bot 启动失败，请查看启动器的 QQ 提示。';
  if (!recovery?.targetUin) {
    return '下一步：打开 SnowLuma 管理台，选择已登录的 QQ 并加载；Core 会保存识别出的目标 QQ。';
  }
  if (!(state.plugin?.configured ?? state.wired)) {
    return '下一步：在 SnowLuma 管理台加载 QQ 并配置 OneBot；然后回本页同步协议配置，填写会话名单。';
  }
  if (recovery.healthSampleExpired === true && !freshSnowSample(snow)) {
    return '管理在线采样已过期，可到 SnowLuma 管理台核对 QQ；无需重填账号。';
  }
  if (!freshSnowSample(snow)) {
    return '到 SnowLuma 管理台查看目标 QQ，回本页刷新连接状态。';
  }
  if (!state.plugin?.running) return '下一步：到插件总览启用 QQ 插件，再检测 OneBot 接入。';
  return '配置检查已就绪；请检测 OneBot，再从允许名单内的账号发消息验收。'
    + '6/6 不代表真实收发已经通过。';
}
