<!-- README.md -->
# TelyinCore

特琳的 agent 框架。

## Linux 本机隔离开发

运行项目根目录或 core 下的 `./start.sh`，默认打开 `http://127.0.0.1:7809/`。
本入口使用中立开发 Bot，不接入现有 Bot 数据、真实模型或 QQ。
运行环境为 `.venv-linux/`，开发数据为 `.linux-dev/`；Windows 仍使用 `start.bat`。

Linux 的同源角色验收：`./start.sh --acceptance`，默认页面 `http://127.0.0.1:7810/`。
从本地 Telyin 复制明确允许的人设、自述、习惯、知识和做法，验收数据在 `.linux-acceptance/`，
不导入旧画像、聊天、账号绑定或 QQ 配置。已有验收资料不会被重新覆盖。
`./start.sh --acceptance --check-documents` 在临时 Bot 上运行文档读写检查。
这是独立验收入口，Linux 正式进程生命周期仍需单独适配。

详见 [Linux 开发启动](docs/Linux开发启动.md)。

设计稿在 `docs/design/` —— **先读 `00-总览.md`**。

## 布局 —— **这份仓库是 `core\`，不是最外层**

```
D:\TelyinCore\               ← 部署根（装多个 bot；**不在 git 里**）
    core\                    ← **本仓库**（代码）
    bots\
        Telyin\              ← 一个 bot 的东西
            persona\            她读的（人格卡、世界认知、语气示例、边界、通道形状）
            memory\             事件库（她的全部经历 —— 不滚，一直长）
            self\               自述 与 关于她自己
```

**⚠️ 她的人格与记忆不在这份仓库里，而在 `bots\Telyin\`。**
**那是刻意的** —— 那样 `git clean` 碰不到她的经历，历史里也不会混进它。
⇒ **所以下面所有命令都在 `D:\TelyinCore\core` 下执行，不是 `D:\TelyinCore`。**

**⇒ 要再加一个 bot：在 `bots\` 下建一个同级目录 ＋ 一个人格插件 ＋ 一份装配。这份仓库一行不用改。**

## Bot 命名与运行归属

- 面向新部署的中性初始名称是 **Mybot**，不是固定的 Telyin。
- 使用 `start.ps1` / `startup.py` 选择或新建 Bot；直接运行 `launch.py` 必须显式传 `--bot`，且目标档案已存在。普通 Web CLI 经只观测父启动器；终端/实验模式不等于已完整接管。
- 管理页的显示名可以修改，不改变文件夹名、历史或数据归属。
- **使用后不要直接改 Bot 文件夹名，也不要复制旧角色作为新 Bot。** 新角色请走“新建 Bot”。
- 启动前检查旧归属、实例文件和目录；缺失、大小写变更、绑定不匹配时拒绝启动，不自动建立空历史。
- 首次通过新保护启动时，为既有有效目录原地登记 `.runtime-binding.json`，并在 `core/data/bot-runtime-bindings.json` 保留对应关系；不搬动历史、不更改 baseline。中断或损坏会拒绝继续，而非自动修复。
- 旧名字有运行数据/实例记录/归属登记时不能重新新建，也暂不支持从管理页删除该 Bot。请保留全部登记和备份，不通过删登记绕过保护。
- **这是保护措施，不是迁移或稳定 ID 功能。** 它无法追溯安装保护之前已经发生的旧名复用。运行中手工移动目录也不受文件系统锁禁止；应先关闭相关实例并恢复原目录，不能据此宣称支持热改名。
- 异常时先恢复原文件夹名，核对 `bot-runtime-owner.json`、绑定登记、`instances` 和 `bot-runtimes`；不要只改 baseline。完整内部改名仍待实现。
- 历史分发包没有自动更新；重新分发前需另做清洁打包与解压验证，不携带这些运行登记、私有档案、聊天或密钥。

### 迁移门禁与旧名退休

`data/bot-migration-gate.json` 只要存在，就阻止正常启动、复用页面、实例登记及 Bot 新建、删除、显示名修改。
即使 JSON 损坏或写着“已完成／已过期”也不会自动放行；进程退出后标记仍生效。不要删标记绕过保护。
该门禁不会停止已运行的服务，不证明请求已排空，也不是生产迁移执行功能。

`data/bot-retired-names.json` 使用 `{"version":1,"names":["旧名字"]}` 记录禁止复用的旧身份。
名字按大小写等价检查；退休名字不要求保留同名 Bot 目录，不会被当成丢失的活身份。
该登记损坏或与现有运行归属冲突会拒绝继续；不能为了启动而清空登记。
目前没有面向真实数据的自动解除门禁、迁移提交或崩溃恢复入口。

## 现在能做什么

跟她的大脑说上一句话：

```powershell
cd D:\TelyinCore\core
.\.venv\Scripts\python.exe -m telyincore.cli "在吗"
```

需要她的 NInfer 引擎在 `:24550` 上跑着
（`D:\ninfer\启动-ninfer-三元-24550.bat`）。

## 运行契约（**别踩这几条**）

**Python 与 venv。** Python **≥ 3.11**（本机 3.11.9）。这个仓库用一个**已有的 venv**：

```
D:\TelyinCore\core\.venv
```

**从仓库根跑。** 这个项目**没有装包**（见下面那条），模块是靠 **cwd 进 `sys.path`** 才找得到的
⇒ 命令必须在 **`D:\TelyinCore\core`** 下执行，用 `.\.venv\Scripts\python.exe`。

**⚠️ 不许用裸 `pytest`。** 那样 `cwd` 进不了 `sys.path` ⇒ `ModuleNotFoundError: telyincore`。
**⚠️ 不许 `pip install -e .`。**

**⚠️ `[build-system]` 是故意没有的**（`pyproject.toml` 里只有 `[project]` 与
`[tool.pytest.ini_options]`）。所以 `pip install -e .` / `pip install .` 会**失败**，
而这**不是**坏掉的配置：本轮不装包，入口走 `python -m telyincore.cli`、测试走 `python -m pytest`。
要打包或加 console_scripts 入口时**再补**它（计划里记着这条）。

### 退出码

| rc | 含义 | 打到哪 |
|---|---|---|
| **0** | 打印出来了 —— **含"只有拒绝"的那种回复**（拒绝不是错误，见下） | stdout |
| **1** | 出错了（连不上 / 超时 / 协议错 / 配置错）：`出错了：<原因>` | stderr |
| **2** | **没给参数**（用法提示） | stderr |

⚠️ **拒绝（`refusal`）走 rc 0**：那是一次**成功的对话**，只是内容是不做。
它的文本照样打在 stdout（正文为空时才回落到它）—— **没有第四个退出码**。

⚠️ **"输出写不出去"不在这张表里**：非 UTF-8 控制台的 `UnicodeEncodeError`、管道给 `head`
时的 `BrokenPipeError` —— 那是**我们这端**的事，故意**不**被 `出错了` 那个 handler 抓住
（把它报成"引擎坏了"是两条完全不同的故障混成一条）。它由解释器自己以 **rc 1 ＋ traceback** 收场。

### 环境变量（四个，全都有默认值）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TELYIN_UPSTREAM` | `http://127.0.0.1:24550/v1` | 引擎的 `/v1` 根；尾部 `/` 会被去掉，代码再拼 `/responses` |
| `TELYIN_MODEL` | `qwen3.8-27b` | 引擎的 `public_model_id`（**不是** `bonsai`） |
| `TELYIN_MAX_TOKENS` | `8192` | 请求体里的 `max_output_tokens`；也定 runaway guard 的上限（×12 字符） |
| `TELYIN_THINKING` | `1` | 开思考。**假值是 `0` / `false` / `off` / `no`（不区分大小写）**，其余一律算开 |

⚠️ **空串当没设**（走默认值）—— 四个变量都是这个口径。
⚠️ **环境变量是最高一层**：配置现在只有**两层**（硬编码默认 ← 环境变量）。
设计稿 S9 里那**第三层**（配置文件 JSON）**不在计划 A 的范围**，
`load_config(env=...)` 的 `env` 参数就是将来与它对接的接缝。

## 测试

**从仓库根**，用 venv 的解释器：

```powershell
cd D:\TelyinCore\core
.\.venv\Scripts\python.exe -m pytest -v
```

⚠️ **不要用裸 `pytest`** —— 原因见上面「运行契约」。

**测试不碰真引擎** —— 传输层是注入的（`Transport` 是个 `Protocol`，
`_run(cfg, text, transport=None)` 也收一个注入点）。

## 许可

TelyinCore 采用自定义的 [《TelyinCore 非商业使用与受限分发许可》1.0](LICENSE)，
SPDX 自定义标识为 `LicenseRef-TelyinCore-NonCommercial-1.0`。这是**源码可见的受限许可**，不是 MIT、GPL 等 OSI 标准开源许可，
也不是 Unlicense 或公有领域奉献。

- 主程序与明确采用本许可的官方内置插件（当前随包提供 `core-infinity-qq`；历史移除组件的许可保持原状）受该许可约束。
- 第三方插件、模型、llama.cpp、库和运行时仍按各自许可。
- 非商业本地使用、必要私有备份、查看公开源码，以及按公开接口开发独立附属插件，在许可条款下被允许；
  商业使用、未经授权的修改和再分发需要另行授权。
- 若通过公开 GitHub 仓库提供，平台条款允许的查看与 fork 权利不受影响；本许可不因此授予商业使用权
  或超出平台范围的再分发权。

正式产品名称是 **TelyinCore**；既有目录、插件 ID、包名及接口标识保持不变。

## 离线租约与新进程恢复（隔离实验）

`bot_offline_lease.py` 在持久门禁与全局身份锁下，检查全部已知运行根、实例记录和协作实例锁。
必须提供可信宿主的完整进程观测；缺失、未知、仍存活／排空中的进程或忙锁均拒绝。
本仓尚未提供生产原生进程观测适配器。不能把调用方 JSON、PID 文件消失或端口不通当作离线证明。
租约退出即失效，新进程必须重新取证，不能复用落盘的“offline=true”。

`python -m tools.bot_recovery_fixture create` 只生成自己的临时合成部署。
该工具的 `apply`／`rollback` 仅能重开生成的临时 `--home`，不接受真实部署目录。
支持真实测试进程异常退出、新进程校验日志、重取隔离离线租约以及验证后解除其自己的夹具门禁。
持久测试签名密钥存放在临时夹具旁，仅用于演练，不是生产密钥／ACL 方案。
没有开放真实 Bot 的迁移、恢复或解除门禁；协议上下文、QQ／插件适配仍待完成。

## 宿主观测适配与协议索引计划

`bot_host_observer.py` 提供 Windows 指定 PID 的只读原生查询及宿主观测适配器。
只读取存活状态、创建时间与映像路径；不枚举全机进程，不读取命令行，不提升权限或停止进程。
仍需可信宿主提供完整、带版本的进程名册；PID 文件和请求 JSON 不能替代它。
生产名册来源尚未接线，普通启动和迁移预检不会自动触发扫描或放行。

`bot_protocol_reindex.py` 只生成 v1 上下文索引迁移计划：要求全部索引都有明确的会话路径、scope、
可选分支上下文与元数据摘要见证；不反解哈希，不丢弃未知索引，不改 context、records 或 reasoning。
没有生产写入入口；未来事务执行器还须保证会话和 RecordStore 原字节、访问边界及回滚。
目前仅用合成会话验证了原始回放读取器和 Console 分支上下文身份的保持。


### Bot 改名：协议/QQ 事务适配隔离演练（2026-10-23）

- `tools/bot_adapter_fixture.py` 将已有协议索引重建计划和 QQ `ownerBot` 字段更新接入跨进程恢复夹具。只接受工具新建的临时合成部署，不接收生产根目录。
- 基线 Bot 保持运行时绝对路径，索引无需改名；独立运行时按明确 session/scope 见证更新索引文件名，索引内容、会话和 RecordStore 字节保持不变。
- `bot_qq_ownership.py` 仅处理调用方提供的有界配置与完整资源见证，输出摘要和 `ownerBot` 补丁，不输出 token。未设置归属时按当前共享基线/独立配置回退规则显式归属到新名；不做全局替换，不改账号、token、端口或历史文本。
- 未知插件、进程/恢复记录、sharing-preview 测试授权、其他未适配资源一律拒绝。该原型不负责证明名册完整、不识别跨 Bot 同号冲突，也不覆盖语音素材等插件数据迁移。
- 每步应用和反向回滚复用持久化 gate、离线租约、签名日志、完整树状态匹配；发生并发修改时保留封锁、不覆盖。夹具 key 和配置 before-image 仅含合成值，不是生产凭证存储或 ACL 方案。
- 生产预检对 QQ/provider-state 等资源的阻断仍保留；不暴露生产执行/解封入口。本轮没有读取真实 QQ 配置、移动真实 Bot 或操作线上服务。进程崩溃恢复不等同于断电耐久性证明。


