/** Compact display; metrics still come only from the selected backend partition. */
type Fetcher = (url: string) => Promise<{ ok: boolean; json(): Promise<unknown> }>;
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue =>
  typeof value === 'object' && value !== null ? value as ObjectValue : {};
const number = (value: unknown): number | null =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
const count = (value: unknown): string => {
  const n = number(value);
  return n === null ? '—' : n.toLocaleString('en-US');
};
const compact = (value: unknown): string => {
  const n = number(value);
  if (n === null) { return '—'; }
  if (n >= 1e6) { return `${Number((n / 1e6).toFixed(1))}M`; }
  if (n >= 1e3) { return `${Number((n / 1e3).toFixed(1))}K`; }
  return String(n);
};
const percent = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
    ? `${Number(value.toFixed(1))}%` : '—';

export function cachePresentation(got: ObjectValue): {
  label: string; title: string; heading: string; amount: string;
  rows: [string, string][]; note: string; diagnostics: string[];
} {
  const allPaths = 'cache_all_view' in got;
  const view = object(allPaths ? got.cache_all_view : got.cache_view);
  const valid = view.version === 1 && view.scope === (allPaths ? 'all_paths' : 'console') && view.unit === 'http_attempt';
  const selected = object(view.selected);
  const total = object(selected.cumulative);
  const ready = valid && view.status === 'ready';
  const reported = number(total.reported_attempts);
  const usable = ready && reported !== null && reported > 0;
  const share = usable ? percent(total.share) : '—';
  const diagnostics = ['统计单位：HTTP 尝试；不回退旧混合累计'];
  if (valid && typeof view.model === 'string') { diagnostics.push(`模型：${view.model}`); }
  for (const [label, raw] of [
    ['累计', selected.cumulative], ...(!allPaths ? [['近20次', selected.recent20]] : []),
    ...(!allPaths ? [['最近一次', object(selected.last).metrics]] : []),
  ] as const) {
    if (!ready) { break; }
    const metric = object(raw);
    diagnostics.push(`${label}：${percent(metric.share)} · 上报 ${count(metric.reported_attempts)}`
      + ` / 尝试 ${count(metric.attempts)}；未上报 ${count(metric.unknown_attempts)}`
      + `，无效 ${count(metric.invalid_attempts)}`);
  }
  diagnostics.push('比率按有效命中 / 输入 token 总和加权，非各次百分比平均。');
  if (allPaths) {
    const groups = object(view.groups);
    for (const [key, label] of [['conversation', '对话与交接后续'], ['consolidation', '后台整理'], ['auxiliary', '辅助请求']]) {
      const row = object(groups[key]);
      diagnostics.push(`${label}：${count(row.input)} 输入 tok · 命中 ${percent(row.share)}；未上报 ${count(row.unknown_attempts)}`);
    }
    diagnostics.push('当前 Bot、当前提供商与模型的本次交接周期；交接成功后重新计数，重启不主动清零。');
    if (typeof view.coverage_note === 'string') { diagnostics.push(view.coverage_note); }
  } else {
    diagnostics.push('累计仅限当前控制台、模型与上下文分区，不代表本次上线增量。');
  }
  let note = view.status === 'read_failed' ? '读取失败，旧读数已隐藏'
    : view.status === 'selection_failed' ? '分区选择失败'
    : valid && view.status === 'no_sample' ? '尚无样本'
    : !ready ? '缓存观测不可用或版本不兼容'
    : !usable ? '上游未上报完整缓存用量' : allPaths ? '全链路 · 本次交接周期' : '当前控制台 · 当前模型累计';
  if (usable && ((number(total.unknown_attempts) ?? 0) > 0
      || (number(total.invalid_attempts) ?? 0) > 0)) {
    note = '统计不完整，仅含有效上报用量';
  }
  const snapshot = object(got.cache_observations);
  if (snapshot.persistence_error || snapshot.observation_error || got.cache_observation_error) {
    note = '观测异常，统计可能不完整';
  }
  const local = object(got.cache_local_view);
  const localReady = local.version === 1 && local.status === 'ready'
    && local.unit === 'serialized_utf8_bytes' && local.scope === 'process_all_paths'
    && number(local.measured) !== null && Number(local.measured) > 0
    && percent(local.share) !== '—';
  const localShare = percent(local.share);
  const localNote = '本地请求前缀复用估算，按序列化 UTF-8 字节加权；不是上游实际缓存命中或计费 token。'
    + '按 1KiB 连续前缀保守匹配，完整相同请求精确匹配。按交接周期分区，仅本次进程留存，重启清零；与同分区最近 4 次请求比较，非上游缓存有效期。';
  if (localReady) {
    diagnostics.push(localNote);
    diagnostics.push(`本地：${count(local.measured)} 次已计算 / ${count(local.attempts)} 次尝试；跳过 ${count(local.skipped)} 次。`);
  }
  if (!usable && localReady) {
    return { label: `本地前缀复用 ${localShare}（估）`,
      title: `实际缓存命中：上游未提供有效统计\n${localNote}`,
      heading: '本地前缀复用（估算）', amount: localShare,
      rows: [['实际缓存命中', '未知'], ['本地前缀复用', localShare],
        ['比较单位', '序列化字节，非 token']], note: localNote, diagnostics };
  }
  return {
    label: usable ? `${allPaths ? '本周期全链路' : '累计'} ${compact(total.input)} tok · 命中 ${share}` : '缓存命中 —',
    title: `输入用量 · ${allPaths ? '当前 Bot 全链路' : '当前控制台'} / ${allPaths ? '本次交接周期' : '当前模型累计'}\n${note}`,
    heading: allPaths ? '本次交接周期输入用量' : '累计输入用量', amount: usable ? `${count(total.input)} tok` : '—',
    rows: usable ? [['缓存命中', share], ['未缓存输入', `${count(total.uncached)} tok`],
      ['缓存读取', `${count(total.cached)} tok`],
      ...(localReady ? [['本地前缀复用（估算）', localShare] as [string, string]] : [])] : [],
    note, diagnostics,
  };
}

/** Newest invocation wins, including failures; never retain an unmarked stale percentage. */
export function latestCacheRead(fetcher: Fetcher):
    (draw: (got: ObjectValue) => void) => Promise<void> {
  let sequence = 0;
  return async (draw) => {
    const mine = ++sequence;
    try {
      const response = await fetcher('/usage');
      if (!response.ok) { throw new Error('unavailable'); }
      const got = object(await response.json());
      if (mine === sequence) { draw(got); }
    } catch {
      if (mine === sequence) { draw({ cache_view: { status: 'read_failed' } }); }
    }
  };
}
