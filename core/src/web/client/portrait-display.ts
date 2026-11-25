/** Display-only labels. Account and profile keys are never rewritten. */
export function portraitAccount(raw: string): string {
  const group = /^group:qq:\d+\|sender=(qq:\d+)$/.exec(raw);
  if (group) return group[1];
  if (/^private:(qq:\d+|console:local-owner)$/.test(raw)) return raw.slice(8);
  if (/^QQ-\d+$/.test(raw)) return 'qq:' + raw.slice(3);
  if (raw === 'Console-local-owner') return 'console:local-owner';
  return raw;
}
export function portraitOrigin(raw: string): string {
  const account = portraitAccount(raw);
  if (/^qq:\d+$/.test(account)) return 'QQ · ' + account.slice(3);
  if (account === 'console:local-owner') return '控制台 · 本机';
  return raw;
}
export function portraitSource(raw: string): string {
  const group = /^group:qq:(\d+)\|sender=qq:(\d+)$/.exec(raw);
  return group ? 'QQ 群 · ' + group[1] : portraitOrigin(raw);
}
type Visit = { account: string; room: string; first?: string; last?: string; label?: string };
type Person = { who: string; plat?: string; display_account?: string; encounters?: readonly Visit[]; said?: readonly { from: string; ts: string }[] };
/** Count distinct rooms observed for this exact account, not every linked identity. */
export function portraitEncounters(person: Person, people: readonly Person[]): Visit[] {
  const account = portraitAccount(person.display_account || person.plat || person.who);
  if (!/^qq:\d+$/.test(account)) return [];
  const rooms = new Map<string, Visit>();
  const add = (visit: Visit) => {
    if (visit.account !== account || !/^\d+$/.test(visit.room)) return;
    const old = rooms.get(visit.room);
    const dates = [old?.first, old?.last, visit.first, visit.last].filter((v): v is string => !!v && !Number.isNaN(Date.parse(v))).sort((a,b) => Date.parse(a)-Date.parse(b));
    rooms.set(visit.room, {account, room: visit.room, label: visit.label || old?.label, first: dates[0], last: dates.at(-1)});
  };
  for (const row of people) {
    for (const visit of row.encounters || []) add(visit);
    for (const source of [{from:row.plat || '',ts:''}, ...(row.said || [])]) {
      const match = /^group:qq:(\d+)\|sender=(qq:\d+)$/.exec(source.from);
      if (match) add({room:match[1],account:match[2],first:source.ts,last:source.ts});
    }
  }
  return [...rooms.values()].sort((a,b)=>(b.last || '').localeCompare(a.last || '') || a.room.localeCompare(b.room));
}
