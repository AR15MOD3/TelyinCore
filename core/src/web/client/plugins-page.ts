import { pluginCategories, pluginCategory, pluginCategoryLabel, pluginRequirement } from './plugin-catalog';
import { pluginUpdateNotice, updateFor, updateText, pluginUpdatesPanel, takePluginUpdateRequest } from './plugin-updates';
import { confirmAction } from './confirm-dialog';
import { uiLabel } from './ui-language';
import { corePhrase, coreSetUIAttribute, coreSetUIText, coreUITextNode } from './ui-language';
import { PLUGIN_GUIDE_VERSION } from './plugin-guide-version';
import { assetUrl } from './asset-url';
import { pageFetch as fetch } from './status-json';
// 插件页提供已安装列表和扩展市场。插件可选，人格、记忆和知识库属于核心。服务端 wired 与空列表分别表示读取能力和安装状态。

import { icon, type Chrome, type Panel } from './shell';

/* 服务端返回的已安装插件信息。 */
export interface InstalledPlugin {
  readonly navigationParent?: string;
  readonly package_id?: string;
  readonly can_remove?: boolean;
  readonly reload_mode?: 'hot'|'restart'|'invalid';
  readonly restart_required?: boolean;
  readonly restart_action?: 'enable'|'disable'|'config'|'';
  readonly restart_reason?: string;
  readonly lifecycle_error?: string;
  readonly tracked?: boolean;
  /** 包名（`package.json` 的 `name` ）。 */
  readonly name: string;
  /* 显示名称；缺省使用包名。 */
  readonly displayName: string;
  /** 版本。 */
  readonly version: string;
  /* 插件类型。 */
  readonly kind: string;
  /** 它**此刻**在不在生效（不是"装没装"—— 装与在效是两件事 ）。 */
  readonly enabled: boolean;
  /** 服务端提供的当前加载状态，不由启用开关推断；
   * ⚠️ 它与 `enabled` **分开**正是为了说清这件事：开了开关 ≠ 跑起来了 。 */
  readonly loaded: boolean;
  /* 插件要求的契约版本。 */
  readonly api: number | null;
  /** **契约那一句**（空那一串 ＝ 对得上 ／ 有字 ＝ 为什么对不上 ）
   * —— 那一句**由服务端判**（`plugins._contract_said` ），页这一侧只把它显示出来 。 */
  readonly contract: string;
  // 详情元数据来自包声明；作者缺省回退到安装来源的 origin.by。
  /** 作者（包里的 `author`；没写就退回"从哪儿来"那一份里的 owner ）。 */
  readonly author?: string;
  /** 许可证（包里的 `license` ）。 */
  readonly license?: string;
  /** 一句话简介（包里的 `description` ）。 */
  readonly description?: string;
  /** 入口那一份（包里的 `main`；没写就是 `index.py` ）。 */
  readonly entry?: string;
  /** **体积**（那一格目录里所有普通文件的字节数之和 —— 量出来的 ）。 */
  readonly bytes?: number;
  /** **文件数**。 */
  readonly files?: number;
  /** 那一格目录在哪（详情面板上"安装位置"那一行 ）。 */
  readonly where?: string;
  /** **从哪儿来**（装的时候记下来的：哪条链接 ／ 谁写的 —— 空那一份 ＝ 不知道 ）。 */
  readonly repository?: string;
  readonly origin?: { readonly source?: string; readonly url?: string; readonly by?: string };
  /** 关键字（包里的 `keywords` ）。 */
  readonly keywords?: readonly string[];
  /** **这是一份"内置的"吗**（随这个项目发的 —— 2026-10-05 作者："下载这个项目内置的插件"）。 */
  readonly builtin?: boolean;
  /** **装了没有** —— 内置那一批**卸了也不消失**（那时这一格是 `false` ⇒ **整块灰着** ）。 */
  readonly installed?: boolean;
}

/** 服务端那一格交回来的东西（`GET /plugins`）。 */
export interface PluginRuntime {
  instance: string; bot?: string; restart_supported: boolean; restart_reason?: string;
}
export interface PluginsAnswer {
  readonly runtime?: PluginRuntime;
  readonly broken?: readonly string[];
  /* 插件安装位置。 */
  readonly where: string;
  /** 装着的那些（一个都没有 ⇒ 空那一串 —— 那是**一个答案**，不是"读不到" ）。 */
  readonly plugins: readonly InstalledPlugin[];
  /** ⚠️ **这个口接了没有**：`false` ⇒ 页上写"还没接上"（与"零个插件"分得开 ）。 */
  readonly wired: boolean;
}

/** 市场里那一个包（`GET /market` ⇒ **GitHub** 按话题搜出来的 ）。 */
export interface MarketPackage {
  readonly source?: string;
  readonly full?: string;
  readonly url?: string;
  readonly name: string;
  readonly version: string;
  readonly description: string;
  readonly author: string;
  readonly date: string;
  readonly keywords: readonly string[];
}

/** 市场那一格的答案。 */
export interface MarketAnswer {
  /** **这一份是"上次问到的"还是"刚问的"**（2026-10-05：先拿旧那一份顶、
   *  同时在后台重问 ⇒ 页上**要说出来**，不说的话它看着像刚问的）。 */
  readonly stale?: boolean;
  readonly error?: string;
  /** 搜到的那些（`[]` ＝ **一个都没有** —— 那是一个答案 ）。 */
  readonly found: readonly MarketPackage[];
  /** ⚠️ **问到了没有**：`false` ⇒ "根本没问到"（与"问到了、没人发过"分得开 ）。 */
  readonly wired: boolean;
  /** 问的那个口（页上要说清"从哪儿来的" ）。 */
  readonly where: string;
  /** 一句人话（"还没人发过" ／ "没拉到" ／ "搜到 N 个" ）。 */
  readonly said: string;
}

/** 这一页交回的东西（照 `Panel` 的形状；`show` ／ `stop` 由壳调 ）。 */
export interface PluginsView extends Panel {
  readonly root: HTMLElement;
  /** 那一格读回来之后画一遍（页面那一侧每次 `show` 都叫它 ）。 */
  draw(answer: PluginsAnswer | null): void;
  /** 市场那一格读回来之后画一遍（与上面那一条分开 —— **两件事两个落点** ）。 */
  drawMarket(answer: MarketAnswer | null): void;
}

// ⚠️ **那一句原先写的是"插件的名单那条路今天还没接上"** —— 而那**与树对不上** ：
// `launch.py` 早就把读法递进去了（线上 `wired: True` ）⇒ "今天还没接上"是一句
// **随时间失效的话** 。 而那一支本身**不是死的**（判据钉着：服务那一层没递读法时
// 它答 `wired: false` ）⇒ 所以改的是**措辞**，把"今天"那半去掉：
// 它现在说的是**那一层此刻有没有拿到读法**（不随日子变 ）。
// ⚠️ **那两句占位删了**（2026-10-05 作者："我在这个页面按 F5 刷新的时候有些时候能看见
//   这两句话，能不能把这两句去掉"）——
// 真因不是"接没接上"，而是**页一建出来就在那一格里摆着那句话** ⇒
// 它在"第一次问回来"之前一直摆着 ⇒ 慢一点就看得见它一闪 。
// ⇒ 现在：**起始空着**（不占一行、不闪 ）， 而**真出错**时才说话 。
const NONE_YET = '暂无已安装插件。';
// 而这一句去掉那半句附注（"不当成零个插件"）—— 那半句是**写给我们自己看的**，
// 摆在页上只会让人以为"页面在解释一个不存在的问题" （ 而事实照旧说清：读不到就是读不到 ）。
const READ_FAILED = '无法读取本机插件。';
// 同一个道理（那一句是"没拉到 GitHub"那一种 —— 而它说的是事实，不带附注 ）。
const MARKET_NOT_WIRED = '暂时无法连接 GitHub。';
const FILTERED_OUT = '没有符合条件的本机插件。';

/** **这一份的源代码仓库在哪**（2026-10-05 作者："2也要做" ⇒ 照参考那张底下那枚
 * 「源代码仓库」）。 两边的来处不同，而**都取自我们手里真有的东西**：
 * · 市场那一份 ⇒ `full`（`<owner>/<repo>` —— 从 GitHub 搜来的）；
 * · 已安装那一份 ⇒ **装的时候记下来的那条链接**（`origin.url` ）里那两段。
 * ⚠️ 取不到 ⇒ 回空串（ 页上那一枚**不画** —— 画一个点了不动的"源代码仓库"是假的 ）。 */
