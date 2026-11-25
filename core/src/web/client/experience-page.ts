import { showTextDetail } from './text-detail';
import { experienceSourceName } from './record-labels';
import { corePhrase, coreSetUIAttribute, coreSetUIText, coreUITextNode } from './ui-language';
import { recordRefresh } from './record-refresh';
import { pageLoad } from './page-load';
import { getStatusJson, StaleStatusError } from './status-json';
import { pageFetch as fetch } from './status-json';
// telyincore —— 「经历」那一页（2026-10-09 **照 v4 设计稿重做**）。
//
// 它画的是 `/experience` 那条口交回来的那些（ **发生过的事** ）：
//     `{experience: [{name, kind, count, ts,
// days: [{day, n, label, sub, beats: [{at, role, text}]}]}], wired}` 。
//
// ⚠️ **这一页的"主"是时间** （ 规格 §一 那张表 ）：
// · **主** ＝ 哪几天 —— **日子是大字**（17px）；
// · **次** ＝ 那一天里**发生了什么** —— **条目是小字**（13px）；
// · 构图 ＝ **一条时间轴**： 左边**一根线 ＋ 圆点**（全页唯一贯穿的东西 ）／
// 右边那件事 。 而"按时间倒着摞"正是她召回的方向（游标往后读 ）。
//
// ⚠️ **2026-10-09 作者点了两处，两处都是这一页的真错** （ 他的原话 ）：
// ① **"不要写个路径进去，让人看不明白到底是什么意思。我记得经历页的表现形式
//       不是这样的。你查一下，你做对了吗。"**
// 我上一版把**份名那条路径**（`经历/对话/对话.jsonl`）摆在**条目正文**那一格
// 而设计稿那一格写的是「**她说：想通了一件事，先记下来**」
// （`世界三页-设计稿-v3.html` ／ `v4` 的 `.beat .txt` ）——
// 一格说"**发生了什么**"，旁边一枚小字才是"**从哪儿来**"
// 而那一枚小字在稿子里是**短的来源名**（「对话」／「手写」），不是路径
// （v2 那稿里确实有路径，而它**也只当小字**用 —— 是我读漏了 v3／v4 ）。
// ⇒ 现在： **一条记录 ＝ 一行** （ 稿子里那几行本来就是记录 ——
// 它的 `.day-head .n` 写「6 条」／「84 条」**数的正是记录** ）；
// 正文那一格 ＝ 「她说：……」／「对方说：……」／「她手写：……」；
// 那一枚小字 ＝ **短的来源名**（把路径折成「对话」／「手写」／「笔记」）。
// ② **圆点压在日子那几个字上** （ 他发来的图三 ）—— 那是稿子的几何：
// 日子的右边**正好落在线上**，而圆点**骑在线上** ⇒ 必然重叠
// ⇒ 现在把**整根轴往右挪**（96 → 132 ）， 日子那一格随之变宽（74 → 96 ）
// ⇒ 装得下「10 月 8 日」，而 两者之间留 **10px**
// （ 我第一版是"把日子往里缩 14px" ⇒ 它当场折成两行 —— 真页上看见的 ）。
//
// ⚠️ **"今天／昨天"排得出来 —— 因为后端**多给了那样东西** **（ 规格 §四 那一条）：
// 每一份多带 `days[].beats`（ 那条口今天**收 `q=`** 吗？不收 ）⇒
// 而筛选**照样做得了** ： 手里那一份**就地筛**
// （ 与插件那一页同一条分界：「筛」不再打一次网 ）——
// 匹配的就是作者点名的那两样：**关键字**（那一条说了什么 ／ 从哪儿来）
// ＋ **时间戳（日期）**（「今天／昨天／10 月 9 日」与那一刻 ）。
//
// ⚠️ **只读** ； 不算天数、不排序、不标红（ 那一刻只当出处看 ）。
// ⚠️ **这一页不做热度** （ 规格 §六）： **时间就是它的热度** 。

import { searchBox } from './shell';
import { loadSpeaker, speaker } from './speaker';
import type { Chrome, Panel } from './shell';

/** 那一天里**一条记录**（ 照 `launch.see_experience()` 的契约 ）。 */
interface A_Beat {
  readonly sort_ts?: number | null;
  readonly scope?: string;
  readonly speaker_name?: string;
  /** 那一刻的人话（「3 小时前」／「23:53」／「10 月 5 日」 —— 由入口算好 ）。 */
  readonly at: string;
  /** 这一条是谁说的（`assistant` ／ `user` ／ `经历` ）—— 话由页上说 。 */
  readonly role: string;
  /** **原话本身** —— 不截断、不总结 。 */
  readonly text: string;
}

