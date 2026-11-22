/** Operator-only presentation; these strings must never become person or account keys. */
export function portraitOrigin(raw: string): string {
  const group = /^group:qq:(\d+)\|sender=qq:(\d+)$/.exec(raw);
  if (group) return `QQ 群 ${group[1]} · 发言者 ${group[2]}`;
  const privateQQ = /^private:qq:(\d+)$/.exec(raw);
  if (privateQQ) return `QQ 私聊 · ${privateQQ[1]}`;
  if (raw === 'private:console:local-owner') return '控制台 · 本机入口';
  return raw;
}
