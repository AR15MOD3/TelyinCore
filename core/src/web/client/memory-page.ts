import { uiLabel } from './ui-language';
import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { displayDate } from './display-date';
import { recordRefresh } from './record-refresh';
import { pageLoad } from './page-load';
import { getStatusJson, StaleStatusError } from './status-json';
import { pageFetch as fetch } from './status-json';
// 「记忆」那一页（ 第一刀：**只做那一件真事** —— 把 `GET /memory` 真读出来画上）。
//
// ⚠️ **它为什么现在能做了**（写清，免得后人以为这是抢跑）：
//    · 服务那一侧**早就有了**：`web.py` 那条 `/memory` 路由 ＋ 入口递的读法
// （`launch.py`：`see_everyone = lambda: memory.everyone(home)` ）；
//    · 而内核那一侧 `memory.py` 交的是 `everyone()`：**每一行四样**（`who` ／ `standing` ／
// `favor` ／ `blocked`）＋ `how_many_times` —— 全都是**它记过的**，不补齐、不算分 。
// ⇒ 所以这一页**今天只画它**： **不画第二件还没接上的东西**
// （"不许把不知道画成一个空壳" —— 知识 ／ 待确认 ／ 那些话那几块要各自先有读法 ）。
//
// ⚠️ **它落哪一组**：与「人设」同一组（`PERSONA & MEMORY` ）—— 而 `INFINITY` 那一组必须
// **留在 `PAGES` 的最后**（`groupsOf()` 按首次出现排 ，见 `pages.ts` 那段注释 ）。

import type { Chrome, Panel } from './shell';
import { personaMemoryTail } from './persona-memory';

/** 它所有说过话的人 —— 一行四样（照 `memory.everyone()` 的契约 ）。 */
export interface A_Person {
  /** 谁（那一格目录的名字）。 */
  readonly who: string;
  /** 操作者界面别名，不作为画像键或模型身份依据。 */
  readonly display_name?: string;
  /** **它给的那条说法**（可能没有 ⇒ `null`）。 */
  readonly standing: string | null;
  /** **它写的**好感度（可能没有 ⇒ `null`；`0` 与 `null` 是两件事 ）。 */
  readonly favor: number | null;
  /** **作者那一格**保底（拉黑／放行那种）。 */
  readonly blocked: boolean;
  /** 记过几条关系（ 那是"记过几次"，不是"关系好不好" ）。 */
  readonly how_many_times: number;
  /** 画像与关系的最近记录时间（毫秒）；不是最后对话时间。 */
  readonly recent_at?: number | null;
  /** 兼容尚未重启的服务，读取已有画像记录的时间。 */
  readonly said?: readonly { readonly ts: string }[];
}

/** 最近记下的六份画像；没有时间的记录保留原顺序，排在有时间的后面。 */
export function recentPeople(everyone: readonly A_Person[]): readonly A_Person[] {
  const recordedAt = (one: A_Person): number => {
    if (typeof one.recent_at === 'number' && Number.isFinite(one.recent_at)) {
      return one.recent_at;
    }
    const times = (one.said ?? []).map(({ ts }) => {
      const raw = String(ts).trim();
      if (/^\d+$/.test(raw)) {
        const value = Number(raw);
        return value > 10_000_000_000 ? value : value * 1000;
      }
      const compact = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(raw);
      const value = compact
        ? new Date(+compact[1], +compact[2] - 1, +compact[3],
          +compact[4], +compact[5], +compact[6]).getTime()
        : Date.parse(raw);
      if (compact) {
        const date = new Date(value);
        if (date.getFullYear() !== +compact[1] || date.getMonth() !== +compact[2] - 1
          || date.getDate() !== +compact[3] || date.getHours() !== +compact[4]
          || date.getMinutes() !== +compact[5] || date.getSeconds() !== +compact[6]) {
          return 0;
        }
      }
      return Number.isFinite(value) ? value : 0;
    });
    return Math.max(0, ...times);
  };
  return everyone.map((one) => ({ one, at: recordedAt(one) }))
    .sort((a, b) => b.at - a.at).slice(0, 6).map(({ one }) => one);
}

/** **她自己写的一份**（照 `persona/她写的/*.md` 盘上的真状态 ）。 */
export interface A_Hers {
  /** 那一份的名字（`自述` ／ `世界认知` ／ `习惯` ）。 */
  readonly name: string;
  /** **相对 `persona/`** 的名字 ⇒ 拿它调 `POST /open` 。 */
  readonly key: string;
  /** 盘上的**真字节数**（ `0` ＝ **还是空的** —— 这一页不许编 ）。 */
  readonly size: number;
  /** **非空的行数**（ 空文件就是 `0` —— 那一行右边要写「N 行」）。 */
  readonly lines?: number;
  /** **最后写于**（ 形如「10 月 5 日」）—— ⚠️ **空文件就是空串** ：
   * 一个 0 字节的文件**没有**「最后写于」可言 ⇒ 那一句**只有非空才给** 。 */
  readonly at?: string;
}

/** `GET /memory` 那一格交回来的东西。 */
export interface MemoryAnswer {
  /** 那一张表（一个都没有 ⇒ 空那一串 —— 那是**一个答案** ）。 */
  readonly everyone: readonly A_Person[];
  /** **心里悬着的事**（2026-10-07，作者拍的「①a」）—— **只读**：
   * 这一页**不批 ／ 不催 ／ 不画期限**（那三条是定下的）； 缺这一格照旧能画 。 */
  readonly pending?: readonly A_Heart[];
  /** **它定下来的那些事**（2026-10-07「②」）—— 同样**只读**（不写、不排序、不算时间）。 */
  readonly knowledge?: readonly A_Note[];
  /** **它经历过什么**（2026-10-07「③」）—— 只读（不写、不排序、不算时间）。 */
  readonly experience?: readonly A_Lived[];
  /**
   * **她自己写的那三份**（2026-10-08 作者：「好好检查一下 —— 那些东西指向的是
   * 文件夹里真实存在的文件」）—— 就是 `persona/她写的/*.md` 那三份 。
   *
   * · `name` ＝ 那一份的名字（`自述` ／ `世界认知` ／ `习惯` ）
   * · `key` ＝ **相对 `persona/`** 的名字 ⇒ 那一页拿它调 `POST /open`
   * · `size` ＝ 盘上的**真字节数** （ 而"0"就是**还是空的** —— 那一页不许编 ）
   * ⚠️ 缺这一格 ⇒ 那四行只画「浮现」那一行 （ 不许编 ）。
   */
  readonly hers?: readonly A_Hers[];
  /** ⚠️ **这个口接了没有**：`false` ⇒ 页上写"这一层没接上读法"（与"一个人都没有"分得开 ）。 */
  readonly wired: boolean;
}

