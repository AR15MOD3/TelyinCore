import { corePhrase, coreSetUIAttribute, coreSetUIText } from './ui-language';
import { pageLoad } from './page-load';
import { getStatusJson, StaleStatusError } from './status-json';
import { pageFetch as fetch } from './status-json';
// Portraits are read-only; account associations retain explicit confirmation and
// the revision from the same server snapshot. Presentation never changes identity keys.

import { searchBox } from './shell';
import { portraitOrigin, portraitSource, portraitEncounters, portraitGroups, portraitLinkedToConsole } from './portrait-display';
import type { Chrome, Panel } from './shell';

/** 她写下的**一条原话**（ 照 `memory.Remembered` 那五格 ）。 */
interface A_Said {
  /** 那一刻**原样**（ 它只当出处看 —— 这一页不算天数、不标红 ）。 */
  readonly ts: string;
  /** 页上左边那一格（「10-08」那种 ）—— 由入口算好 （ 页上没有本机钟 ）。 */
  readonly when: string;
  /** 从哪儿来（ 页上那枚来源胶囊 ）。 */
  readonly from: string;
  /** 那一类（`名字` ／ `事实` ）—— 页上**不各起小节** ，只让 `名字` 退一档 。 */
  readonly role: string;
  readonly memory_kind?: string;
  readonly kind_label?: string;
  readonly source_cursor?: string;
  /** **原话本身** —— 不知截断、不总结 。 */
  readonly text: string;
}

/** `/people` 交回来的一条（ 照 `launch.see_everyone()` 的契约 ）。 */
interface A_Person {
  readonly who: string;
  /** 来话显示名仅供操作者辨认，绝不用于画像键或账号归属。 */
  readonly display_name?: string;
  readonly display_account?: string;
  readonly display_scope?: string;
  readonly encounters?: readonly {
    account: string; room: string; label: string; first: string; last: string; common_name: string;
  }[];
  readonly identity?: A_Identity;
  readonly association?: {
    readonly revision: string;
    readonly profile_keys: readonly string[];
    readonly accounts: readonly string[];
    readonly separate_accounts?: readonly string[];
  };
  readonly standing: string | null;
  readonly favor: number | null;
  readonly blocked: boolean;
  /** 那一个人**来自哪儿**（ 取自本子上那一格 `source` —— **空就不画那枚胶囊** ）。 */
  readonly plat?: string;
  /** **第一次**（「9 月 30 日」）—— 空就不画那一格 。 */
  readonly first?: string;
  /** **最近**（「10 月 8 日」）—— 同上 。 */
  readonly last?: string;
  readonly said?: readonly A_Said[];
  /** **2026-10-10：那个人的画像记满了没有** （ 服务算好的 ——
   * 它够不到那个常量，`web.py` 也够不到 ⇒ 由入口递 ）。 */
  readonly full?: boolean;
}

interface A_Identity {
  readonly id: string;
  readonly label: string;
  readonly profile_key: string | null;
  readonly profile_keys?: readonly string[];
  readonly accounts: readonly string[];
}

interface PeopleAnswer {
  readonly everyone: readonly A_Person[];
  readonly identities?: readonly A_Identity[] | null;
  readonly identity_error?: boolean;
  readonly wired?: boolean;
  readonly q?: string;
}

const READ_FAILED = '画像加载失败，请重试。';
const NOT_WIRED = '当前服务暂不支持查看画像。';
/** 空态那句话**必须写准** （ 不许写"还没跟谁说过话" —— 那句与真相不符 ）。 */
const NOBODY_YET = '暂无画像记录。';
const ROSTER_EMPTY = '暂无人物。';

/** 一开始摆几条（ 其余的收进"还有 N 条 · 展开全部" ）。 */
const SAID_SHOWN = 5;

