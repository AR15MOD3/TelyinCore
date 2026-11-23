# `src/web/` —— 那一页，与它的构建

> 这一刀（第二段 · Task 3）：**打开一个地址 ⇒ 一页聊天 ⇒ 你打一句、那边答一句**。
> 服务那一侧在 `telyincore/web.py`（只听回环 ＋ SSE），这一页是它的脸。

## 怎么重建那一页（从仓根跑 —— 两条命令 ＋ 一个必须留在树里的文件）

```
pnpm install          # ① 装进本仓自己的 node_modules/（它不进 git）
pnpm run build:web    # ② 一条命令 ⇒ dist/web/
```

- **从哪儿跑**：**仓根** —— 就是有 `.venv/` 与 `telyincore/` 的那一层（`pnpm install`
  认的是这里那份 `package.json`；`scripts/build-web.mjs` 也是从仓根算那几个路径的）。
- **工具链从哪来**：**PATH 上系统装的** `node` ／ `pnpm`（本机实测：`node` v24.21.0 ／
  `pnpm` 12.3.4，`Get-Command node` ／ `Get-Command pnpm` 指的就是它们）。
   **脚本里零绝对路径** —— 一个 `.dsh\dsh-runtimes\…` 都不许出现（DSH 自己那份运行时
  **用不着**；写进去就等于把"借谁的运行时"那件事重新引回来）。
- **它花什么**：一次 `pnpm install` 的**网络往来**（**三个依赖**，都在 `devDependencies` 里：
  `esbuild` ＋ `tailwindcss` ＋ `@tailwindcss/cli`；各带一份按系统架构落的二进制／原生包）
  ＋ 仓里多一个 `node_modules/`（**不进 git**）；之后每次构建都是本地的，几毫秒。
- **换一台机器也能建**：装了 `node` ＋ `pnpm` 的机器都行（这一页不依赖那台机器的任何路径）。
- ⚠️ **Python 那一套不需要它**：`node_modules/` 不在的时候，`pytest` 照旧全绿 ——
  构建那一条测试**跳过**，而"怎么发那一页"那几条用一份**假产物**量。
   **跳过的判定是两件事各量一次**：PATH 上有没有 `node`、以及 `node_modules/esbuild`
  在不在 —— **少一样就跳过**（两种"少"是两件不同的事：前者那条命令起不来，后者它
  起得来而交回非零）。 两臂都实测过（摘掉 PATH 上的 `node` ／ 从一个**真 clone** 里跑）
  —— 结果一样：那一条跳过，别的全绿。
  ⚠️ **点名的边界**：那个判据看的是 `node_modules/esbuild` **那个目录在不在**，**不是**它
  **能不能被 import** ⇒ 一份"装了一半"的依赖（目录在、esbuild 进不来）**照旧会跑、
  而那条构建红** —— 它是**登记**，不是"没问题"（判据自己的 docstring 上有同一句）。

```bash
pnpm install && pnpm run build:web    # 想一行写完也行（同两条）
```

## 产物与那两条命令的分工

| 文件 | 责任 |
|---|---|
| `src/web/client/index.html` | 那一页的**落点声明** —— 见下面 ① |
| `src/web/client/shell.ts` | **壳**（第二刀 · T1）—— 左栏 ／ 顶栏（两层）／ 正文那一列 ／ 底部那一行 |
| `src/web/client/pages.ts` | **那张页表 ＋ 那个路由器**（第二刀 · T1）—— 见下面 ② |
| `src/web/client/live.ts` | **页一：聊天**（第二刀 · T1）—— 正文那一列 ＋ 底部那一行 |
| `src/web/client/engine-page.ts` | **页二：引擎**（第二刀 · T1）—— 浅灰卡片 ＋ 那句**边界**（柔红） |
| `src/web/client/main.ts` | 入口：建壳 ／ 摆组 ／ 亮当前那一页 ＋ 三条路 |
| `src/web/client/state.ts` | 顶上那条计数与引擎那三行的**算法**（ 一个数都不写死） |
| `src/web/client/chat.ts` | 聊天区（一行一行往那儿放；一律 `textContent`） 见下面 ③ |
| `src/web/client/engine.ts` | 引擎那一栏 ＋ **写入那条路的边界** |
| `src/web/client/style.css` | **词汇表 ＋ 形状**（Tailwind v4：`@theme` ＋ 一条 `@import`） 见下面 ④ |
| `scripts/build-web.mjs` | **两步**：esbuild ⇒ `app.js`；Tailwind 的 CLI ⇒ `style.css` |

** 那几行省下来的话，写在这儿**（第二刀之后新加的四份 ＋ 两处变了的）：

```
① `index.html`：**只剩四个空锚**（`#rail` ／ `#content` ／ `#topbar` ／ `#bottom`）
   ＋ `<script>` —— **一个页标都没有**（页表是那唯一的登记处，见 ②）。