/** 那一份里**某一天**的样子（ 照 `launch.see_experience()` 的契约 ）。 */
interface A_Day {
  /** `YYYY-MM-DD`（ 认不出时刻的那些是空串 ）。 */
  readonly day: string;
  /** 那天有几条 —— **记录**的条数（ 稿子里「6 条」「84 条」数的是它 ）。 */
  readonly n: number;
  /** 那一组的大字（「今天」／「10 月 8 日」／ 空 ）。 */
  readonly label: string;
  /** 那一组的小字（「昨天」／「10 月 9 日」／ 空 ）。 */
  readonly sub: string;
  readonly beats: readonly A_Beat[];
}

/** `/experience` 交回来的一份。 */
interface A_Lived {
  readonly name: string;
  readonly activity?: 'conversation' | 'reflection';
  /** `记录` 或 `文档` 。 */
  readonly kind: string;
  /** 记录 ⇒ 几条；文档 ⇒ 多少字节 （ **不是时间** ）。 */
  readonly count: number;
  /** **最近那一条的 `ts`**（原样 ）—— 文档那一份是空串 。 */
  readonly ts?: string;
  /** 按天摞好的那一列（ 文档那一份是空那一串 —— 它不在时间轴上 ）。 */
  readonly days?: readonly A_Day[];
  /** **对方那个名字**（ 由入口递 —— 页这一侧够不到它 ／ 没给就是空串 ）。 */
  readonly other?: string;
}

interface ExperienceAnswer {
  readonly experience: readonly A_Lived[];
  readonly wired?: boolean;
}

const READ_FAILED = '经历加载失败，请重试。';
const NOT_WIRED = '当前服务暂不支持查看经历。';
const NOTHING_YET = '暂无经历。保存的对话和记录会显示在这里。';

/** 一组里先摆几条（ 稿子里那四行 —— 其余"展开更多" ）。 */
const SHOWN = 4;

/** **这一条是谁说的**（ 话由页上说 —— 而**两边都用名字** ）。
 *
 * ⚠️ **2026-10-09 作者点的那一句** ：「**对方说 对方是谁？不应该用对方，
 * 而是那个用户的名字。**」
 * 我上一版写「对方说」是**为了躲开性别那一条**（ 树里没有「性别」那一格 ⇒
 * 不许假定那个人的性别 ）—— 而 **躲错了** ：
 * 本仓那条规矩的原文是「指**那个人**时**一律用名字**」 ——
 * 用名字**本来就满足它** ， 而"对方"是**我自己发明的第三种写法** 。
 * ⚠️ **两个名字各从哪儿来**：
 * · **她自己**那个 ⇒ `speaker()`（ 从她自己的档案里读 —— 页上不写死它 ）；
 * · **对方**那个 ⇒ **由入口递**（`days` 那一族的 `other` ）——
 * 页这一侧够不到它 （`speaker.ts` 只读她自己那一个 ）。
 * ⚠️ **两个都读不到时**： 她自己那个退 `BOT`（`speaker.ts` 那一条 ）；
 * 对方那个退「对方」 —— **那是最后一道退路，不是默认写法**
 * （ 与那两件读工具同一条："得先有那个人的名字" ）。
 * ⚠️ **「手写」那一类不走"说"** ： 它是她自己写下来的一段 ⇒ 用「{她}手写：」。 */
const theWho = (one: A_Beat, other: string): string => {
  const mine = speaker();
  if (one.role === 'user') return (other === '' ? '对方' : other) + '说';
  if (one.role === '经历') return mine + '手写';
  if (one.role === 'assistant') return mine + '说';
  return '记下';
};

/** **那一条的正文**（ 前面那一小截是"谁说的"，后面**原话照抄** ）。
 *
 * ⚠️ `head` 只有**多行的那几条**才用得上： 那一行先摆**头一行** ，
 * 而 **全文一个字都没丢** （ 点「展开」就地摊开 —— 见 `stamp()` 那一段 ）。 */
const theWords = (one: A_Beat, other: string, head = false): string =>
  theWho(one, other) + '：' + (head ? theFirstLine(one.text) : one.text);

