# 支持矩阵

> 本矩阵描述 `research-kb-app` 公开仓库当前可见版本的支持边界。`公开 beta` 不构成
> 生产可用性承诺或服务级别协议；`accepted` 状态以维护者在发布记录中列出的证据为准。

## 版本状态

| 版本 | 状态 | 说明 |
|---|---|---|
| App `0.1.1b2` | **当前公开 beta（pre-release）** | 已发布到 [GitHub Releases](https://github.com/ZhangChengwei0722/research-kb-app/releases/tag/v0.1.1b2) 与 [PyPI](https://pypi.org/project/research-kb-app/0.1.1b2/)；包含 Core PDF closure 固定 pin，修复 b1 的 clean-install 依赖漂移。 |
| App `0.1.1b1` | **已取代，不推荐** | 已标记为 pre-release 并在 GitHub Release 页面标注被 b2 取代；PyPI 上的旧页面在维护者按平台流程完成处理前仍可能存在，请勿继续安装。 |
| Core `0.1.1` | **当前固定依赖** | App 元数据固定 `research-kb-core[pdf]==0.1.1`；不接受其他 Core 版本或本地替换。 |

b1 到 b2 的安装路径见 [`docs/installation.md`](installation.md)。已发布的 b2
wheel/sdist 字节不会再被替换或重建。

## 平台与运行时矩阵

| 维度 | 支持状态 | 说明 |
|---|---|---|
| Windows 64-bit | **支持（beta）** | 当前验证平台；要求本机 NTFS 和受保护 ACL。 |
| CPython 3.11 | **支持（beta）** | CI 与 clean-install 验证覆盖。 |
| CPython 3.12 | **支持（beta）** | CI 与 clean-install 验证覆盖。 |
| CPython 其他版本 | 不支持 | `requires-python >=3.11,<3.13` 之外拒绝安装或运行。 |
| 32 位 Python | 不支持 | 未发布对应 wheel，也未验收。 |
| macOS | 暂不支持 | 尚未完成与 Windows 同等级别的正式 App pilot 和验收。 |
| Linux | 暂不支持 | 当前只声明 Windows-only beta。 |
| 远程/网络部署 | 不支持 | 产品只绑定 `127.0.0.1`；不提供远程部署承诺。 |

## 功能支持状态

| 功能 | 状态 |
|---|---|
| 受管 setup、workspace 创建/采用 | 支持（beta） |
| PDF 导入、Registry、Parse、Source Adequacy | 支持（beta） |
| Agent 交接、导入、预览、审批 | 支持（beta；Agent 由用户外部运行） |
| Evidence 回源、阅读、报告式问答 | 支持（beta） |
| Europe PMC Discovery 与显式 OA 获取 | 支持（beta；只接 Europe PMC） |
| Obsidian 受管目录单向生成 | 支持（beta） |
| Knowledge base exchange（安全预检） | 支持（beta；导入记录为 immutable `external_unreviewed`） |
| 桌面安装程序 | 不支持 |
| 遥测、自动更新、support 自动上传 | 禁用（egress policy `telemetry/update/support_upload = disabled`） |
| 物理 sleep/resume | 不在支持承诺内 |
| legacy workspace migration / cutover | 不在支持承诺内 |
| hostile-PDF sandbox / 进程与网络隔离 | 不在支持承诺内 |

## 验收证据状态

| 证据 | 状态 |
|---|---|
| GitHub Release + PyPI 发布且字节与候选一致 | 已记录 |
| 全新 virtualenv 安装 + `pip check` + CLI smoke | 已记录 |
| 两轮生命周期回放 + headless GUI e2e（逐 spec） | 已记录 |
| 严格全新 Windows 账户下的干净安装 | 待完成 |
| headed（有界面）GUI 观察 | 待完成 |
| 完整 beta 验收结论 `Windows public beta accepted` | **尚未宣称** |

维护者不会用已记录的自动化/headless 证据替代尚未完成的全新账户和 headed GUI 观察。

## 支持入口

- 非安全问题：在公开仓库的
  [GitHub Issues](https://github.com/ZhangChengwei0722/research-kb-app/issues) 提交，
  只使用 synthetic fixture 和脱敏材料；提交前阅读 [`SUPPORT.md`](../SUPPORT.md)。
- 安全问题：按 [`SECURITY.md`](../SECURITY.md) 私下报告，不要在公开 issue 或 PR 中披露。
- 当前不承诺响应时间、修复时间或服务级别协议。