### Bot 改名：跨 Bot QQ 归属与插件资源见证（2026-10-23）

- `bot_plugin_ownership.py` 新增只读 `PluginOwnershipObserver`，必须提供可信宿主库存回调；当前没有生产库存提供者，也不自动读取实际账号配置、探测网络或枚举进程。客户端 JSON 中的 complete=true 不构成生产完整性证明。
- 清单校验 core 范围、Bot 全集、baseline、资源路径/归属/配置摘要；固定 generation 和完整快照，连 generation 不变时的资源修改也拒绝。QQ 补丁规划前后再次观察；不能替代真正离线锁内的重检。
- 明确账号见证统一数字/字符串与前导零后做跨 Bot 重复声明检查。未知账号、重复同号、共享/独立配置路径错配及配置摘要不匹配均阻断，不从端口、token 或文件名猜账号。
- 未知插件、未知资源、进程/恢复记录、语音素材和 sharing-preview 授权仍全部阻断。输出只列资源路径、插件、类型、归属和分类阻断原因，不导出账号标识或配置正文。
- `tools/bot_adapter_fixture.py` 新建合成部署时现使用两 Bot 的合成配置/账号清单通过上述检查，再构造签名事务；提交/恢复仍依赖整树哈希匹配。未宣称旧夹具已有此新建时检查，未开放生产 apply。
- 尚未落实：权威库存采集与账号来源交叉核验、与权威进程名册联合离线协调、更多插件适配、生产凭证备份保护及分发产物验收。只读生产预检原有阻断不解除。


### Bot 改名：插件/进程联合离线预检协调（2026-10-23）

- `bot_joint_offline.py` 提供 `coordinated_offline_lease`，复用现有 gate、全局身份锁和全部发现运行根锁。必须提供一个可信宿主原子库存回调，里面同时包含插件清单与进程名册；没有生产默认提供者，不安装/移除 gate、不停启进程、不执行迁移。
- 联合校验 core、Bot 全集、磁盘 baseline、共同 generation、两个子清单及其完整快照。插件归属检查与 HostObserver 的指定 PID 原生探测在同一身份锁范围内进行；探测后重取原子快照，变化则撤销预检。
- 活跃租约可再次检查、只读规划 QQ ownerBot，或为未来目录移动释放运行根句柄；全局身份锁和 gate 不释放。正常退出前还会复核，异常退出保留 gate，租约对象离开上下文即失效。
- `tools/bot_joint_fixture.py` 只创建新合成部署，演示基线/独立布局的联合预检；无外部根路径参数、apply 或解封命令。演练保留临时 fixture fence，不变更其迁移步骤前缀。
- 这只是预迁移布局的联合协调，不是已完成的生产事务接线。实际改名会改变 scope/路径/配置，当前固定快照会拒绝它；后续须设计受签名事务约束的逐阶段期望与重新观察，不能放宽为接受任意新清单。
- 待完成：可信宿主库存实际采集与登录账号交叉核验、阶段感知执行/恢复协调、生产机密备份/ACL、断电耐久性和分发产物验收。两个 complete=true 或相同版本字符串本身不构成真实完整性/离线证明。


### Bot 改名：签名阶段执行与跨进程恢复夹具（2026-10-23）

- `tools/bot_phase_fixture.py` 新增 PhaseFixture，只创建/重开带对应签名 execution_profile 的临时合成部署。旧恢复/适配夹具严格拒绝该新执行配置；删掉阶段日志也不能直接回退到旧入口。生产入口未开启。
- 每个合法 prefix 从已验证备份、签名 before 状态与精确操作镜像推导，不能从当前磁盘状态重置快照。阶段日志额外绑定 manifest SHA256、确认游标和 pending 步骤；恢复仅接受已确认位置，或该 pending 步骤写入前/后的两个准确状态。
- 一个全局身份锁覆盖整次执行/回滚；各阶段按签名运行根获取实例锁，目录移动前释放运行锁句柄但保留身份锁与 fence，随后在新阶段重新获取并联合观察。每次变更前后核对完整树、精确插件/账号清单、发现范围与进程名册；观察后再次核对认证输入。QQ/控制写入还会即时核对 before-image，不覆盖已变化文件。
- 插件普通检查器未放宽。迁移中暂时存在的目录/owner 不一致，仅在已签名的精确中间态且持有 fence 时允许；起终点重新通过普通归属规则。合成账号声明始终互异，任何未知进程/资源清单或未计划变化都阻断且不解封。
- 完整恢复后才先保存终态日志、复核后移除自己拥有的合成 fence；已提交事务不允许直接反向 rollback。`--crash-before`、`--crash-after`、`--crash-terminal` 仅用于短生命周期测试进程退出。
- 本轮复用 HostObserver、插件归属检查、原操作白名单与完整树规则，新增独立阶段协调循环；没有放宽原预检联合租约，也没有把旧夹具执行结果追认为已走新阶段检查。
- 仍未落实：真实宿主原子库存/账号登录证据采集、生产凭证备份与 ACL、非协作写入与断电耐久性保证、其他插件适配和分发产物验收。夹具内的账号映射/空进程名册仅是合成证据，不是生产完整性证明。


### Bot 改名：只读宿主库存适配接口（2026-10-23）

- `bot_host_inventory.py` 新增 ReadonlyHostInventory，可作为联合预检的 inventory 回调。接口要求真实宿主注册表持读锁的 capture 上下文；无默认生产提供者，未接启动器或线上服务，客户端 JSON 包成 contextmanager 仍不构成权威来源。
- 从明确部署读取有界身份控制文件与 QQ config 摘要，枚举 core/extensions、各 Bot infinity、infinity-processes 和 sharing-preview 资源；不读聊天、人格正文、未知插件代码/媒体/日志正文。未知资源 sha256 为带 metadata-only-not-content 域标签的元数据摘要，不是内容哈希，始终属于未适配阻断资源，不能作为写入镜像证明。
- QQ 账号只能来自可信 get_login_info 证据：绑定配置路径/摘要、Bot、宿主 generation、目标/实际账号、单调时钟新鲜度和已验证端点进程身份。活跃端点必须匹配原生 PID/创建时间/映像以及权威名册；已退出端点需在名册中完成退休且被原生探测确认为不存在。不能从 ownerBot/端口/token 猜账号。
- 采集期间持宿主读锁，前后复核原生身份、磁盘范围/资源和库存对象，变化或权限不明即拒绝。inspect 只导出脱敏清单与计数，不导出账号、token、配置正文或离线授权。独立 inspect 使用既有 identity lock，可能初始化协作锁文件；适配器不写配置、不发网络请求、不启停服务。
- `tools/bot_host_inventory_fixture.py` 只创建新的合成部署，使用明确标注的合成退休端点/账号证据演示扫描和联合预检；不接受外部根路径，不提供 apply。真实原生探测专项只查询自建短生命周期 Python 测试进程。
- 尚待落实：真实宿主注册表读锁/完整名册生命周期接线、可信 OneBot 查询与端点证明的实际生产者、阶段事务所需的库存投影与受控变更、机密备份/ACL 和分发验收。本轮没有把合成证据追认为真实完整性或生产离线证明。


### Bot 改名：注册表读侧与只读账号证据生产者（2026-10-23）

- `bot_host_registry.py` 新增 HostEvidenceRegistry，capture 可直接接入 ReadonlyHostInventory。注册表默认 unsealed，没有启动钩子或自动接管。初始空宿主必须由可信独占覆盖方证明，不能把静态 complete=true 当作所有历史/非协作入口已守卫的证据。
- 生命周期写方采用 identity lock → 注册表非阻塞 mutex 的锁序；capture 持 mutex 读侧至原生/文件观察结束。启动方须在 start_window 内自行创建子进程并提交原生 PID/创建时间/映像；未提交、失败或重复/旧 PID 撤销覆盖，不盲目取消或抹去已有进程。窗口本身不创建进程。
- bind_config 只发布已有配置摘要和目标账号，不改配置/登录。显式 refresh_account 先撤销旧缓存，再只对已登记、原生身份一致且 HTTP/WS 本机监听 PID 均匹配的端点执行一次 POST /get_login_info；禁用环境代理/重定向，限制 64KiB/6 秒，不登录、不发消息、不重试。
- 证据绑定完整配置摘要、目标/实际账号、端点身份和原始单调时间；账号/配置切换和失败查询撤销旧证据。retire 只接受明确原生 absent，不停止进程、不删 PID 文件，允许在 fence 保留时记录已确认退出。退休和其他进程的版本变更不延长账号证据寿命。
- `tools/bot_host_registry_fixture.py` 只创建新的合成部署：初始覆盖、进程/监听数据为明确测试桩，HTTP 使用 MockTransport；验证活跃进程阻断、确认退出后联合预检可用，树状态不变且保留临时 fence。无外部 root、apply 或解封接口。
- 尚未落实：将窗口/失效通知接入全部实际启动、退出、账号切换和 Bot CRUD 入口；初始独占/非协作进程覆盖权威来源；生产阶段库存变更与账号缓存协同。新模块未挂到现有线上入口，生产迁移仍禁用，测试桩不构成真实宿主证明。


### 启动器生命周期接线（默认关闭，非生产迁移授权）

`startup.main(*, host_registry=None)` 新增仅程序调用的可选注册表注入。
普通命令行与环境变量不会创建、封存或启用注册表；默认启动与复用行为不变。
显式注入必须是同一 core 的 `HostEvidenceRegistry` 且包含所选 Bot，已有服务不能被接管。
实际 `Popen` 在全局身份锁及注册表启动窗口内执行，登记成功才进入就绪等待。
退出沿用原有自有子进程清理路径，再由注册表独立核验原生 PID 不存在后退休；
核验失败保留记录，不因 `wait()` 返回就认定宿主离线。

重要：这个钩子只覆盖启动器的直接 Core 子进程，不覆盖它的插件/服务子进程、
直接运行 `launch.py`、Infinity 控制器、旧版本启动器或其他入口。
因此提交直接子进程时强制使用 `untracked_descendants=True`，在同一出生锁内
原子撤销完整覆盖声明。即使该子进程随后已退出，也不能自动恢复完整覆盖。
该标记只能撤销可信性，不能证明子进程入口已经受控；不能为了让检查通过而移除它。
注册表状态中 `production_entrypoints_connected=False` 仍指未完成完整生产入口覆盖，
不表示源码没有这个实验性程序调用钩子。

验收使用生成的临时根与假启动目标；原生集成只启动本轮自建短命 Python 子进程，
不执行真实 `launch.py`，不启动 Bot、不请求真实网络、不读取真实账号配置。
后续仍需跨进程生命周期通道、Infinity/账号切换/Bot CRUD 接线及可信完整覆盖权威；
本节不授权生产部署，也不开放生产迁移。


### 父子宿主生命周期私有通道（隔离原型，未连接生产）

`bot_lifecycle_channel.py` 提供显式创建的私有继承 Pipe、双向 HMAC 会话帧及宿主代理。
Windows spawn 仅向指定测试子进程复制端点；启动能力密钥/会话只在私有启动参数中传递，
不写文件、环境变量、命令行或日志。不开放 TCP/命名公共入口，消息使用有界 JSON，
不对通道消息调用 pickle 反序列化。双向区分、会话随机值、严格序号与重复字段检查防止重放/反射。

父宿主须先通过受控出生窗口登记控制子进程；broker 独立核验 PID、创建时间和映像，
在收到请求以及操作前后重复核验。子端只能请求经白名单限定的 ping / describe / start(slot) / status(slot) / stop(slot) / retire(slot)，
不能传 PID、命令、路径、Bot、账号或配置。slot 对应父宿主内部可信零参数启动工厂；
实际派生和登记由父宿主完成，不能采信子进程自报的任意 PID。
退休仍须独立原生退出证据；断连、超时、鉴权/身份错误不会杀进程或遗忘已知句柄/登记。

`HostEvidenceRegistry.revoke_coverage()` 新增只减权的不可逆故障闩锁，断连时无需获取
已持有的身份锁/读锁即可撤销完整覆盖。进行中的 capture 在退出时发现故障会抛错，
故障对象不能再 bootstrap 恢复可信性；必须另建注册表并重新取得真实空宿主权威。

