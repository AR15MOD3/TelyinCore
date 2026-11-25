import { corePhrase, coreSetUIText } from './ui-language';
import { displayDate } from './display-date';
import { pageLoad } from './page-load';
import { getStatusJson, StaleStatusError } from './status-json';
import { pageFetch as fetch } from './status-json';
// telyincore —— 「知识库」那一页（2026-10-09 **照 v4 设计稿重做** ＋ 当天作者按实物提的那几条）。
//
// 它画的是 `/knowledge` 那条口交回来的那些（ **他定的设定** ）：
// `{knowledge: [{name, text, lines, at}], wired, q}` 。
//
// ⚠️ **为什么重做**（ `世界三页-交接.md` §二）：上一版是**照记忆页的骨架抄的**
// ⇒ 作者：「**没有推翻排版**」 ／「**主次搞错了**」。
// 这一版的构图 ＝ **一本摊开的书**：左窄目录（246px ／ 一条竖线 ／ 小字）／ 右宽正文
// —— 主是**正文** （ 目录、搜索只服务"找得到" ）。
//
// ⚠️ **2026-10-09 作者按实物又改了这一页的四处**（ 真页跑起来之后他看的 ）：
// ① **抬头那两句删掉** ——「你定下来的那些设定。看一眼能认出来，想找一条找得到。」
// 与「共 4 份 · 34 行」那一格**都不要** （ 那个数**尾那一行本来就有** ）。
// ② **搜索那一个框换了一种形式** —— 原来是一条**光秃秃的下划线** ＋ 右对齐的字
// （ 作者：「不够显眼」 ／「交互做的有点差」 ／「换种形式」）⇒
// 现在走 `shell.ts::searchBox()` 那一个共用件（ 看得出是个框 ）；
// ⚠️ 占位那句也换了 ——「找一条设定」**不像人话** （ 作者那一句 ）⇒
// 现在写「搜索设定…」（ 一眼知道这是个搜索框 ）。
// ③ **标题那一行反过来了** —— 原来 `.title` 是「你定的 · 图书馆.md」（13px 灰）
// 而**正文第一句**是 22px 加粗 。 作者：「图书馆.md 这种**是标题**，为什么不加粗显示，
// 反而正文的第一句加粗显示？标题和简介一样重要，但也有个主次之分」
// ⇒ 现在：**文件名 ＝ 标题**（22px ／ 600 ）／ **正文第一非空行 ＝ 简介**（退一档 ）。
// 而「你定的 ·」那个前缀**一律删掉** —— 「该是什么文件就叫什么文件。
// 别分我写的还是她写的。没必要。」
// ④ **两句"这一页只读"的话删掉** —— 脚底那格「这一页只读」与尾那一句
// 「改它要去改那几份文件 —— 这一页不写」都不要 。
//
// ⚠️ **只读** ：不写、不算时间、不排序 （与记忆那一页同一条规矩 ）。
// ⚠️ **`wired: false`（没接上）与"零份"是两句话** —— 本页分开画 。
// ⚠️ **不许编** ：目录那一行的「N 行 · M 月 D 日」与尾那一行**都是现算** ，
// 而"最后写于"那一句**只有非空才给** （ 一个 0 行的文件没有那句话可言 ）。

import { searchBox } from './shell';
import type { Chrome, Panel } from './shell';

/** `/knowledge` 交回来的一条（ 照 `launch.see_knowledge()` 的契约 ）。 */
interface A_Note {
  /** 那一份的文件名 —— **它就是标题** （ 作者："该是什么文件就叫什么文件"）。 */
  readonly name: string;
  readonly title?: string;
  readonly version?: number;
  /** 正文（ **原样** —— 这一页先剥注释、再按行摆、一个字不改 ）。 */
  readonly text: string;
  /** 正文里**非空的行数**（ 空文件就是 `0` ）—— 缺了就照正文现数 。 */
  readonly lines?: number;
  /** **最后写于**（形如「10 月 7 日」）—— ⚠️ **空文件就是空串** （那一句不画 ）。 */
  readonly at?: string;
  /** **2026-10-10：服务给的那一份归哪一组** （ 作者当天点的那一件 ）。
   *
   * ⚠️ **为什么组名不由这一页算** ： 他要归一组的那三份**名字各不相同**
   * （ 一份没有前缀、 两份带前缀 ）⇒ 只能点名 ； 而**那张表不许写在
   * 页上** —— 客户端有一条边界判据： 「"**这一台安装叫谁**"不许写在页上
   * （ 别人的安装里不是它 ）」 ⇒ 由**入口**算好、 随每一份透传
   * （ 与这一页那两个 `kind` ／ `full` **同一个成法** ）。
   * ⚠️ **没给就退回"按名字前两段分"** （ 别的安装 ／ 旧数据 —— 不编 ）。
   */
  readonly group?: string;
}