// ── **"哪一路来的"那一小截前缀** （2026-10-17，作者定的形状）──────────────
//
// 作者那一条（原话）：前缀太碍眼，而且"对方说"是错的；他定了形状 ——
//     [终端]特莉波卡说：
//     [QQ·私]XXX说：      ／  [QQ·群]XXX说：
//   「私」「群」是一个链接，点一下就地展开成
//     [QQ私·100000001]XXX说： ／ [QQ群·100000004]XXX说：
// 而「微／B…」那些别的平台，**由各自的插件附带增加**。
//
// ⚠️⚠️ **所以这一页不认识任何平台名** —— 它只认插件交上来的一份"词表 ＋ 标记正则"：
//   `package.json` 的 `telyincore.channels`（`GET /plugins` 带出来），形状：
//     `[{label: "QQ", markers: [{scope: "群", pattern: "[QQ群(?<room>\\d+)·消息#…"}]}]`
//   ⇒ 命中哪一条，就用那一条的 `label` ／ `scope` ／ 正则里的 `room` 与 `who` 拼前缀。
//   **加一个平台 = 那个插件在它自己的 package.json 里多写一条**，这一页一个字不用改。
//
// ⚠️ **为什么前缀从正文里"摘"而不是从后端要一份结构化的**：那些标记本来就是插件写进
//   正文的（`[QQ群100000004·消息#…] 示例成员：…`），而后端那一层只保证"正文原样"。
//   摘的时候**只摘命中的那一截**，后面的原话**一个字不动**。

/** 插件交上来的那一条标记（`/plugins` 的 `channels[]`）。 */
interface A_Marker {
  readonly scope?: string;
  readonly pattern?: string;
}

/** 插件交上来的那一路平台。 */
interface A_Channel {
  readonly label?: string;
  readonly routes?: readonly A_Marker[];
  readonly markers?: readonly A_Marker[];
}

/** 一条正文"从哪一路来的"（摘出来之后剩下的那截原话在 `rest`）。 */
interface A_Spoken {
  readonly label: string;
  readonly scope: string;
  readonly room: string;
  readonly who: string;
  readonly rest: string;
}

/** 每次进入页面重新读取插件词表，插件可以热重载。 */
let channels: readonly A_Channel[] = [];
let channelEpoch = 0;

/** 把插件交上来的词表读一次（读不到 ⇒ 一直用退路，不拦这一页）。 */
const learnChannels = async (): Promise<void> => {
  const epoch = ++channelEpoch;
  channels = []; matchers = null;
  try {
    const got = await fetch('/plugins');
    if (!got.ok) return;
    const answer = await got.json() as { plugins?: readonly { channels?: readonly A_Channel[] }[] };
    const all: A_Channel[] = [];
    for (const one of answer.plugins ?? []) {
      for (const ch of one.channels ?? []) {
        if (ch && Array.isArray(ch.markers)) all.push(ch);
      }
    }
    if (epoch !== channelEpoch) return;
    channels = all;
    matchers = null;
  } catch {
    // 读不到插件那一份 ⇒ 下面走退路（`[终端]` ／ 无名），这一页照常画
  }
};

/** 编译过的那几条（`pattern` 是插件的，坏了就跳过那一条 —— 不许带走整页）。 */
let matchers: readonly { channel: A_Channel; marker: A_Marker; re: RegExp }[] | null = null;

const theMatchers = (): readonly { channel: A_Channel; marker: A_Marker; re: RegExp }[] => {
  if (matchers !== null) return matchers;
  const out: { channel: A_Channel; marker: A_Marker; re: RegExp }[] = [];
  for (const channel of channels) {
    for (const marker of channel.markers ?? []) {
      const src = String(marker.pattern ?? '');
      if (src === '') continue;
      try {
        out.push({ channel, marker, re: new RegExp(src) });
      } catch {
        continue; // 插件写的正则坏了 ⇒ 只丢它那一条
      }
    }
  }
  matchers = out;
  return out;
};

/** **从正文里认出"哪一路来的"** —— 认不出 ⇒ `null`（走退路）。 */
const theSpoken = (text: string): A_Spoken | null => {
  const whole = String(text ?? '');
  for (const one of theMatchers()) {
    const got = one.re.exec(whole);
    if (got === null || got.index !== 0) continue;
    const groups = (got.groups ?? {}) as Record<string, string | undefined>;
    const scope = String(one.marker.scope ?? '');
    const rest = whole.slice(got[0].length).replace(/^\s+/, '');
    return {
      label: String(one.channel.label ?? ''),
      scope,
      room: String(groups.room ?? ''),
      who: String(groups.who ?? ''),
      rest,
    };
  }
  return null;
};