限制必须保留：broker 无后台线程，宿主必须周期性调用 serve_once（单次等待至多5秒，
会话寿命至多30秒）。早期版本曾依赖完整 send_bytes 消息；该限制已由下节原生限时传输替代。
仍不抵抗同用户调试器或受控宿主失陷。
当前派生仍强制 untracked_descendants=True，首次派生即撤销完整覆盖并阻止后续派生；
不能移除此限制来冒充完整多子进程监管。生产需要进一步的跨进程入口覆盖与传输加固。

`python -m tools.bot_lifecycle_channel_fixture` 只创建新临时根，运行自建短命 Python
控制进程与叶进程，演示认证、代理派生及确认退出。测试另验证真实管道 EOF 时
控制进程仍活着的失败关闭行为；不启动真实 Bot、不访问真实网络或账号。
startup/launch/Infinity 尚未接入此通道；生产迁移继续禁用。


### 生命周期通道传输加固（Windows 原生绝对 IO 时限）

`bot_channel_transport.py` 使用本地继承的 overlapped message-mode Pipe。
每条消息采用4字节长度头与至多4096字节正文，单次内核消息即完整帧；
部分头、部分正文、长度不符、超大内核消息均直接关闭，不等待补齐、不排空或分配远端声明的长度。
旧的无传输头签名消息也失败关闭，没有旧 poll()+recv_bytes() 降级路径。

读写均使用 `_winapi` overlapped IO 和绝对 monotonic deadline，等待只使用有限时长，
不使用无限等待或 GetOverlappedResult(True)，不启后台读取线程。
客户端发送/接收共用一次截止时间；broker 接收/回复共用一次截止时间，
不能按分块续期或在过期后返回成功。截止/错误后取消 IO 并关闭端点。
取消收尾最多另等50ms；极端取消未完成时保留 OVERLAPPED/缓冲区引用并隔离新 IO，
直到确认完成再回收，不以释放内核仍在使用的内存来换取表面超时。

这是一项传输等待上界，不是实时系统承诺：OS 调度、原生身份探测和可信启动工厂
不受该 IO 时限抢占。工厂超时返回后禁止迟到的成功回复，但已经创建的子进程仍须
保留登记/句柄并失败关闭。宿主仍须周期性 pump；生产多入口完整覆盖仍未实现。
仅支持由可信宿主创建的 Windows overlapped message Pipe；不接受任意同步句柄或 TCP 回退。

新专项用真实本地内核 Pipe 验证空读取消、背压写取消、畸形/超大消息与绝对截止，
另以自建 Python 子进程验证“对端仍活着但闲置/仅发送部分帧”的拒绝；
取消永不完成的隔离分支使用显式模拟内核验证，不冒称真实 OS 一定会复现该极端状态。
`python -m tools.bot_channel_deadline_fixture` 只创建这些测试进程与私有 Pipe，
不启动 Bot、不打开网络监听、不读取真实账号。生产入口未接线，生产迁移继续禁用。


### 实际 TTS 生命周期入口接线（显式注入，默认关闭）

`launch.main(..., process_lifecycle=None)` 将显式 `LifecycleProcessBridge` 传给
真实 `QQControl`。默认 None 不改变现有启动/启停行为；无 CLI、环境变量或自动 bootstrap 开关。
只允许程序调用注入，不能与 sharing-preview / acceptance-isolated 宿主混用。

`bot_lifecycle_entry.py` 将批准的 TTS argv/cwd/env 固定到单次 slot；绑定时通过认证
`describe` 核对父宿主 core 与 Bot 插件范围，拒绝客户端/父端范围错配。
实际 QQControl._start 在显式模式不创建本地进程或本地进程日志，而是请求父端受控派生。
参数不符、通道断开、槽位重复均失败关闭，无 Popen 回退。

父端 broker 增加 status/stop：仅可查询/停止本通道先前创建并持有的子进程，
需要原生创建身份匹配；stop 使用保留的自有 Popen 句柄，不采信请求中的 PID。
停止不等于退休；代理 wait 仍需 status 的原生 absent 及独立 retire 核验。
正常 tts_stop 和 QQControl.close 均走同一路径，已经退出的代理也确认退休；
不把确认 absence 的返回0冒充原子进程的真实退出码。

本轮只接 TTS。显式模式拒绝 SnowLuma start/recovery/stop/install-stop 与 QQ 凭据保存，
跳过旧 SnowLuma 恢复接管并禁用自动 QQ health；不能借用旧共享管理身份作为该通道权威。
SnowLuma/账号切换/完整启动器 bootstrap 及其他插件生命周期仍待完成；
整个宿主覆盖仍不完整，untracked_descendants=True 继续保留，生产迁移不得启用。

`python -m tools.bot_lifecycle_entry_fixture` 用全新临时 core、合成预检输入和只睡眠的
Python 叶进程，经真实 action(tts_start/tts_stop)/close 代码验证跨进程路径。
不会启动真实模型、Bot、QQ 或服务，read/模型预检在夹具中隔离；不等于完整 UI/真实 TTS 验收。


### 一次性自动验收入口

`python -m tools.bot_lifecycle_acceptance --output <全新证据目录>` 串起入口/传输/注册表、
相关回归、崩溃恢复以及控制器/语音扩展套件，两组测试进程并行执行（不是模型子代理）。
保留命令、日志、JUnit、用例去重及运行前后源码指纹；有失败、证据缺失或源码变化即非零退出，
不会把旧结果当通过，不是全仓验收，也不会开启生产迁移或调度未来任务。
本轮同时修复了 test_plugin_signals 对已删除 logRefresh 监听器的过时截取边界，
只约束真实 pollLog 函数，并补“函数内禁止调用仍可检测、后续处理器不误报”反例；
没有修改 infinity-page.ts 前端实现。


### 账号/端点配置修改前的持续撤权屏障（实验接线，默认关闭）

显式 LifecycleProcessBridge 模式下，QQControl._commit_config 先请求认证的
prepare-account-change；父端从已绑定的 Bot 范围计算配置路径，RPC 不接受路径、账号、
凭据或“解除屏障”字段。父端清除旧回执并将该配置记为持续 pending 后才回复。
这不同于旧 invalidate_account 的单次丢弃：后续 bind/refresh/bootstrap 都不能在同一个
注册表上恢复账号权威，没有 finish/end/unlock RPC；成功、失败、回滚或退出均不自动解除。

收到 ACK 后，子端才获取本地全局身份锁，重新检查迁移围栏与精确的配置/备份范围。
禁止持有该身份锁等待需要同一把锁的父端 RPC；屏障在 ACK 到实际写入之间阻止重新发证。
真实 settings/routing 保存共用此入口。实验模式拒绝未受控 reload 回调、凭据导入及
子端直接 HTTP/WS 检测；SnowLuma 登录/恢复仍不开放，不以这个屏障冒充账号切换已完成。

受控写入做原字节复核、随机独占临时文件/备份创建、flush/fsync 与原子替换；
检测到并发内容变化就保留它，不用旧副本覆盖回去。拒绝把备份写到其他 Bot 的目录。
这些检查不是对非合作 OS 写入者的原子 CAS，也未证明目录持久性或生产备份 ACL。
未启用 bridge 的旧控制器路径保持原状；不宣称所有外部/旧版写入入口已受控。

python -m tools.bot_account_change_fixture 使用全新临时根、合成配置、真实私有 Pipe 和
本轮自建 Python 控制进程验证旧回执撤销→真实 save_routes→保留 pending/备份。
HTTP 只走 MockTransport，原生查询只针对自建进程，没有真实账号请求或服务启停。
新的20项专项已纳入一次性自动验收入口；生产迁移仍禁用。

### 受控 Python 控制进程 bootstrap（显式实验辅助器，未连接生产）

`bot_controller_bootstrap.ControllerBootstrap` 要求调用者提供已有的
HostEvidenceRegistry、Bot、可信可 spawn 的 Python 入口与固定参数；不自行调用
bootstrap_empty、不读取配置或凭据、不扫描/接管进程、不提供 CLI/环境变量开关。
它在身份启动窗口内创建 Windows spawn 子进程，通过私有继承 Pipe 和私有参数
交付密钥/会话。子端先等待私有 Event，父端原生出生身份注册和 Broker 构造均成功后
才放行目标代码；失败不放行，等待有上限，并保留 Event/自有进程句柄供退出确认。

**该辅助器不是可信空宿主权威，也不是完整启动器接线。** 控制进程仍有未跟踪的
后代/旧入口，因此出生提交始终使用 untracked_descendants=True；这会立即撤销完整
覆盖，并阻止后续 start_window，不能拿它绕开 TTS/SnowLuma 启动限制。不会替真实
宿主伪造 complete=true，也没有恢复覆盖的开关。默认 launch/startup 路径未改变。

调用者显式泵 serve_once；Broker 的 30 秒会话寿命限制未延长。close/传输失败
只撤权、断开，不自动停止/忘记控制进程。wait_and_retire 先作有界 join，再由注册表
独立确认原生 PID 已消失后退休；存活、状态未知或失败均保留句柄/记录，不借 exitcode
单独放行。可信 Python 目标本身的执行时间不受 IO/门闩等待时限承诺保护。

`python -m tools.bot_controller_bootstrap_fixture` 仅在全新临时根运行本轮自建 Python
控制进程，演示 describe/ping、保守覆盖撤销与退出退休；其空宿主声明仅属合成夹具。
没有启停真实 Bot，没有读取账号/聊天/记忆或发送 QQ/模型请求。SnowLuma/实际账号
切换、完整入口覆盖、真实排他空宿主权威、生产阶段投影、ACL/持久性与分发仍待完成。

### 实验控制器的未中介能力封闭（默认模式不变）

显式 LifecycleProcessBridge 模式不再构造/暴露旧 Recovery、ASRInstaller 或
TTSInstaller 对象。即使调用者注入旧 recovery，实验控制器也只持有无路径、无凭据、
无工作线程的 UnavailableRecovery；start/save/connection_config 拒绝，cancel 空操作。
两种安装器仅返回 unavailable，不允许经 launch 的嵌套 installer.start 路由下载或
启动工作线程。默认未注入 bridge 的 Recovery/安装器行为不变。

此前只封闭了 SnowLuma action/import/check，但直接 recovery 属性、安装器属性、
状态采样和部分辅助入口仍保留旧能力。现已在实验模式拒绝直接 HTTP/WS 探测、
恢复/归属记录操作、安装探测、Snow 日志读取、文件夹打开、
语音素材库及试听请求；管理/WS 信任函数不借用旧本机证据。

实验 read() 现在返回明确的精简 capability view：legacyStatusAvailable=false，
服务 running=null，健康 unknown，无账号/凭据回读、日志、进程发现、QQ 环境采样或
socket IO。它**不是旧 WebUI 的完整状态 schema，不表示真实服务停止/账号离线**；
因此本实验仍不能作为整套管理 UI/启动器就绪证明。受控配置提交与 TTS 句柄启停
保持原路径；TTS 启动既有本地预检不在本轮无 IO 状态视图的保证范围内。

这是一轮未中介能力的拒绝式封闭，不是 SnowLuma 登录/切换实现，不是整个
launch/plugin 进程沙箱。可信 Python 调用者自身仍能访问 OS；完整旧入口覆盖、父端
账号与恢复中介、排他宿主权威、阶段投影、ACL/持久性及发布验收仍未完成。

### 验收源码闭包与 JUnit 证据完整性（不是宿主覆盖证明）

tools.bot_lifecycle_acceptance 不再只比较手写短名单。tools.bot_source_snapshot 从
显式种子出发，静态解析本仓库 Python import/import-from，保守纳入包 __init__、
函数内导入、相对导入和 tests 辅助模块；不执行待测源码，不查询 sys.path/站点包，
不扫描配置/聊天/记忆。文件加载的 QQ 与视觉插件入口显式加入种子。

缺失种子、语法错误、预先存在的链接/目录联接、单文件/总大小或数量超限均拒绝。
限制为 4096 文件、单文件 4 MiB、总共 64 MiB；外部包不追入。根脚本的包式相对
导入按虚拟仓库包保守处理，超出仓库根的相对导入拒绝。源码指纹前后不一致或
结束时无法读取闭包，最终验收失败；开头无法读取会在 source-error.json 留下
异常类型并在测试启动前停止，不输出任意异常详情。

JUnit 每组必须包含合法非空 suite、非空测试身份和一致的 tests/failures/errors/
skipped 计数；跨组重复、嵌套隐藏 testcase、坏/缺 XML、非零进程退出、超时、
失败/错误/跳过均不放行。模拟空 XML 或通过计数不能构成成功验收。

**静态闭包仍不是完整运行时依赖发现或持续文件监控。** 动态 import、字符串/
文件加载、包路径重映射等需要继续补显式种子；测试前后瞬间恢复的临时变更、
非合作文件系统换链竞态和第三方运行时版本不在该哈希保证中。它只提高隔离
验收证据的可复核性，不授予真实宿主完整性，也不启用生产迁移。