② `pages.ts`： **加一页 = 加一个文件 ＋ 一行**（就是这里那一行）——
   组标 ／ 页标 ／ 顺序 ／ 图标**全从它读**①。
③ `chat.ts`： 它还管**那一列真的会滚** —— `scrollTop` 那一句的下家是
   `.live-stream`（那条 `overflow-y: auto` 在 ④ 那一份里）。
④ `style.css`： 它同时是**那一条 flex 链**的所在（`.console` ／ `#content` ／
   `.live` ／ `.live-stream` ／ `#bottom`）—— 那五块少一处，轮子 ／ 方向键 ／
   滚动条就一起没有（`body { overflow: hidden }` 会把视口那一层也关掉）。
⑤ `main.ts` 那三条路：`GET /events`（推）／ `POST /say`（写入）／ `GET /state`（那几个数）。
⑥ `shell.ts` 里**一个页名都没有**：它只认页表给的位次（`place(i, …)`）。
```


 **那两步都在这一份脚本里**（`--outdir=` 那个参数两条都认）—— 而 **v4 不需要 config 文件**
（主题写在 `style.css` 的 `@theme` 里）：那几份 `tailwind.config.*` 树里一个都没有。

产物 `dist/web/`（`index.html` ／ `app.js` ／ `style.css`）**进 git 的不进**：`.gitignore`
里收着 `dist/` 与 `node_modules/`。

- **`pnpm-lock.yaml` 进 git**（把那三个依赖的版本钉死，别让下一次构建换一个版本）；
  它是 **pnpm 自己生成的** —— 里面那些 `integrity:` 会长过 100 字符，
  而 ≤100 那条是**源文件**的规矩（手折行会把那份文件改坏）。
- **`pnpm-workspace.yaml` 也进 git**，而且**必须留着**：pnpm 11 起把"审过哪些依赖的
  构建脚本"搬到了那里（`allowBuilds`）—— 少了它，`pnpm install` 在 pnpm 12 上**交回非零**
  （`ERR_PNPM_IGNORED_BUILDS`），那条构建命令当场变成一句会失败的话。
   本刀（Task 2）那一格从一行变两行：`esbuild: true`（它要落那份二进制）＋
  `'@parcel/watcher': false`（Tailwind CLI 的一个传递依赖，只为 `--watch` 存在 ——
  我们那条构建是一次性的 ⇒ **审过，不让它跑**；写成 `false` 之后构建照旧跑得通，实测）。

## 怎么打开它

```
.\.venv\Scripts\python.exe -c "import asyncio,sys; from telyincore.config import load_config;
    from telyincore.console import Console; from telyincore.web import serve;
    sys.exit(asyncio.run(serve(Console(), config=load_config())))"
```

起来之后会把地址打出来（缺省 `http://127.0.0.1:7799/`）—— 在浏览器里打开它。

- ⚠️ **`load_config()` 要递**：那一份配置既是装配要的、也是**引擎那一栏（端点 ／ 模型）**要的
  —— 必须是**同一份**（不递的话，`assemble()` 自己会读一份，而那一栏会是 `—`）。
- ⚠️ **从仓根跑**：`serve()` 的 `root` 缺省是 `data/`（**相对当下工作目录**），
  在别处跑，会话 ／ 事件库 ／ 闹钟表会一起落到别处。
- ⚠️ **按 Ctrl-C 之后可能等一会儿**（数十秒量级）：叫停只停那条泵，而**正在跑的那一轮**
  要自己认输才回来（"取消那一轮"在本仓今天没有接上 —— 见 `App.stop()` ③）。对着一个
  连不上的端点，那一步由引擎那一侧的额度管。

## 写入那条路的边界（`POST /say`）

服务**只听回环**（`127.0.0.1`）—— 而它**不校验 `Origin`**、**没有令牌**：

> 「只听回环」是「**这一台机器**」，**不是**「只有这一页」，更**不是**「已鉴权」。
> 本机任何一个进程、本地浏览器里任何来源的一页，都塞得进一行。

⇒ 今天适合的用法只有一种：**一个人在本机、开着自己那一页**。要收紧的话，收紧点只有一处
（`/events` 与 `/say` 都按 `Origin` 判一遍）—— **这一刀刻意不做**（设计稿 §五：鉴权 ）。
 那句话也**显示在那一页上**（引擎那一栏），免得"打开这个地址"被读成"只有我能打开"。

## 经历页：来源与展开（2026-10-18）

- 页面入口为 `#/experience`；实现位于 `client/experience-page.ts`。
  进入时先同步名字和插件 `channels` 词表，再读取 `/experience`；筛选在已加载数据上完成。
- 数据除文件小结外还有 `days[].beats[]`，每条含 `at`、`role`、`text` 和可选 `scope`。
  文件 `activity=reflection` 表示整理，显示“记下”，不冒充对外发言。