/** 那一页。 */
export function peoplePage(shell: Chrome): Panel {
  const expandedPeople = new Set<string>();
  const root = document.createElement('section');
  root.className = 'page-people page-world world-studio';

  const head = document.createElement('div');
  head.className = 'head';
  const title = document.createElement('h1');
  coreSetUIText(title, 'textContent', corePhrase("画像 · PROFILE"));
  // ⚠️ **2026-10-09 作者又删了两句** ：
  // · 抬头那句「**她给你的说法**」 —— 删（ 抬头只留标题 ＋ 那个筛选框 ）；
  // · 尾那句「**这个说法是她写的 —— 拉黑那道闸是你拉的**」 —— 也删
  // （ 尾从此只剩两格：`` ＋ `共 N 个人` ）。
  // 筛选那一格走 `shell.ts::searchBox()`（ 三页共用那一个件 ）——
  // 占位照这一家的写法：「**筛选：**用户名」（与插件那页那句同一个形状 ）。
  const find = searchBox('搜索姓名或账号');
  head.append(title, find.element);
  root.append(head);

  const faces = document.createElement('div');
  faces.className = 'faces';
  const roster = document.createElement('div');
  roster.className = 'roster';
  const face = document.createElement('div');
  face.className = 'portrait-detail';
  faces.append(roster, face);
  root.append(faces);

  const tail = document.createElement('div');
  tail.className = 'tail page-footer';
  root.append(tail);

  shell.content.replaceChildren(root);

  const line = <K extends keyof HTMLElementTagNameMap>(tag: K, cls: string,
    text?: string): HTMLElementTagNameMap[K] => {
    const one = document.createElement(tag);
    if (cls !== '') one.className = cls;
    if (text !== undefined) one.textContent = text;
    return one;
  };

  /** 身份变更统一在页内确认，不让浏览器原生弹窗打断页面的阅读与焦点。 */
  const confirmIdentity = (
    heading: string, subject: string, detail: string, action: string,
    submit: () => Promise<string | null>,
  ): void => {
    const opener = document.activeElement;
    const shade = line('div', 'identity-confirm-backdrop');
    const dialog = line('section', 'identity-confirm');
    dialog.setAttribute('role', 'alertdialog');
    dialog.setAttribute('aria-modal', 'true');
    const title = line('h2', '', heading);
    const subjectLine = line('div', 'identity-confirm-subject', subject);
    const description = line('p', '', detail);
    const error = line('p', 'identity-confirm-error');
    title.id = 'identity-confirm-title';
    description.id = 'identity-confirm-description';
    dialog.setAttribute('aria-labelledby', title.id);
    dialog.setAttribute('aria-describedby', description.id);
    error.setAttribute('role', 'status');
    const actions = line('div', 'identity-confirm-actions');
    const cancel = line('button', '', '取消') as HTMLButtonElement;
    const proceed = line('button', 'primary', action) as HTMLButtonElement;
    cancel.type = 'button';
    proceed.type = 'button';
    let busy = false;
    const close = (): void => {
      if (busy) return;
      shade.remove();
      document.removeEventListener('keydown', onKey);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
      else find.input.focus();
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key !== 'Tab') return;
      event.preventDefault();
      (document.activeElement === cancel ? proceed : cancel).focus();
    };
    cancel.addEventListener('click', close);
    proceed.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      cancel.disabled = true;
      proceed.disabled = true;
      coreSetUIText(proceed, 'textContent', corePhrase("处理中…"));
      const problem = await submit();
      busy = false;
      if (problem === null) { close(); return; }
      error.textContent = problem;
      cancel.disabled = false;
      proceed.disabled = false;
      proceed.textContent = action;
      proceed.focus();
    });
    shade.addEventListener('click', (event) => {
      if (event.target === shade) close();
    });
    document.addEventListener('keydown', onKey);
    actions.append(cancel, proceed);
    dialog.append(line('span', 'identity-confirm-kicker', '身份管理'), title,
      subjectLine, description, error, actions);
    shade.append(dialog);
    root.append(shade);
    cancel.focus();
  };

  /** 一步坐标（ 只有**有底**的那几格才画 —— 空的不编 ）。 */
  const facts = (one: A_Person): HTMLElement => {
    const box = line('div', 'who-facts');
    const put = (lead: string, value: string, thin: boolean): void => {
      if (value === '') return;
      const bit = line('span', thin ? 'thin' : '');
      bit.append(lead + ' ');
      bit.append(line('b', '', value));
      box.append(bit);
    };
    // **2026-10-10：记满了就说出来** （ 与 `/memory` 那边同一句、同一个成法 ）。
    // ⚠️ **为什么非说不可** ： 那本有上限（**每个文件 50** ）⇒ 一满了，
    // 再记一条就**压掉最旧的** ⇒ 不说就是**悄悄丢东西**
    // （ 本仓的规矩：不许静默 ）。
    // ⚠️ **这一格是服务算好的** （`launch.see_everyone()` 那一条带 `full` ）——
    // 因为 `web.py` 与这一页都够不到那个常量 。
    if (one.full === true) {
      box.append(line('span', 'thin', '已达记录上限，新增记录会替换最旧记录'));
    }
    // 画像卡沿用缺值显示 0；记忆页缺值显示「—」，两页不是同一展示合同。
    if (one.favor != null) put('关系评分', String(one.favor), true);
    return box;
  };

  /** **第 ② 行那一句** —— 全页的焦点（她给这个人的说法 ）。 */
  const gist = (one: A_Person | null): HTMLElement => {
    const said = String(one?.standing ?? '');
    if (said === '') {
      return line('p', 'gist void', one === null ? NOBODY_YET : '');
    }
    return line('p', 'gist', said);
  };

  /** **一句一条**：最新的在顶上 ／ 更早的淡下去 ＋ 挂个小标「早先」（ 不删不藏 ）。 */
  const theSaid = (one: A_Person, at_most: number): HTMLElement[] => {
    const rows = [...(one.said ?? [])].reverse();
    const newestOf: Record<string, number> = {};
    rows.forEach((said, index) => {
      if (newestOf[said.role] === undefined) newestOf[said.role] = index;
    });
    return rows.slice(0, at_most).map((said, index) => {
      // ⚠️ **"盖住"只做得了"同类"那一半** ： 同一类里后面那条盖住前面那条 ——
      // 而"哪两条说的是同一件事"今天分不出（ 那要存储加一格 ）⇒
      // **只淡、不删、也不藏**： 淡下去本身就说明了"她改过主意" 。
      const older = newestOf[said.role] !== index;
      // ⚠️ **两个类是挂在**这一行**上的** （ 规格那两条选择器是
      // `.said.naming .words` 与 `.said.older .words` —— 挂错了就一条也不生效 ）。
      const item = line('div', 'said' + (said.role === '名字' ? ' naming' : '')
                               + (older ? ' older' : ''));
      const date = /^(\d{4})(?:-?)(\d{2})(?:-?)(\d{2})(?:T|[- ]|$)/.exec(said.ts ?? '');
      const fullDate = date ? date.slice(1).join('-') : '';
      const duplicateDate = fullDate !== '' && said.text.startsWith(fullDate + ' ');
      const when = line('span', 'when', fullDate || said.when);
      if (fullDate) when.title = fullDate;
      item.append(when);
      const words = line('div', 'words');
      const text = line('div', 'said-text', duplicateDate ? said.text.slice(fullDate.length + 1) : said.text);
      if (duplicateDate) text.title = said.text;
      words.append(text);
      const meta = line('div', 'said-meta');
      if (said.kind_label) meta.append(line('span', 'from', said.kind_label));
      if (said.from !== '') {
        const source = line('span', 'from', portraitSource(said.from));
        source.title = said.from; meta.append(source);
        if (said.source_cursor) meta.append(line('span', 'from', '来源事件 #' + said.source_cursor));
      }
      if (older) meta.append(line('span', 'from', '早先'));
      if (meta.childElementCount) words.append(meta);
      item.append(words);
      return item;
    });
  };

  const draw = (answer: PeopleAnswer | null): void => {
    roster.replaceChildren();
    face.replaceChildren();
    tail.replaceChildren(line('span', 'page-footer-label', '人物与关系'));

    if (answer === null || answer.wired === false) {
      const said = line('p', 'gist void', answer === null ? READ_FAILED : NOT_WIRED);
      face.append(said);
      return;
    }

    const groups = portraitGroups(answer.everyone ?? []);
    const rows = groups.flatMap(group => group.people);

    if (rows.length === 0) {
      roster.append(line('div', 'empty', ROSTER_EMPTY));
      face.append(gist(null));
      tail.append(
                  line('span', '', '共 0 个人'));
      return;
    }

    let at = Math.max(0, rows.findIndex(one => one.who === selectedWho));
    const showFace = (index: number, expanded?: boolean): void => {
      const one = rows[index];
      expanded = expanded ?? expandedPeople.has(one.who);
      if (expanded) expandedPeople.add(one.who); else expandedPeople.delete(one.who);
      face.replaceChildren();
      // ① 名字那一行 ＋ ② 那一句总述 ＋ ③ 一行坐标
      const card = line('div', 'portrait-head');
      const body = line('div', 'who-body');
      const nameRow = line('div', 'who-name');
      nameRow.append(line('h2', '', one.display_name || one.who));
      if (String(one.plat ?? '') !== '') {
        nameRow.append(line('span', 'plat', portraitOrigin(one.display_account || String(one.plat))));
      }
      // 拉黑那一道闸是**作者唯一负责的一格** ⇒ 它写在名字旁边（红字 ）。
      if (one.blocked) nameRow.append(line('span', 'gate', ' 已屏蔽'));
      body.append(nameRow);
      const rooms = portraitEncounters(one, rows);
      if (rooms.length) {
        const context = document.createElement('section');
        context.className = 'portrait-encounters';
        const summary = line('h3', '');
        summary.append(line('span', '', '相遇记录'), line('span', 'encounter-count', rooms.length + ' 个 QQ 群'));
        context.append(summary);
        const list = line('div', 'encounter-list');
        for (const visit of rooms) {
          const item = line('div', 'encounter-row');
          item.append(line('span', '', 'QQ 群 · ' + visit.room));
          const date = (value?: string) => value ? new Date(value).toLocaleDateString('zh-CN') : '';
          item.append(line('span', 'encounter-date', visit.first
            ? '首次 ' + date(visit.first) + (visit.last !== visit.first ? ' · 最近 ' + date(visit.last) : '')
            : '来自已保存的群聊记录'));
          list.append(item);
        }
        context.append(list); body.append(context);
      }
      if (one.standing?.trim()) {
        const overview = line('section', 'portrait-overview');
        overview.append(line('h3', '', '人物概述'), gist(one)); body.append(overview);
      }
      body.append(facts(one));
      card.append(body);
      face.append(card);

      // ② 主体：一句一条
      const count = one.said?.length ?? 0;
      face.append(line('h3', 'portrait-records-title', '画像记录 · ' + count));
      if (!count) face.append(line('p', 'portrait-empty', '暂无画像记录。'));
      for (const item of theSaid(one, expanded ? count : SAID_SHOWN)) face.append(item);
      const hidden = count - SAID_SHOWN;
      if (hidden > 0 && !expanded) {
        const zone = line('div', 'more-zone');
        const first = line('div', 'line1');
        first.append(line('span', 'lead', expanded ? '已显示全部记录' : '还有 ' + String(hidden) + ' 条记录'));
        const op = line('button', 'op', '显示全部记录') as HTMLButtonElement;
        op.type = 'button';
        op.addEventListener('click', () => { showFace(index, true); });
        first.append(op);
        zone.append(first);
        face.append(zone);
      }
      const join = theJoin();
      if (join !== null) face.append(join);
    };

    /** 人工确认账号归属。只改映射，不迁移旧画像，也不改变模型当前收到的历史。 */
    const theJoin = (): HTMLElement | null => {
      if (!Array.isArray(answer.identities)) {
        return answer.identity_error ? line('p', 'identity-status',
          '身份绑定记录暂时读不到，已停止绑定操作；画像仍可查看。') : null;
      }
      const one = rows[at];
      // identity and association.revision belong to the same C-produced row snapshot.
      // The separate identities list is display-only and cannot fill a missing identity.
      const identity = one.identity;
      const zone = line('details', 'join identity-link compact-disclosure');
      zone.append(line('summary', 'line1', '账号关联'));
      zone.append(line('p', 'why',
        '把同一个人的不同账号关联起来，方便 Bot 识别。昵称相同不会自动关联。'));
      const status = line('p', 'identity-status');
      const update = async (body: Record<string, unknown>): Promise<string | null> => {
        try {
          const got = await fetch('/people/identity', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...body, confirm: true }),
          });
          const result = await got.json() as { ok?: boolean; said?: string };
          if (got.status === 409) return '账号关联已变化，请刷新后重新确认。';
          if (!got.ok || result.ok !== true) {
            return result.said || '操作没有完成。';
          }
          await refresh(find.input.value.trim());
          return null;
        } catch (why) {
          console.error('身份绑定操作失败：', why);
          return '操作失败，身份绑定未确认。';
        }
      };
      const association = one.association;
      if (!association?.revision) {
        zone.append(line('p', 'identity-status', '账号信息不完整，请刷新后再操作。'));
        return zone;
      }
      const advanced = document.createElement('section');
      advanced.className = 'identity-advanced';
      const summary = line('h3', '', '高级管理');
      advanced.append(summary);
      if (association) {
        const linking = line('section', 'identity-linking');
        linking.append(line('h4', '', '关联其他画像'));
        const target = document.createElement('select');
        target.className = 'engine-input'; coreSetUIAttribute(target, 'aria-label', corePhrase("同一个人的另一份画像"));
        const current = document.createElement('option');
        current.value = ''; coreSetUIText(current, 'textContent', corePhrase("仅确认当前账号"));
        target.append(current);
        for (const row of rows.filter(r => r.who !== one.who)) {
          const option = document.createElement('option'); option.value = row.who;
          option.textContent = (row.display_name || row.who) + ' · ' + portraitOrigin(row.display_account || row.plat || row.who);
          target.append(option);
        }
        const associate = line('button', 'identity-button', '确认是同一个人');
        associate.addEventListener('click', () => {
          const chosen = target.value ? rows.find(row => row.who === target.value) : undefined;
          const participants = chosen ? [one, chosen] : [one];
          if (participants.some(row => !row.association
              || row.association.revision !== association.revision)) {
            coreSetUIText(status, 'textContent', corePhrase("关联信息已变化，请刷新后重试。")); return;
          }
          const profiles = [...new Set(participants.flatMap(r =>
            [...r.association!.profile_keys]))].sort();
          const accounts = [...new Set(participants.flatMap(r =>
            [...r.association!.accounts]))].sort();
          confirmIdentity('确认是同一个人', profiles.join(' 与 '),
            '将关联画像：' + profiles.join('、') + '。账号：' + (accounts.join('、') || '暂无')
              + '。旧内容保留、关联读取；不开放其他群聊历史，不把私聊带进群里。'
              + '本机控制台仅代表入口，请核对实际使用者。',
            '确认关联', () => update({ action: 'associate',
              profile_keys: participants.map(r => r.who).sort(),
              expected_profiles: profiles, expected_accounts: accounts,
              revision: association.revision }));
        });
        const controls = line('div', 'identity-controls'); controls.append(target, associate);
        linking.append(controls); zone.append(linking);
        if (identity?.profile_keys && identity.profile_keys.length > 1) {

          const separate = line('button', 'identity-button subtle', '分开这份画像');
          separate.addEventListener('click', () => {
            const accounts = [...(association.separate_accounts ?? [])];
            confirmIdentity('分开这份画像', one.who,
              '保留这份画像的原内容，并移到独立身份。随它分开的账号：'
                + (accounts.join('、') || '无') + '。其余关联保留。',
              '确认分开', () => update({ action: 'separate', profile_key: one.who,
                expected_accounts: accounts, revision: association.revision }));
          });
          separate.classList.add('identity-separate');
          advanced.append(separate);
        }
      } else {
        // Older running servers retain the original controls until a controlled restart.
      }
      if (!identity) {
        advanced.append(line('p', 'why', '尚未设置账号关联。'));
        const create = line('button', 'identity-button', '建立身份');
        create.addEventListener('click', () => {
          confirmIdentity('建立独立身份', one.who,
            '只为这份画像建立身份记录。不会自动合并其他画像，也不会迁移旧消息。',
            '确认建立', () => update({ action: 'create', profile_key: one.who }));
        });
        advanced.append(create);
      } else {
        const renameBlock = line('section', 'identity-editor');
        renameBlock.append(line('h4', '', '修改称呼'));
        const name = document.createElement('input');
        name.type = 'text';
        name.maxLength = 80;
        name.value = (identity.profile_keys ?? []).includes(identity.label) ? '' : identity.label;
        name.placeholder = '人物称呼';
        name.setAttribute('aria-label', '人物称呼');
        const rename = line('button', 'identity-button', '保存称呼');
        rename.addEventListener('click', () => {
          const label = name.value.trim();
          if (!label) {
            status.textContent = '请填写人物称呼。';
            return;
          }
          confirmIdentity('确认人物称呼', label, '已绑定的私人入口会使用这个称呼。',
            '保存称呼', () => update({ action: 'rename', person_id: identity.id,
              revision: association.revision, label }));
        });
        const naming = line('div', 'identity-add');
        naming.append(name, rename);
        renameBlock.append(naming); advanced.append(renameBlock);
        const linked = line('section', 'identity-linked');
        linked.append(line('h4', '', '已关联账号'));
        for (const account of identity.accounts) {
          const row = line('div', 'identity-account');
          row.append(line('span', '', portraitOrigin(account)));
          const remove = line('button', 'identity-button subtle', '解绑');
          remove.disabled = !association?.revision;
          remove.addEventListener('click', () => {
            if (!association?.revision) { coreSetUIText(status, 'textContent', corePhrase("请刷新后再解绑。")); return; }
            const expected = { action: 'unbind', account,
              expected_person_id: identity.id, revision: association.revision };
            confirmIdentity('解绑账号', account,
              '解除这个账号与当前身份的关联。已有画像和历史消息不会被改写。',
              '确认解绑', () => update(expected));
          });
          row.append(remove);
          linked.append(row);
        }
        zone.insertBefore(linked, zone.querySelector('.identity-linking'));
        const accountBlock = line('section', 'identity-editor');
        accountBlock.append(line('h4', '', '手动添加账号'));
        const add = line('div', 'identity-add');
        const account = document.createElement('input');
        account.type = 'text';
        account.autocomplete = 'off';
        coreSetUIText(account, 'placeholder', corePhrase("填写账号"));
        coreSetUIAttribute(account, 'aria-label', corePhrase("平台账号"));
        const platform = document.createElement('select');
        platform.setAttribute('aria-label', '账号平台');
        platform.append(new Option('QQ', 'qq'), new Option('控制台', 'console'), new Option('其他平台', 'custom'));
        platform.addEventListener('change', () => {
          account.hidden = platform.value === 'console';
          account.placeholder = platform.value === 'custom' ? '平台:账号' : '填写 QQ 号';
        });
        account.placeholder = '填写 QQ 号';
        const bind = line('button', 'identity-button', '绑定账号');
        bind.addEventListener('click', () => {
          const raw = account.value.trim();
          const key = platform.value === 'console' ? 'console:local-owner' : platform.value === 'qq' ? (raw ? 'qq:' + raw : '') : raw;
          if (!key) { coreSetUIText(status, 'textContent', corePhrase("请填写平台账号。")); return; }
          status.textContent = '';
          confirmIdentity('确认账号归属', key,
            '请核对这个账号与「' + one.who + '」属于同一人。' +
              '确认后，下一次私人请求才会使用这条关联。',
            '确认绑定', () => update({ action: 'bind', person_id: identity.id, account: key }));
        });
        add.append(platform, account, bind);
        accountBlock.append(add); advanced.append(accountBlock);
      }
      const separate = advanced.querySelector('.identity-separate');
      if (separate) {
        const row = line('div', 'identity-separate-row');
        row.append(line('span', '', '解除当前画像的关联，保留原始记录。'), separate);
        advanced.append(row);
      }
      const fields = line('div', 'identity-advanced-body');
      while (advanced.children.length > 1) fields.append(advanced.children[1]);
      advanced.append(fields);
      zone.append(advanced);
      zone.append(status);
      zone.append(line('p', 'why',
        '关联后从下次对话生效。原始记录保留，不会跨群共享聊天内容。'));
      return zone;
    };

    const pick = (index: number): void => {
      at = index;
      selectedWho = rows[index].who;
      for (const [i, el] of [...roster.querySelectorAll<HTMLElement>('.one')].entries()) {
        el.classList.toggle('on', i === index);
        el.setAttribute('aria-pressed', String(i === index));
      }
      showFace(index);
      face.scrollTop = 0;
    };

    let rowIndex = 0;
    for (const group of groups) {
      const section = line('section', 'roster-group');
      const heading = line('h2', 'roster-group-heading');
      heading.append(line('span', '', group.label), line('span', 'roster-group-count', String(group.people.length)));
      section.setAttribute('aria-label', group.label);
      const items = line('div', 'roster-group-items');
      section.append(heading, items);
      roster.append(section);
      group.people.forEach(one => {
      const index = rowIndex++;

      const item = line('button', 'one') as HTMLButtonElement;
      item.type = 'button';
      const label = line('span', 'n', one.display_name || one.who);
      label.title = one.who; item.append(label);
      if (one.blocked) item.append(line('span', 'gate', ''));
      if (String(one.plat ?? '') !== '') {
        item.append(line('span', 'p', portraitOrigin(one.display_account || String(one.plat))));
      }
      item.addEventListener('click', () => { pick(index); });
      if (portraitLinkedToConsole(one)) item.append(line('span', 'roster-linked', '已关联控制台'));
      items.append(item);
      });
    }
    pick(at);

    tail.append(
                line('span', '', '共 ' + String(rows.length) + ' 个人'));
  };

  /** 那一个框是真的：`/people?q=` 那条口后端也接了 （先精确、再模糊 ）。 */
  let selectedWho = '';
  let requestId = 0;
  const readHint = pageLoad(root, '画像', () => { void refresh(find.input.value.trim()); });
  const refresh = async (q: string): Promise<void> => {
    const mine = ++requestId;
    const ask = q === '' ? '/people' : '/people?q=' + encodeURIComponent(q);
    readHint.start();
    try {
      const answer = await getStatusJson<PeopleAnswer>(ask);
      if (mine !== requestId || root.hidden) return;
      draw(answer); readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) { void refresh(q); return; }
      console.error('画像读取失败：', why); readHint.fail();
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