### SnowLuma 恢复的代际/信任回调边界（不启用实验登录）

Recovery.run 与同步 connection_config 现在先固定线程本地代际，在首次恢复配置读取、
QQ 进程枚举或凭据解密之前要求管理端归属信任。操作结束/失败均清除线程本地范围；
拒绝同线程嵌套 run/connection_config 覆盖外层 pin。connection_config 的整个鉴权/
配置导入协议现在共享同一代际与配置 revision，不再只在响应后局部比较。

_require_trusted_management 在信任回调前后均检查取消、generation 与已固定 revision。
回调即使返回 true，只要在回调内取消/改代/改配置，就不能发送旧 token 或 load。
恢复流程在 processes、discover、launch、HTTP client 构造的前后重新经过相同门禁，
避免发现回调/客户端构造期间换账号后继续启动或请求。已经完成的 launch 不会凭
一次迟到撤权自动 kill 或假装撤回，只停止后续步骤，不发布在线成功。

这强化的是现有手动恢复协议的合作式代际边界，不是原子 OS 事务、进程归属中介或
完整生命周期监督：最终核对与 OS/HTTP 调用间仍可能发生并发变化，可信回调本身的
副作用/耗时没有被沙箱化。显式 LifecycleProcessBridge 下 Recovery 依然不可用，
没有放开实验 SnowLuma 登录/注入，没有改变 untracked_descendants 或生产迁移禁用。
反例仅合成配置、纯 Python 回调和 httpx.MockTransport；不枚举或启动真实 QQ。

### 恢复设置存储的父端持续撤权（显式 store-only 原型）

Recovery(..., process_lifecycle=bridge) 是单独的显式存储原型，不是运行恢复模式。
仅允许精确 baseline data/infinity-processes/snowluma-recovery.json 或已绑定 Bot 的
infinity/processes/snowluma-recovery.json。save 的旧文件读取、加密、备份、写入和
clearCredentials 备份删除均包在同一个 guard 中；直接公共 _write 也先经过 guard。

guard 先用认证 describe 对照父端 core/Bot，再发送无参数 prepare-recovery-change，
取得父端持续撤权 ACK，之后才取子端身份锁和检查迁移围栏/精确文件及 .tmp/.json.bak
兄弟路径。RPC 不传凭据、账号、路径或重置标志；不能在持有子端身份锁时嵌套 RPC。
父端从可信 Bot 推导账号 scope，即便 QQ 配置尚未绑定也先设置永久 pending 并移除
既有账号回执；不能 rebind/refresh/bootstrap 清除，也没有 end/reset RPC。任何
账号 pending 均阻止新 start_window；已存在自有进程的独立退休不因此被禁止。

该 store-only 对象不暴露运行时 processes/discover/launch/client；start/run/导入/
解密/管理请求拒绝，状态 unknown 且不读文件。QQControl 的实验恢复对象仍是
UnavailableRecovery，launch/startup 默认路径没有接线。此功能不是 SnowLuma 登录/
注入、账号切换或完整宿主生命周期实现，不放宽 untracked_descendants/生产迁移禁用。

默认无 bridge 的 Recovery 仍保留旧 .tmp 写法；store-only 的新受控写入见下节。合作身份锁不是非合作写入者的原子 CAS，
兄弟路径预检不是恶意换链竞态证明，也未证明备份机密 ACL/断电持久性。默认无 bridge
行为不变；真实上线前仍需完善存储、父端监督与完整入口覆盖。

python -m tools.bot_recovery_store_fixture 只用新临时根、合成恢复配置和本轮自建 Python
控制进程，通过真实私有 Pipe 演示无绑定时先撤权再实际 Recovery.save。加密回调是
明确的可逆合成夹具，不是 DPAPI/真实凭据保护证明；不枚举 QQ，不发送 QQ/模型请求。

### Store-only 存储的有界快照与独占临时替换

bot_guarded_store.GuardedStore 仅在调用者持有父端撤权/身份 guard 时使用。先固定
主文件和固定 .json.bak 的原字节；两者各最多64KiB，原 JSON 必须是对象、无重复键、
无 NaN/Infinity。保存过程的读取都复核快照；加密回调或其他写方改变主文件/备份时
拒绝继续，不以旧原件回滚覆盖别人。

受控 writer 不再复用旧静态 .tmp：主文件/备份均用随机 UUID 的 xb 独占临时文件，
flush/fsync 后复核主文件及备份 pin，再 os.replace 安装。备份保存精确原字节。
失败只清理本轮创建且文件身份仍匹配的临时文件，不删除碰撞文件或已被替换的
临时路径；已安装的有效原件备份不会为伪造全事务回滚而删除。

clearCredentials 仅在主文件替换成功后、同一 guard 内复核并删除固定受管备份。
若备份被并发修改或删除失败则报错，不恢复旧凭据，也不声称凭据已彻底遗忘。
这可能是主文件已更新但备份仍存在的部分结果，不是多文件全有或全无事务。

仅显式 store-only 模式使用新 writer；默认 Recovery 文件行为不变，也不会回退到
默认旧 writer。文件 fsync 不等于目录/断电持久性，两个替换并非原子多文件提交，
字节复核并非针对非合作写入者的原子 CAS；同用户恶意换链/文件 ID 复用/OS 调用间
竞态、备份 ACL 和跨宿主重启的否决持久性仍未证明。生产迁移和实验登录仍禁用。

### 客户端 RPC 全事务互斥与中断撤权

LifecycleClient 的 codec 编号、send 和对应 recv 现在组成一个非阻塞互斥事务。
传输层每次读写的锁不能替代该事务锁；并发/回调重入请求会直接关闭本地通道并拒绝，
不排队、不编码第二个请求、不借已有 slot 自动重试。已关闭期间到达的合法迟到响应
也不得作为成功返回。顺序调用的序号和原有 IO 绝对 deadline 保持不变。

KeyboardInterrupt/SystemExit 等 BaseException 会先关闭通道、释放事务锁，再保留
中断语义传播；普通 backend 错误仍只返回固定错误。close 清理出错不恢复可用状态，
不回显 backend 详情。此机制是失败关闭，不是并发安全的业务队列或长期宿主监督器。

请求可能已在父端执行，关闭不表示操作回滚或未发生；父端仍保留自有进程句柄，
不能盲目重试。客户端 close 本身不等于父端立即观察 EOF/立即撤权，仍依赖显式泵、
原生身份检查和时限。没有延长30秒会话寿命，没有授予完整入口/生产迁移权威。

### 可选的撤权拒绝记录（普通进程重启，非生产持久性证明）

显式 HostEvidenceRegistry(..., revocations=RevocationJournal(core)) 可在本 core 的
专用 data/bot-host-revocations 目录记录否决元数据；默认不配置，不读/建该目录。
记录只有 version、core 路径摘要、相对账号 scope 和随机 nonce，无 token/密码/
账号查询结果/聊天。该摘要用于防误绑，不是签名或防恶意篡改证明。

prepare_account_change/prepare_recovery_change 先撤销内存回执、设置 pending，再用
随机最终文件名 xb 独占创建、flush/fsync 记录，成功后才允许上层 ACK。失败使当前
注册表故障撤权；不删半写记录。新实例从记录恢复 pending，在 bootstrap/start/
bind/refresh/capture 前检查；capture 退出再查，期间新否决使该上下文作废。

最多1024条、单条4096字节；损坏/半写、未知文件/字段/scope、重复 JSON 键、误绑 core、
预先存在的链接或目录联接均拒绝，不将坏元数据当空宿主。坏日志不阻止原生缺席
确认后的自有进程退休。没有自动删除、reset/end 或清除持久否决的 API。

仅证明选用该原型的普通进程退出/新进程加载会继续否决，不证明断电目录持久性、
ACL、防删除/回滚或持续监控。文件写入者仍须遵守身份锁契约；默认/旧入口未强制
选用，遗漏该参数能绕过这层原型，故不能宣称全入口持久撤权或生产就绪。启用后
合法解除否决仍需未来可信事务设计，不能靠删除记录或合成 complete=true 放行。
生产迁移、实验登录、完整宿主权威和 untracked_descendants 否决均不改变。


### 启动窗口的 Q / Ctrl-C（控制器退出，不是完整离线证明）

默认与显式受控启动窗口均不再向 `http://127.0.0.1:<port>/shutdown` 发送停止请求。
端口和实例标记不是关闭权威，不能因旧端口复用而向另一实例发关机命令。
Q / Ctrl-C 仅控制本窗口保留的自有控制器进程；默认路径先发私有协作请求，动作前再次 poll，
受控路径另外保留原生身份/注册行检查。终止或等待失败向上传播，不返回虚假成功；
main 的 finally 仍清理本次创建的 Job/句柄，不是失败时零资源副作用承诺。

协作请求失败/超时后可能强制结束，不保证应用数据 flush、QQ/SnowLuma 离线或所有后代退出。
Windows 保留 Popen 句柄语义不等同 POSIX PID/信号；旧默认入口的发现/复用与 B 浏览器
仍不是完整受控入口。复用既有实例仅打开页面后返回，不获得关闭它的权力。
私有 IPC 只请求协作取消，不证明完整优雅停机；若有未保存操作，请先在应用内完成保存。
`untracked_descendants`、撤权及真实宿主缺席要求不变，不因此获得生产改名/迁移许可。


### 显式父宿主私有控制器退出（协作取消，非全宿主停机）

`ControllerBootstrap(..., private_shutdown=True)` 为本轮 spawn 另外创建私有继承 OS Event；
可信 target 收到 `shutdown_receiver` 关键字后，须原样传给 `launch.main(..., shutdown_receiver=...)`。
未选用时旧 target 调用约定不变。无公开端口、CLI/环境变量/文件 token 或旧实例发现。
父端 `request_shutdown()` 钉住已登记的原 PID/出生/映像和注册行，撤权后一次性设置该 Event。
失败保留自有句柄/登记，不自动重试，不据事件或返回值退休进程；仍需独立原生缺席确认。

实际 launch 异步入口监视这一个 Event，收到后取消本次应用 task，并等待其 finally；
监视任务同样随入口收口，不派生后台 stop 线程或枚举取消其他 task。返回130表示请求过协作取消，
不是数据 flush/QQ离线/所有后代退出。自然返回/应用错误/外部取消保留原语义；清理错误不吞没。
协作代码拒绝退出或清理阻塞时没有全局硬期限，父端需保留句柄并做独立有界 join；
该 Event 能力不强制 terminate。默认 startup 的 stdin 接线见下节，超时仍可能强制结束自有控制器。
这只是受信 Python/子进程间继承能力，不抵御恶意子进程/同进程反射，不构成完整入口或生产权威。
`untracked_descendants=True`、30秒会话、默认/TTS桥的SnowLuma动作与生产迁移否决均不改变。


### 默认启动窗口：私有 stdin 协作退出已接线

`startup.main` 默认启动 `bot_launcher_child.py`，为本轮子进程单独创建无缓冲二进制
stdin PIPE；子入口在 import launch/配置装配前核实它是管道，不读取普通文件/控制台。
Q / Ctrl-C 仅向保留的原 stdin 写一次单字节请求，再有界等待；不用服务端口或公开
/shutdown。子端非阻塞轮询继承管道，复用 ShutdownReceiver 取消本次异步应用并等待 finally。
无 token 文件/环境变量/CLI 密钥；管道继承是能力，不防恶意同进程或有权复制句柄者。
EOF/非法帧为失败，不是确认；退出130只是请求过取消，不证明数据落盘或账号/后代离线。

主窗口等待8秒超时或请求失败会传播错误；main finally 仍可能强制关闭自己创建的 Job/
控制器，故这是“尝试协作、失败自有清理”，不是保证优雅停机。直接 watch 的旧调用
若不提供私有能力仍保留强制自有终止分支。默认发现/复用和 B 浏览器尚未成为完整受控
宿主；SnowLuma/插件与可信生产覆盖未因此完成，untracked_descendants 及生产否决不变。

受控子进程的显式环境现在仅含 PYTHONIOENCODING 和 Windows 下的 SystemRoot（由
GetWindowsDirectoryW 原生读取，不枚举宿主环境/凭证）。只给编码而缺少 SystemRoot
可使 Windows asyncio/_overlapped 导入报10106。本轮同时让 ConsoleOutput 支持无缓冲
RawIO stdout；默认 legacy 环境继承行为未改变，测试入口仅使用合成环境。
私有控制 stdin 被专用，子入口拒绝将它同时用作 --console 交互输入。


### 控制器关闭后的 SnowLuma 恢复入口

