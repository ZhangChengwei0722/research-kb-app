# 变更记录

本文件记录 App 公开 beta 的用户可见变化。`公开 beta` 不等于最终验收；当前
`Windows public beta accepted` 尚未宣称，见
[`docs/support-matrix.md`](docs/support-matrix.md)。

## [0.1.1b2] - 2026-08-16

当前公开 beta（pre-release），已发布到
[GitHub Releases](https://github.com/ZhangChengwei0722/research-kb-app/releases/tag/v0.1.1b2)
和 [PyPI](https://pypi.org/project/research-kb-app/0.1.1b2/)。

### 修复

- 干净公开安装的 Core dependency profile 校验失败：`charset-normalizer` 会解析为与
  受审 lock 不同的版本。现在把受审 Core PDF closure 的 16 个发布依赖以精确 pin 写入
  App `Requires-Dist`，包括 `charset-normalizer==3.5.0`；
- `core-compatibility.json` 保持与受审版本字节一致，未重新生成。

### 变更

- 新增 lock / marker metadata / closure 一致性测试，防止依赖闭包再次漂移；
- `0.1.1b1` 的 GitHub Release 标记为 pre-release 并添加被 b2 取代说明；b1 资产未被
  替换或删除；
- 公开文档改为描述真实的公开仓库、GitHub Release 与 PyPI 状态。

### 验证记录

- 全新 virtualenv 安装 `research-kb-app==0.1.1b2`、`pip check` 和 CLI smoke 通过；
- 两轮产品生命周期回放通过；
- 四个 GUI e2e spec（setup wizard、discovery、trusted-parse、bootstrap）在已安装 b2
  上逐个通过；
- 严格全新 Windows 账户干净安装与 headed GUI 观察仍待完成。

## [0.1.1b1] - 2026-08-16（已被 0.1.1b2 取代）

第一个公开发布的 Windows beta 候选，已发布到 GitHub Release 和 PyPI，后因
clean-install 依赖漂移被 b2 取代。b1 资产保持不变；不建议继续安装。PyPI 上的旧页面
在维护者按平台流程完成处理前仍可能存在。

### 新增

- Apache License 2.0、许可证文件 metadata 和中文优先的安全、支持与贡献边界；
- 面向本地 Windows beta 的 package metadata、验证规则和治理入口。

### 变更

- bootstrap 要求已存在的 `package-lock.json`，使用 `npm ci` 安装前端依赖，并在 Python
  package 使用前完成 frontend build。

### 已知问题（已在 b2 修复）

- 干净安装会把 `charset-normalizer` 解析为与受审 lock 不同的版本，触发 Core
  dependency profile 校验失败。

### 当前限制

- Windows-only beta，不提供 macOS/Linux 验收、桌面安装包或远程部署；
- migration、cutover、embedded LLM、hostile-PDF sandbox 和 physical sleep/resume 不在
  支持承诺内；
- 详细边界见 [`README.md`](README.md)、[`docs/support-matrix.md`](docs/support-matrix.md)、
  [`docs/known-limitations-and-privacy.md`](docs/known-limitations-and-privacy.md)、
  [`SECURITY.md`](SECURITY.md) 和 [`SUPPORT.md`](SUPPORT.md)。
