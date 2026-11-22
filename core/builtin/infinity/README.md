# Infinity 内置插件

此目录集中保存 TelyinCore 自带的 `core-infinity` 插件源码：

- `core-infinity-qq/`：QQ 连接插件包，当前提供私聊、群聊文字收发。
视觉叠加已退役，源码在 Core 外的磁盘归档保留，不再提供包内安装入口。

插件管理页从这里读取内置源；安装时把单个包复制到项目根目录的
`extensions/<包名>/`。运行配置写在安装副本中，源码目录不保存令牌。
SnowLuma 是独立运行的 QQ 协议端，源码见 https://github.com/SnowLuma/SnowLuma 。

## 许可

当前官方内置插件采用 [《TelyinCore 非商业使用与受限分发许可》1.0](../../LICENSE)
（`LicenseRef-TelyinCore-NonCommercial-1.0`）。这是受限源码许可，不是 MIT、GPL 等 OSI 标准开源许可，也不是 `Unlicense`。
第三方插件、模型、依赖与运行时仍按各自许可。商业使用、未经授权的修改与再分发需要另行授权。
