# 隐私与已知限制

> **适用版本：** App `0.1.1b2` Windows-only 公开 beta。本文是产品边界的公开说明，
> 不替代 [`SECURITY.md`](../SECURITY.md) 的安全报告流程。

## 隐私边界

### 网络与本地数据

- 产品只绑定 OS 选择的 `127.0.0.1` socket，不监听外部网卡，不提供远程访问。
- 论文 PDF、解析文本、结构化产物、Agent payload、log 和 App state 都留在本机。
- 浏览器只接收 workspace option ID、record ID 和界面展示数据；服务器不把文件绝对
  路径、ACL 或 Core authority object 发给浏览器。文件夹选择通过原生 helper 完成，
  浏览器只拿到短时 opaque lease 和显示标签。

### Token 与会话

- startup token 是一次性的，只打印在控制台；不进入 URL、日志、浏览器存储或 App
  配置。
- session/CSRF token 保存在服务端内存；不持久化到浏览器或磁盘配置。
- 不要在日志、issue 或公开材料中粘贴 startup/session/CSRF token。

### 凭据与外部 Agent

- App 不保存模型账号、API key 或外部 Agent 登录凭据。
- App 不在内部启动 Agent。用户自行把 App 生成的 prompt manifest 交给 Codex CLI 或
  Claude Code CLI，再把单个 schema-bound JSON 导回。
- 受限内容的一键剪贴板复制只在 Windows clipboard history 和 cloud sync 都被确定性
  关闭时允许；否则 fail closed，改用 create-only 本地 task package。
- workspace 的 `agent_policy` 决定哪些内容类别可以进入任务 payload；浏览器不能放宽
  该策略。

### Egress 与更新

- egress policy 中 `telemetry`、`update`、`support_upload` 均为 `disabled`；当前版本
  不提供遥测、自动更新或自动上传。
- 主动对外访问只有用户显式触发的 Europe PMC Discovery/OA 获取；这些请求只发送该
  功能的 metadata/检索内容，不附带本地论文或 workspace 数据。

### 报告问题时的脱敏

按 [`SUPPORT.md`](../SUPPORT.md) 和 [`SECURITY.md`](../SECURITY.md) 的要求，删除或
替换 token、凭据、绝对路径、源文本、Evidence quote、研究笔记、workspace export、
机构信息和外部访问链接。复现优先使用 synthetic `p2-small` fixture，不要上传真实 PDF
或真实 workspace 材料。

## 已知限制

| 限制 | 说明 |
|---|---|
| Windows-only beta | 当前只声明 64 位 Windows 和 CPython 3.11/3.12；macOS/Linux 未验收。 |
| 无桌面安装程序 | 使用 `pip install research-kb-app==0.1.1b2`，不提供 MSI/EXE 安装包。 |
| 物理 sleep/resume | 不在当前 beta 支持承诺内，需 beta 后另行验证。 |
| 不执行 migration/cutover | legacy CLI workspace 仍是 source of truth；不迁移、不 write-freeze、不切换生产 workspace。 |
| Discovery 只接 Europe PMC | 其他文献检索源不在首版范围内。 |
| Obsidian 单向同步 | 只生成受管目录视图；不反向导入 Markdown，不做双向合并。 |
| Exchange 不自动语义合并 | 导入记录默认 immutable `external_unreviewed`，不会自动成为本地事实依据。 |
| 无 hostile-PDF sandbox | PDF 与解析文本按不可信数据处理，但不提供独立沙箱、进程/网络隔离承诺。 |
| 无内置 Agent/LLM | 语义任务由用户交给外部 CLI 执行，App 只交接、预览和审批。 |
| 验收证据未闭环 | 全新 Windows 账户干净安装和 headed GUI 观察尚未完成，因此尚未宣称 `Windows public beta accepted`。 |

## b1 -> b2 修复说明

`0.1.1b1` 的干净公开安装会把 `charset-normalizer` 解析为与受审 lock 不同的版本，
导致 Core dependency profile 校验失败。`0.1.1b2` 保持受审
`core-compatibility.json` 不变，并把 Core PDF closure 的 16 个发布依赖以精确 pin
写入 App 元数据，使干净安装得到与受审环境一致的依赖闭包。该问题只在 b2 中修复；
b1 不应继续使用。详情见 [`CHANGELOG.md`](../CHANGELOG.md) 和
[`docs/support-matrix.md`](support-matrix.md)。
