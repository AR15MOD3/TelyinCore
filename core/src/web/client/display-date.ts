/** Compact local dates; preserve full source timestamps in the title. */
export function displayDate(value: string | null | undefined): string {
  if (!value) return '';
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '日期未知' : `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}