/** `GET /skills` 交回来的一条（**只读**：这一页不合并、不回滚、不改正文）。 */
export interface A_Skill {
  readonly id: string;
  readonly title: string;
  /** `active` ／ `merged` —— 已并入的**只在历史那一格**出现。 */
  readonly state: string;
  /** **前 12 位**（完整 SHA-256 在元数据里；页上不比全串）。 */
  readonly version: string;
  readonly merged_into?: string | null;
  readonly op?: string | null;
  readonly history: number;
  readonly kind?: 'communication' | 'operation' | 'unclassified';
  readonly recorded_at?: string;
  readonly body?: string;
  readonly references?: readonly string[];
}

/** 一次合并操作的账（只读）。 */
export interface A_SkillOp {
  readonly op: string;
  readonly stage: string;
  readonly keep: string;
  readonly sources: readonly string[];
  readonly at: string;
}

/** **运行状态**（服务算好的六档之一；`unknown` 表示"读不到／没接"）。 */
export interface A_RunStatus {
  readonly state: string;
  readonly recovery_pending?: number;
  readonly why?: string;
  /** 同一任务的历史事实（2026-10-18 补）：状态说**当前结果**，这里说**发生过什么**。 */
  readonly detail?: {
    readonly partial?: boolean;
    readonly resolved?: boolean;
    readonly committed?: readonly string[];
    readonly error?: string;
    readonly error_class?: string;
    readonly failed_at?: string;
    readonly followup_uncertain?: boolean;
    readonly attempts?: number;
  };
}

/** `GET /skills` 那一格交回来的东西。 */
export interface SkillsAnswer {
  readonly wired: boolean;
  readonly active?: readonly A_Skill[];
  readonly merged?: readonly A_Skill[];
  readonly missing?: readonly A_Skill[];
  readonly index_status?: { readonly state: string; readonly indexed_at?: string };
  readonly ops?: readonly A_SkillOp[];
  readonly active_count?: number;
  readonly merged_count?: number;
  readonly status?: A_RunStatus;
  readonly error?: string;
}

/** **经历过的一份**（照那一棵树交回来的三格 ）。 */
export interface A_Lived {
  /** 那一份的名字（相对她那个家）。 */
  readonly name: string;
  /** 是"记录"还是"文档"。 */
  readonly kind: string;
  /** 记录 ⇒ 几条；文档 ⇒ 多少字节（ **不是时间** ）。 */
  readonly count: number;
}

/** **它记下的一条知识**（照 `memory.knowledge_of()` 交回来的那两格 ）。 */
export interface A_Note {
  /** 那一条的名字（文件名）。 */
  readonly name: string;
  /** 正文（ 这一页只摆**头一行**，全文归它自己那一条 ）。 */
  readonly text: string;
}

/** 一件**悬着的事**（照 `memory.pending_of()` 交回来的那几格 ）—— **一个字都不解释它**。 */
export interface A_Heart {
  readonly full?: boolean;
  /** 那一刻**只当出处看**（ **不当期限、不算天数、不标红** ）。 */
  readonly ts: string;
  /** 那件事的原话。 */
  readonly text: string;
  /** 哪一类（她自己观察的 ／ 关于自己的）。 */
  readonly role: string;
  /** 从哪儿来。 */
  readonly source: string;
  readonly proposal?: { id: string; section: string; why: string; before: string };
  /**
   * **这一条是哪一格**（2026-10-08 作者拍的「①甲 ＋ ②甲」）——
   * 由入口那一侧盖上来（`launch.see_pending()` ）， 因为 `web.py` **零同胞 import**
   * ⇒ 它够不到 `unresolved` 。
   * · `proposal` = 她写下的提议（ 去处是「人设」那一页，等放行 ）
   * · `selfmade` = **她自己那一本**（ 去处是**她下一轮的眼前** ）
   * · `unfixed` = 她还**没放下**的（ 含闹钟到点那些 ）
   * ⚠️ 缺省当 `proposal`（ 老数据与老替身都走这一条 —— 不许因为多一格就画不出来 ）。
   */
  readonly kind?: string;
  /**
   * `kind === 'outbox'` 那一行才有的三格（ 2026-10-17 补 —— 用户点的：
   * "你看到了今天 0 封，但你没看到最近一封是什么" ）。
   * · `today` ＝ **今天**主动发起过几封；
   * · `day` ＝ 最近那一封是哪一天（`YYYY-MM-DD`）；
   * · `how_many` ＝ 匣子里一共几封（不限今天）。
   */
  readonly today?: number;
  readonly day?: string;
  readonly how_many?: number;
}

const READ_FAILED = '读不到记忆那一格。';
const NOT_WIRED = '记忆这一层没接上读法。';
const NOTHING_HANGING = '心里没有悬着的事。';

/** 那一页。 */
const RUN_STATE: readonly (readonly [string, string])[] = [
  ['running', '运行中'],
  ['idle', '空闲'],
  ['failed', '失败'],
  ['partial', '部分完成'],
  ['recovery-pending', '待恢复'],
  ['unknown', '未知／未提供'],
];

const stateName = (said: string): string =>
  RUN_STATE.find((one) => one[0] === said)?.[1] ?? said;