/** `[Q·群]` 就地展开为 `[Q·群·会话号]`；整段同色、无下划线。
 * 没保存会话号的旧记录不伪装成可点的链接。
 */
const thePrefixShape = (
  one: A_Spoken, opened: boolean,
  make: (tag: string, cls: string, text?: string) => HTMLElement,
  flip: () => void,
): HTMLElement => {
  const hot = one.room !== '';
  const box = make('span', 'ch', '');
  box.append(document.createTextNode('['));
  box.append(make('span', 'ch-who', one.label));
  box.append(make('span', 'ch-dot', '·'));
  const word = make('span', opened ? 'ch-scope is-open' : 'ch-scope',
                    opened ? one.scope + '·' + one.room : one.scope);
  if (hot) box.title = `${one.label} · ${one.scope} · ${one.room}`;
  box.append(word);
  box.append(document.createTextNode(']'));
  return box;
};

/** **路径折成一个短的来源名** （ 作者：「不要写个路径进去」）。
 *
 * ⚠️ 稿子里那一枚小字是「对话」／「手写」（`世界三页-设计稿-v3.html` ）——
 * 而那两样正是 `经历/` **底下那一层目录的名字** ⇒ 取倒数第二段 。
 * ⚠️ 只有两段（`经历/xxx.jsonl` ）⇒ 退一步取文件名（不带后缀 ）。
 */
const theFrom = (one: string): string => {
  const parts = String(one || '').split('/').filter((x) => x !== '');
  if (parts.length >= 3) return parts[parts.length - 2];
  return (parts[parts.length - 1] ?? '').replace(/\.[^.]+$/, '');
};

/** 那一份自己叫什么（ 不带路径、不带后缀 —— 下面那一列"一份一份的小结"用它 ）。 */
const theOwnName = (one: string): string =>
  String(one || '').split('/').filter((x) => x !== '').pop()?.replace(/\.[^.]+$/, '') ?? '';

/** 记录给条数；文档只给类别，文件体积不是读者要找的内容。 */
const theCount = (one: A_Lived): string =>
  one.kind === '记录' ? String(one.count) + ' 条' : '文档';

/** 页上的一行（ 一条记录 ＝ 一行 ）。 */
interface A_Row {
  readonly beat: A_Beat;
  readonly from: string;
  readonly day: A_Day;
  readonly file: A_Lived;
  /** 对方那个名字（ 那一份上带下来的 —— 没给就是空串 ）。 */
  readonly other: string;
  /** 它在**没筛过**那一整串里的序号 —— 只用来认"这一条摊开过没有" 。 */
  readonly id: string;
}

/** **一条记录的头一行**（ 那一格是"一行一条"，见下面 `theFold` 那条账 ）。 */
const theFirstLine = (one: string): string => String(one ?? '').split('\n')[0];

/** **一条记录是不是多行的**（ 是 ⇒ 那一行后面挂一个「还有 N 行 ▾」）。
 *
 * ⚠️ **它只判"有没有下文"** —— 不判长短、不按字数切
 * （ 盘上量到： 头一行最长 **161 字**，那一种摊开也只是一行半 ；
 * 而**最长的正文 1295 字**（那一条"悬着的事一共 23 件：……"）——
 * 它换行很多 ⇒ 落在"要展开"这一支里 ）。
 */
const theFold = (one: string): boolean => String(one ?? '').includes('\n');

/** 那一条还压着几行（ 就是那一枚「还有 N 行 ▾」上的 N ）。 */
const theRest = (one: string): number => String(one ?? '').split('\n').length - 1;

/** **一个筛字对不对得上那一行**（ 作者点名的那两样：**关键字** ＋ **时间戳（日期）** ）。
 *
 * ⚠️ **它只筛手里已经拿到的那一份** —— 不再打一次网
 * （ 与插件那一页那条「筛」与「搜」的分界同一条 ）。
 */
const hit = (one: A_Row, q: string): boolean => {
  const needle = q.toLowerCase();
  const fields = [one.beat.text, one.beat.role, theWords(one.beat, one.other), one.beat.at,
                  one.from, one.file.name, one.file.kind, theCount(one.file),
                  one.day.day, one.day.label, one.day.sub];
  return fields.some((x) => String(x).toLowerCase().includes(needle));
};