interface KnowledgeAnswer {
  readonly knowledge: readonly A_Note[];
  readonly wired?: boolean;
  readonly q?: string;
  readonly search_limited?: boolean;
}

const READ_FAILED = '知识库加载失败，请重试。';
const NOT_WIRED = '当前服务暂不支持查看知识库。';
const NOTHING_YET = '暂无知识库内容。';

/** **注释不算正文的一行** —— `<!-- … -->` 那几段是**写给我们看的** 。 */
const stripComments = (one: string): string => one.replace(/<!--[\s\S]*?-->/g, '');

/** 那一段正文里**非空的行**（ 与入口那边数的是同一件事 ）。 */
const theLines = (text: string): string[] =>
  stripComments(String(text || '')).split('\n').filter((one) => one.trim() !== '');

/** **摆出来的名字不带后缀** （ 2026-10-09 作者："`.md` 这个也要去掉"）。
 *
 * ⚠️ **剥的只是显示那一份** —— 那一份**真名**（`one.name` ）照旧原样留着 ：
 * 尾那一句"改它要去改那几份文件"说的还是文件本身 ，而 **页上不念后缀**
 * （ 与"该是什么文件就叫什么文件"并不打架： 认得出是哪一份就够了 ）。
 */
const theShownName = (one: string): string =>
  String(one || '').replace(/\.md$/i, '');