QQControl.close 先设置永久本地关闭闩，再关闭健康观察/Recovery、清理本次自有资源；
关闭后的 action/_action/_start、恢复设置/导入/连接探测及 read 被拒绝，不能由迟到请求
重新启动恢复。Recovery.close 与可重用 cancel 不同：关闭后 start/run/save/导入、
读写及请求信任入口不可再用；state 仅返回无配置读取的 closed/unknown，不证明账号离线。
既有 SnowLuma 保持运行策略不变，不因 Core 关闭停止共享安装；其它自有进程清理失败
保留句柄并传播，后续 close 仅重试清理。已发出的外部请求不能撤回，同进程私有字段
操作不是沙箱；尚不覆盖所有设置/私有辅助函数，也不授予完整宿主或生产改名权威。

显式 MediatedRecoverySettings.close 同样关闭内部 Recovery；QQControl 关闭会传递
到已持有的 metadata settings 子能力。关闭后 save 在父端RPC和文件读写前拒绝，
state 标为 closed/unknown、settingsStoreAvailable=false；不解除父宿主既有 pending 否决。

### 默认启动包的缺件拒绝

`start.bat -> start.ps1` 在安装依赖、读取 Bot 配置或创建子进程之前，检查固定启动源文件（包括 `bot_launcher_child.py` 和 `bot_controller_shutdown.py`）存在且为文件；缺件退出码 2，不自动下载或降级到旧启动入口。此项只检查存在性，不验证内容、链接目标、完整依赖、发行版或宿主权威，检查之后的文件变化仍不在保证范围。源码候选 ZIP 匹配也不代表可部署或生产就绪。

### SnowLuma 原生观察与停止重试

已固定的进程句柄仅在原生等待返回 signalled 时视为该进程结束；等待失败/未知/已关闭句柄均拒绝，不当作离线。显式停止可以清理自然结束或先前超时后结束的同一个已持有子进程，仍需等待/独立退休；不按PID重新打开或发现接管。观察、等待、记录删除或句柄关闭失败保留本地引用供清理重试。该结果不是QQ账号、后代或宿主离线证明，默认/TTS桥不因此获得SnowLuma能力；显式父宿主进程能力见下节。

### 恢复与导入单任务入口

`Recovery.run`、后台恢复和 `connection_config` 共用本实例运行准入；同步调用也计入busy/workerAlive。其它恢复/导入/设置写入不能在同一实例运行期间交错开始。取消/关闭等待任务完成事件及自有后台线程，最多共用5秒等待预算；不join同步调用者的其它工作线程。超时保留busy/workerAlive与取消否决，任务结束后才解除占用；这不是账号离线或父宿主完整覆盖证明，实验桥登录仍拒绝；SnowLuma进程仅支持下节显式固定能力。

### 显式父宿主 SnowLuma 进程能力（非生产）

受信程序可显式构造 `LifecycleProcessBridge(slot="snowluma", fixed_contract=True, ...)`，环境必须声明 `SNOWLUMA_HOOK_AUTOLOAD=0`。QQControl 的该能力实例仅接受 `snowluma_start/stop`，按父宿主固定计划启动一次、操作其保留句柄并经原生缺席退休；不读取本地SnowLuma配置、凭据、日志或发现接管，不自动恢复/登录。其它动作和旧TTS能力的SnowLuma动作仍拒绝；失败不本地fallback。开关仅程序化，没有CLI/环境/默认入口启用。此能力不是账号在线/离线证据，单槽实例不与TTS混用；多能力须显式提供下节companion计划。宿主覆盖、pending、untracked_descendants和会话期限仍可否决启动；标准bootstrap的未跟踪后代否决未去除。关闭Core继续保活SnowLuma，父宿主仍保留该进程和阻断记录。此处未认证上游环境标志语义或完整账号生命周期，仍不代表生产就绪。

### 同一父宿主通道的显式双进程能力（非生产）

`LifecycleProcessBridge(..., slot="tts", fixed_contract=True, companion_plan={"argv": ..., "cwd": ..., "env": ...})` 可显式组合TTS主计划与SnowLuma从计划。两计划分别绑定slot、HMAC、环境及单次启动记录，SnowLuma从计划仍强制声明no-autoload。此时QQControl两类start/stop都仅取受信固定计划，不读本地模型/QQ配置或发现进程；同一通道和账号guard不拆分。配置两能力不授予同时启动权：首个子进程的untracked_descendants和覆盖撤销仍可否决第二个，退休也不恢复覆盖；本轮未为测试移除该否决。登录/恢复/接管、默认启用、健康和生产权威仍未授予。

### 默认QQ设置与路由提交的并发保护

默认 `save/save_routes` 写入现对原字节、操作内文件身份及迁移门禁做复核；独占随机备份与pending文件，flush/fsync后原子替换，只清理自身inode。插件reload在身份锁外；失败仅在当前仍是本次字节和inode时回滚，否则保留并发版本、报告失败且不重放reload。取消也尝试有条件文件回滚，但不自动重放插件加载。该机制仅是协作式CAS，不是OS原子比较交换、ACL/断电持久性或插件状态已恢复的证明；已有实验父宿主写入否决不变。

统一隔离验收的每个pytest组使用本轮独占短临时根（写入verification的test_root），不再使用pytest全局保留清理路径，避免顺带清理其它会话的旧测试证据；本工具不递归清理这些根。


### 已退役的视觉叠加入口

视觉叠加插件已从 Core 移除，其源码及旧专用测试保留在 Core 外的磁盘归档。
宿主配置别名、依赖、页面注册、健康轮询、安装/预览/配置路由不再注册。
旧 QQ 状态响应只保留恒定的禁用占位用于兼容已打开的旧页面，不读取配置或模型档案，
也不提供保存能力。通用图片输入、媒体校验、QQ 图片收发保持原合同。
历史版本文档和许可中的组件名称不是当前可用入口。

### 默认恢复配置本地持久提交

默认 Recovery 本地持久提交已使用有界 GuardedStore，并在实际 QQControl 中显式绑定
宿主根和 root/data 身份锁/迁移门禁。设置保存从旧配置读取、凭据保护到写入和清备份
保持同一协作锁与字节/inode 快照；原有固定 .tmp 不再使用。
固定 .json.bak 只替换/清除仍匹配本次快照的版本；外来同字节换 inode 也拒绝。
文件提交前复核取消/关闭代际。共享 GuardedStore 保留首个 IO/取消异常，
临时清理失败只补充类型 note。失败可能保留备份或不确定提交，不认证运行态回滚。
本节完成时运行恢复/登录仍沿用选定字段 revision；后续完整运行快照见末节。
本地持久提交不补齐完整账号父宿主授权，也不证明跨网络原子事务、ACL/断电持久性或生产可部署性。


### 默认 SnowLuma 所有权记录的协作持久边界

默认恢复记录按64 KiB合法对象及pid/绝对node.exe路径/创建时间结构读取，
不持身份锁跨CIM/原生句柄回调；核对安装身份、固定原生句柄并确认仍存活后，
重新检查原字节/inode/迁移门禁才接纳。未接纳句柄显式关闭；关闭失败保留待清理引用。
页面新建子进程记录使用同一有界文件快照、独占随机pending、fsync和协作身份锁，
不再写固定.tmp。持久提交失败保留原始子进程并禁止继续恢复，允许原句柄清理。
普通停止仅在成功观察/等待原持有进程后清除与该进程绑定且未改变的记录；
没有收据、并发换记录或显式安装目录停止均不无条件删除/冒充已退休原持有句柄。
Core关闭只释放自己的原生观察句柄，不停止SnowLuma；失败引用保留可重试。
这些是旧默认入口的协作防并发保护，不是可写JSON的宿主权限背书、
账号/后代离线、ACL/断电持久性或完整父宿主默认启用证明。实验桥否决保持不变。


### 默认管理/HTTP/WS条件信任快照

共享管理记录现使用有界字节/inode快照并复核root/data迁移门禁；CIM、原生pin及
监听表回调在身份锁外，不取控制/恢复/生命周期锁。自有/共享模式均在最后监听采样后及释放观察句柄后
复核关闭、门禁、原持有者与文件快照，监听阶段核对原生存活；严格拒绝bool PID、附加字段或非精确loopback行。
WS健康检查以同一个原生pin覆盖管理端与HTTP/WS两个监听采样，不再拼接不同pin时刻
的阳性结果。共享模式始终不接管停止所有权，也不写入基线记录。
原生观察器关闭失败保留引用、停止否决并拒绝后续信任；Core.close可仅清理句柄，
不会停止被观察服务。此结果只是条件OS快照，不是原子凭据发送屏障、TLS身份、
完整父宿主账号权限或生产启用证明；最后检查到实际网络发送仍有时序窗口。


### 默认恢复/导入的跨网络配置快照

`Recovery.run/connection_config` 仅在初次管理信任检查通过后建立线程局部的完整
原字节/inode主文件与备份快照；网络期间不持文件身份锁。后续读取、运行态校验及
持久提交复用同一快照，不能把中途出现的新文件默默重新当成原件；自有成功写入
更新收据，从而支持正常连续session/loginCompleted提交。未知字段改动、同字节
换inode、备份或路径绑定变化也拒绝。结束/取消/异常均移除线程局部快照与准入字段；
文件阶段失败使旧健康采样失效，不报告账号离线。此处仍是协作式检查，不是跨网络
原子事务、凭据发送屏障、恶意写者沙箱或完整父宿主账号权限/生产就绪证明。


### 默认启动器的只观测父宿主（非完整宿主）

默认 `startup.main()` 新建本轮子进程时现自动使用未封印的 `observe_window`，
只记录其原生出生与自有实例标记。没有空宿主回调或 `complete=true`；进入观察
窗口即永久撤销该 registry 覆盖，退出/retire后也不能在同一对象封印或恢复启动
权威。出生强制保留 `untracked_descendants=True`。
私有 HMAC describe 的 `instance_only=true` 选择专用 InstancePublisher；参数、
环境、WebUI/客户端 JSON 不决定模式。该 broker 只接 ping/describe/publish-instance，
不接账号准备、登录、进程 start/stop/retire；端口以外的路径/PID/Bot不可自选。
默认 launch 收到的是“实例发布器”而不是完整进程桥，因此不为了凑完整覆盖而
禁用原有 QQ/插件，也绝不将它们称为已经被宿主管住。正常默认环境继承保持；
测试只传无密钥的合成环境。旧服务仅保留原只读复用入口，不登记、接管或收尾。
普通 Web 的直接 launch.py CLI 已转交只观测 startup；程序化 main、终端/实验模式仍未完整覆盖。完整 bootstrap、插件/
账号/模型/核心工具控制面与发布验收仍未完成，生产启用仍为 false。

### 受控启动器自有实例标记收据（非生产）

受控 `launch` 的就绪标记只经私有 `publish-instance(port)` 交给父 broker；
子进程不能指定 PID、Bot、路径或删除操作。仅显式启动器 broker 开启此能力，
默认其它 broker 拒绝。父端核对已登记原生身份，独占随机临时文件+fsync 后
以硬链接原子“不覆盖”发布；不碰旧固定 `.tmp`，已存在的标记即使内容相同也
不接管。目标原字节/inode、目录绑定和不确定发布的收据保留在父对象中。
仅在本轮控制器原生缺席、登记行/字节/inode/目录绑定仍匹配时条件删除；
失败保留收据/登记行和并发标记。父端先收尾标记，再独立原生 retirement。
关闭通道或30秒会话到期不丢收据，也不删除仍存活控制器的标记。
需要支持硬链接的文件系统；没有破坏性 replace 回退。这是协作式检查，不是
内核原子条件 unlink、ACL/掉电持久性或全后代离线证明。pending、
`untracked_descendants=True` 和完整宿主阻断均保留。默认 startup 已接只观测
父宿主；普通直接 Web CLI 也走同一路径，但程序化/终端/实验入口不作覆盖声明，不把真实遗留标记升级为可接管权威。

### 受控 launch 装配：插件运行能力缺失时显式阻断（非生产）

受控 `launch.main` 在读取引擎/记忆前校验父认证 root、已有归属与 Bot scope；
缺少归属不自动认领，归属文件有界、拒绝重复字段/非有限值。它不再先
触发必定拒绝的 `plugins.wake` 而中途崩溃：装配无插件工具的 core，投影明确
`lifecycle=blocked,wired=false,complete=false`，不把空列表伪装成已读空宿主。
不读取插件清单、不预热/请求市场；插件安装、开关、卸载与 Infinity 操作在
锁/磁盘/网络前拒绝。仅 Bot 身份只读接口用于本轮自有控制器就绪核验。
受控分支不启动心跳/梦/跟进任务；父桥不授予账号登录、QQ或插件运行能力。
整个 core/WebUI 仍不是安全沙箱：模型/工具等其它控制面尚未做完整父宿主管理。
这是装配层的拒绝闭环，不是完整插件运行或可信空宿主 bootstrap；默认 CLI
保持原行为。隔离验收使用新临时根和合成 Bot/配置，不启动真实服务。