- 来话与回复都显示可确认的通道。终端由核心提供；外部平台简称与路由格式由插件清单声明。
  QQ 为 `[Q·群]` / `[Q·私]`，点击后显示 `[Q·群·群号]` / `[Q·私·对方账号]`。
  旧记录缺来源时明确说明，不按上一句、昵称或相近时间猜测。
- 每日默认四条；“再往下看”展开后限高为 `min(60vh, 560px)`，内部滚动。
  上下“收起”、经历页内空白处及 Esc 均可收拢；正文、链接、搜索控件和文字选择不会误触收起。
- 多行正文可就地展开全文；会话号按钮是独立动作，重绘保留列表滚动位置。
  按钮支持键盘焦点、`aria-expanded`，日分组按钮还有 `aria-controls`。
- 前端构建后刷新生效；来源投影、入口及通道解析属于 Python 后端，改动后需重启 Core。
  本轮文档同步不会代为重启服务。

详细数据与交互契约见[经历页规格 §四](../../docs/handoff/世界三页-规格.md)；
插件声明见[Infinity 开发规范第 6 节](../../docs/Infinity插件开发规范-v1.17.md)。
开发规范的 HTML 由 `scripts/render-plugin-spec.mjs` 从同一份 Markdown 生成，
构建产物为 `dist/web/infinity-plugin-spec.html`，不应独立手改生成页正文。

## 那一页上的数从哪来（ 不是装饰）

`GET /state` 交回两份东西：顶层是**服务自己的**（`clients` ／ `connected` ／ 序号），
`machine` 那一段是**那台机器的读数**（`telyincore/web.py::state_of`）：

| 页面上 | 从哪个对象读 |
|---|---|
| `tokens` | `session.est_tokens()`（与 `tokens.estimate_messages_tokens` 同一口径） |
| `messages` | `len(session.records())` |
| `batches` | `console.batches`（这个出口真的递出去几批） |
| `paused` | `console.done` 或 `console.signal` 立起（**输入到头／收工**——见 `state_of` 那段准话） |
| `endpoint` ／ `model` | `config.base_url` ／ `config.model` |
| `connected` | 服务那边 `clients > 0`（**页面连着那条通道**，不是"引擎通了没有"） |

 **那几个数各自活多久 —— 不一样，而这一点从名字上看不出来**（本波写清的那一句）：
`tokens` ／ `messages` 是**会话的**（读的是那份会话盘：`data/session-main.jsonl`）
⇒ **跨轮次、跨一次重启**都是累计的（同一个 `root` 里，会话接着上一次）；而 `batches`
是**这一个进程的**（`Console.batches` 是那个对象上的一个计数器，从 0 起）⇒ **进程一重开就回 0**。
 于是"只有 3 批，却有 4000 token"不是一个矛盾，而是两个不同寿命的数并排放在一起
（一个是这本账、一个是这一趟）。 而"不知道"**不画成零**：没有会话 ⇒ `tokens` ／ `messages`
交回 `null`（页上画「—」），而 `batches` 那时是真的 `0`（见 `state_of` 那段）。

 没有那个对象的时候它交回 `null`，而那一页画「—」—— **"不知道"不画成"零"**。


## 前端页面异步加载约定

Core 页面、内置插件原生页及通用配置宿主统一遵守：

- 同步挂载标题、布局及配置框架；数据未返回不能冒充空列表或默认配置。
- 独立区块独立请求、独立显示；不以慢状态接口阻塞其他区块。
- JSON/text API 使用 `src/web/client/status-json.ts` 的 `pageFetch` 或 `getStatusJson`。
  仅合并同 URL、同请求头及凭据模式的在途 GET，不缓存已完成响应。
  超时覆盖响应体读取；HTTP 状态和错误正文保留。
- 写操作不自动重试；发送前及结束后作废在途快照，避免旧响应覆盖写入后的状态。
- 首次配置读回前禁用编辑和相关操作；读回失败不能保存占位值。
- 后台状态刷新不得重填未保存表单；保留已有内容，并明确标记读取失败、提供重试。
  共用 `pageLoad` 仅首次读取显示加载行，成功后的后台刷新不重新插入占位；失败提示保留到重试成功。
- 操作结果默认显示简短摘要；版本号、完整路径和长错误放入可展开详情，保留完整信息，
  详情内换行并限制高度。关闭键等固定尺寸控件须显式清除通用 padding，避免字符挤出。
- 离页或后续请求取代旧请求时，用页面代次拒绝晚到响应；不在离页后重绘。
- 流式事件、二进制附件和媒体保持专用通道，不交给 JSON/text 读取层。
- 通用配置页只管理自己的字段及保存契约；不修改第三方 iframe 内部实现或放宽 sandbox。

对应回归：`tests/test_unified_page_loading.mjs`、`tests/test_infinity_page_first_paint.mjs`、
`src/web/test/page-load.dom.mjs`、`src/web/test/memory-page.dom.mjs`。