/** 那一页。 */
export function knowledgePage(shell: Chrome): Panel {
  const root = document.createElement('section');
  root.className = 'page-knowledge page-world world-studio';

  const line = (tag: string, cls: string, text?: string): HTMLElement => {
    const one = document.createElement(tag);
    if (cls !== '') one.className = cls;
    if (text !== undefined) one.textContent = text;
    return one;
  };

  // ── 抬头（ **极轻** —— 抢焦点的是右栏那一段正文 ／ 只有标题 ＋ 搜索框 ）──
  const head = document.createElement('div');
  head.className = 'head';
  const title = document.createElement('h1');
  coreSetUIText(title, 'textContent', corePhrase("知识库 · KNOWLEDGE"));
  const find = searchBox('搜索文档名称');
  head.append(title, find.element);
  root.append(head);

  // ── 那两栏的落点（ 左窄目录 ／ 右宽正文 ）────────────────────────────────
  const book = document.createElement('div');
  book.className = 'book';
  const toc = document.createElement('div');
  toc.className = 'toc';
  const body = document.createElement('div');
  body.className = 'page-body';
  book.append(toc, body);
  root.append(book);

  const tail = document.createElement('div');
  tail.className = 'tail page-footer';
  root.append(tail);

  shell.content.replaceChildren(root);

  const draw = (answer: KnowledgeAnswer | null, q: string): void => {
    toc.replaceChildren();
    body.replaceChildren();
    tail.replaceChildren(line('span', 'page-footer-label', '知识与资料'));

    if (answer === null || answer.wired === false) {
      // ⚠️ **两句不同的话** ：读不成 ／ 这一层没接上（ 与"零份"分开 ）。
      const prose = line('div', 'prose');
      prose.append(line('p', 'fade', answer === null ? READ_FAILED : NOT_WIRED));
      body.append(prose);
      return;
    }

    if (q && answer.search_limited) {
      tail.append(line('span', 'fade', '本次使用字面检索，语义检索暂不可用。'));
    }
    const rows = answer.knowledge ?? [];
    const howMany = rows.reduce((sum, one) => sum + (one.lines ?? theLines(one.text).length), 0);

    if (rows.length === 0) {
      // ⚠️ **"搜不到"与"一份都没有"不是一件事** —— 送过 `q=` 就照实说搜的事 。
      toc.append(line('div', 'empty', q === ''
        ? '暂无文档。'
        : '没有匹配结果。'));
      const prose = line('div', 'prose');
      prose.append(line('p', 'fade', q === '' ? NOTHING_YET : '「' + q + '」没有匹配文档。'));
      body.append(prose);
      tail.append( line('span', '', '共 0 份 · 0 行'));
      return;
    }

    // 正文那一段由**选中的那一份**决定（点目录换的是它 ）。
    let at = 0;
    const showBody = (index: number): void => {
      const one = rows[index];
      const heads = theLines(one.text);
      body.replaceChildren();
      body.scrollTop = 0;
      // ① **标题 ＝ 文件名** （ 不是"你定的 · xxx" ／ **不带 `.md`** ）
      body.append(line('h2', '', one.title ?? theShownName(one.name)));
      // ② **简介 ＝ 正文第一非空行** （ 退一档： 标题是主、它是次 ）
      body.append(line('p', 'gist', heads[0] ?? '暂无正文。'));
      // ③ 正文（ 剩下的那些行 —— 原样摆，不改写 ）
      const prose = line('div', 'prose');
      for (const said of heads.slice(1)) prose.append(line('p', '', said));
      body.append(prose);
      // ④ 脚底那两格（ 「这一页只读」那一格**删了** —— 作者 2026-10-09 ）
      const foot = line('div', 'foot');
      foot.append(line('span', '', String(one.lines ?? heads.length) + ' 行'));
      if (one.version) foot.append(line('span', '', one.name + ' · 版本 ' + String(one.version)));
      // ⚠️ **"最后写于"只有非空才给** （ 0 行的文件没有那一句话可言 ）。
      if ((one.at ?? '') !== '') foot.append(line('span', '', '最后写于 ' + displayDate(one.at)));
      body.append(foot);
    };

    const pick = (index: number): void => {
      at = index;
      // Selection uses the source index, including progressively loaded groups.
      for (const el of toc.querySelectorAll<HTMLElement>('.one')) {
        el.classList.toggle('on', el.dataset.at === String(index));
      }
      showBody(index);
    };

    // Group documents with visible headings; each group loads in batches.
    const keyOf = (name: string): string => {
      const shown = String(name ?? '').replace(/\.md$/, '');
      const parts = shown.split('·');
      return parts.length >= 3 ? parts.slice(0, 2).join('·') : shown;
    };
    // 那一行怎么画（ 与从前**逐字一样** —— 只是搬成了一个小函数 ）
    // ⚠️ **多了一格 `dataset.at`** —— 它是**那一份在 `rows` 里的号** ；
    // 选中的那一下**认它**， 不认"第几个建出来的"（ 见 `pick` ）。
    const drawOne = (index: number): HTMLElement => {
      const one = rows[index];
      const item = line('div', 'one');
      item.dataset.at = String(index);
      item.title = one.at || '';
      item.append(line('div', 'n', one.title ?? theShownName(one.name)));
      const n = one.lines ?? theLines(one.text).length;
      item.append(line('div', 'm', (String(n) + ' 行'
                                    + ((one.at ?? '') === '' ? '' : ' · ' + displayDate(one.at)))));
      item.addEventListener('click', () => { pick(index); });
      return item;
    };
    if (q !== '') {
      rows.forEach((_, index) => toc.append(drawOne(index)));
    } else {
      const groups = new Map<string, number[]>();
      rows.forEach((one, index) => {
        // **2026-10-10：优先用服务给的组名** （ 作者点的那一件 ）。
        // ⚠️ **为什么组名不由这一页算** ： 他要归一组的那三份**名字各不相同**
        // ⇒ 只能点名 ； 而**那张表不许写在页上** —— 客户端有边界判据：
        // 「"这一台安装叫谁"不许写在页上（ 别人的安装里不是它 ）」
        // ⇒ 表在**入口**那一层 （`launch._the_group_of` ）， 随每一份透传 。
        // ⚠️ **没给就退回"按名字前两段分"** （ 别的安装 ／ 旧数据 ——
        // 一个字都不编 ）。
        const key = String(one.group ?? '') || keyOf(one.name);
        groups.set(key, [...(groups.get(key) ?? []), index]);
      });
      groups.forEach((idxs, key) => {
        const box = document.createElement('section');
        box.className = 'group';
        const head = document.createElement('h3');
        head.className = 'group-head';
        head.textContent = key + ' · ' + String(idxs.length);
        box.append(head);
        let shown = 0;
        const more = document.createElement('button'); more.type = 'button'; more.className = 'infinity-text-link';
        const build = (): void => {
          const next = idxs.slice(shown, shown + 30); shown += next.length;
          for (const i of next) box.insertBefore(drawOne(i), more);
          more.hidden = shown >= idxs.length;
          more.textContent = '显示更多（剩余 ' + (idxs.length - shown) + '）';
        };
        more.onclick = build; box.append(more); build();
        toc.append(box);
      });
    }
    pick(at);

    tail.append(
                line('span', '', '共 ' + String(rows.length) + ' 份 · '
                      + String(howMany) + ' 行'));
  };

  let requestId = 0;
  const readHint = pageLoad(root, '知识库', () => { void refresh(find.input.value.trim()); });
  const refresh = async (q: string): Promise<void> => {
    const mine = ++requestId;
    const ask = q === '' ? '/knowledge' : '/knowledge?q=' + encodeURIComponent(q);
    readHint.start();
    try {
      const answer = await getStatusJson<KnowledgeAnswer>(ask);
      if (mine !== requestId || root.hidden) return;
      draw(answer, q); readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) { void refresh(q); return; }
      console.error('知识库读取失败：', why); readHint.fail();
    }
  };

  find.onAsk((q) => { void refresh(q); });

  return {
    root,
    show(): void {
      root.hidden = false;
      void refresh(find.input.value.trim());
    },
    stop(): void {
      requestId += 1;
      root.hidden = true;
    },
  };
}