### 启动器受控分支的真实父通道接线（非生产）

`startup.main(host_registry=...)` 受控Popen路径现经独占继承消息Pipe接到父broker，
key/session只经原始匿名stdin有界前缀发送，不进argv/env/文件；数值句柄仅端点标签。
真实bot_launcher_child在导入launch/配置/插件之前完成有界前缀读取、认证describe和
metadata-only桥绑定，再把process_lifecycle传给真实launch.main。默认CLI仍不自动
构造完整宿主权威；受控分支无进程计划或factory，TTS/Snow运行/登录不可用，
已有插件confinement生效，账号元数据仍需父prepare ACK并留下pending。
启动就绪与watch显式泵父通道；丢失/超时不回退旧默认入口。出生仍标
untracked_descendants=True；会话不续期，30秒到期关闭元数据能力，父泵不因此终止
控制器；后续元数据写入拒绝而不回退本地。不是长期账号控制面、生产或完整插件运行覆盖。

### 普通直接 Web CLI 父通道（仍非完整宿主）

`python launch.py --bot NAME` 现在经 `launch.cli -> startup.main` 使用同一只观测父宿主，
不再直接运行无父收据的普通 Web 服务。默认不打开浏览器；`--port` 保留分配语义。
仅接受本 core/data 根；原来用于其他数据根的普通 CLI 现在显式报错，不悄悄切换根。
`--counterpart`、`--channel-context-v1`、`--cache-prefix-diagnostics` 经有界精确类型契约
转交，且父级认证 describe 返回的选项与子进程 argv 顺序和值完全一致；不得借尾参
覆盖 root/Bot/port 或加入账号/进程能力；console 仅经明确认证的普通启动模式接入。没有选项的旧五字段描述保持兼容。
直接执行 `bot_launcher_child.py` 必须有私有父端点；裸子入口在接触 Bot 或读取管道前拒绝。

该阶段最初不覆盖的 `launch.main()` 普通程序化调用与 `--console`，现由后续章节补齐；仍不覆盖
显式 acceptance/preview 实验路径及旧分发。它们保留原语义，不被标为父级覆盖。
QQ/插件正常行为未被禁用来冒充 complete；账号/模型/工具全链生命周期、可信 bootstrap、
生产投影、ACL/持久性及可部署发行验收仍待完成。源码/夹具通过不是生产就绪。

### 默认 QQ 插件工作线程退出（局部闭环，非账号离线证明）

QQ 内置插件现在串行化 start/stop；重复启动或部分启动失败未清理时拒绝重启。
停止只等待本实例保留的 WebSocket 线程与 CPython 媒体 executor 线程，共享 3 秒预算；
不扫描整个宿主。超时、线程身份不可用、自线程停止、client.close 失败均保留未完成状态
和可重试引用，不再 shutdown(wait=False) 后立即忘掉 executor 或超时后清掉 WS 线程。
线程存活时不提前关闭其 HTTP 客户端；退出确认后才释放资源。
默认 loader 对失败 stop 保留原实例，阻止重复装配。该改动没有禁用正常 QQ 功能，
也不代表所有同步发送/计时器/外部账号/后代受控，仍不是完整宿主 bootstrap 或生产就绪。

### 默认恢复 QQ 唤起记录（非进程/账号离线权威）

默认 Recovery 在实际调用 QQ 启动器之前写入本对象的单次尝试闩；默认 launch_qq 返回并
由 Recovery 保留 Popen。若启动回调返回前发生取消、关闭或配置改变，返回对象仍保留；
回调抛错或没有返回句柄则保持 uncertain，不把不确定结果当成“未启动”重试。
同一 Recovery 再次看到空 QQ 列表时不重复唤起；已经可见的 QQ 仍可走原管理验证路径。
进程返回/退出、cancel、save 或 close 不清除此闩，因为都不能证明全部后代/账号离线。
state 仅补充 launchAttempted/launchOutcome（unattempted/uncertain/returned），不返回 PID、
启动路径、句柄或新的账号权威；returned 不是成功登录。close 不停止或接管真实 QQ。
该记录驻内存、限本 Recovery 对象；新 Core/新对象及其他入口仍需完整宿主协同，
不是跨崩溃防重或生产就绪，不因此声称完成账号生命周期。

### 默认恢复的 QQ 目标原生快照（不是持有/接管进程）

默认 Recovery 在 POST refresh/load 前后，通过只查询所选 PID 的原生探针核对存活、
创建 FILETIME、QQ.exe 绝对映像及已配置的 QQ 路径；同一恢复操作首次快照绑定后，
后续请求和 online 发布前继续比对。未知、退出、映像/创建时间变化均阻断后续操作。
快照跨越 refresh 到 load，不把同 PID 的替换进程视为同一目标。无额外全宿主扫描。
测试探针仅经可信 Python 构造参数注入，客户端 JSON/标志/环境不提供豁免。
这只是前后探测，不是远端 PID API 与本地探针的原子事务；请求在途发生替换时，
后置失败仅说明结果不确定，无法撤销已发生副作用。没有给外来 QQ 停止/接管权限，
更不能证明 QQ 号归属、全部后代离线或完整宿主覆盖。原生正例 QQ 未实测；生产仍禁用。

### 默认恢复跨任务变更防重记录（拒绝元数据，不是完成证明）

refresh/load 在发送 HTTP 前，向本 recovery store 邻接的 `.process-attempts` 目录追加
无覆盖记录。只保存 store 绑定摘要、随机任务 nonce、原生身份摘要与操作名；不存
凭据、QQ 号、PID 或映像。当前任务获得可信成功 refresh 响应后才允许继续一次 load；
其他任务/新 Recovery 对象遇到记录、残缺/未知文件或竞争失败时拒绝重放。
取消、关闭、改设置、在线自报与原生进程退出均不清除记录；没有 reset/complete API。
只读在线检查仍可执行，不禁用插件或冒充完整宿主。记录保守地保留成功请求，未来再次
需要 refresh 的场景也可能要求可信宿主核验，当前不提供绕过此门槛的按钮或标志。

发布使用独占随机临时文件、fsync、无覆盖 hardlink，固定 store 父目录派生协作锁；
这是正常文件保留条件下的跨对象拒绝，不是 ACL 防篡改、目录断电持久性或远端事务。
同用户删除/伪造文件、快照回退、旧分发/其他入口、不同 store 或 root 仍需宿主协同。
HTTP 结果不确定不能回滚；原生前后快照不是远端原子 PID 绑定。生产仍不就绪。

### 默认 QQControl 跨 Bot store 的同一原生目标防重

实际默认 QQControl 固定向 Recovery 注入 core/data 的共享拒绝命名空间，不从配置、
WebUI 或环境变量选择。refresh/load 先在原生身份摘要的共享 slot 发布拒绝记录，再
发布本 store 记录，两者都成功才发送 HTTP。同一任务确认 refresh 后才能 load；
不同 Bot store、对象或普通重开进程不能再次刷新同一原生身份。局部发布失败、
取消及成功响应均不回滚共享记录；只读在线检查不清记录。运行中更换命名空间按
存储绑定变更拒绝。显式未传 mutation_root 的独立 Recovery 仍只有原 store 保护。

这是同一 core/data、协作入口与正常文件保留下的原生目标互斥，不是 QQ 账号唯一性：
新 PID/创建时间、不同 core/root、旧入口、同用户篡改/删除与远端幂等仍未覆盖。
无可信对账/清除接口，不提供 complete 标志或解除 untracked_descendants。共享锁、
fsync/hardlink 不等于 ACL、目录断电持久性或可部署发行验收，生产仍禁用。

### 普通 Web 的公开 Python 入口也走独立父进程

未注入生命周期/停机能力的 launch.main(argv) 不再直接装配普通 Web，而与 launch.cli
共用 startup 父进程、严格 core/data 与显式 Bot 参数及认证后的有界选项。非法自定义
root、缺失 Bot 或混入验收参数在装配/插件/模型启动前拒绝；父进程失败不回落到本地。
有显式私有 Python 能力的 child/controller 与验收/preview 保持各自原合同；普通 console 的独立父进程见后节。
内部 _run_local 只用于可信装配与测试，不是公开默认启动推荐，也不是恶意 Python 沙箱。

父进程仍只观察自己直接创建的 child，保留 untracked_descendants=true 和撤销完整覆盖。
未据此宣称任意私有入口、插件/模型/工具或账号生命周期已全部纳管，生产仍禁用。

### 默认恢复的配置账号预留（意图互斥，不是实际账号归属）

默认 QQControl 向 Recovery 固定注入 core/data 的账号预留命名空间；恢复在枚举/
启动 QQ、创建 HTTP 客户端或解密凭据之前，要求明确 targetUin，并将规范化账号
与 recovery store 路径的摘要做无覆盖预留。同一 store 可重验自己的记录；其他
store 即使选择不同原生进程也不能在相同命名空间下恢复同一配置账号。

取消、关闭、成功、改配置/清凭据与在线自报均不释放预留。记录只代表配置意图，
不是登录身份、OneBot 会话或全宿主完整性证明；不新增 release/reset/complete。
账号摘要可被枚举，属于可关联元数据而非匿名化，不应公开原始预留目录。
同用户篡改/删除/快照回退、不同 core/root、旧入口及未注入该命名空间的独立
Recovery 不在全局唯一性保证内。仍无可信迁移/对账/转移权威，生产不就绪。

### 默认 console 的独立父进程与分离控制管道

startup --console 与公开 launch CLI/main 的普通 console 现共用观察型独立父进程。
前文 Web 链路的控制 stdin 保持原状；console 的用户原始 stdin 只由子进程读取；启动密钥/会话与私有 Q 停机字节走另一条匿名继承
管道，不混入用户输入。console 模式由认证后的选项确认，数字 handle 只是标签，
未认证或混用 web/console 的参数拒绝。普通 Q/B 字符仍是用户消息，不是父进程热键。

子进程在实际 on_assembled 接口接入让出事件循环的 stdin 读法，避免同步读阻塞私有取消监视；此读线程仍不是完整后代/落盘证明。
父进程不读取终端，不建立 HTTP 就绪探针、不打开浏览器、不声称 console 有 Web
实例标记；原生 child 返回码保持原义。父进程 Ctrl-C 请求本次自有 child 协作退出，
超时/失败保留失败而非当作离线 ACK，最终仅清理本轮持有的进程/管道/Job。
公开普通 console 同样要求固定 core/data 与明确 Bot；显式 Python controller 与
acceptance/preview 保留原合同。这不是完整插件/模型/工具/账号或后代覆盖，
untracked_descendants=true 与完整宿主撤销仍保留；生产依旧未就绪。


### 默认私有 child 的插件退出边界（2026-10-24）

普通 Web/console 私有父进程路径在运行层 finally 中，对实际 `p_root`
执行 `plugins.shutdown_root`：先永久禁止该 root 的 carry/switch/wake，再逐个调用
由该 loader 确实创建、对象身份与 root 均匹配的实例 stop；不改持久 enabled 意图。
失败实例与线程引用保留，可重试 stop，不能重新开启 root。其他 root 不接管；
未知/被替换对象返回 unresolved，不靠读 package.json 或清空表伪造所有权。
即使插件失败也会取消并等待本次 tick，尝试 QQControl.close；清理失败不会被私有
取消接收器吞成普通 130，原取消保留为异常 cause，普通主异常保留并附清理 notes。

这是合作式 Python loader 的局部退出合同：同步 stop 可能阻塞，顶层 Thread
追踪不是全部后代清点。任意插件直接 create、外部 Python/模型/核心工具、装配期
早于运行 finally 的失败、完整账号对账仍须继续覆盖。显式内部 `_run_local` 无默认
私有 instance 能力时不自动取得 root 所有权。`complete` 始终 false，不解除
`untracked_descendants`，不代表真实 Bot 已离线、落盘或可部署发布。


补充：默认私有 `_run_local` 外层 finally 还会以已校验 publisher 的固定 core
执行同一 root drain，覆盖 wake 后但尚未进入运行 finally 的同步装配失败。
运行层已成功停止的对象不会重复 stop；保留失败可能在外层再尝试一次，但原失败
不因后一次成功而清除。receiver.close 与 sharing scope finally 仍会执行。
这只补齐默认私有入口的 loader-owned 插件失败清理，不表示装配期所有其他资源、
直接插件创建或全部模型/工具/后代已完整纳管。


