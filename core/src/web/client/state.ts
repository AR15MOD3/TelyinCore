// 顶上那条计数 ／ 引擎那一栏 —— 这两处显示的**每一个数都从 `GET /state` 来**。
//
// ⚠️ 这一页里**没有一个数被写死**：`/state` 交回什么，它就画什么；交回 `null`，
//    它就画「—」（不是 0 —— "不知道"与"真的是零"在那台机器上是两件事，见
//    `telyincore/web.py::state_of`）。

/** `/state` 里 `machine` 那一段：四个计数 ＋ 端点 ／ 模型（ 来源在服务那一侧）。 */
export type Machine = {
  tokens: number | null;
  context_estimate_source?: string | null;
  messages: number | null;
  batches: number | null;
  paused: boolean;
  stopped_by_hand?: boolean;
  starting?: boolean;
  processing?: boolean;
  interrupting?: boolean;
  yielding?: boolean;
  activity?: { state: 'active' | 'idle' | 'paused' | 'stopping'; background_running: number; background_waiters: number; local_observations?: { recoverable: number; uncertain: number; business_acknowledged: false; replay_uncertain: false } } | null;
  round_id?: string | null;
  endpoint: string | null;
  model: string | null;
  /** 这一条档案的**名字**（顶栏那一格要它 —— web.py::state_of 发的）。 */
  profile?: string | null;
  /** 仅本地档案探活：true 在线、false 离线、null 未探测。 */
  model_available?: boolean | null;
};

/** `/state` 整一份：服务自己的那几项（`connected` ／ `clients`）＋ 那台机器那一段。 */
export type State = {
  connected: boolean;
  clients: number;
  machine: Machine | null;
};

/** 顶栏那四个名字 —— 与 `telyincore/web.py::COUNTER_KEYS` 是同一份。 */
export const COUNTER_KEYS = ['tokens', 'messages', 'batches', 'paused'] as const;

/** 引擎那一栏的那两个名字 —— 与 `telyincore/web.py::ENGINE_KEYS` 是同一份。 */
// 与服务那一份互为镜像（web.py::ENGINE_KEYS）：2026-10-05 才发现这两份**漂了**
//    —— 服务那边加了 profile（这一条档案的名字），这里没跟。
export const ENGINE_KEYS = ['endpoint', 'model', 'profile', 'model_available'] as const;

/** 一个数画成字：没有那个数就画「—」， 不画 0。 */
export function count(value: number | null | undefined): string {
  return typeof value === 'number' ? String(value) : '—';
}

/** 运行状态以服务实际状态为准；连接状态单独显示。 */
export function machineStatus(m: Machine | null): string {
  if (m === null) return '—';
  if (m.starting) return '正在唤醒';
  if (m.activity?.state === 'stopping') return '正在休止';
  if (m.paused) return '已休止';
  return m.stopped_by_hand ? '静候中' : '已唤醒';
}

export function counterChips(m: Machine | null): Array<[string, string]> {
  if (m === null) {
    return [['tokens', '—'], ['messages', '—'], ['batches', '—'], ['paused', '—']];
  }
  return [
    ['tokens', m.context_estimate_source === 'standby_archive_preview' && typeof m.tokens === 'number' ? `~${m.tokens}` : count(m.tokens)],
    ['messages', count(m.messages)],
    ['batches', count(m.batches)],
    ['paused', machineStatus(m)],
  ];
}

/** 引擎那一栏的三行：端点 ／ 模型 ／ 连接状态。 */
export function engineRows(s: State): Array<[string, string]> {
  const m = s.machine;
  return [
    ['端点', m === null || m.endpoint === null ? '—' : m.endpoint],
    ['模型', m === null || m.model === null ? '—' : m.model],
    ['连接', m?.model_available === false ? '本地模型离线'
      : m?.model_available === true ? '本地模型在线'
        : s.connected ? `页面已连接（${s.clients} 条）` : '页面未连接'],
  ];
}