/** 那一页。 */
export function experiencePage(shell: Chrome): Panel {
  const root = document.createElement('section');
  root.className = 'page-experience page-world world-studio';

  const line = (tag: string, cls: string, text?: string): HTMLElement => {
    const one = document.createElement(tag);
    if (cls !== '') one.className = cls;
    if (text !== undefined) one.textContent = text;
    return one;
  };

  const head = document.createElement('div');
  head.className = 'head';
  const title = document.createElement('h1');
  coreSetUIText(title, 'textContent', corePhrase("经历 · EXPERIENCE"));
  // ⚠️ **抬头那句删了** （作者 2026-10-09 ）—— 抬头只留标题 ＋ 那个筛选框 。
  const find = searchBox('关键词或日期…');
  head.append(title, find.element);
  root.append(head);

  const axis = line('div', 'axis');
  const scroll = line('div', 'page-scroll experience-scroll');
  scroll.append(axis);
  root.append(scroll);

  const sources = line('div', 'experience-source-list');
  const sourceDialog = document.createElement('dialog');
  sourceDialog.className = 'avatar-crop-dialog experience-source-dialog';
  sourceDialog.setAttribute('aria-label', '记录来源');
  const closeSources = line('button', '', '关闭') as HTMLButtonElement;
  closeSources.type = 'button';
  closeSources.onclick = () => sourceDialog.close();
  const sourceActions = line('div', 'avatar-crop-actions');
  sourceActions.append(closeSources);
  sourceDialog.append(line('h2', '', '记录来源'), sources, sourceActions);
  const sourceButton = line('button', 'page-footer-link', '记录来源') as HTMLButtonElement;
  sourceButton.type = 'button';
  sourceButton.onclick = () => { sourceDialog.showModal(); closeSources.focus(); };
  sourceDialog.addEventListener('close', () => { if (!root.hidden) sourceButton.focus(); });
  sourceDialog.addEventListener('click', event => event.stopPropagation());
  sourceDialog.addEventListener('keydown', event => event.stopPropagation());
  const tail = document.createElement('footer');
  tail.className = 'tail page-footer';
  root.append(tail, sourceDialog);

  shell.content.replaceChildren(root);

  /** 已经摊开的那些天（ "展开更多"点过的 ）／ 已经摊开的那些条（ "展开"点过的 ）。 */
  const dayLimits = new Map<string, number>();
  /** 已经点开"会话号"的那些前缀（键 = 那一条自己的 id ＋ 标记本身）。 */
  const openedHeads = new Set<string>();
  let mine: ExperienceAnswer | null = null;
  let asked = '';

  /** 那一格（ 日子大字落在线的左边 ＋ 圆点骑在线上 ＋ 右边那个数 ）。 */
  const stamp = (key: string, label: string, sub: string, rows: A_Row[]): HTMLElement => {
    const box = line('div', 'stamp');
    box.dataset.dayKey = key;
    const day = line('div', 'day', label === '' ? '日期未记录' : label);
    if (sub !== '') day.append(line('span', '', sub));
    box.append(day, line('div', 'dot'));
    const limit = dayLimits.get(key) ?? SHOWN;
    const body = line('div', 'day-beats');
    body.id = 'experience-day-' + encodeURIComponent(key);
    body.setAttribute('role', 'region');
    coreSetUIAttribute(body, 'aria-label', [(label || '日期未记录'), corePhrase('的经历')]);
    const more = (): HTMLElement => {
      const button = line('button', 'fold-toggle', '显示更多'); button.setAttribute('type', 'button');
      button.addEventListener('click', () => { dayLimits.set(key, limit + 20); draw(mine, asked); });
      return button;
    };
    const meta = line('div', 'stamp-meta');
    meta.append(line('span', 'n', String(rows.length) + ' 条'));
    box.append(meta, body);
    const showed = rows.slice(0, limit);
    for (const one of showed) {
      const whole = one.beat.text;
      const row = line('div', 'beat');
      row.append(line('span', 'at', one.beat.at));
      // 来话与她的回复均按可信来源显示；整理记录不是对外说话。
      const marked = one.beat.role === 'user' ? theSpoken(whole) : null;
      let routed: A_Spoken | null = null;
      for (const channel of channels) {
        for (const route of channel.routes ?? []) {
          try {
            const match = new RegExp(route.pattern || '^$').exec(one.beat.scope || '');
            if (match && match.index === 0 && match.groups?.room) {
              routed = { label: channel.label || '', scope: route.scope || '',
                room: match.groups.room, who: '', rest: whole };
            }
          } catch { /* Ignore one malformed plugin route. */ }
        }
      }
      const spoken = marked ? { ...marked, room: marked.room || routed?.room || '' }
        : routed;
      const folded = theFold(whole);
      const txt = line('span', 'txt', '');
      if (one.file.activity === 'reflection') {
        txt.append(line('span', 'ch', '[整理]'));
        txt.append(coreUITextNode([[speaker(), corePhrase("记下：")], ((folded ? theFirstLine(whole) : whole))]));
      } else if (spoken !== null) {
        const headKey = String(one.id) + '|' + spoken.label + spoken.scope + spoken.room;
        const opened = openedHeads.has(headKey);
        txt.append(thePrefixShape(
          spoken, opened, line,
          () => {
            if (openedHeads.has(headKey)) openedHeads.delete(headKey);
            else openedHeads.add(headKey);
            draw(mine, asked);
          }));
        const who = one.beat.role === 'assistant' ? speaker()
          : one.beat.speaker_name || (spoken.who !== '' ? spoken.who : (one.other || '用户'));
        txt.append(coreUITextNode([[who, corePhrase("说：")], ((folded ? theFirstLine(spoken.rest) : spoken.rest))]));
      } else {
        const isTerminal = ['console:local-owner', 'private:console:local-owner']
          .includes(one.beat.scope || '');
        const isSpeech = ['user', 'assistant'].includes(one.beat.role);
        if (isTerminal || isSpeech) {
          const origin = line('span', 'ch', isTerminal ? '[终端]' : '[来源未记录]');
          if (!isTerminal) coreSetUIText(origin, 'title', (one.beat.scope ? corePhrase("记录保存了通道标识，但对应插件尚未提供显示规则。") : corePhrase("这条旧记录未保存通道信息，无法可靠确认来自终端还是外部平台。")));
          txt.append(origin);
        }
        txt.append(coreUITextNode((one.beat.role === 'user' ? [[((isTerminal ? (one.other || '用户') : corePhrase("用户"))), corePhrase("说：")], ((folded ? theFirstLine(whole) : whole))] : theWords(one.beat, one.other, folded))));
      }
      txt.append(line('span', 'src', one.from));
      row.append(txt);
      // Keep the timeline concise; the complete text opens in a separate reader.
      if (theFold(whole)) {
        const view = line('button', 'op', '查看全文'); view.setAttribute('type', 'button');
        view.addEventListener('click', () => showTextDetail('经历记录', whole)); txt.append(view);
      }
      body.append(row);
    }
    const left = rows.length - showed.length;
    if (left > 0) {
      const status = line('div', 'fold', '还有 ' + left + ' 条 · '); status.append(more()); box.append(status);
    }
    return box;
  };

  const draw = (answer: ExperienceAnswer | null, q: string): void => {
    axis.replaceChildren();
    sources.replaceChildren();
    tail.replaceChildren(line('span', 'page-footer-label', '对话与经历'));
    sourceButton.disabled = true;
    mine = answer;
    asked = q;

    if (answer === null || answer.wired === false) {
      const prose = line('div', 'prose');
      prose.append(line('p', 'fade', answer === null ? READ_FAILED : NOT_WIRED));
      axis.append(prose);
      return;
    }

    // 旧服务仍可能把备份文件列进来；经历页只展示原始记录与文档。
    const rows = (answer.experience ?? []).filter((one) => !one.name.includes('.bak-'));
    // 那一份**记录类合起来多少条**（ 现算 —— 数不许编 ）—— 它只进尾那一行 。
    const howMany = rows.filter((one) => one.kind === '记录')
      .reduce((sum, one) => sum + one.count, 0);

    // ── 一条记录 ＝ 一行（ 顺序**照入口排好的**（一天里倒着排 ） —— 页上不再排 ）──
    let flat: A_Row[] = [];
    let id = 0;
    for (const one of rows) {
      for (const day of one.days ?? []) {
        for (const beat of day.beats) {
          flat.push({ beat, from: theFrom(one.name), day, file: one,
                      other: String(one.other ?? ''), id: JSON.stringify([one.name, beat.role, beat.sort_ts, beat.scope, beat.text]) });
          id += 1;
        }
      }
    }
    // **筛**（ 手里那一份，就地 ）—— 一个都没对上的时候照实说 。
    if (q !== '') flat = flat.filter((one) => hit(one, q));

    // Keep dated groups newest first and show undated records separately at the end.
    const days = new Map<string, A_Row[]>();
    for (const one of flat) {
      const key = one.day.day;
      const box = days.get(key) ?? [];
      box.push(one);
      days.set(key, box);
    }
    const keys = [...days.keys()].filter(key => key !== '').sort((a, b) => b.localeCompare(a));
    if (days.has('')) keys.push('');
    for (const entries of days.values()) entries.sort((a, b) =>
      (b.beat.sort_ts ?? 0) - (a.beat.sort_ts ?? 0));
    if (flat.length === 0) {
      const prose = line('div', 'prose');
      prose.append(line('p', 'fade', q === '' ? NOTHING_YET : '「' + q + '」没有匹配记录。'));
      axis.append(prose);
    } else {
      for (const key of keys) {
        const box = days.get(key) as A_Row[];
        const label = key === '' ? '日期未记录' : (box[0].day.label || key);
        axis.append(stamp(key || 'undated', label, key === '' ? '' : box[0].day.sub, box));
      }
    }

    // ── 一份一份的小结（ 稿子 v2 那块 `.sources` —— 它是"次"⇒ 沉在下面 ）────
    // ⚠️ **它也认那个筛字** —— 不然筛成一条了、下面还列着三份 ⇒
    // 那读起来像"筛没生效" 。 两种都算命中： **那一条对得上**
    // （那一份里有命中的记录 ）或 **那一份自己对得上**（名字 ／ 类别 ／ 条数 ）。
    if (rows.length > 0) {
      const kept = new Set<string>();
      for (const one of flat) kept.add(one.file.name);
      if (q !== '') {
        const needle = q.toLowerCase();
        for (const one of rows) {
          const mine2 = [one.name, one.kind, theCount(one), theOwnName(one.name)];
          if (mine2.some((x) => String(x).toLowerCase().includes(needle))) kept.add(one.name);
        }
      }
      const sourceList = sources;
      for (const one of rows) {
        if (q !== '' && !kept.has(one.name)) continue;
        const bit = line('span', 'one', experienceSourceName(one.name));
        bit.title = one.name;
        bit.append(line('span', 'thin', one.kind === '记录' ? theCount(one)
                                                              : '文档'));
        sourceList.append(bit);
      }
    }

    sourceButton.disabled = sources.childElementCount === 0;
    sourceButton.textContent = '记录来源 · ' + sources.childElementCount;
    tail.append(sourceButton,
                line('span', 'page-footer-summary', '共 ' + String(rows.length) + ' 份 · '
                      + String(howMany) + ' 条'));
  };

  /** 只打一次网（ 之后"筛"就在手里那一份上做 —— 作者要的那个框就是这个意思 ）。 */
  let requestId = 0;
  let previousSnapshot = '';
  const readHint = pageLoad(root, '经历', () => { void refresh(); });
  const refresh = async (): Promise<void> => {
    const mine = ++requestId;
    readHint.start();
    try {
      const answer = await getStatusJson<ExperienceAnswer>('/experience');
      if (mine !== requestId || root.hidden) return;
      const snapshot = JSON.stringify(answer);
      if (snapshot !== previousSnapshot) {
        previousSnapshot = snapshot; draw(answer, find.input.value.trim());
      }
      readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) { void refresh(); return; }
      console.error('经历读取失败：', why); readHint.fail();
    }
  };

  find.onAsk((q) => { if (mine !== null) draw(mine, q); });

  const liveRecords = recordRefresh(root, refresh);

  return {
    root,
    show(): void {
      root.hidden = false;
      liveRecords.start();
      // Start the timeline now. Slow name/plugin metadata must not delay its read.
      void refresh();
      const epoch = requestId;
      void Promise.all([learnChannels(), loadSpeaker()]).then(() => {
        if (epoch === requestId && !root.hidden && mine !== null) {
          draw(mine, find.input.value.trim());
        }
      });
    },
    stop(): void {
      liveRecords.stop();
      if (sourceDialog.open) sourceDialog.close();
      requestId += 1;
      root.hidden = true;
    },
  };
}