### 默认核心工具批次退出（2026-10-24）

默认私有 instance child 的 Web/console run_tools 现在共用 ToolWork 包装：每次调用
在 handler/回执账开始前准入并创建自有 async task；限定单一事件循环及最多 128
份未退休任务。关闭先 seal，拒绝旧回调和新批次，然后只取消/等待自有 task，默认
最多等待 2 秒。成功/取消已结束任务退休，失败与超时任务引用保留；超时不等于成功。
工具 drain 失败仍尝试插件/tick/QQControl 清理，私有取消不能把该失败吞成 130。
外层装配失败也 seal 回调；内部显式装配不自动升级。此包装不是模型请求、同步
handler、子线程或进程后代的完整管理，更不是恶意 Python 沙箱。complete 始终 false，
不解除 untracked_descendants，不代表生产离线/落盘/发行就绪。

若工具取消后仍 pending，只 fence 插件 root，保留实例且延后 stop，避免关闭仍被
该批次使用的插件资源；runtime 与外层均不把此状态当完成。工具真正结束后才可
重试关闭，不能通过删任务/删实例放行。


### 默认模型请求退出（2026-10-24）

默认私有 Web/console 的 generate 回调现使用独立 ModelWork（最多 32 份未退休
模型 task），包装的仍是真实 telyincore.generate.generate；console 的媒体预算、
协议适配和观察层仍在原位。请求 body/HTTP stream 开始前准入，退出同时 seal
模型与工具 scope，再各自取消/等待自有 task；失败/超时保留，不以丢引用放行。
任一 scope pending 时只 fence 插件 root，不提前停止可能仍在使用的插件资源。
外层装配异常也封住已捕获回调，错误/取消 cause 保留。scope 只纳管 generate
协程，不获得 Httpx client、Local native 进程、任意子线程/后代的完整所有权；
现有 client close 路径未因此升级为完整 transport 离线证明。complete 仍 false。


### 默认 Local native 模型控制（2026-10-24）

默认私有 Web set_gate 不再调用旧 cmd /c start 或按端口 netstat/taskkill 路径。
暂停只终止本 invocation 实际创建并保留 Popen 的 native child；HTTP ready 只能
证明可用性，绝不据此接管已有服务。显式内部/legacy Python 入口仍是不同合同，
未宣称其全部覆盖。新自动启动声明必须显式 native_argv_v1=true，argv 为绝对本机
.exe + 参数数组，可选 cwd 为绝对目录；旧 start/bat 不自动迁移或执行。禁止 shell
解释器启动包装，argv/cwd 在首次创建时固定，变更合同不替换进程。
例如合成格式 {"native_argv_v1":true,"argv":["D:/SYNTHETIC/model.exe","--example"],"ready_seconds":20}。
readiness watcher 是本 scope 自有 async task，退出/暂停使旧代次失效；超时不丢
child/句柄、不自动重复拉起。创建不确定失败为 sticky fault，不能重试掩盖。
退出先 seal，模型/工具 pending 或插件清理未确认时不提前终止 Local child，保留
对象报告 unresolved；确认安全后才重试。实际创建的 immediate native child 已退出
不等于模型全部后代/HTTP listener/账号离线，complete 仍 false，不解除
untracked_descendants。未读取或迁移真实本地模型配置、未操作真实服务。

实际 WebService 的 gate 请求来自 HTTP worker 线程：默认 callback 先通过绑定到
运行层的 owner loop 队列执行，不在 worker 里直接调用 asyncio。请求等待默认 4 秒，
排队未执行的超时会取消，不能迟到再创建进程；已运行而超时为 sticky unresolved。
新增加的真实自建 loopback WebService /run、/pause 反例覆盖该线程边界，不代表
线上端点验收；没有操作已有监听服务。


### 默认 generate stream 关闭确认（2026-10-24）

默认 ModelWork 现对交给真实 generate 的每个 stream 建立精确 lease；先保留创建
槽，再调用已有 transport.stream_chat，不改写 URL/body/header，不接管借用的 client。
只有该 stream.aclose 真正成功且从未失败才退休 lease。generate 在取消/主异常时
优先保留主因可能吞掉 aclose 次错，lease 会独立留下该失败，不能把 task.cancelled
当作 socket/stream 已完成；重调 close 也不清故障或删 pending 放行。
已无请求 task 但仍有可关闭 stream 时，scope 以自有 close task 有界尝试关闭；
若请求仍在使用 iterator，不并发强关。关闭任务和失败引用均保留。模型/工具请求
pending 或 stream/close 仍未确认时，runtime/外层都只 fence 插件、延后 Local child
停止。此合同只到被包装 stream 的 aclose ACK，不证明所有适配器内部响应、Httpx
client/连接池、socket、全部后代或真实账号离线，complete 仍 false。


默认恢复的“已在线”分支同样核验选定 QQ PID 的原生身份：即使无需 refresh/load，
也不能只凭管理进程列表的 status=online 发布账号健康。首次 pin 后在样本发布前复核
PID/创建时间/QQ.exe 路径；未知、替换、取消或配置/信任变化不发布在线。
这是管理观察与原生身份快照的局部交叉检查，不是 OneBot/WebSocket 业务确认，
不授予 QQ 进程、账号、完整后代或宿主生命周期所有权，不清除未决记录。


默认 QQ 插件对本实例已观察的 self_id / backfill get_login_info 账号声明做否决式交叉检查。
两个有效声明不一致时，永久封口当前实例：停止后续入站/分享发布和 HTTP 动作，撤销旧 QQ
事件的 current 资格，取消未发布队列，WebSocket 离开自有连接且不自动重连；干净 stop
不清除此冲突，不能在同一对象上 restart 洗白。后台任务/客户端仍须显式 stop 排空。
首个声明不是可信账号证明；缺失/非法 legacy self_id 仍是 unknown，不升级为已验证身份。
此机制不授予进程/账号/宿主所有权，不代替端点验证、账号排他或原子远程操作。


Recovery GuardedStore 的新 primary/backup 暂存文件现在在创建瞬间使用私有文件策略：
Windows 为进程用户显式 owner、仅该 SID 与 SYSTEM 的 protected DACL、CREATE_NEW、
不继承句柄并直接把同一 native handle 转为 Python 文件；失败不退回普通 inherited ACL。
非 Windows 独占创建 0600 文件。随后原有 fsync/guarded replace 保留该新文件权限。
不批量读取/改写旧真实文件 ACL，不改变目录 ACL；这不保证管理员/所有者无法另行改权，
也不等于目录持久性/断电恢复、全部其他存储入口或生产就绪。


QQControl 的默认配置写入、令牌原字节备份、reload失败后的条件回滚以及实验
父宿主账号写入路径，均复用 open_private_new 的私有创建策略；不再让新副本继承宽松
目录ACL。原有身份锁/CAS/reload条件回滚与父宿主撤权前置不变；安全创建失败无普通
open fallback。不批量改权旧真实文件，不证明全应用目录/旧副本/断电持久性或生产就绪。


L1 默认Windows私有启动入口现于 ParentLauncherChannel.handoff 之前创建并核验原生
ChildJob：精确Popen handle的job membership、原有KILL_ON_JOB_CLOSE限制和job句柄
不继承均须确认，失败不发送私有准入帧。构造失败的job随异常保留，close失败不清句柄。
这将原有“关闭启动器job会终止其成员”的策略从私有应用入口开始生效；QQControl.close
自身的保活策略不能被解释为能逃过启动器job关闭。可持久保活的SnowLuma边界仍待L2
明确分离/实现，不能据此宣称完整账号离线或生产就绪。Python解释器初始化/assignment
之前的后代及job外服务依然不确定，untracked_descendants保持，L1整体尚未完成。


### L1 有限启动出生 profile（本地实现，非生产放行）

默认 `startup.py` Web 与 `--console` 的新控制器使用 `windows10-x64-cpython311-job-list-v1`：Windows 10+/Server 2016+、CPython 3.11 x64。其他解释器/平台与未知 spawn 参数显式拒绝，无普通 Popen 回退。固定参数仅支持绝对可执行文件/cwd、显式环境、受限句柄名单和既有二进制管道。

`bot_birth_process.BirthPopen` 将现有非继承 KILL_ON_JOB_CLOSE 作业以 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 传给原生 CreateProcessW，并使用 CREATE_SUSPENDED；保留精确进程/主线程句柄、核验作业成员/限制后才 ResumeThread，解释器初始化亦不先于验证。保留 Popen 管道/wait/poll 语义，不全局替换 subprocess。属性或核验失败不 fallback，异常持有本次 job/进程/线程，默认入口清理自身资源；关闭失败保留原句柄。

这不是未知插件/外部服务的全宿主权威；仍保留 untracked_descendants、production_enabled=false。L1 的完整支持入口权威尚待验收；L2 的 SnowLuma 保活与作业关闭隔离尚未完成。出生作业继承可能包含本次控制器的系统控制台辅助进程，不能将作业 active 数量等同 Python 控制器数。真实 Bot/QQ/模型未运行，源码候选也不是可部署发行版。

原生接口依据：[Microsoft UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)，[CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)。文档不替代本机隔离反例。


### L1 支持入口宿主会话（有限本地合同）

默认支持的规范入口仅 `startup.web` 和 `startup.console`：`startup.main`、普通 `launch.cli` 与无显式能力的 `launch.main` 均路由到这两种模式。实验性自定义 Python capability、`_run_local`、acceptance/preview、任意脚本/模块、任意插件外部管理入口不因此获得该合同或完整覆盖。插件生命周期沿用实际 root 的 fence/task-drain/shutdown_root；正常原生子进程继承出生作业，但外部已有服务、RPC触发的外部进程与任意可信Python绕过仍未知。

`bot_launcher_host.LauncherHost` 在出生前冻结规范 argv/core/Bot/mode/控制端点和已知子入口源码像；拒绝未知入口、`-c/-m`、错根或已修改的入口文件。出生后只接受真实 `BirthPopen` 的原始句柄/作业/命令/cwd/stdio与继承白名单收据，重新核验原生作业及入口像，绑定成功后默认启动器才发送私有应用准入frame。源码像是运行中的可信宿主本地一致性条件，不是签名发行版、任意恶意Python沙箱或防止绑定后的磁盘变更的承诺。

退出沿用既有强制终止自有作业策略，但不再把 CloseHandle 当后代退出证明：保留原作业，明确 TerminateJobObject，再在有界时间内确认本作业 accounting=0、精确 controller.wait 成功，最后关闭句柄。本地 retired 仅限该出生容器；未知后代/查询失败/超时/关闭失败进入 sticky pending、保留资源并拒绝自动重试。仍保留 untracked_descendants、全宿主 complete=false 和 production_enabled=false，不能据此改名/接管真实Bot。

本地 L1 的出生、准入、容器排空与有限入口合同不代替 L2 账号/SnowLuma生命周期，不代替 P1-P3 真实服务与隐私边界验收。


### L2 管理登录的未决生命周期（局部实现）

实际 `Recovery.run` 在 QQ 枚举、client 创建或凭据解密前核验固定 store 的 `.login-attempts`。`POST /api/login` 前私有持久化 intent，配置修改、cancel/close、新 Recovery 实例和已保存的 session 都不能清除未决 intent；同 store 并发登录仅一个 owner。`connection_config` 也必须先通过相同门禁，不能拿已写入但未完成提交的 session 绕过未决状态。

只有成功返回的管理 session 已通过原受保护 store 写入、`_recover` 的 HTTP client context 成功退出、配置 revision 仍一致，才为本任务写入本地 committed 收据。管理鉴权/账号在线采样在此之前延迟发布；客户端关闭、请求、配置写入或提交未知时不能先显示在线。committed 只是这次本地管理登录流程已收尾，不是 QQ 账号归属、OneBot 握手、进程所有权或离线凭据。

记录不存密码/token/账号明文，仅存 owner、store/revision 指纹（不是匿名化或权威证明）。采用已有 private CREATE_NEW、fsync 后独占发布；部分文件、未知 metadata、坏记录、缺 intent 的 commit、关闭不确定均 fail closed。最多32次已确认本地登录，达到上限必须可信宿主对账；无 reset/release/伪造 complete 接口。现有同号跨 store 意图预留与刷新/加载 journal 保留。

L2 尚未整体完成：持久 SnowLuma 与启动器出生容器的分离、宿主账号授权/撤权及完整排空链路仍待落实；本改动不替代这些工作，不把真实账号/P1验收标为通过，production_enabled 仍 false。


### L2 默认管理 HTTP 客户端的实际准入与排空

