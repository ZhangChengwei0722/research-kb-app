# 变更记录

本文件记录 App 候选版的用户可见变化。当前条目是未发布的本地 Windows beta 候选版，
不是公开 release、已接受 milestone 或可下载发行包的声明。

## [0.1.1b1] - 未发布

### 新增

- Apache License 2.0、许可证文件 metadata 和中文优先的安全、支持与贡献边界；
- 面向本地 Windows beta 候选的 package metadata、验证规则和治理入口。

### 变更

- bootstrap 要求已存在的 `package-lock.json`，使用 `npm ci` 安装前端依赖，并在 Python
  package 使用前完成 frontend build；
- 文档明确候选版仍未公开发布，不声明尚未关闭的目标仓库身份、公开支持入口或发布承诺。

### 当前限制

- Core 与 App 都不是公开发布包，当前不提供 public repository、PyPI、桌面安装包或远程部署；
- migration、cutover、embedded LLM、hostile-PDF sandbox 和 physical sleep/resume 不在本候选
  支持承诺内；
- 详细边界见 [`README.md`](README.md)、[`SECURITY.md`](SECURITY.md)、[`SUPPORT.md`](SUPPORT.md)
  和 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
