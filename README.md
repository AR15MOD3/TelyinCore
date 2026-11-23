# TelyinCore

Windows 与 Linux 共用 `core/` 中的引擎、记忆、模型管理和 Web 控制台。Windows 的启动逻辑在 `core/scripts/windows_acceptance.py`，Linux 的启动逻辑在 `core/start.sh`。发行包、版本公告和按平台区分的更新附件请见 [Releases](https://github.com/AR15MOD3/TelyinCore/releases)。

## 使用控制台更新

启动、唤醒和运行期间每 30 分钟会检查发行版本。点击顶栏「更新」可手动检查。有适用的增量包时，点击「立即更新并重启」。Core 校验下载文件和本机版本，正常关闭当前服务，替换程序文件，然后在原端口重新启动。Windows 多 Bot 部署请先正常休止其余 Bot。

首次部署优先复用本地模型和离线依赖。插件市场、模型、运行环境和更新包默认直连镜像，失败后尝试官方源。官方连接遵循设置中对应的市场或下载代理开关。更新包的 SHA-256 由 GitHub 官方提供。

旧版只有下载入口，首次切换需要按对应发布说明升级一次。主要程序文件经过本地修改时，更新器会显示冲突并保留原文件。Bot 档案、聊天记录和已下载模型位于各自的数据目录。

## 开发与发版

在 `core/` 下使用项目的 Python 依赖和 Node 前端工具。前端构建入口为 `node scripts/build-web.mjs`。Linux 源码提供普通桌面启动入口和 OneBot 服务接入。SnowLuma 等第三方原生组件按各组件许可单独提供。项目许可见 [LICENSE](LICENSE)。

一次迭代验收后，将两端共享的源码提交到本仓库。普通提交供开发使用。将该平台上一次完整部署包和从本次提交构建并验收的完整部署包交给发布工具，按示例执行。发布机需已登录具有此仓库写入权限的 GitHub CLI。

```bash
python3 release_tools/publish.py 上一版完整包.zip 本版完整包.zip --out /tmp/core-release --notes-file 发布说明.md --publish
```

发布工具将标签指向当前提交，并上传增量 ZIP 和校验文件。Windows 标签形如 `win-beta.0.5.4.2`，Linux 标签形如 `linux-beta.0.1.2`。下载入口按平台、已安装版本和目标版本匹配。若要支持跨过多个版本，可从对应旧基线构建额外增量包，上传到同一 Release 并将 ZIP 的附件标签设为 `core-online-update-v1`。未提供直升包时，用户可按发布说明逐版升级。
