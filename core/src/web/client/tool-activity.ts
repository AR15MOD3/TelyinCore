/** Only the receipt envelope prefix denotes failure; quoted history is data. */
export function toolReceiptFailed(name: string, text?: string): boolean {
  return name !== 'handoff_note' && /^\s*\[tool failed\]/.test(text || '');
}

/** Describe observed calls and receipts; never invent the model's intent. */
export function describeTool(name: string, call: string | undefined, result: string | undefined, fallback: string, knownTitles?: ReadonlyMap<string, string>): string {
  let args: Record<string, unknown> = {};
  try { const value = JSON.parse(call || '{}'); if (value && typeof value === 'object' && !Array.isArray(value)) args = value; } catch { /* Preserve unknown calls. */ }
  const compact = (s: string, max = 100) => { const t = s.replace(/\s+/g, ' ').trim(); return t.length > max ? t.slice(0, max) + '…' : t; };
  const value = (key: string) => typeof args[key] === 'string' ? String(args[key]).trim() : '';
  const reason = ['why', 'reason', 'purpose'].map(value).find(Boolean);
  const failed = toolReceiptFailed(name, result);
  let action = fallback;
  if (name === 'skill_read' || name === 'kb_read') {
    const target = value('name');
    let title = '';
    if (!failed && result) {
      if (name === 'skill_read') title = result.match(/^\[sk-[^\]]+\]\s*(.*?)\s*｜/m)?.[1] || '';
      else title = result.match(/^\[kb-[^\]]+\][^\n]*\n([^\n]+)/)?.[1] || '';
      if (/^(记录时间|修订来历|第\s*\d)/.test(title)) title = '';
    }
    if (!title && target) title = knownTitles?.get(target) || '';
    if (!title && target && !/^(kb|sk)-[\w-]+$/.test(target)) title = target;
    const offset = typeof args.offset === 'number' ? args.offset : 0;
    action = !target && name === 'skill_read' ? '查看技能目录' :
      (offset > 0 ? '续读' : '读取') + (title ? '《' + compact(title) + '》' : name === 'skill_read' ? '指定技能' : '关联知识');
    const range = result?.match(/第\s*(\d+)\s*[–—-]\s*(\d+)\s*行/);
    const total = result?.match(/正文共\s*(\d+)\s*行/);
    if (range) action += ` · 第 ${range[1]}–${range[2]} 行`;
    else if (offset > 0) action += ` · 从第 ${offset + 1} 行起`;
    else if (total) action += ` · ${total[1]} 行`;
  } else if (value('query')) {
    action = '检索「' + compact(value('query')) + '」';
    const count = result?.match(/(?:有|查到)\s*(\d+)\s*(?:份|条)/);
    if (!failed && count) action += ' · 找到 ' + count[1] + ' 条';
  } else if (value('text')) action = '记录「' + compact(value('text')) + '」';
  else if (value('name')) action = fallback + '《' + compact(value('name')) + '》' + (value('section') ? ' · ' + value('section') : '');
  if (reason) action = compact(reason, 140) + ' · ' + action;
  if (failed) action += ' · 未完成';
  return action;
}

export function rememberToolTitles(result: string, titles: Map<string, string>): void {
  for (const match of result.matchAll(/^·\s+(kb-[a-zA-Z0-9-]+)\s+(.+?)(?:（匹配分数|｜)/gm)) titles.set(match[1], match[2].trim());
  const skill = result.match(/^\[(sk-[^\]]+)\]\s*(.*?)\s*｜/m);
  if (skill) titles.set(skill[1], skill[2]);
  const knowledge = result.match(/^\[(kb-[^\]]+)\][^\n]*\n([^\n]+)/);
  if (knowledge && !/^(记录时间|修订来历|第\s*\d)/.test(knowledge[2])) titles.set(knowledge[1], knowledge[2]);
}
