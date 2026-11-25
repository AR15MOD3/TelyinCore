/** Labels for known Core-generated record files; unknown user names remain intact. */
export function experienceSourceName(path: string): string {
  const name = path.split(/[\\/]/).filter(Boolean).at(-1)?.replace(/\.[^.]+$/, '') || '';
  if (/^对话(?:记录)?$/.test(name)) return '对话';
  if (/^自我记录归位-\d{8}$/.test(name)) return '自我记录';
  if (/^自述工具用法归位-\d{8}$/.test(name)) return '工具说明';
  const labels: Record<string,string> = {'整理那一趟':'记忆整理','设定修订':'设定修改','交接笔记':'会话摘要'};
  return labels[name] || name;
}
