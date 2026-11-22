export interface QQHealthSnapshot {
  readonly wired?: boolean;
  readonly overlay?: { readonly profile?: string };
  readonly plugin?: { readonly configured?: boolean; readonly running?: boolean };
  readonly snowluma?: {
    readonly healthSchema?: string; readonly configured?: boolean; readonly running?: boolean;
    readonly waitingForDesktopExit?: boolean; readonly localAccountOnline?: boolean; readonly webuiRunning?: boolean; readonly transportReachable?: boolean; readonly websocketVerified?: boolean | null;
    readonly businessRoundtripVerified?: boolean | null;
    readonly protocolHealth?: { readonly schema?: string; readonly targetUin?: string;
      readonly sampleAgeSeconds?: number | null; readonly fresh?: boolean;
        readonly checking?: boolean;
      readonly status?: string; readonly identityVerified?: boolean; readonly online?: boolean;
      readonly good?: boolean; readonly pluginConnected?: boolean } | null;
    readonly recovery?: { readonly healthSchema?: string; readonly accountOnline?: boolean;
      readonly healthSampleExpired?: boolean; readonly healthStatus?: string;
        readonly managementAuthenticated?: boolean;
      readonly targetUin?: string; readonly healthTarget?: string;
      readonly revision?: string; readonly healthSampleAgeSeconds?: number | null };
  };
  readonly tts?: { readonly enabled?: boolean; readonly configured?: boolean;
    readonly model?: string;
    readonly running?: boolean };
  readonly asr?: { readonly enabled?: boolean; readonly configured?: boolean;
    readonly model?: string;
    readonly running?: boolean };
}

export interface HealthSignal {
  readonly state: 'on' | 'off' | 'warn';
  readonly label: string;
}

export function serviceSignal(name: string, configured: boolean, running: boolean): HealthSignal {
  return {
    state: !configured ? 'warn' : running ? 'on' : 'off',
    label: `${name} · ${!configured ? '未配置' : running ? '已配置 · 运行中' : '已配置 · 未运行'}`,
  };
}

/** A schema version alone, TCP and legacy running are never positive evidence. */
export function freshSnowSample(snow: QQHealthSnapshot['snowluma']): boolean {
  const r = snow?.recovery;
  return snow?.healthSchema === 'snow-health-v2' && r?.healthSchema === 'snow-health-v2'
    && r.managementAuthenticated === true && r.healthStatus === 'online'
    && r.accountOnline === true && !!r.targetUin && r.targetUin === r.healthTarget
    && typeof r.revision === 'string' && r.revision.length > 0
    && typeof r.healthSampleAgeSeconds === 'number'
    && Number.isFinite(r.healthSampleAgeSeconds)
    && r.healthSampleAgeSeconds >= 0 && r.healthSampleAgeSeconds < 30;
}

/** Current OneBot identity/status, not a management-session expiry or TCP guess. */
export function freshProtocolSample(snow: QQHealthSnapshot['snowluma']): boolean {
  const p = snow?.protocolHealth;
  return p?.schema === 'onebot-readonly-v1' && p.fresh === true
    && typeof p.sampleAgeSeconds === 'number' && Number.isFinite(p.sampleAgeSeconds)
    && p.sampleAgeSeconds >= 0 && p.sampleAgeSeconds < 30
    && !!p.targetUin && p.targetUin === snow?.recovery?.targetUin
    && p.status === 'online' && p.identityVerified === true && p.online === true && p.good === true;
}

export function snowSignal(snow: QQHealthSnapshot['snowluma']): HealthSignal {
  if (snow && (snow.localAccountOnline !== undefined ||
      (snow.healthSchema === 'snow-health-v2' && typeof snow.webuiRunning === 'boolean'))) {
    if (snow.localAccountOnline || freshProtocolSample(snow) || freshSnowSample(snow)) return {state:'on', label:'SnowLuma · QQ 在线'};
    if (snow.webuiRunning) return {state:'warn', label:'SnowLuma · 管理服务已启动，QQ 在线状态待确认'};
    return {state:'off', label:'SnowLuma · 未启动'};
  }
  const protocol = snow?.protocolHealth;
  const running = protocol?.schema === 'onebot-readonly-v1'
    ? protocol.pluginConnected === true
    : freshSnowSample(snow) && snow?.websocketVerified === true;
  return serviceSignal('SnowLuma', snow?.configured === true, running);
}

/** Shared ordering and semantics for the rail and the page header. */
export function qqSignals(value: QQHealthSnapshot | null, loaded = false): HealthSignal[] {
  const marker = serviceSignal;
  return [
    marker('QQ 插件', value?.plugin?.configured ?? value?.wired === true,
      value?.plugin?.running ?? loaded),
    snowSignal(value?.snowluma),
    marker('TTS', !!value?.tts?.model,
      value?.tts?.enabled !== false && value?.tts?.running === true),
    marker('ASR', !!value?.asr?.model,
      value?.asr?.enabled !== false && value?.asr?.running === true),
  ];
}