`Recovery.run` 与 `connection_config` 在枚举 QQ、解密/发送 session、创建 client 前取得该 store 的唯一私有 HTTP 生命周期标记。另一个 facade/进程或上次未确认退出的客户端存在时，不新建连接。标记本身不是账号所有权；只用于本次默认管理操作的资源互斥。

支持的客户端为实际 `httpx.Client`（可用 MockTransport 验收）；保留工厂返回对象和 `__enter__` 返回对象。未知类型/变更身份、构造/进入/退出失败、未确认 `is_closed`、标记被换像均保留 pending，不自动二次 close、不在 facade.close 中丢弃原资源。仅该对象一次成功 `__exit__` 且确认 closed 后，才能按原 inode/字节删除自己的活动标记；工厂根本没调用时可明确释放 unused lease。此释放不清理登录 intent、刷新/加载 journal、同号预留或任何 untracked_descendants。

缓存 session 和新登录都延迟发布管理鉴权/账号采样，必须 HTTP client 排空后才发布；正文错误与关闭错误并发时保留原异常及 exact lease，不让次要关闭错误掩盖原失败。资源标记恒为一个活动工件，不为成功的只读验证不断增加历史/次数限制。关闭确认属于本地 HTTP 库资源合同，不是 QQ/OneBot 离线或全宿主资源证明。

L2 的账号宿主授权/撤权与持久 SnowLuma 所有权分离仍未整体完成，生产就绪仍 false；不得据此接管/停止真实服务。


### L2：独立 companion 原生所有权（宿主显式能力，尚非默认保活闭环）
`bot_independent_owner.IndependentProcessOwner` 由仍存活的外层宿主持有，
只接受其 exact BirthPopen controller 与冻结的显式计划。出生前核验宿主不在 controller
作业内；companion 使用独立出生作业，不关闭或放宽任一 KILL_ON_JOB_CLOSE。
controller 作业退役不终止 companion；宿主仍须持有 owner，不能退出或把 PID 当作接管凭证。
实际 FixedProcessFactory 可绑定这一 exact owner；失败保留原生资源，禁止重试。
broker 的 retire 先要求根进程退出，再排空独立作业及输出线程，最后才能移除登记。
这只实现父宿主独立进程能力：默认入口的能力交接、宿主长期保活/重连与账号授权撤权
尚待接线。不能称默认 SnowLuma 已可持久保活，更不能称账号已离线或生产就绪。
所有验收只启动新根自建惰性 Python 进程，不运行真实 SnowLuma/QQ，不读取真实配置。


### L2：默认入口的独立 companion 私有交接（账号闭环尚未完成）
默认 Windows 原生启动器现在通过继承私有管道明确交接 `companion_supported`。
`InstancePublisher` 与实际 `launch → QQControl` 路径使用宿主固定的 SnowLuma/Node
计划：不让子控制器发送 argv、环境、PID、配置或凭据，不在子控制器内回退 Popen。
未使用过 companion 时不探测安装目录或 Node；实际请求时由宿主选择固定安装入口、
冻结镜像/源文件并检查，使用仅显式原生环境与 SNOWLUMA_HOOK_AUTOLOAD=0。
默认子控制器不再按磁盘 PID 记录接管外部进程，外部安装 stop_installation 不在此能力内。
同一次启动单次准入；未知回包不自动重启。停止/退役由宿主持有的独立作业排空后确认。
原有实例发布仍有30秒期限，独立 companion 能力只在该原生 controller 仍受私有会话
绑定时处理四个固定操作；这不续约实例、账号或完整宿主权威。
关闭 QQControl/控制器/私有通道不自动停止 companion。默认父启动器在退役控制器和
清理实例/输出后继续存活，持有独立 owner，直到 companion 自然退出后排空余留后代。
因此启动器窗口/宿主必须保持存活；宿主崩溃或被操作系统关闭不在保活保证内，
KILL_ON_JOB_CLOSE 不变。本阶段尚不提供跨宿主重连/重新接管；不能凭旧PID改写归属。
账号实际授权、撤权与完整排空仍未接入此默认宿主，L2仍未完成；P1真实账号核对、
真实服务与Node/Snow部署联调均未执行，production_enabled仍为false。


默认父宿主的 companion 现在在读取计划/出生前创建私有持久 custody 标记，
同一安装目录的第二宿主（包括控制器退出后的重开）不能并发启动或凭 PID 重接。
只有原 owner 确认独立作业/输出排空、且原标记字节和 inode 未变时才释放标记。
关闭控制器/RPC、宿主崩溃、依赖或出生不确定都不清标记；不提供过期、重置或
自动删旧标记 API。该 custody 仅是有限安装资源的失败保留，不是账号登录/离线权威；
默认账号授权/撤权仍待实现，P1-P3、complete=false 与 production_enabled=false 不变。


默认账号撤权现在由父HOST固定Bot作用域派生，私有RPC不接受账号、路径或凭据。
QQControl.save_recovery先等待父HOST私有持久撤权，再进入原本地Recovery保存；
撤权失败不读取/加密/备份新凭据。后续实际管理端信任判定先询问父HOST是否已撤权，
撤权/未知拒绝发送凭据；同一Bot的新宿主及新companion启动同样被持久撤权拒绝。
撤权ACK仅表示拒绝已落盘，不表示worker、HTTP、QQ、账号已排空或离线。
当前没有恢复授权/清除撤权API，保存后需后续可信宿主对账实现；L2仍未完成，
不能把无撤权记录视为真实账号授权。旧实验/直接库调用不据此扩大能力。


默认父宿主account-check现已要求正向本地操作准入：父HOST自行读取固定Bot恢复存储，
绑定明确目标/用户名/QQ路径/保护凭据版本、当前exact controller与独立companion作业，
并在私有固定账号admission记录CREATE_NEW/fsync。请求帧不含目标、路径、凭据或revision。
同账号的另一HOST/存储不能借配置意图预留放行；当前对象每次重验版本/标记inode/原生资源。
登录更新session不改变目标配置准入，账号/凭据变化、资源死亡、未知/部分记录保持pending。
记录没有释放/过期/reset API，不因退出或停止进程推断账号已离线；完整grant退役、
在途操作排空及保存后可信对账仍待实现，L2仍未完成，production_enabled=false。


默认Recovery.run与connection_config现在先向父HOST登记本次account-work，再创建本地
HTTP/QQ工作资源。父HOST在准入回包前私有持久登记，一次未排空禁止同对象重入；
未知/部分/失联不自动清理。真实HTTP上下文确认关闭且没有未决QQ唤起句柄后，受信
controller仅确认本轮本地资源结束，父HOST核对原work记录字节/inode后退役该work。
这不删除账号grant、撤权、login intent或mutation journal，不表示外部账号离线；
撤权后也可收取原work的本地排空回执，但不能因此恢复许可。完整账号grant退役、
QQ唤起资源的宿主化排空和保存后的可信对账仍待实现，L2/生产均未就绪。

默认受限 HOST 的 QQ 唤起：Recovery 仅请求固定 qq-start（不传路径/PID/命令），父宿主从已准入合成/配置意图的 qqPath 固定 QQ.exe，并保留独立出生 Job。只有本 HOST 新建 QQ 的显式 Recovery.close 或 controller 退役才排空该 Job；不停止预先启动的 QQ。原生后代及输出排空前不确认在途 work；回包/出生/排空未知保留拒绝，不凭 root 退出或 HTTP 关闭清除账号账本。此处仍非外部账号离线或完整 L2 对账权威。

默认恢复工作完成与QQ排空分开：已确认本轮HTTP关闭、父宿主持有同一原生活QQ时，account-work-finished只结束这次工作，保留原QQ Job和账号grant，不宣称QQ/账号离线。后续工作须重新准入和重验，未知回包/资源漂移拒绝。显式close仍负责原QQ实际排空；account-work-drained维持严格原生退役语义。

首次默认恢复设置：仅同一尚未尝试启动/工作/账号准入的原生HOST，在固定恢复store及历史同名前缀记录原先不存在时，可在本地保存后校验固定配置、无既有同号grant、原fresh fence字节/inode并退役本次设置门禁。失败/crash/旧fence/历史账号或副本保留撤权；不是清除旧账号状态或外部对账API。已使用账号仍不可经保存自动恢复权限。

同一未使用HOST内可连续编辑其已确认初次设置：父宿主保留主文件与固定备份的有界hash/inode，下一次保存前须完全匹配，保存后固定备份须等于已知主文件前像。无原内存receipt、新HOST、外来文件/备份、未知历史或已开始任何账号/进程工作仍保留撤权；该许可不覆盖已使用账号对账。

默认SnowLuma按代际重启：正常stop或原root自然退出后，必须由原native owner确认整Job/输出排空并退役原custody；固定私有请求携带HOST生成的代际nonce，旧facade不能控制新进程。同一HOST可保留同账号admission并仅重新绑定已验证的新Snow原生资源，不删除grant/login/mutation、不宣称账号离线；未结束work、撤权、配置漂移、未知出生/回包/排空仍拒绝重启。QQ原程序按既有策略保留，仅确认原HOST持有时可跨Snow重启。

最终HOST本地账号能力退役：仅原controller已退出、原Snow与QQ原生资源均确认排空、work无未决时，保留原account admission并私有写入local-retired终态收据，拒绝复用本对象；收据明确external_account_offline=false。旧账号预留/登录/变更/撤权不清除，新HOST仍不能凭本地退役接管同号。未知文件/排空/work/写入失败保留pending，不自动重试。普通Snow停止/重启不触发此终态。

已准入同HOST保存未改变账号/凭据/QQ路径的设置：先重验原grant/native/无work、HTTP/登录/refresh-load未决，再fresh撤权和实际Guarded保存；仅固定revision不变、原admission hash/inode未变、backup等于已知主前像时退役本次fresh gate。同号预留不删除，真实账号/密码/路径变化仍保留撤权待对账，旧fence或未知状态不自动恢复。

同一已准入账号可在原活HOST无work/HTTP/登录/refresh-load未决时更新管理用户名/凭据：原account digest与QQ路径须保持，fresh撤权和实际保存后父私有保存admission前像，再原子替换其revision，保留原账号名/nonce/owner，验证后仅退役自有fresh gate。更换目标账号或QQ路径仍拒绝；未知写入/并发/回包保留pending，账号预留和登录变更历史不清除，后续登录另走原既有ledger。


## 本地语义检索与旧版升级

Windows 启动入口与 Linux `--acceptance` 入口会自动检查 CPU 依赖和 BGE-M3；
旧部署覆盖程序更新包后，也会重新检查，不沿用缺少模型的旧“环境已就绪”结论。
首次需要约 2.3 GB 模型及推理依赖下载空间。模型优先从 https://hf-mirror.com 下载，
失败回退 Hugging Face 官方源。固定上游版本与逐文件哈希校验一致，推理离线执行。
模型保存在 `core/models/bge-m3`，程序更新保留此目录。旧部署的 `models/bge-m3` 会在启动器自检时校验并迁移，复用已有权重。
可用本部署 Python 执行 `core/scripts/setup_semantic.py --source 已有模型目录` 导入；
`--check` 只检查，不下载。`CORE_EMBEDDING_MODEL` 可指定本机模型目录。

知识、做法和可检索记忆在运行时后台增量建立向量；内容不变时复用，修改后补建，
删除的资料不会再作为候选。长文分块覆盖，向量是可重建的派生数据，不改动记忆原文。
补建暂未完成或模型故障时，字面检索继续工作，并明确报告语义检索覆盖不足。
`kb_search` 和 `memory_search` 在字面检索无结果时使用语义候选，仍保留可见范围、
原文引用及“候选不等于已证实事实”的提示。模型安装不消耗聊天 API tokens。

### 分发包更新提醒

启动器在唤醒自检时核对本平台的 GitHub 版本公告，从控制台唤醒也会检查。Core 运行期间每 30 分钟自动检查，即使控制台页面已关闭。点击顶栏右侧的「更新」可查看本机版本并手动检查；发现新版时，入口显示新版号，可打开发布说明。Windows 和 Linux 各自比较版本。若官方 Release 附有适用于本机起始版本的增量包，控制台会显示「下载更新包」。下载期间可继续使用 Core，安装前请正常退出服务。网络暂时不可用时可以照常使用 Core。

发版时，在 `AR15MOD3/TelyinCore-Releases` 发布一条 Release。Windows 用 `win-beta.0.5.4.1` 这样的标签，Linux 用 `linux-beta.0.1.0` 这样的标签，数字须与对应包内 `candidate.json` 的 `version` 一致。发布说明写明适用的升级起点、群文件名和校验值。已公开的预发布版本也会参与比较；草稿和只创建了标签的记录不会触发提醒。