export function memoryPage(shell: Chrome): Panel {
  const root = document.createElement('section');
  root.className = 'memory page-memory memory-studio';
  // 2026-10-09 作者：「**标签也统一一下。**」 ⇒ **`h2` → `h1`**
  // —— 三个世界页的标题都是 `h1` ，而这一页一直是 `h2` （一个页面按惯例该有一个 `h1` ）。
  // ⚠️ 只换标签 —— 字号 ／ 字重 ／ 文字**一个都不动** （ 那两样在 `style.css` 里 ）。
  const title = document.createElement('h1');
  title.className = 'plugins-title';
  // 2026-10-08 作者：「把 记忆 改成 **记忆 · MEMORY**」 —— ⚠️ **改的是这一页的标题**
  // **不是侧边栏那一格**（ 那一格在 `shell.ts` 里、一个字不动 ）。
  // 2026-10-09 作者为这一行标题连点了**三步**， **最后一步是"撤掉"** ⇒ **这一行回到原样**
  // —— 账留在这儿， 免得下一班把这三步再走一遍 ：
  // · 「**在记忆的记字前面插入两个换行**」⇒ 我**看错了**：把 `· MEMORY` 换成了两行 ；
  // · 他纠正：「**不是让你换行 · MEMORY**」⇒ 改成**标题上面空两个空行** ；
  // · 「**去掉一个空行，一个就够了。**」⇒ 空一个 ；
  // · 「**把这个空行也撤掉。**」⇒ **一条都不留** 。
  // ⇒ 今天标题就是**一行 `记忆 · MEMORY`** 、**上面没有任何额外空行**
  // （ 那一笔的账在 `style.css` 那一处也留了一份 ）。
  coreSetUIText(title, 'textContent', corePhrase("记忆 · MEMORY"));
  const note = document.createElement('p');
  note.className = 'plugins-note';
  note.setAttribute('role', 'status');
  note.setAttribute('aria-live', 'polite');
  // **初值空着**（ 那一条老账：起始别摆一句会闪的话 ）
  note.textContent = '';
  const head = document.createElement('header');
  head.className = 'memory-head';
  head.append(title, note);
  root.append(head);
  // ⚠️ **"挂上去"那一步**（少了它左栏点得动、正文是空的 —— 真驱动浏览器抓到过一次 ）
  shell.content.replaceChildren(root);

  // Polling may replace content, but must not move the reading position.
  const keepPosition = (render: () => void): void => {
    const parents: Array<[HTMLElement, number, number]> = [];
    for (let el: HTMLElement | null = root; el; el = el.parentElement) parents.push([el, el.scrollTop, el.scrollLeft]);
    const x = window.scrollX, y = window.scrollY;
    const inner = [...root.querySelectorAll<HTMLElement>('.memory-pull-content')].map(el => el.scrollTop);
    render();
    root.querySelectorAll<HTMLElement>('.memory-pull-content').forEach((el, i) => { el.scrollTop = inner[i] || 0; });
    for (const [el, top, left] of parents) { el.scrollTop = top; el.scrollLeft = left; }
    window.scrollTo(x, y);
  };
  const draw = (answer: MemoryAnswer | null): void => keepPosition(() => drawContents(answer));
  const drawContents = (answer: MemoryAnswer | null): void => {
    const expanded = new Set([...root.querySelectorAll<HTMLDetailsElement>('.memory-entry')].filter(e => e.open).map(e => e.id));
    for (const old of root.querySelectorAll('.memory-row')) { old.remove(); }
    if (answer === null) { note.textContent = READ_FAILED; return; }
    if (!answer.wired) { note.textContent = NOT_WIRED; return; }
    note.textContent = '';
    const mine = answer.pending ?? [];
    const selfmade = mine.filter((one) => (one.kind ?? 'proposal') === 'selfmade');
    const unfixed = mine.filter((one) => (one.kind ?? 'proposal') === 'unfixed');
    const proposals = mine.filter((one) => (one.kind ?? 'proposal') === 'proposal');

    // **那三格的名字只写一处**（2026-10-08 作者：「这两句的标题，差了一个字，
    // 查证一下有没有影响」）—— 查证的结论是： **功能上不影响** （三处各是各的字面量）
    // 而 **看的人会以为是两样** ⇒ 干脆**抽成一处** ：
    // · 名字用的那个是 **树里的权威名** —— `telyincore/unresolved.py` 的抬头与
    // `tools.py` 那两件工具的说明，写的都是「**还没放下的事**」；
    // · 而"三格数"与"那几样"**都从这一处取** ⇒ **再也不会两样** 。
    const THE_THREE: readonly (readonly [string, string, string])[] = [
      ['proposal', '心里悬着的', '暂无待处理事项。'],
      ['selfmade', '留下的', '暂无留下的记录。'],
      ['unfixed', '还没放下的', '暂无未完成事项。'],
    ];
    const nameOf = (kind: string): string =>
      THE_THREE.find((one) => one[0] === kind)?.[1] ?? kind;
    const blurbOf = (kind: string): string => {
      const said = THE_THREE.find((one) => one[0] === kind)?.[2] ?? '这一格还没有东西。';
      // **2026-10-10：到顶了就说出来** —— 与上面那个 `kind` **同一个成法** ：
      // 那一格是**服务算好的** （`launch.see_pending()` 每一条带一个 `full` ），
      // 因为 `web.py` 够了不到那两本的上限 、而这页也够不到 。
      // ⚠️ **本仓的规矩是"不许静默"** ： 一本满了以后**再写就压掉最旧的**
      // ⇒ 页上不说， 那就是**悄悄丢东西** 。
      const one = mine.find((it) => (it.kind ?? 'proposal') === kind);
      if (one !== undefined && one.full === true) {
        return said + ' 这本已经到顶了 —— 再写就会压掉最旧的。';
      }
      return said;
    };

    // ── **她最近写下的那一句**（2026-10-08 照 v10 稿：薄荷底那一块）────────────
    // 取她自己那一本的**最新一条** ； 一条都没有 ⇒ 照实说"还是空的" （不编 ）。
    const star = document.createElement('div');
    star.className = selfmade.length === 0
      ? 'memory-star memory-row is-empty' : 'memory-star memory-row';
    const starLbl = document.createElement('div');
    starLbl.className = 'lbl';
    coreSetUIText(starLbl, 'textContent', corePhrase("最近写下的那一句"));
    const starSay = document.createElement('p');
    starSay.className = selfmade.length === 0 ? 'say void' : 'say';
    coreSetUIText(starSay, 'textContent', (selfmade.length === 0 ? corePhrase("还是空的 —— 这边还没写下过第一句。") : String(selfmade[selfmade.length - 1].text)));
    const starBy = document.createElement('p');
    starBy.className = 'by';
    coreSetUIText(starBy, 'textContent', corePhrase("只在这边回头看的时候才会写；不是每天都有。"));
    star.append(starLbl, starSay, starBy);
    root.append(star);
    // **顶上那三格数**（2026-10-08 照 v10 稿）—— 而**三个数都是现算的**
    // （ 稿子里那三格的词我换成了真算得出的： 数不许编 ）。
    const howMany = (kind: string): number =>
      mine.filter((one) => (one.kind ?? 'proposal') === kind).length;
    const figs = document.createElement('div');
    figs.className = 'memory-row memory-figs';
    const FIG_ROWS: readonly (readonly [number, string, string, boolean])[] = [
      [howMany('proposal'), '件', nameOf('proposal'), true],
      [howMany('selfmade'), '条', nameOf('selfmade'), false],
      [howMany('unfixed'), '件', nameOf('unfixed'), false],
    ];
    for (const [count, unit, label, hot] of FIG_ROWS) {
      const one = document.createElement('div');
      one.className = hot ? 'memory-fig hot' : 'memory-fig';
      const num = document.createElement('div');
      num.className = 'n';
      num.textContent = String(count);
      const em = document.createElement('em');
      em.textContent = unit;
      num.append(em);
      const key = document.createElement('div');
      key.className = 'k';
      uiLabel(key, label);
      one.append(num, key);
      figs.append(one);
    }
    head.append(figs);




    // ── **那一节四行**（2026-10-08 照 v10 稿：自述 ／ 世界认知 ／ 习惯 ／ 浮现）────
    // 四行是**固定的四行** ； 各自的值从对应的地方取 ； 没有的照实说 。
    const head2 = document.createElement('h2');
    head2.className = 'memory-document-title';
    coreSetUIText(head2, 'textContent', corePhrase("自我记录"));
    const documents = document.createElement('section');
    documents.className = 'memory-documents memory-row';
    documents.append(head2);
    const list = document.createElement('ul');
    list.className = 'memory-list';
    // **那四行的真身**（2026-10-08 作者："好好检查一下 —— 它们指向的是
    // 文件夹里真实存在的文件" ）： 前三行是 `persona/她写的/*.md` **那三份真文件**
    // （由 `/memory` 的 `hers` 交上来 ）， 而第四行「浮现」**不落成文件** 。
    // ⚠️ **值只说真话**： 那三份各 **0 字节** ⇒ 写"还是空的"
    // （ 原来那四行**是编的** —— "10 条"／"还有 9 行"／"还没有长出来" 全都没有底 ）。
    const hers = answer.hers ?? [];
    // 「那一行右边」要的两个数： **行数** 与 **最后写于**（ 都从 `hers` 现取
    // —— 而后端那一侧是**现读盘**给的 ／ 空文件就照实是 0 行与空串 ）。
    const sizeOf = (k: string): number =>
      hers.find((one) => one.key === k)?.lines ?? 0;
    const atOf = (k: string): string =>
      hers.find((one) => one.key === k)?.at ?? '';
    const ROWS: (readonly [string, string, string, string])[] = [];
    for (const one of hers) {
      ROWS.push([one.name, one.size === 0 ? '还是空的' : String(one.size) + ' 字节',
                 one.size === 0 ? 'void' : '', one.key]);
    }
    ROWS.push(['浮现', '今天没有浮出什么；只在有话说的时候才出现。', 'void', '']);
    for (const [key, value, cls, keyOf] of ROWS) {
      const li = document.createElement('li');
      const line = document.createElement('div');
      line.className = 'memory-line';
      const k = document.createElement('span');
      k.className = 'k';
      k.textContent = key;
      const v = document.createElement('span');
      v.className = cls === 'void' ? 'v void' : 'v';
      v.textContent = value;
      const doit = document.createElement('span');
      doit.className = 'do';
      if (keyOf === '') {
        // **2026-10-09 作者：「把记忆页的不落成文件删掉。」** ——
        // 第四行（「浮现」）那一个**行尾的字**（`不落成文件` ）**整个不画了**
        // （ 那一格 `doit` 就空着 —— 与"那一行本来就没有那一下"是同一件事 ）。
        // ⚠️ 那一行**本身**（「浮现 ＋ 今天没有浮出什么…」）**一个字没动** ；
        // 另外三行那一下（「0 行 · 在文件夹里打开」）也**一个字没动** 。
        doit.textContent = '';
      } else {
        // **行尾那句元信息**（2026-10-08 作者：「做①」 —— 照 v10 稿：
        // 「**N 行 · 最后写于 X 月 X 日**」）。
        // ⚠️ **它只说真话** ： 那三份今天是 **0 行** ⇒ 只写「0 行」
        // ／ 而"最后写于"那一句**只有非空才给** （ 0 字节的文件没有
        // "最后写于"可言 —— 不编 ）。
        // ⚠️ 这一格**本来就归稿子那一列**（`<span class="do">` ）⇒
        // 「收起 · 在文件夹里打开」那种写法这里也用 （ 两样并排 ）。
        const meta = document.createElement('span');
        meta.className = 'when';
        coreSetUIText(meta, 'textContent', ([[String(sizeOf(keyOf)), corePhrase(" 行")], ((atOf(keyOf) === '' ? corePhrase("") : [corePhrase(" · 最后写于 "), atOf(keyOf)]))]));
        // **在文件夹里打开**（2026-10-08： 后端那条口**早就做了** ——
        // `POST /open {kind, key}` ⇒ 这里接上它 ）。
        const open = document.createElement('button');
        open.className = 'op';
        open.type = 'button';
        coreSetUIAttribute(open, 'aria-label', [corePhrase("在文件夹里打开"), key]);
        coreSetUIText(open, 'textContent', corePhrase("在文件夹里打开"));
        const openStatus = document.createElement('span');
        openStatus.setAttribute('role', 'status');
        open.addEventListener('click', (ev) => {
          ev.stopPropagation();
          open.disabled = true;
          openStatus.textContent = '';
          void fetch('/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: 'persona', key: keyOf }),
          }).then((response) => {
            if (!response.ok) coreSetUIText(openStatus, 'textContent', (response.status === 404 ? corePhrase(" · 未找到文件") : corePhrase(" · 无法打开文件夹")));
          }).catch(() => {
            coreSetUIText(openStatus, 'textContent', corePhrase(" · 无法连接本机服务"));
          }).finally(() => {
            open.disabled = false;
          });
        });
        doit.append(meta, document.createTextNode(' · '), open, openStatus);
      }
      line.append(k, v, doit);
      li.append(line);
      list.append(li);
    }
    documents.append(list);
    root.append(documents);
    root.append(skillsBox);
    // ── **她写下的那几样**（2026-10-08 照 v10 稿：两列 ＋ 一条绿竖线 ）────
    // ⚠️ **这一块用的是 `.memory-pull`**（ 两列 ）—— 而它**不是** `.memory-cards`
    // （ 那一条是给"它自己写的那几份"用的、单列 ）： 两处**形状不同** 。
    const pull = document.createElement('div');
    pull.className = 'memory-row memory-pull';
    const pullHead = document.createElement('h2');
    pullHead.className = 'memory-sec memory-row';
    coreSetUIText(pullHead, 'textContent', corePhrase("记下的事"));
    root.append(pullHead);
    const CARDS: readonly (readonly [string, A_Heart[], boolean])[] = [
      ['proposal', proposals, false],
      ['selfmade', selfmade, false],
      ['unfixed', unfixed, false],
    ];
    for (const [kindOf, rowsOf, quiet] of CARDS) {
      const title = nameOf(kindOf);
      const one = document.createElement('div');
      one.className = (quiet ? 'item quiet' : 'item')
        + (rowsOf.length === 0 ? ' is-empty' : '');
      const h = document.createElement('h3');
      uiLabel(h, title);
      one.append(h);
      const content = document.createElement('div');
      content.className = 'memory-pull-content';
      if (rowsOf.length === 0) {
        const said = document.createElement('p');
        uiLabel(said, blurbOf(kindOf));
        content.append(said);
      }
      // **同一条只画一次，后面写 ×N**（ 乙 ）—— 照 v10 的写法：
      // "×N"单独一行摆在那一句下面 （ 稿子里就是那样 ）。
      const merged = new Map<string, { one: A_Heart; how: number }>();
      for (const each of rowsOf) {
        const key = each.proposal?.id || each.text;
        const had = merged.get(key);
        if (had) { had.how += 1; } else { merged.set(key, { one: each, how: 1 }); }
      }
      const entries = document.createElement('div');
      entries.className = 'memory-entry-list';
      // Newest first; cap the overview without deleting the source records.
      const latest = [...merged.values()].reverse();
      const shown = latest.slice(0, 1);
      for (const { one: each, how } of shown) {
        const entry = document.createElement('details');
        entry.className = 'memory-entry';
        entry.id = 'memory-entry-' + encodeURIComponent(kindOf + ':' + (each.proposal?.id || String(each.ts || '') + ':' + each.text.slice(0, 160)));
        entry.open = expanded.has(entry.id);
        const summary = document.createElement('summary');
        const short = (value: string): string => value.replace(/\s+/g, ' ').slice(0, 160);
        summary.textContent = each.proposal
          ? short(each.source + ' · ' + each.proposal.section + '：' + each.proposal.why)
          : short(String(each.text));
        if (how > 1) summary.textContent += ' ×' + String(how);
        const body = document.createElement('div');
        body.className = 'memory-entry-detail';
        const said = document.createElement('p');
        coreSetUIText(said, 'textContent', (each.proposal ? [corePhrase("讨论稿\n"), each.text] : String(each.text)));
        body.append(said);
        if (each.proposal) {
          const original = document.createElement('p');
          coreSetUIText(original, 'textContent', [corePhrase("原文\n"), each.proposal.before]);
          body.append(original);
          const state = document.createElement('p');
          state.className = 'when';
          coreSetUIText(state, 'textContent', corePhrase("尚未落定 · 请在对话中交流，由她决定修改、保留或暂缓。"));
          body.append(state);
        }
        if (each.ts) {
          const when = document.createElement('p');
          when.className = 'when';
          when.textContent = String(each.ts);
          body.append(when);
        }
        entry.append(summary, body);
        entries.append(entry);
      }
      if (latest.length > shown.length) {
        const count = document.createElement('p');
        count.className = 'when';
        coreSetUIText(count, 'textContent', [[[[corePhrase("显示最近 "), String(shown.length)], corePhrase(" 条，共 ")], String(latest.length)], corePhrase(" 条；完整记录保留在文件中。")]);
        entries.append(count);
      }
      content.append(entries);
      one.append(content);
      pull.append(one);
    }
    // 先挂上记事，再呈现人和世界入口，避免空状态插在两组内容之间。
    root.append(pull);
    // ── 人那一块 ────────────────────────────────────────────────────────────
    // ⚠️ 照 v10 稿，它在**那几样之后、那条尾之前** （ 不是开头 ）。
    const people = document.createElement('section');
    people.className = 'memory-row memory-people';
    const peopleHead = document.createElement('div');
    peopleHead.className = 'memory-people-head';
    const peopleTitle = document.createElement('h2');
    coreSetUIText(peopleTitle, 'textContent', corePhrase("最近的画像"));
    coreSetUIText(peopleTitle, 'title', corePhrase("按画像与关系的最近记录时间排列"));
    const allPeople = document.createElement('a');
    allPeople.className = 'memory-people-more';
    allPeople.href = '#/people';
    coreSetUIText(allPeople, 'textContent', corePhrase("查看全部画像 →"));
    peopleHead.append(peopleTitle, allPeople);
    people.append(peopleHead);
    const recent = recentPeople(answer.everyone);
    if (recent.length > 0) {
      const plist = document.createElement('ul');
      plist.className = 'memory-people-grid';
      for (const one of recent) {
        const item = document.createElement('li');
        item.className = 'memory-person';
        const who = document.createElement('strong');
        who.className = 'memory-person-name';
        who.textContent = one.display_name || one.who;
        who.title = one.who;
        who.title = one.who;
        const said = document.createElement('p');
        said.className = 'memory-person-standing';
        // 那一行**照实说**：说过什么就写什么，没说过就写「（还没给过说法）」
        said.textContent = one.standing || '（还没给过说法）';
        said.title = said.textContent;
        const meta = document.createElement('span');
        meta.className = 'memory-person-meta';
        coreSetUIText(meta, 'textContent', [[[[corePhrase("关系评分 "), ((one.favor === null ? corePhrase("—") : String(one.favor)))], corePhrase(" · 记过 ")], String(one.how_many_times)], corePhrase(" 条")]);
        item.append(who, said, meta);
        if (one.blocked) {
          const blocked = document.createElement('span');
          blocked.className = 'memory-person-blocked';
          coreSetUIText(blocked, 'textContent', corePhrase("拉黑着"));
          item.append(blocked);
        }
        plist.append(item);
      }
      people.append(plist);
    }
    root.append(people);

    // v10 里第四张卡是"它主动找过你"（ 那一格今天只有一句实话：还没有送到手机的那条路 ）
    const quietOne = document.createElement('div');
    quietOne.className = 'item quiet';
    const qh = document.createElement('h3');
    coreSetUIText(qh, 'textContent', corePhrase("主动请求"));
    const qp = document.createElement('p');
    // ★★ 2026-10-17：**这一格原来是一句硬编码的说明，从来没接过数据**
    //   （`'这一格这边自己也看得见：今天说了几封、最近一封是什么。'`）
    //   ⇒ 用户看到的永远是那句空话。现在读 `answer.pending` 里那条 `kind='outbox'`
    //   的汇总行（由 `launch.see_pending()` 盖上来，`web.py` 一个字不用动）。
    //   ⚠️ 两半都要给：**今天几封** ＋ **最近一封是什么**（用户点的就是后半句 ——
    //      今天 0 封不等于她没主动说过话）。
    const sent = mine.find((one) => (one.kind ?? '') === 'outbox');
    const dayOf = (raw: string): string => {
      const got = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(raw || ''));
      return got ? `${Number(got[2])} 月 ${Number(got[3])} 日` : '';
    };
    if (sent === undefined) {
      // 服务那一侧还没交上来（老进程）⇒ 照实说没接上，不编一个 0
      coreSetUIText(qp, 'textContent', corePhrase("这一格还没接上。"));
    } else if (!sent.how_many) {
      coreSetUIText(qp, 'textContent', corePhrase("暂无主动请求。"));
    } else {
      const when = dayOf(String(sent.day || ''));
      const parts = ['今天 ' + String(sent.today ?? 0) + ' 封'];
      parts.push((when === '' ? '最近一封' : '最近一封是 ' + when)
        + '：' + String(sent.text || ''));
      qp.textContent = parts.join('；');
    }
    // ⚠️ 2026-10-08 作者：「那句也删了」 —— 原来这里还有一句
    // 「话会先落在一处等着；…还没有一条路能送到你手机上。」 ⇒ 整句去掉了 。
    const quietContent = document.createElement('div');
    quietContent.className = 'memory-pull-content';
    quietContent.append(qp);
    quietOne.append(qh, quietContent);
    pull.append(quietOne);

    const tail = personaMemoryTail();
    tail.classList.add('memory-row');
    root.append(tail);
  };

  // ── **做法**（有效 ／ 历史）＋ **运行状态**（2026-10-18 批④）────────────
  // ⚠️ 这一页**只读**：没有合并 ／ 回滚 ／ 恢复 ／ 编辑 ／ 删除的按钮（那是她自己的事）。
  // ⚠️ 正文 ／ 标题 ／ 操作 ID 一律 `textContent` —— 历史里的内容**不会变成可执行 HTML**。
  const skillsBox = document.createElement('section');
  // ⚠️ **不许带 `memory-row`**（2026-10-18 补验）：`draw()` 每次重画前会
  //    `root.querySelectorAll('.memory-row')` 全删 —— 带上它，做法块会在
  //    每次 /memory 刷新时被一起清掉（页上看就是"刷一下就没了"）。
  skillsBox.className = 'memory-skills';
  root.append(skillsBox);

  let latestSkills: SkillsAnswer | null = null;
  const visibleLimits = new Map<string, number>();
  const toolbar = document.createElement('div'); toolbar.className = 'memory-skill-toolbar';
  const search = document.createElement('input'); search.type = 'search';
  coreSetUIText(search, 'placeholder', corePhrase("搜索标题或正文")); coreSetUIAttribute(search, 'aria-label', corePhrase("搜索做法与技能的标题或正文"));
  const category = document.createElement('select'); coreSetUIAttribute(category, 'aria-label', corePhrase("筛选技能类别"));
  for (const label of ['全部类别', '沟通做法', '操作技能', '待分类', '已并入（历史）', '正文缺失']) {
    const option = document.createElement('option'); option.value = label; uiLabel(option, label); category.append(option);
  }
  toolbar.append(search, category);
  const refilter = () => { visibleLimits.clear(); drawSkills(latestSkills); };
  let composing = false;
  search.addEventListener('compositionstart', () => { composing = true; });
  search.addEventListener('compositionend', () => { composing = false; refilter(); });
  search.addEventListener('input', event => { if (!composing && !(event as InputEvent).isComposing) refilter(); }); category.addEventListener('change', refilter);

  const drawSkills = (answer: SkillsAnswer | null): void => keepPosition(() => drawSkillsContents(answer));
  const drawSkillsContents = (answer: SkillsAnswer | null): void => {
    latestSkills = answer;
    if (composing) return;

    const opened = new Set([...skillsBox.querySelectorAll<HTMLDetailsElement>('details[data-section]')].filter(el => el.open).map(el => el.dataset.section));
    // Keep the search input mounted: detaching it cancels native IME composition.
    for (const child of [...skillsBox.children]) if (child !== toolbar) child.remove();
    const head = document.createElement('h2');
    head.className = 'memory-document-title';
    coreSetUIText(head, 'textContent', corePhrase("做法与技能"));
    skillsBox.prepend(head);

    if (answer === null) {
      const bad = document.createElement('p');
      bad.className = 'memory-quiet';
      coreSetUIText(bad, 'textContent', corePhrase("做法这一格读不到（服务没答）。"));
      skillsBox.append(bad);
      return;
    }
    if (!answer.wired) {
      const off = document.createElement('p');
      off.className = 'memory-quiet';
      coreSetUIText(off, 'textContent', corePhrase("这一台没有接做法读口。"));
      skillsBox.append(off);
      return;
    }

    const maintenance = document.createElement('details'); maintenance.className = 'memory-skill-maintenance';
    maintenance.dataset.section = 'maintenance'; maintenance.open = opened.has('maintenance');
    const maintenanceTitle = document.createElement('summary');
    maintenance.append(maintenanceTitle);
    // 整理状态属于维护信息，与技能正文分层。
    const status = answer.status ?? { state: 'unknown' };
    const runline = document.createElement('p');
    runline.className = 'memory-quiet';
    const pending = Number(status.recovery_pending ?? 0);
    coreSetUIText(runline, 'textContent', [[((status.state === 'unknown' && (answer.active_count ?? answer.active?.length ?? 0) === 0 ? corePhrase("尚无整理记录") : [corePhrase("整理状态："), stateName(String(status.state))])), ((pending > 0 ? [[corePhrase("（"), String(pending)], corePhrase(" 笔停在半路）")] : corePhrase("")))], ((status.why && status.state !== 'unknown' ? [corePhrase(" —— "), String(status.why)] : corePhrase("")))]);
    maintenance.append(runline);
    const index = answer.index_status;
    if (index) {
      const labels: Record<string, string> = {
        waiting: '等待首次同步', running: '同步中', ready: '已同步', retry: '待重试',
      };
      const indexLine = document.createElement('p');
      indexLine.className = 'memory-quiet';
      coreSetUIText(indexLine, 'textContent', [[corePhrase("索引状态："), (labels[index.state] ?? '未知')], ((index.indexed_at ? [corePhrase(" · 上次同步 "), displayDate(index.indexed_at)] : corePhrase("")))]);
      maintenance.append(indexLine);
    }

    // **历史事实**（2026-10-18 补）：状态那一行只说当前结果；这几行说发生过什么 ——
    // ⚠️ 不许把"上一趟部分提交 ／ 上一次失败"藏起来（那会读成"从没出过事"）。
    const detail = status.detail;
    if (detail && (detail.partial || detail.error)) {
      const past = document.createElement('p');
      past.className = 'memory-quiet';
      const bits: string[] = [];
      if (detail.partial) {
        const committed = detail.committed ?? [];
        const names = committed.length > 0 ? committed.join('、') : '（没有落下的文件）';
        // ⚠️ **两种话分开说**（2026-10-18 补验三）：办完了是"曾经…已经办完"，
        //    没办完才是"还欠着" —— 一句写死会把历史欠项冒充成当前欠项。
        bits.push((detail.resolved === true ? '曾经部分提交：' : '上一趟部分提交：') + names
          + (detail.resolved === true ? ' —— 后续动作已经办完'
            : detail.followup_uncertain ? ' —— 后续投递结果待核对' : ' —— 文件写了，后续动作还欠着'));
      }
      if (detail.error) bits.push('上一次失败：' + (detail.error_class === 'PersonaShapeRefused' ? '文档结构保护拒绝了本次改写；已保存的内容保留，草稿待复核。' : String(detail.error)));
      if (detail.failed_at) bits.push('中断于 ' + detail.failed_at);
      past.textContent = bits.join('；');
      maintenance.append(past);
    }

    if (!toolbar.isConnected) skillsBox.append(toolbar);
    const query = search.value.trim().toLocaleLowerCase();
    let matched = 0;
    const active = answer.active ?? [];
    const merged = answer.merged ?? [];

    const drawRows = (rows: readonly A_Skill[], said: string): void => {
      if (category.value !== '全部类别' && category.value !== said) return;
      const newest = [...rows].sort((a, b) =>
        (Date.parse(b.recorded_at || '') || 0) - (Date.parse(a.recorded_at || '') || 0)
        || b.id.localeCompare(a.id, undefined, { numeric: true }));
      const filtered = newest.filter(one => !query || [one.title, one.body || '', one.id].join(' ').toLocaleLowerCase().includes(query));
      matched += filtered.length;
      if (query && !filtered.length) return;
      const pageSize = query ? 8 : 2;
      const limit = visibleLimits.get(said) ?? pageSize;
      const section = document.createElement('section'); section.className = 'memory-skill-section';
      const heading = document.createElement('h3'); heading.textContent = said; section.append(heading);
      if (!rows.length) {
        const empty = document.createElement('p'); empty.className = 'memory-skill-empty'; coreSetUIText(empty, 'textContent', corePhrase("尚未记录")); section.append(empty);
      }
      for (const one of filtered.slice(0, limit)) {
        const item = document.createElement('details'); item.className = 'memory-skill-entry';
        item.dataset.section = one.id; item.open = opened.has(one.id);
        const title = document.createElement('summary');
        const name = document.createElement('span'); name.textContent = one.title;
        const affordance = document.createElement('span'); affordance.className = 'memory-skill-affordance'; coreSetUIText(affordance, 'textContent', (item.open ? corePhrase("收起正文") : corePhrase("查看正文")));
        const overview = document.createElement('span'); overview.className = 'memory-skill-overview';
        const excerpt = document.createElement('span'); excerpt.className = 'memory-skill-excerpt';
        excerpt.textContent = (one.body || '').split('\n').find(line => line.trim())?.slice(0, 140) || '暂无正文';
        overview.append(name, excerpt); title.append(overview, affordance); item.append(title);
        item.addEventListener('toggle', () => { coreSetUIText(affordance, 'textContent', (item.open ? corePhrase("收起正文") : corePhrase("查看正文"))); });
        const body = document.createElement('div'); body.className = 'memory-skill-body';
        body.textContent = one.body ?? (one.state === 'missing' ? '正文文件缺失。' : '正文未提供，请更新服务后重试。'); item.append(body);
        const metadata = document.createElement('div'); metadata.className = 'memory-skill-metadata';
        coreSetUIText(metadata, 'textContent', [[[((one.recorded_at ? [[corePhrase("记录于 "), displayDate(one.recorded_at)], corePhrase(" · ")] : corePhrase(""))), corePhrase("历史 ")], String(one.history ?? 0)], corePhrase(" 版")]);
        coreSetUIText(metadata, 'title', [[[one.id, corePhrase(" · 版本 ")], one.version], ((one.recorded_at ? [corePhrase(" · "), one.recorded_at] : corePhrase("")))]);
        item.append(metadata);
        if (one.references?.length) {
          const refs = document.createElement('div'); refs.className = 'memory-skill-references';
          coreSetUIText(refs, 'textContent', [corePhrase("关联资料："), one.references.join('、')]); item.append(refs);
        }
        section.append(item);
      }
      if (filtered.length > limit) {
        const more = document.createElement('button'); more.type = 'button'; more.className = 'memory-skill-more';
        coreSetUIText(more, 'textContent', corePhrase("显示更多")); coreSetUIAttribute(more, 'aria-label', [corePhrase("显示更多"), said]);
        more.addEventListener('click', () => {
          visibleLimits.set(said, limit + pageSize); drawSkills(latestSkills);
          const updated = [...skillsBox.querySelectorAll<HTMLElement>('.memory-skill-section')].find(el => el.querySelector('h3')?.textContent === said);
          const next = updated?.querySelectorAll<HTMLElement>('summary')[limit]; next?.focus();
        });
        section.append(more);
      }
      if (limit > pageSize) {
        const less = document.createElement('button'); less.type = 'button'; less.className = 'memory-skill-more';
        coreSetUIText(less, 'textContent', corePhrase("收起")); coreSetUIAttribute(less, 'aria-label', [corePhrase("收起"), said]);
        less.addEventListener('click', () => { visibleLimits.delete(said); drawSkills(latestSkills); });
        section.append(less);
      }
      skillsBox.append(section);
    };

    drawRows(active.filter(one => one.kind === 'communication'), '沟通做法');
    drawRows(active.filter(one => one.kind === 'operation'), '操作技能');
    const unclassified = active.filter(one => !one.kind || one.kind === 'unclassified');
    if (unclassified.length) drawRows(unclassified, '待分类');
    if (merged.length) drawRows(merged, '已并入（历史）');
    coreSetUIText(maintenanceTitle, 'textContent', [[corePhrase("维护记录 · "), ((status.state === 'failed' ? corePhrase("上次文档整理未完成") : [corePhrase("整理"), stateName(String(status.state))]))], ((index ? [corePhrase(" · 索引"), ({ready: '已同步', running: '同步中', waiting: '待同步', retry: '待重试'}[index.state] ?? '未知')] : corePhrase("")))]);
    skillsBox.append(maintenance);
    if (answer.missing?.length) drawRows(answer.missing, '正文缺失');

    if (!matched && (query || (category.value !== '全部类别' && !skillsBox.querySelector('.memory-skill-section')))) {
      const empty = document.createElement('p'); empty.className = 'memory-skill-empty';
      coreSetUIText(empty, 'textContent', (query ? corePhrase("未找到匹配条目，请换个关键词或类别。") : corePhrase("此类别尚未记录。")));
      skillsBox.insertBefore(empty, maintenance);
    }


    const ops = answer.ops ?? [];
    if (ops.length > 0) {
      const box = document.createElement('div');
      const lbl = document.createElement('div');
      lbl.className = 'memory-star-label';
      coreSetUIText(lbl, 'textContent', [[corePhrase("操作账（最近 "), String(ops.length)], corePhrase(" 笔）")]);
      box.append(lbl);
      const ul = document.createElement('ul');
      ul.className = 'memory-list';
      for (const one of ops) {
        const li = document.createElement('li');
        const line = document.createElement('div');
        line.className = 'memory-skill-line';
        coreSetUIText(line, 'textContent', [[[[[String(one.op), corePhrase(" ｜ ")], String(one.stage)], corePhrase(" ｜ 留 ")], String(one.keep)], ((one.sources.length > 0 ? [corePhrase(" ｜ 并 "), one.sources.join('、')] : corePhrase("")))]);
        li.append(line);
        ul.append(li);
      }
      box.append(ul);
      maintenance.append(box);
    }
  };

  let skillsRead = 0;
  let skillsSnapshot = '';
  let memorySnapshot = '';
  const skillsHint = pageLoad(root, '做法', () => { void refreshSkills(); });
  const refreshSkills = async (): Promise<void> => {
    const mine = ++skillsRead; skillsHint.start();
    try {
      const answer = await getStatusJson<SkillsAnswer>('/skills');
      if (mine !== skillsRead || root.hidden) return;
      const snapshot = JSON.stringify(answer);
      if (snapshot !== skillsSnapshot && !composing && !skillsBox.contains(document.activeElement)) { drawSkills(answer); skillsSnapshot = snapshot; }
      skillsHint.ready();
    } catch (why) {
      if (mine !== skillsRead || root.hidden) return;
      if (why instanceof StaleStatusError) { void refreshSkills(); return; }
      console.error('做法读取失败：', why); skillsHint.fail();
    }
  };

  let requestId = 0;
  const readHint = pageLoad(root, '记忆', () => { void refresh(); });
  const refresh = async (): Promise<void> => {
    const mine = ++requestId;
    readHint.start();
    try {
      const answer = await getStatusJson<MemoryAnswer>('/memory');
      if (mine !== requestId || root.hidden) return;
      // Memory redraw moves the skills section; defer it while its controls are active.
      const snapshot = JSON.stringify(answer);
      if (snapshot !== memorySnapshot && !root.contains(document.activeElement)) { draw(answer); memorySnapshot = snapshot; }
      readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) { void refresh(); return; }
      console.error('记忆读取失败：', why); readHint.fail();
    }
  };

  const liveRecords = recordRefresh(root, async () => { await Promise.all([refresh(), refreshSkills()]); });

  return {
    root,
    show(): void {
      root.hidden = false;
      liveRecords.start();
      void refresh();
      void refreshSkills();
    },
    stop(): void {
      liveRecords.stop();
      requestId += 1; skillsRead += 1;
      root.hidden = true;
    },
  };
}
