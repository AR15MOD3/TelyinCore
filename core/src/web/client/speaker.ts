import { pageFetch as fetch } from './status-json';
// 显示名共同读取 /persona 与 /infinity/bots；当前 Bot displayName 优先。
// 初始缺值使用 BOT；瞬时失败保留已确认名字，切 Bot 时重定身份；迟到读取不覆盖新值。
/** 读不到时退的那个中性词 —— 与 `MINE_NAME`（`USER`）成对的那一个通用角色名。 */
export const NEUTRAL = 'BOT';

/** **那一行的唯一读法**：`- **名字**：X。…` ⇒ 取 X（到句号 ／ 逗号那一类为止）。
 *  ⚠️ 它认的是**那一行的形状**，不是文件名 ⇒ 别人的档案把它放在哪一份里都读得到。
 *  ⚠️ **没匹配上 ⇒ `null`**（交给 `name_in` 退中性词）；而"匹配上、取到的是空白"那一种
 *      **也由 `name_in` 退中性词**（`.trim()` 之后是空串）—— 见那里的那两句。 */
export const NAME_LINE = /^[ \t]*-[ \t]*\*\*名字\*\*[ \t]*[：:][ \t]*([^。，,.;；!！?？\n（(]+)/m;

/** 那一份报告里**所有正文**（两处都在交：`segments[].body` ／ `rows[].file_infos[].body`）。 */
function bodies_of(report: unknown): string {
  const pick = (list: unknown, get: (one: Record<string, unknown>) => string): string[] => {
    if (!Array.isArray(list)) return [];
    const out: string[] = [];
    for (const one of list) {
      if (one === null || typeof one !== 'object') continue;
      out.push(get(one as Record<string, unknown>));
    }
    return out;
  };
  const into_rows = (one: Record<string, unknown>): string => {
    const infos = Array.isArray(one.file_infos) ? one.file_infos : [];
    return infos.map((info) => String((info as { body?: unknown }).body ?? '')).join('\n');
  };
  const source = (report as { segments?: unknown }).segments;
  const rows = (report as { rows?: unknown }).rows;
  return [...pick(source, (one) => String(one.body ?? '')), ...pick(rows, into_rows)].join('\n');
}

/** 报告 ⇒ 那个名字（**纯函数**：找不到 ⇒ `NEUTRAL` —— 不猜、不编）。
 *
 *  ⚠️⚠️ **两样都退 `NEUTRAL`：没匹配上，以及匹配上了而那一格是空的**（本刀 Fix 4）。
 *  后一条是可达的：`- **名字**：␠␠␠`（那一行在、方括号那一格全是空白）⇒ 正则**匹配得上**
 *  （那一格允许空白）、`hit[1]` 是 `"   "` ⇒ `.trim()` 之后是 **`""`**
 *  ⇒ **只判 `hit === null` 那一半会画出"一个没有名字的气泡"**（不崩，但那一条上没有名字）。
 *  ⚠️ 判据是 `tests/test_web.py::test_a_blank_name_in_the_archive_falls_back_…`
 *  （跑真产物：那一格空着时左栏 ／ 顶栏 ／ 气泡三处都退中性词）。 */
export function name_in(report: unknown): string {
  const hit: RegExpExecArray | null = NAME_LINE.exec(bodies_of(report));
  if (hit === null) return NEUTRAL;
  const name = hit[1].trim();
  return name === '' ? NEUTRAL : name;
}

/** **这一页上她那个名字的当前值**（一处状态：`chat.ts` 每画一条都来读它）。 */
let current = NEUTRAL;
export function speaker(): string { return current; }
let ready = false;
export function speakerReady(): boolean { return ready; }

/** **那一趟读只发一次**（本刀加的）—— 一处 `Promise` 缓存。
 *
 * ⚠️ **为什么非加不可**（真量到过）：`loadSpeaker()` 是**异步**的，而 `main.ts:103`
 * 那一句 `route()` **跑在它前面** ⇒ 任何"一画就要那个名字"的页（今天的「经历」
 * 就是 ）第一遍画出来的是 **`BOT`** —— 而 `GET /persona` 是**重**的那一条
 * （它要把整份档案拼出来 ）⇒ 让每个页各读一次**不行** 。
 * ⇒ 缓存这一趟： 谁先叫谁发，后面来的**拿到同一个 promise**
 * （ 而"读不到就退中性词"那一条一个字没动 —— 失败也照样收口 ）。
 */
let loading: Promise<string> | null = null;

/** **要去读那一趟**：共同读取 persona/Bot 显示名；失败保留已确认名字（不抛）。
 *
 * ⚠️ **并发调用共用一趟**（见上面那段账 ）； 成功了就把结果留在 `current` 上 。 */
let nameRead = 0;
let activeIdentity = '';
export function loadSpeaker(force = false): Promise<string> {
  if (force) loading = null;
  if (loading === null) {
    const read = ++nameRead;
    loading = (async (): Promise<string> => {
      let next = current;
      let identity = activeIdentity;
      let personaName: string | null = null;
      const botsRead = fetch('/infinity/bots', { cache: 'no-store' });
      void botsRead.catch(() => {});
      try {
        const got = await fetch('/persona', { cache: 'no-store' });
        if (got.ok) {
          const parsed = name_in(await got.json());
          personaName = parsed;
          if (parsed !== NEUTRAL) next = parsed;
        }
      } catch { /* A transient failure must not erase a verified display name. */ }
      try {
        const bots = await botsRead;
        if (bots.ok) {
          const state = await bots.json() as {
            active: string; bots: Array<{ id: string; displayName: string }> };
          const active = state.bots?.find(one => one.id === state.active);
          if (active && state.active !== activeIdentity) {
            next = personaName && personaName !== NEUTRAL ? personaName : active.id;
          }
          identity = state.active;
          if (active?.displayName) next = active.displayName;
          else if (next === NEUTRAL && active?.id) next = active.id;
        }
      } catch { /* Persona naming remains supported by older cores. */ }
      if (read === nameRead) { current = next; activeIdentity = identity; ready = true; }
      return current;
    })();
  }
  return loading;
}

// Display labels never participate in identity resolution or permission checks.
let localUser = '用户';
let labelRequest: Promise<string> | null = null;
let labelEpoch = 0;
export function userLabel(): string { return localUser; }
export function loadUserLabel(force = false): Promise<string> {
  if (force) { labelEpoch += 1; localUser = '用户'; labelRequest = null; }
  if (labelRequest) return labelRequest;
  const epoch = labelEpoch;
  labelRequest = fetch('/conversation/labels').then(async (response) => {
    if (response.ok) {
      const labels = await response.json() as { user?: string };
      if (epoch === labelEpoch) localUser = String(labels.user || '用户');
    }
    return localUser;
  }).catch(() => localUser).finally(() => { if (epoch === labelEpoch) labelRequest = null; });
  return labelRequest;
}