function repoOf(one: InstalledPlugin | MarketPackage, from: 'installed' | 'market'): string {
  if (from === 'market') {
    const full = (one as MarketPackage).full || '';
    return full ? `https://github.com/${full}` : '';
  }
  const url = (one as InstalledPlugin).origin?.url || '';
  const cut = /^https:\/\/github\.com\/([^/]+\/[^/#?]+)/.exec(url);
  return cut ? `https://github.com/${cut[1]}` : '';
}
const MARKET_FAILED = '无法读取插件市场。';
// 操作已经把请求发出去之后，列表那一格仍可能读不回来 —— 这时**不能**把操作
// 那一句真话吞掉，也不能让页面假装自己是最新的。两句一起说。
const REFRESH_FAILED = '列表刷新失败，当前显示可能不是最新。';

/** **版本那一格的写法**（2026-10-05 作者给的排法：`版本：v0.1.0`）。
 *  ⚠️ 只对**数字开头**的那一种补 `v`（`0.1.0` ⇒ `v0.1.0`）——
 *  而「分支，没打 tag」那一类**不补**（它不是一个版本号）。 */
function showVersion(said: string): string {
  const one = (said || '').trim();
  if (!one) { return '未声明版本'; }
  return /^[0-9]/.test(one) ? `v${one}` : one;
}

/** 那一页。 */
export function pluginOverviewPage(shell: Chrome): PluginsView { return pluginsPage(shell, 'overview'); }

export function pluginsPage(shell: Chrome, mode: 'market'|'overview' = 'market'): PluginsView {
  const overview=mode==='overview';
  const root = document.createElement('section');
  root.className = 'plugins page-plugin';
  root.dataset.pageRole=mode;
  // **2026-10-09 作者：「已安装和插件市场做成其他几页的标题一样的大小和标签。」**
  // ⇒ **`h2` → `h1`** （ 其他几页的页标题都是 `h1` ）； 字号那一半在
  // `style.css` 的 `.plugins-title-row .plugins-title` 上 （ 只有这两处吃它 ）。
  const title = document.createElement('h1');
  title.className = 'plugins-title';
  // 2026-10-05 作者：「让你在市场的上面写一个已安装的标题，把安装的插件显示在那里」
  //  ⇒ 那一段的标题就叫「已安装」（与下面那一段对称）。
  // **2026-10-09 作者：「给它们俩也加上 · 英文翻译」** —— 与另外几页同一个写法
  // （`知识库 · KNOWLEDGE` ／ `画像 · PROFILE` ／ `经历 · EXPERIENCE` ／ `记忆 · MEMORY` ）。
  coreSetUIText(title, 'textContent', corePhrase("已安装 · INSTALLED"));
  // **标题右边那一枚小胶囊**（2026-10-05 作者："你好好看图。参考它的排版。"）——
  // 参考那一行是「已安装」＋ **一枚写着安装位置的胶囊** （而我们原先把那一句
  // 写成标题**下面**一行小字 ）。 而这一枚里写的是**真地址**（`answer.where` ）。
  const whereChip = document.createElement('span');
  whereChip.className = 'plugins-where';
  const titleRow = document.createElement('div');
  titleRow.className = 'plugins-title-row';
  titleRow.append(title, whereChip);
  const note = document.createElement('p');
  note.className = 'plugins-note';
  // **初值空着**（ 这一句就是"按 F5 能看见那两句"的来处 —— 它一直摆到第一次问回来 ）
  note.textContent = '';
  // ⚠️⚠️ **两段各有一个自己的容器**（2026-10-05 真驱动浏览器抓到的）：
  // `draw()` ／ `drawMarket()` 原先把列表 `append` 到 `root` 的**末尾**
  // ⇒ 于是"已安装"那张卡**跑到"插件市场"那一段底下**去了
  // （ 而那在屏幕上看着像"市场里装着它" —— 一个纯粹的次序 bug ）。
  // ⇒ 两段各有各的落点：正文进自己的那一个格 （`note` ／ `marketNote` 同一条口径 ）。
  const installedBox = document.createElement('div');
  installedBox.className = 'plugins-box plugins-installed-box';
  // ⚠️ **那一条大分隔线删掉了**（2026-10-09 作者指着它说：「**然后这根线能不能删掉。**」）
  // —— 它原先在这儿（「参考那张图上，两段之间有一条横贯的线」）， 而这一页的两段
  // **各有各的标题** ⇒ 那根线是多余的 。
  // ⚠️ 它那一格样式（`.plugins-divider`）**留着** —— 别处还提得到它，删样式是另一件事 。
  // **第二段：插件市场**（照它那一页的两段版式：已安装 ／ 扩展市场 ）
  // ⚠️ **两段各有各的落点**（`note` ／ `marketNote`）—— 一个落点写两种话，
  // 页上就分不出"哪一段在说什么"了 （与那两格"读不到 ／ 零个"同一条口径 ）。
  // 同上那一句（ 作者：「已安装和插件市场做成其他几页的标题一样的大小和标签。」）。
  const marketTitle = document.createElement('h1');
  marketTitle.className = 'plugins-title';
  // 同上那一句（ 「给它们俩也加上 · 英文翻译」）。
  coreSetUIText(marketTitle, 'textContent', corePhrase(overview ? "插件总览" : "插件市场"));
  // **「插件市场」那一行**（2026-10-05 作者："根据图2的样子，把隐藏已安装放到图3里的右侧，
  // 记得做好对齐和排版"）—— 照图2那条（「已安装」＋ 紧跟它右边那一枚胶囊 ）：
  // 标题与它那一枚**同一行**，用的是**同一个** `.plugins-title-row` （一处一条 ）。
  const marketTitleRow = document.createElement('div');
  // **2026-10-09 作者：「插件市场上面塞一个换行。」** —— 那根分隔线删掉之后，
  // 两段之间就**一点空都不剩**了 ⇒ 给这一行一个自己的类 （ 那一脚在 style.css 上 ）。
  marketTitleRow.className = 'plugins-title-row plugins-market-row';
  const developmentGuide = document.createElement('a');
  developmentGuide.className = 'infinity-text-link';
  coreSetUIText(developmentGuide, 'textContent', [[corePhrase("插件开发规范 "), PLUGIN_GUIDE_VERSION], corePhrase(" ↗")]);
  developmentGuide.href = assetUrl('infinity-plugin-spec.html');
  developmentGuide.target = '_blank'; developmentGuide.rel = 'noopener noreferrer';

  const marketNote = document.createElement('p');
  marketNote.className = 'plugins-note plugins-market-note';
  // 初值同样空着
  marketNote.textContent = '';
  /** 市场那一段的正文容器（与上面那一个同一条口径 ）。 */
  const marketBox = document.createElement('div');
  marketBox.className = 'plugins-box plugins-market-box';
  // ⚠️⚠️ **详情面板那一格要在这儿造**（2026-10-05 真驱动浏览器抓到的 —— 同一类错**第二次**）：
  // 我原先把 `const detail = …` 写在 `openDetail` 那儿（**在 `root.append(...)` 之后** ）
  // ⇒ `root.append(…, detail)` 撞上 TDZ ⇒ 页构建时 `ReferenceError` ⇒ **整页空白**
  // （连那两行提示都读不到 ）—— 而**打包一句话都没说** 。
  // ⇒ 落点：**凡是"要挂上去的"，都在挂之前造** 。
  // ⚠️⚠️ **2026-10-05 作者："详情应该是个浮窗不是打开在下面，这样根本看不见了"** ——
  // 所以这一格是**浮窗**：`detail` 是**那一层压暗的底**，`detailCard` 才是**那张纸**
  // （ 位置与尺寸全在 `style.css` 里 —— 它 `position: fixed` 覆盖整屏 ）。
  const detail = document.createElement('dialog');
  detail.className = 'plugins-detail';
  detail.setAttribute('aria-label', '插件详情');
  const detailCard = document.createElement('div');
  detailCard.className = 'plugins-detail-card';
  detail.append(detailCard);
  // **点那一层底就关**（浮窗的常规 —— 而纸**里面**点不关：那一下不该被读成"关" ）
  detail.addEventListener('click', (ev) => {
    if (ev.target === detail) { detail.close(); }
  });
  // **市场那一段的表头**（2026-10-05 作者："做"）—— 照参考那份工具栏：
  // 一个「隐藏已安装」的开关（它那一栏的右边那一枚 ）。
  // ⚠️ **为什么值一个开关**：同一个插件会**同时**出现在两段里（ 那**不是错** ——
  // 那是"市场里有它，而你可能已经装了"），而**有人只想看还没装的** 。
  // **两处搜索栏**（2026-10-05 作者："把这个搜索栏做出来，已安装和插件市场都要有一个"）——
  // 照参考那张工具栏左边那个框（占位那句「筛选：名称、类型、描述」）。
  // ⚠️ **它是真筛**（不是画个框）：值一变就**用已经拿到手的那一份**重画 ——
  // 不再打一次网（ 那正是"筛"与"搜"的分界 ）。
  const findIn = (hint: string): HTMLInputElement => {
    const it = document.createElement('input');
    it.type = 'search';
    it.className = 'plugins-find';
    it.placeholder = hint;
    it.setAttribute('aria-label', hint);
    return it;
  };
  const installedFind = findIn('筛选：插件名、描述、作者');
  const marketFind = findIn('筛选：插件名、描述、作者');
  const searchField = (input: HTMLInputElement, name: string): HTMLElement => {
    const field = document.createElement('label');
    field.className = 'plugins-search';
    input.setAttribute('aria-label', name);
    field.append(icon('search'), input);
    return field;
  };
  // **每页显示**（2026-10-05 作者："每页显示数量要做，我们的 core 页面没有滚动条，
  // 只能显示当前页里面的一定数量的插件卡片"）—— 而**那不是猜的**：正文那一格
  // 实测 `overflow: hidden` 而 `scrollHeight 669 > 高 351` ⇒ **下面的东西看不见也滚不到**
  // ⇒ 所以"每页几个"是**必须**的 。
  const perSelect = (): HTMLSelectElement => {
    const box = document.createElement('label');
    box.className = 'plugins-perlabel';
    box.append(coreUITextNode(corePhrase("每页显示")));
    const it = document.createElement('select');
    it.className = 'plugins-per';
    for (const n of [6, 12, 24]) {
      const one = document.createElement('option');
      one.value = String(n);
      one.textContent = `${n}`;
      it.append(one);
    }
    it.value = '12';
    box.append(it);
    box.dataset.for = 'plugins';
    return it;
  };
  const installedPer = perSelect();
  const marketPer = perSelect();
  /** 一枚"再来一次"（照参考那张右边那枚「↻ 刷新」）—— **它真去重问一次** 。 */
  const againBtn = (said: string, what: () => void): HTMLButtonElement => {
    const it = document.createElement('button');
    it.type = 'button';
    it.className = 'btn ghost plugins-again';
    it.textContent = said;
    it.addEventListener('click', what);
    return it;
  };
  const installedRow = document.createElement('div');
  installedRow.className = 'plugins-toolbar';
  installedRow.append(searchField(installedFind, '筛选已安装插件'),
                      installedPer.parentElement as HTMLElement,
                      againBtn('↻ 刷新', () => { void refresh(); }));
  // 更新筛选由更新标签页提供。
  const marketRow = document.createElement('div');
  marketRow.className = 'plugins-toolbar';
  marketRow.append(searchField(marketFind, '筛选插件市场'),
                   marketPer.parentElement as HTMLElement);
  const hideBox = document.createElement('label');
  hideBox.className = 'plugins-hide';
  const hideTick = document.createElement('input');
  hideTick.type = 'checkbox';
  // 2026-10-05 作者：「我上传的 GitHub 插件并没有安装，应该放在插件市场里」
  //  ⇒ 两段真正分开：市场那一段只列还没装的。
  hideTick.checked = false;
  hideTick.className = 'plugins-hide-tick';
  const hideSay = document.createElement('span');
  coreSetUIText(hideSay, 'textContent', corePhrase("隐藏已安装"));
  hideBox.append(hideTick, hideSay);
  marketRow.append(againBtn('↻ 刷新', () => { void refreshMarket(); }));
  // 它现在挂在标题那一行（ 而不是工具栏里 —— 照图2的排法 ）
  marketTitleRow.append(marketTitle, developmentGuide);
  marketRow.append(hideBox);
  const updatePanel=pluginUpdatesPanel();
  const marketTabs=document.createElement('nav');marketTabs.className='plugin-market-tabs';
  marketTabs.setAttribute('aria-label','插件管理分区');marketTabs.setAttribute('role','tablist');
  const tabs=['本机插件','发现插件'].map(label=>{const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('role','tab');marketTabs.append(button);return button;});
  const categoryBar=document.createElement('nav');categoryBar.className='plugin-categories';categoryBar.setAttribute('aria-label','插件用途');
  let category='all';
  const categoryButtons=new Map<string,HTMLButtonElement>();
  for(const [id,label] of pluginCategories){
    const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('aria-pressed',String(id===category));
    button.onclick=()=>{category=id;pageOut=1;for(const [key,node] of categoryButtons)node.setAttribute('aria-pressed',String(key===category));if(lastMarket)drawMarket(lastMarket);};
    categoryButtons.set(id,button);categoryBar.append(button);
  }
  const categoryCounts=(rows:readonly MarketPackage[])=>{
    for(const [id,button] of categoryButtons){const count=id==='all'?rows.length:rows.filter(one=>pluginCategory(one)===id).length;
      button.textContent=`${pluginCategories.find(([key])=>key===id)![1]} ${count}`;
      button.hidden=id!=='all'&&count===0&&id!==category;
    }
  };
  let selectedTab=0;
  let showingUpdates=false;
  const updateButton=document.createElement('button');updateButton.type='button';updateButton.className='btn ghost plugin-updates-entry';updateButton.textContent='检查更新';
  installedRow.append(updateButton);updateButton.hidden=overview;
  const returnFromUpdates=document.createElement('button');returnFromUpdates.type='button';returnFromUpdates.className='btn ghost plugin-updates-back';returnFromUpdates.textContent='← 本机插件';
  updatePanel.root.prepend(returnFromUpdates);
  const selectMarket=(index:number, updates=false)=>{
    selectedTab=index;showingUpdates=!overview&&updates;
    root.dataset.marketView=showingUpdates?'updates':overview||index===0?'installed':'discover';
    titleRow.hidden=true;
    note.hidden=installedRow.hidden=installedBox.hidden=(!overview&&index!==0)||showingUpdates;
    marketNote.hidden=marketRow.hidden=marketBox.hidden=categoryBar.hidden=overview||index!==1;
    updatePanel.root.hidden=!showingUpdates;
    marketTabs.hidden=overview;
    tabs.forEach((button,i)=>{button.setAttribute('aria-selected',String(i===index));button.tabIndex=i===index?0:-1;});
    if(showingUpdates)updatePanel.render();
  };
  updateButton.onclick=()=>selectMarket(0,true);
  returnFromUpdates.onclick=()=>selectMarket(0);
  tabs.forEach((button,i)=>button.onclick=()=>selectMarket(i));
  marketTabs.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();const index=event.key==='Home'?0:event.key==='End'?1:1-selectedTab;
    selectMarket(index);tabs[index].focus();
  });
  const updateTabLabel=()=>{const count=lastInstalled?.plugins.filter(one=>one.installed!==false&&updateFor(one.name)?.status==='available').length||0;updateButton.textContent=count?`可更新 ${count}`:'检查更新';};
  const openRequestedUpdates=()=>{if(overview)return;const request=takePluginUpdateRequest();if(request){selectMarket(0,true);updatePanel.search(request.name);}};
  const runtimeFilter=document.createElement('select');runtimeFilter.setAttribute('aria-label','运行状态筛选');runtimeFilter.className='plugins-per';
  for(const [value,label] of [['all','全部状态'],['enabled','已启用'],['disabled','已停用'],['issue','已启用但未运行']])runtimeFilter.add(new Option(label,value));
  if(overview)installedRow.append(runtimeFilter);
  runtimeFilter.onchange=()=>{pageIn=1;if(lastInstalled)draw(lastInstalled);};
  root.append(marketTitleRow,marketTabs,titleRow,note,installedRow,installedBox,marketNote,marketRow,categoryBar,marketBox,updatePanel.root,detail);
  selectMarket(0);
  // ⚠️ **那个开关要看"两段的名字"** ⇒ 它得记住**上面那一段现在有什么**
  let haveInstalled = new Set<string>();
  // **最后拿到手的那一份**（2026-10-05）—— 筛的时候用它**重画**， 不再打一次网
  let lastInstalled: PluginsAnswer | null = null;
  let lastMarket: MarketAnswer | null = null;
  // ⚠️ **三次读在赛跑**：页一建出来、按刷新、装完再拉 —— 都会各发一条 GET。
  // 慢的那条**不能**在快的那条之后把新状态盖回去（服务端已经变了，页上却退回旧样子）。
  // ⇒ 每一条读带一个序号，只有**最后发出**、且**没有跨过 mutation** 的那条能落笔。
  let installedRead = 0;
  let marketRead = 0;
  /** 页面还在这（`stop()` 之后旧回调一律不许写隐藏面板 ）。 */
  let viewEpoch = 0;
  /** 每开始一次写操作就加一 —— 写之前的 GET 全部作废。 */
  let mutationEpoch = 0;
  // 页面级互斥：刷新重建按钮、详情与卡片切换也不能重复提交。
  let mutationBusy = false;
  const stillCurrent = (view: number, mutation: number): boolean =>
    root.hidden !== true && view === viewEpoch && mutation === mutationEpoch;
  /** 左栏那一份导航读的是同一格 `/plugins` —— 操作不论成败都提醒它重读。 */
  const notifyPluginsChanged = (): void => {
    if (typeof window.dispatchEvent === 'function' && typeof Event !== 'undefined') {
      window.dispatchEvent(new Event('plugins-changed'));
    }
  };
  /** 失败那一句与刷新那一句**都要留下**（后者只加一句，不覆盖前者 ）。
   *  成功时**没有回执要写** —— 页面不会因为「装好了」多出一行、把布局撑高 。
   */
  const withRefreshFailed = (failure: string,
                              fresh: { installed: boolean; market: boolean }): string => {
    const line = failure.trim();
    if (fresh.installed && fresh.market) { return line; }
    return line ? `${line} ${REFRESH_FAILED}` : REFRESH_FAILED;
  };

  /** 一条"这一份算不算命中"（ 不分大小写；空查询 ⇒ 都命中 ）。 */
  const hits = (hay: readonly (string | undefined)[], query: string): boolean => {
    const q = query.trim().toLowerCase();
    if (!q) { return true; }
    return hay.map((one) => (one || '').toLowerCase()).join(' ').includes(q);
  };
  installedFind.addEventListener('input', () => {
    pageIn = 1; // 换了查询 ⇒ 回第 1 页（ 不然会停在一个空页 ）
    if (lastInstalled) draw(lastInstalled);
  });
  marketFind.addEventListener('input', () => {
    pageOut = 1;
    if (lastMarket) drawMarket(lastMarket);
  });
  // **分页那三个格子**（2026-10-05）—— 每页几个 ／ 现在第几页（两段各一套 ）
  let pageIn = 1;
  let pageOut = 1;
  async function confirmUninstall(one: InstalledPlugin): Promise<boolean> {
    return confirmAction('卸载插件',
      '此安装包由所有档案共享。确认后将先在各档案停用并卸载插件，但不会删除配置和缓存。保留的数据不会自动清理。',
      '确认卸载', false, { className: 'plugin-uninstall-dialog', detail: one.displayName || one.name });
  }
  function uninstallButton(button: HTMLButtonElement, one: InstalledPlugin,
                           go: () => Promise<unknown>): void {
    let busy = false;
    button.addEventListener('click', () => {
      if (busy) return;
      busy = true; button.disabled = true;
      void (async () => {
        if (await confirmUninstall(one)) await go();
      })().catch(error => { console.error('卸载插件未完成', error); })
        .finally(() => { busy = false; button.disabled = false; });
    });
  }
  /** **两步走**（2026-10-05 作者："在停用和卸载这两个按钮按下去的时候要做一个二次确认…
   * 原理同模型页删除实例的那个按钮一致"）—— 照 `engine-page.ts:890` 那一条：
   * 点一下先变成**红框的「确认…」**，再点一下才办； 而过一会儿**自己退回去**
   * （不然它一直红着，像坏了 ）。
   * ⚠️ `calm` 在**退回时**把原来那一串类名装回去（ 别把它自己那一格丢了 ）。
   */
  function twoStep(btn: HTMLButtonElement, plain: string, hot: string, calm: string,
                   go: () => unknown | Promise<unknown>): void {
    let armed = false;
    let busy = false;
    let timer: number | undefined;
    const reset = (): void => {
      armed = false;
      if (timer !== undefined) { window.clearTimeout(timer); timer = undefined; }
      uiLabel(btn, plain);
      btn.className = calm;
      btn.disabled = false;
    };
    btn.addEventListener('click', () => {
      // 第二次点击之后按钮虽然 disabled，程序化点击仍可能进来 —— 再挡一层。
      if (busy) { return; }
      if (!armed) {
        armed = true;
        uiLabel(btn, hot);
        btn.className = 'btn danger';
        // ⚠️ **那两句提示删了**（2026-10-05 作者："不需要，而且会强制改变页面的样式，
        // 凭空多出来一行，导致整个页面往下一行"）—— 那个"凭空多出一行"是真的：
        // 提示写在某一格上 ⇒ 那一行从"空"变"有" ⇒ **整页往下挪** 。
        // ⇒ 而"点了一下会变成红框的确认…"这件事**按钮自己就说清了** （一处一条 ）。
        timer = window.setTimeout(() => {
          timer = undefined;
          if (!busy) { reset(); }
        }, 5000);
        return;
      }
      if (timer !== undefined) { window.clearTimeout(timer); timer = undefined; }
      armed = false;
      busy = true;
      btn.disabled = true;
      // 成功、失败、刷新失败、回调抛了 —— 都从 `finally` 回到可再点的那一格。
      // 卡在「确认安装 ／ 确认卸载」比多要一次确认坏得多。
      void Promise.resolve()
        .then(go)
        .catch((exc: unknown) => { console.error('插件操作没走完：', exc); })
        .finally(() => { busy = false; reset(); });
    });
  }

  /** 一次写操作的那条共同的路：**发请求 → 不论成败都拉真快照 → 再落话**。
   *  以前每一支各写一遍，于是有的支成功才刷新、失败只改按钮 —— 后端已经变了，
   *  页上还停在旧状态；而确认态又永远退不回来。
   *  ⚠️ 成功**只回 `ok`**：服务端那句成功回执不再写进页面（避免凭空多一行 ）；
   *  失败那一句照旧取 `back.said` ／ HTTP 正文 。
   */
  const runMutation = async (path: string, payload: unknown, target: HTMLElement,
                             fallback: string):
    Promise<{ ok: boolean; fresh: { installed: boolean; market: boolean } }> => {
    if (mutationBusy) {
      return { ok: false, fresh: { installed: false, market: false } };
    }
    mutationBusy = true;
    const token = ++mutationEpoch;
    const view = viewEpoch;
    installedRead += 1;
    marketRead += 1;
    let failure = '';
    let ok = false;
    try {
      try {
        const got = await fetch(path, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!got.ok) {
          failure = (await got.text()).trim() || fallback;
        } else {
          const back = await got.json() as { ok?: boolean; said?: string };
          // /plugins/remove 的业务失败仍可能是 HTTP 200，不能当成成功。
          if (back.ok === false) { failure = back.said || fallback; }
          else { ok = true; }
        }
      } catch (exc) {
        failure = fallback;
        console.error('插件操作没走到服务：', exc);
      }
      // 即使 POST 报错，磁盘也可能已经改变；左栏与两段列表都重读。
      notifyPluginsChanged();
      if (root.hidden) {
        return { ok, fresh: { installed: false, market: false } };
      }
      const fresh = await refresh();
      if (stillCurrent(view, token)) {
        const line = withRefreshFailed(failure, fresh);
        if (line) { target.textContent = line; }
      }
      return { ok, fresh };
    } finally {
      mutationBusy = false;
    }
  };

  /** **翻页那一行**（上一页 / 第 N 页 / 下一页）—— 只有一页时那两枚**不画** 。 */
  function pager(pages: number, now: number, go: (n: number) => void): HTMLElement {
    const bar = document.createElement('div');
    bar.className = 'plugins-pager';
    if (pages <= 1) { return bar; }
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'btn ghost plugins-turn';
    coreSetUIText(back, 'textContent', corePhrase("上一页"));
    back.disabled = now <= 1;
    back.addEventListener('click', () => { go(now - 1); });
    const where = document.createElement('span');
    where.className = 'plugins-where';
    coreSetUIText(where, 'textContent', [corePhrase("第 "), now, corePhrase(" / "), pages, corePhrase(" 页")]);
    const next = document.createElement('button');
    next.type = 'button';
    next.className = 'btn ghost plugins-turn';
    coreSetUIText(next, 'textContent', corePhrase("下一页"));
    next.disabled = now >= pages;
    next.addEventListener('click', () => { go(now + 1); });
    bar.append(back, where, next);
    return bar;
  }
  installedPer.addEventListener('change', () => {
    pageIn = 1;
    if (lastInstalled) { draw(lastInstalled); }
  });
  marketPer.addEventListener('change', () => {
    pageOut = 1;
    if (lastMarket) { drawMarket(lastMarket); }
  });
  // 照参考那张图右上角写的「× 关闭 (Esc)」—— **Esc 也关它**
  window.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { detail.close(); }
  });
  // **两段各一行数**（照参考底下那句「显示 1-5，共 5 个」）
  const count = document.createElement('p');
  count.className = 'plugins-note plugins-count';
  const marketCount = document.createElement('p');
  marketCount.className = 'plugins-note plugins-count';
  // 那个开关一动 ⇒ **只重画市场那一段**（ 上面那一段没变 ）
  hideTick.addEventListener('change', () => {pageOut=1;if(lastMarket)drawMarket(lastMarket);});
  // ⚠️ **这一句是"挂上去"那一步**（照 `persona-page.ts:733` ／ `engine-page.ts:199` 那两处）——
  // **少了它，左栏那一项点得动、而正文是空的** （实测抓到过一次：`content.children` 是空数组）。
  // 而 `show()` 只负责**取消刚藏**（上一页走的时候把它的 `root.hidden` 设成 `true` ）。
  shell.content.replaceChildren(root);

  const draw = (answer: PluginsAnswer | null): void => {
    // ⚠️⚠️ **先把上一次画的那一份清掉**（2026-10-05 真驱动浏览器时抓到的 bug）：
    // `draw()` **会被叫不止一次**（每次 `show` ／ 每次按开关之后重画 ）⇒
    // 少了这一句，**那几行会一次一次地往上堆** （实测：按一次开关 ⇒ 名单里出现**两行**
    // 同一份插件 —— 而那在屏幕上看着像"装了两个"）。
    // 照 `persona-page.ts` ／ `engine-page.ts` 那两处的口径：**这一个是那一份正文**，
    // 重画就是把旧的换掉（`replaceChildren` 那一条 ）。
    // ⚠️ 清的是**这一段的容器**（ 不是全页找 `.plugins-row` —— 那会把另一段的也清掉 ）
    installedBox.replaceChildren();
    for (const old of root.querySelectorAll('.plugins-warn')) old.remove();
    if (answer === null) { note.textContent = READ_FAILED; return; }
    // 这一层**没接上读法**时照旧要说（ 不然"读不到"会被读成"一个都没装" ）——
    // 而它说的是**那一句不带附注的**（ 而它只在真的没接上时出现，不在刷页那一瞬 ）。
    if (!answer.wired) { note.textContent = READ_FAILED; return; }
    // **记下"已安装那一段现在有什么"**（2026-10-05：「隐藏已安装」那个开关要用 ）——
    // ⚠️ 记的是**名字**（ 而市场那一边给的也是名字 —— 两边比的就是它 ）。
    haveInstalled = new Set(answer.plugins
      .filter((one) => one.installed !== false).map((one) => one.name));
    lastInstalled = answer;
    updateTabLabel();

    const localPlugins=answer.plugins.filter(one=>!overview||one.installed!==false);
    if (localPlugins.length === 0) {
      note.textContent = NONE_YET;
      installedFind.hidden = true; // 一个都没有 ⇒ 那个框没意义（收起 ）
      return;
    }
    installedFind.hidden = false;
    // **过了筛子的那些**（2026-10-05：那个搜索框是真筛 ）
    const shown = localPlugins.filter(one=>!overview||runtimeFilter.value==='all'||runtimeFilter.value==='enabled'&&one.enabled||runtimeFilter.value==='disabled'&&!one.enabled||runtimeFilter.value==='issue'&&one.enabled&&!one.loaded).filter((one) => hits(
      [one.name, one.displayName, one.description, one.author, one.kind], installedFind.value)).sort((a,b) => (!overview ? Number(!!b.builtin)-Number(!!a.builtin) || Number(updateFor(b.name)?.status === 'available') - Number(updateFor(a.name)?.status === 'available') : 0) || a.name.localeCompare(b.name));
    if (shown.length === 0) {
      // 与市场那一段同一条口径：**筛空也说一行数**（ 而不是只剩一句提示 ）
      note.textContent = FILTERED_OUT;
      coreSetUIText(count, 'textContent', corePhrase("共0个已安装，共0个已启用。"));
      installedBox.append(count);
      return;
    }
    // 位置那一句挪到标题右边那一枚里了（照参考那一行）—— 而这一行留给别的话。
    coreSetUIText(whereChip, 'textContent', (answer.where ? [corePhrase("安装位置："), answer.where, corePhrase("")] : corePhrase("")));
    whereChip.hidden = !answer.where;
    // ⚠️ **这一句要清**（2026-10-05 真驱动浏览器抓到的）：那一句初值（"还没接上"）
    // 原先是由"位置那一句"顺手抹掉的 ⇒ 那一句挪进胶囊之后，它**永远留着了** 。
    note.textContent = '';
    if (answer.broken?.length) {
      coreSetUIText(note, 'textContent', [[corePhrase("插件目录读取失败："), answer.broken.join('、')], corePhrase("。请检查各目录的 package.json。")]);
    }
    const list = document.createElement('ul');
    list.className = 'plugins-row';
    // **只画当前那一页**（ 正文那一格没有滚动条 ⇒ 多了就够不着 ）
    const every = Math.max(1, Number(installedPer.value) || 6);
    const pages = Math.max(1, Math.ceil(shown.length / every));
    if (pageIn > pages) { pageIn = pages; }
    const from = (pageIn - 1) * every;
    const slice = shown.slice(from, from + every);
    let group: boolean|undefined;
    for (const one of slice) {
      if(!overview&&group!==!!one.builtin){
        group=!!one.builtin;
        const heading=document.createElement('li');heading.className='plugin-origin-heading';
        const title=document.createElement('h2');title.textContent=group?'Core 内置':'自行安装';
        const note=document.createElement('span');note.textContent=group?'随 Core 提供与更新':'来自社区或本地导入';
        heading.append(title,note);list.append(heading);
      }
      const item = document.createElement('li');
      // **内置而没装 ⇒ 整块灰着**（2026-10-05 作者："插件卡片整体灰色处理"）——
      // ⚠️ 而它**照旧在名单里**（那是"卸了也不消失"那一条 ）。
      const inactive = one.installed === false || (one.builtin && !one.enabled);
      item.className = inactive ? 'plugins-card plugins-inactive' : 'plugins-card';
      if (one.installed === false) { item.classList.add('plugins-gone'); }
      // 已安装卡片：头像、名称、状态、元信息和操作；统一使用主题变量。
      const head = document.createElement('div');
      head.className = 'plugins-head';
      // 2026-10-05 作者：「把头像，也就是图标去掉」⇒ 这一格删了。
      const names = document.createElement('div');
      names.className = 'plugins-names';
      const who = document.createElement('strong');
      who.className = 'plugins-name';
      who.tabIndex=0;who.setAttribute('role','button');
      who.onclick=()=>openDetail(one,'displayName' in one?'installed':'market');
      who.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();who.click();}};
      // 2026-10-05 作者给的排法：那一行写 core-infinity-sample（标题就是包名）。
      // 原先把 displayName 当标题、把包名塞进元信息里 ⇒ 同一件事写了两遍。
      who.textContent = 'displayName' in one && one.displayName ? String(one.displayName) : one.name;
      const meta = document.createElement('span');
      meta.className = 'plugins-meta';
      // 元信息照它那一行（"内置扩展 · 版本 · 作者"）—— 我们这几格今天有的是这些
      // 2026-10-05 作者：「参考图1这个，图2现在这个太简洁了」——图1那一行是**写实**的两行：
      //   「包名：…」／「版本：… 作者：…」。⇒ 照它写（别写成"infinity · 版本 …"那种设计腔）。
      coreSetUIText(meta, 'textContent', [[corePhrase("版本："), showVersion(one.version || ''), corePhrase("")], [corePhrase("　作者："), one.author || '未声明', corePhrase("")]]);
      names.append(who, meta);
      const update = updateFor(one.name);
      if (!overview&&update?.status === 'available') names.append(pluginUpdateNotice(one.name));
      const tag = document.createElement('span');
      tag.className = (overview ? one.enabled : one.installed!==false) ? 'plugins-tag on' : 'plugins-tag';
      tag.textContent=overview?(one.loaded?'运行中':one.enabled?'已启用 · 未运行':'已停用'):(one.installed===false?'未安装':'已安装');
      const mark=document.createElement('span');mark.className='plugin-package-mark';mark.append(icon('puzzle'));mark.setAttribute('aria-hidden','true');
      head.append(mark,names, tag);
      item.append(head);
      // **简介也要在这张卡上**（2026-10-05 作者："已安装那里…没有把简介显示出来"）——
      // ⚠️ 原先它**只在详情浮窗里有**（要点进去才看得见 ），而市场那一段的卡一直在显示
      // ⇒ 两段的卡**同一个形状**（一处一条 ）： 那句读过 `package.json` 的 `description` 。
      if (one.description) {
        const line = document.createElement('p');
        line.className = 'plugins-said';
        line.textContent = one.description;
        item.append(line);
      }
      const foot = document.createElement('div');
      foot.className = 'plugins-foot-line';
      item.append(foot);
      // **那个开关**（`POST /plugins/switch` —— 一条新写路径 ⇒ 服务端有同源校验 ）。
      const flip = document.createElement('button');
      flip.type = 'button';
      flip.className = 'btn ghost plugins-flip';
      // 2026-10-05 作者："关闭改成停用"（而"启用"那一半上一刀已经改了 ）
      coreSetUIText(flip, 'textContent', (one.enabled ? corePhrase("停用") : corePhrase("启用")));
      let flipBusy = false;
      const doFlip = async (): Promise<void> => {
        if (flipBusy) { return; }
        flipBusy = true;
        flip.disabled = true;
        try {
          await runMutation('/plugins/switch', { name: one.name, on: !one.enabled },
                            note, READ_FAILED);
        } finally {
          flipBusy = false;
          flip.disabled = false;
        }
      };
      // **停用要两步**（2026-10-05 作者："在停用和卸载这两个按钮按下去的时候要做一个
      // 二次确认"）—— 而**启用那半不两步**：它是**往回走**的那一下，拦它只是招人烦 。
      twoStep(flip, one.enabled ? '停用' : '启用',
        one.enabled ? '确认停用' : '确认启用', 'btn ghost plugins-flip', doFlip);
      // ⚠️⚠️ **这一句删了**（2026-10-05 作者看图 1：灰卡的按钮排版坏了）——
      // 它**单独**把这枚开关挂到卡上（卡是 `flex-col` ⇒ 那一枚变成**整行宽的一行** ），
      // 而后面 `right.append(flip, …)` 又把它挪进那一组 ⇒ **灰卡那一支不进那一组**
      // （见下面 `goneNow` 那一行 ）⇒ 于是它**留在卡上整整占了一行** （截图里那个大「启用」）。
      // ⇒ 落点：**这一枚只从那一组进**（一处一条 ）。
      // **删掉它**（2026-10-05 作者："参考它的排版" ⇒ 照参考那张卡底下右边那枚
      // 「删除扩展」）—— **而那是一个真动作**：服务端会把三件都做全
      // （先关掉 ⇒ 删那一格目录 ⇒ 忘掉那一格名字），而这一趟**真的动盘上的东西** 。
      // ⚠️ 它**照旧走同源校验**（那一条在 `web.py` 那一侧 —— 页只管发 ）。
      // ⚠️ **删完要两段都重画**（ 上面少了一个、而下面**多了一个"可以装"的** ）。
      const drop = document.createElement('button');
      drop.type = 'button';
      // 2026-10-05 作者：安装与卸载是**同一个位置**的按钮 ⇒ 两者样式统一
      // （同一个 `plugins-act` 底座 + 两种颜色：put 薄荷 / drop 柔红）。
      // **没装（内置被人卸过）⇒ 这一枚是「安装」**（作者："如果需要重新启用就要重新安装"）——
      // 而它走的就是**已有的那一条装路**：`{"from": 内置那一格}` ⇒ 收进 `extensions/` 。
      const goneNow = one.installed === false;
      drop.className = goneNow ? 'btn plugins-act put' : 'btn plugins-act drop';
      coreSetUIText(drop, 'textContent', (goneNow ? corePhrase("安装") : corePhrase("卸载")));
      if (goneNow) {
        twoStep(drop, '安装', '确认安装', 'btn plugins-act put',
          () => runMutation('/market/install', { from: one.where || '' },
                            note, READ_FAILED));
      } else {
      // **卸载也两步**（同上）—— 而它**真的删盘上的东西**，这一道更该拦 。
      uninstallButton(drop, one,
        () => runMutation('/plugins/remove', { name: one.name, confirm_shared: true }, note, READ_FAILED));
      }
      // **那一行动作**（照参考：左边一枚、右边一枚，而右那枚是"有代价"的那一种）
      // **那枚「详情」**（2026-10-05 作者："详情按钮，要做一个"）——
      // 照参考那张卡底下的排法：**左「详情」／右「卸载」** 。
      const look = document.createElement('button');
      look.type = 'button';
      look.className = 'btn ghost plugins-look';
      coreSetUIText(look, 'textContent', corePhrase("详情"));
      look.addEventListener('click', () => { openDetail(one, 'installed'); });
      // 2026-10-05 作者：详情留左边，关闭跟在卸载左边 —— 也就是**那一组都贴右边**。
      const right = document.createElement('div');
      right.className = 'plugins-acts';
      // 内置而**没装**的 ⇒ 那一枚开关**不画**（ 它没装、开不了 —— 画了会是一句空话 ）
      if(!overview){if(goneNow)drop.textContent='恢复安装';right.append(drop);}else{
        const configure=document.createElement('a');configure.className='btn ghost plugin-configure';configure.textContent='配置';configure.href=`#/infinity/${encodeURIComponent(one.name)}`;
        right.append(configure,flip);
      }
      const actions = document.createElement('div');
      actions.className = 'plugins-actions';
      actions.append(look, right);
      item.append(actions);
      // **契约那一句**（有字才画 —— 对得上时**不画**，那正是"没话说"）。
      if (one.contract) {
        const warn = document.createElement('p');
        warn.className = 'plugins-warn';
        warn.textContent = one.contract;
        item.append(warn);
      }
      list.append(item);
    }
    installedBox.replaceChildren(list);
    // **底下那一行数**（照参考那张底下那句「显示 1-5，共 5 个」）
    // 2026-10-05 作者："共 1 个已安装。 改成 共x个已安装，共x个已启用。"
    // ⚠️ **而那两格现在要分开算**（2026-10-05 内置那一批落了之后）：
    // 那一份名单里**同时有"装着的"与"内置而还没装的"**（后者灰着 ）⇒
    // "已安装"**不许把灰着的算进去**（那会把数字说成假的 ）。
    const here = shown.filter((one) => one.installed !== false).length;
    const on = shown.filter((one) => one.enabled).length;
    const goneCount = shown.length - here;
    const stateCount=overview?` · ${on} 个已启用`:(goneCount?` · ${goneCount} 个内置插件未安装`:'');
    count.textContent=`${here} 个已安装${stateCount}（本页 ${slice.length ? from+1 : 0}–${from+slice.length}）`;

    installedBox.append(count);
    installedBox.append(pager(pages, pageIn, (n) => { pageIn = n; draw(answer); }));
  };

  /* 插件详情面板。体积、文件数、作者及来源由服务端提供，卸载操作由服务端执行。 */
  const openDetail = (one: InstalledPlugin | MarketPackage, from: 'installed' | 'market'): void => {
    const mid = 'name' in one ? one.name : '';
    const row = (k: string, v: string): HTMLElement => {
      const line = document.createElement('div');
      line.className = 'plugins-kv';
      const key = document.createElement('span');
      key.className = 'plugins-kv-key';
      key.textContent = k;
      const val = document.createElement('span');
      val.className = 'plugins-kv-val';
      val.textContent = v;
      line.append(key, val);
      return line;
    };
    const head = document.createElement('h3');
    head.className = 'plugins-detail-head';
    // 2026-10-05 作者："样例插件 改成 core-infinity-sample" ⇒ 浮窗标题也用**包名**
    // （与卡上那一条同一个写法 —— 一处一条 ）。
    head.textContent = one.name;
    const shut = document.createElement('button');
    shut.type = 'button';
    shut.className = 'btn ghost plugins-detail-shut';
    coreSetUIText(shut, 'textContent', corePhrase("× 关闭"));
    shut.addEventListener('click', () => { detail.close(); });
    const bar = document.createElement('div');
    bar.className = 'plugins-detail-bar';
    bar.append(head, shut);

    const say = document.createElement('p');
    say.className = 'plugins-detail-said';
    say.textContent = one.description || '暂无简介';

    const info = document.createElement('div');
    info.className = 'plugins-kv-box';
    if (!overview&&from === 'installed') {const update = updateFor(mid); if (update) info.append(row('更新状态', updateText(update)));}
    if (from === 'installed') {
      const box = one as InstalledPlugin;
      info.append(
        row('插件名', box.name),
        row('作者', box.author || '未声明'),
        row('版本', showVersion(box.version || '')),
        row(box.installed === false ? '内置源目录' : '安装位置', box.where || ''),
        row('入口', box.entry || 'index.py'),
        row('许可证', box.license || '未声明'),
        row('体积', `${box.bytes ?? 0} 字节（${box.files ?? 0} 个文件）`),
        row('关键字', (box.keywords || []).join('　') || '未声明'),
        // 内置插件（含未安装的源）来源写“核心”；第三方继续按 origin 如实显示。
        row('来源', box.builtin === true
          ? '核心'
          : (box.origin?.url
            ? `${box.origin.source || '网址'} · ${box.origin.url}`
            : '-')),
        // 2026-10-05 作者："浮窗内的 状态 改成 启用或是停用"
        row('状态', box.enabled ? '启用' : '停用'),
        row('契约', box.api === null ? '未声明接口版本' : `infinity · api ${box.api}`),
      );
      if (box.contract) {
        const warn = document.createElement('p');
        warn.className = 'plugins-warn';
        warn.textContent = box.contract;
        info.append(warn);
      }
    } else {
      const box = one as MarketPackage;
      info.append(
        row('插件名', box.name),
        row('作者', box.author || '未声明'),
        row('版本', showVersion(box.version || '')),
        row('仓库', box.full || ''),
        row('更新于', box.date || '未声明'),
        row('关键字', (box.keywords || []).join('　') || '未声明'),
        row('来源', box.source || ''),
        row('归档链接', box.url || ''),
      );
    }

    const acts = document.createElement('div');
    acts.className = 'plugins-actions';
    const close2 = document.createElement('button');
    close2.type = 'button';
    close2.className = 'btn ghost';
    coreSetUIText(close2, 'textContent', corePhrase("关闭"));
    close2.addEventListener('click', () => { detail.close(); });
    const last = document.createElement('button');
    last.type = 'button';
    // 同一个位置的那一枚：两个方向**同一个底座**（只有颜色按方向分 ）
    const removedBuiltin = from === 'installed' && (one as InstalledPlugin).installed === false;
    const removing = from === 'installed' && !removedBuiltin;
    const action = removing ? '卸载' : '安装';
    last.className = removing ? 'btn plugins-act drop' : 'btn plugins-act put';
    last.textContent = action;
    const fail = document.createElement('p');
    fail.className = 'plugins-warn';
    fail.hidden = true;
    const goLast = async (): Promise<void> => {
      fail.hidden = true;
      fail.textContent = '';
      const path = removing ? '/plugins/remove' : '/market/install';
      const body = removing ? { name: mid, confirm_shared: true } : removedBuiltin
        ? { from: (one as InstalledPlugin).where || '' } : { url: (one as MarketPackage).url };
      const detailView = viewEpoch;
      const done = await runMutation(path, body, fail,
        removing ? READ_FAILED : MARKET_FAILED);
      if (!root.hidden && detailView === viewEpoch) {
        if (done.fresh.installed) {
          if (fail.textContent) {
            (from === 'installed' ? note : marketNote).textContent = fail.textContent;
          }
          detail.close();
        } else { fail.hidden = fail.textContent === ''; }
      }
    };
    // 卸载／安装**两半都两步**（ 它们都动盘上的东西 ）；「关闭」照旧一步 。
    if(from==='market'&&haveInstalled.has(mid)){last.disabled=true;last.textContent='已安装';}
    if (removing) uninstallButton(last, one as InstalledPlugin, goLast);
    else twoStep(last, action, '确认' + action, 'btn plugins-act put', goLast);
    // **源代码仓库那一枚**（有真链接才画 ）—— 挂在那一行的**左边**
    const repo = repoOf(one, from);
    if (repo) {
      const go = document.createElement('a');
      go.className = 'btn ghost plugins-repo';
      go.href = repo;
      go.target = '_blank';
      go.rel = 'noreferrer';
      coreSetUIText(go, 'textContent', corePhrase("源代码仓库"));
      acts.append(go);
    }
    const rightP = document.createElement('div');
    rightP.className = 'plugins-acts';
    if(!overview)rightP.append(last);
    acts.append(rightP);

    // 纸是那张 `detailCard`（浮窗里那一张 ），而 `detail` 是压暗的底
    detailCard.replaceChildren(bar, say, info, fail, acts);
    detail.showModal();
    detailCard.scrollTop = 0;
  };

  /** 读失败时：手上已有旧快照就**留着**（还能重试），但把“可能不是最新”说明白；
   *  一次都没读到过才摆“读不到”。 */
  const installedReadFailed = (): void => {
    if (lastInstalled !== null) {
      note.textContent = `${READ_FAILED} ${REFRESH_FAILED}`;
      return;
    }
    draw(null);
  };
  const marketReadFailed = (): void => {
    if (lastMarket !== null) {
      marketNote.textContent = `${MARKET_FAILED} ${REFRESH_FAILED}`;
      return;
    }
    drawMarket(null);
  };

  /** **只问市场那一段**（2026-10-05：「隐藏已安装」那个开关一动就只重画它 ）。 */
  const refreshMarket = async (view = viewEpoch, mutation = mutationEpoch,
    response?: Promise<Response>): Promise<boolean> => {
    if (!stillCurrent(view, mutation)) { return false; }
    const ask = ++marketRead;
    try {
      const got = await (response ?? fetch('/market', { cache: 'no-store' }));
      if (got.ok) {
        const answer = await got.json() as MarketAnswer;
        if (stillCurrent(view, mutation) && ask === marketRead) { drawMarket(answer); }
        return true;
      }
    } catch { /* 与下面同一句：留着旧快照，说清没刷新 */ }
    if (stillCurrent(view, mutation) && ask === marketRead) { marketReadFailed(); }
    return false;
  };

  /** **两段各去问一次**（2026-10-05 抽出来的）—— 装完要重画时也走它 。
   *  `installed` ／ `market` 报的是“这一条读拿到了没有”，调用方据此把刷新失败说出来。
   */
  const refresh = async (): Promise<{ installed: boolean; market: boolean }> => {
    const view = viewEpoch;
    const mutation = mutationEpoch;
    const ask = ++installedRead;
    let installed = false;
    const marketResponse = overview ? undefined : fetch('/market', { cache: 'no-store' });
    // Attach a handler immediately; it may fail before /plugins finishes.
    void marketResponse?.catch(() => {});
    // ⚠️ **这一格答 404 ⇒ "还没接上"**（不是"零个插件" —— 页上分得开 ）。
    try {
      const got = await fetch('/plugins', { cache: 'no-store' });
      if (got.ok) {
        const answer = await got.json() as PluginsAnswer;
        if (stillCurrent(view, mutation) && ask === installedRead) { draw(answer); }
        installed = true;
      } else if (stillCurrent(view, mutation) && ask === installedRead) {
        installedReadFailed();
      }
    } catch {
      if (stillCurrent(view, mutation) && ask === installedRead) { installedReadFailed(); }
    }
    // **市场那一段**（走上面那一个 —— 同一个落点，不写第二遍 ）
    const market = overview ? true : await refreshMarket(view, mutation, marketResponse);
    return { installed, market };
  };

  const drawMarket = (answer: MarketAnswer | null): void => {
    // ⚠️ 同上：**先把上一次画的那一份清掉**（理由与上面那一句一字不差 ）。
    marketBox.replaceChildren();
    if (answer === null) { marketNote.textContent = MARKET_FAILED; return; }
    // ⚠️ **"没问到"与"问到了、没人发过"是两件事**（`wired` 就是把它们分开的那一位 ）。
    if (!answer.wired) { marketNote.textContent = answer.said || MARKET_NOT_WIRED; return; }
    // **那一句不再显示**（2026-10-05 作者："删除「GitHub 上打了…有 1 个。」"）——
    // 而"几个"那一件事由**底下那一行数**说 （一处一条 ）；
    // ⚠️ 而"没答上来"／"还没接上"那两种**照旧要说**（那是**说清**，不是那句计数 ）。
    coreSetUIText(marketNote, 'textContent', (answer.stale ? [corePhrase("当前显示上次缓存的插件列表。"), answer.error || '正在后台刷新，请稍后点击刷新查看结果。', corePhrase("")] : corePhrase("")));
    lastMarket = answer;
    const community=answer.found.filter(one=>!lastInstalled?.plugins.some(local=>local.builtin&&local.name===one.name));
    categoryCounts(community);
    // **「隐藏已安装」那一半**（2026-10-05 作者："做"）——
    // ⚠️ **比的是"已安装那一段现在有的名字"**（由 `draw()` 那一趟记下来 ）；
    // 而**藏起来的是"市场里那一条"**（不是"已安装那一条" —— 那一段照旧要显示 ）。
    // ⚠️ **两个"筛"要分开算**（2026-10-05 真驱动浏览器抓到的**谎**）：
    // · 「隐藏已安装」那一个（勾着才有 ）；· 搜索框那一个（后来才有的 ）。
    // "一个不剩"的**理由**必须分开说 —— 我第一版把两者混在一起，
    // 于是在搜索框里打一个词筛空时，它说「 都装过了」 —— **那是一句谎** 。
    const afterHide = community.filter(
      (one) => !(hideTick.checked && haveInstalled.has(one.name)));
    const rows = afterHide.filter((one) => hits(
      [one.name, one.description, one.author, one.full, one.source], marketFind.value)).filter(one=>category==='all'||pluginCategory(one)===category);
    // 藏完一个不剩 ⇒ **说一句**（ 而不是画一个空列表 —— "空"与"藏起来了"是两件事 ）
    if (rows.length === 0) {
      // 一个不剩 ⇒ **底下那一行数**说清为什么（ 而不是在提示那一行再写一句 ）
      // ⚠️ **那一句删了**（2026-10-05 作者："共0个插件（ 都装过了 —— 上面那个开关可以放开）。
      // 这句话删了。"）—— 而**那一行数照旧在**（"一个不剩"与"本来就空"是两件事，
      // 而"藏起来了"这件事页上**本来就看得出**：上面那个开关正勾着 ）。
      coreSetUIText(marketCount, 'textContent', (marketFind.value.trim() || category!=='all' ? corePhrase("没有匹配的插件。") : ((answer.stale ? corePhrase("缓存中暂无插件，正在刷新。") : corePhrase("暂无插件。")))));
      marketBox.append(marketCount);
      return;
    }
    const list = document.createElement('ul');
    list.className = 'plugins-row plugins-market-row';
    // 同上：只画当前那一页
    const everyOut = Math.max(1, Number(marketPer.value) || 6);
    const pagesOut = Math.max(1, Math.ceil(rows.length / everyOut));
    if (pageOut > pagesOut) { pageOut = pagesOut; }
    const fromOut = (pageOut - 1) * everyOut;
    const sliceOut = rows.slice(fromOut, fromOut + everyOut);
    for (const one of sliceOut) {
      const item = document.createElement('li');
      item.className = 'plugins-card';
      // 市场卡片沿用已安装卡片的主题、间距和元信息布局。
      const head = document.createElement('div');
      head.className = 'plugins-head';
      // 同上（两段的卡一个形状）：头像那一格删了。
      const names = document.createElement('div');
      names.className = 'plugins-names';
      const who = document.createElement('strong');
      who.className = 'plugins-name';
      who.tabIndex=0;who.setAttribute('role','button');
      who.onclick=()=>openDetail(one,'displayName' in one?'installed':'market');
      who.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();who.click();}};
      who.textContent = 'displayName' in one && one.displayName ? String(one.displayName) : one.name;
      const meta = document.createElement('span');
      meta.className = 'plugins-meta';
      // 元信息那一行 —— 而**来源要说出来**（"从哪儿来的"是本仓那条口径 ）
      // 同上：照图1写实（那一张是「版本：1.0.0　作者：alont1」）
      coreSetUIText(meta, 'textContent', [[corePhrase("版本："), showVersion(one.version || ''), corePhrase("")], [corePhrase("　作者："), one.author || '未声明', corePhrase("")]]);
      names.append(who, meta);
      // 那枚标签（照它卡上那枚"已加载"）—— 写**来源**： 那就是"它从哪儿来"
      const tag = document.createElement('span');
      tag.className = 'plugins-tag';
      tag.textContent = pluginCategoryLabel(one);
      // ⚠️⚠️ **这里是那条真凶**（2026-10-05 真驱动浏览器查出来的）：头像删掉之后，
      // 这一句**还在拿 `face`** ⇒ `ReferenceError` ⇒ 而它被 `refreshMarket` 那个
      // `catch` **吞掉**、画成"市场那一格没答上来" —— **看着像网络问题，其实是代码** 。
      // 教训：那一格 `catch` 是"拉不到就说拉不到"，而它会**连自己的 bug 一起收进来** 。
      const mark=document.createElement('span');mark.className='plugin-package-mark';mark.append(icon('puzzle'));mark.setAttribute('aria-hidden','true');
      head.append(mark,names, tag);
      item.append(head);
      if (one.description) {
        const line = document.createElement('p');
        line.className = 'plugins-said';
        line.textContent = one.description;
        item.append(line);
      }
      // **标签行**（照它那行 `MIT ／ 日期 ／ 下载量` 的小胶囊）—— 我们有的只有日期。
      if (one.date) {
        const chips = document.createElement('div');
        chips.className = 'plugins-chips';
        const when = document.createElement('span');
        when.className = 'plugins-chip';
        when.textContent = one.date;
        chips.append(when);
        item.append(chips);
      }
      // ⚠️⚠️ **那条线要挪到这儿**（2026-10-05 作者："上下显示的内容差的很多，我觉得排版有些
      // 头重脚轻"）—— 它原先在**日期之前**，于是日期被那条线**切下来孤零零挂在下面**
      // （上面两块、下面一块 ⇒ 看着就是头重脚轻 ）。
      // 这里跟参考那一页的**本意**对齐：那条线是**"信息那几行"与"动作那一行"之间的**一条
      // ⇒ 所以它该在**日期后面**（日期属于信息那几行 ），而不是插在信息中间 。
      const foot2 = document.createElement('div');
      foot2.className = 'plugins-foot-line';
      item.append(foot2);
      // **那个"装"钮**（2026-10-05 作者："画"）—— 走 `POST /market/install` 。
      // ⚠️ 它是**一条新写路径** ⇒ 服务端那一侧有**同源校验** （页只管发 ）。
      // ⚠️ **按来源选那一路**（`source` 那一格在，就是为了这一下 ）：
      // GitHub ⇒ **归档链接**（`{"url": …}` ）；而"包名"那一路**属于 npm** ⇒ 已删 。
      // ⚠️ **装完要把两段都重画**：这一份从"市场"里挪到"已安装"那一段去了
      // —— 只重画市场那一段的话，页上会**同时**在两处显示它 （而那是假的 ）。
      const put = document.createElement('button');
      put.type = 'button';
      put.className = 'btn plugins-act put';
      coreSetUIText(put, 'textContent', corePhrase("安装"));
      if (haveInstalled.has(one.name)) {put.disabled = true; put.textContent = '已安装';}
      let putBusy = false;
      put.addEventListener('click', async () => {
        if (putBusy) { return; }
        putBusy = true;
        put.disabled = true; // 一下就好（免得连点装两遍）
        try {
          const box = one.url ? { url: one.url } : { name: one.name };
          await runMutation('/market/install', box, marketNote, MARKET_FAILED);
        } finally {
          putBusy = false;
          put.disabled = false;
        }
      });
      const look2 = document.createElement('button');
      look2.type = 'button';
      look2.className = 'btn ghost plugins-look';
      coreSetUIText(look2, 'textContent', corePhrase("详情"));
      look2.addEventListener('click', () => { openDetail(one, 'market'); });
      // 同上：详情贴左，安装贴右（两段同一个排法）。
      const right2 = document.createElement('div');
      right2.className = 'plugins-acts';
      right2.append(put);
      const actions2 = document.createElement('div');
      actions2.className = 'plugins-actions';
      const date=item.querySelector('.plugins-chips');
      if(date)actions2.append(date);
      const requirement=pluginRequirement(one);
      if(requirement){const label=document.createElement('span');label.className='plugin-requirement';label.textContent=requirement;actions2.append(label);}
      actions2.append(look2,right2);
      item.append(actions2);
      list.append(item);
    }
    marketBox.replaceChildren(list);
    coreSetUIText(marketCount, 'textContent', [[[corePhrase("共"), rows.length, corePhrase("个插件")], [corePhrase("（本页显示 "), sliceOut.length ? fromOut + 1 : 0, corePhrase("-"), fromOut + sliceOut.length, corePhrase("）")]], ((answer.stale ? corePhrase("（缓存结果，正在刷新）") : corePhrase("。")))]);
    marketBox.append(marketCount);
    marketBox.append(pager(pagesOut, pageOut, (n) => { pageOut = n; drawMarket(answer); }));
  };

  const updatesChanged = () => { if(root.hidden)return;updatePanel.render();updateTabLabel();if(lastInstalled)draw(lastInstalled); };
  return {
    root,
    draw,
    drawMarket,
    show(): void {
      // ⚠️ **取消刚藏**（上一页走的时候把它的那一块 `hidden` 了 ；照 `persona-page.ts` 那条）。
      root.hidden = false;
      window.addEventListener('plugin-updates-changed', updatesChanged);
      window.addEventListener('plugin-updates-open',openRequestedUpdates);openRequestedUpdates();
      // 回来之后重新开一轮 —— 走之前发出去的回调不许写这一轮。
      viewEpoch += 1;
      void refresh();
    },
    stop(): void {
      // **走的时候把它藏起来**（照那两页；不藏的话换页之后两页的正文会**叠在一起** ）。
      root.hidden = true;
      window.removeEventListener('plugin-updates-changed', updatesChanged);
      window.removeEventListener('plugin-updates-open',openRequestedUpdates);detail.close();
      // 导航离开：旧 GET ／ 旧 POST 回来得再晚，也不许落到已经藏起来的面板上。
      viewEpoch += 1;
      installedRead += 1;
      marketRead += 1;
    },
  };
}
