# 贡献指南

> **发布状态：** App `0.1.1b2` 是 Windows-only 公开 beta。本仓库接受通过 pull request
> 的贡献，但 `main` 受保护：所有必需检查通过后才能合并；任何贡献者都不得自行发布
> tag、Release 或 PyPI 包。

## 贡献入口

1. 对公开仓库 fork 或创建分支，在 pull request 中提交 focused patch；
2. 保持变更小、可审查、可回退，不修改 CI、fixture/public-source、锁文件、权限或
   release 治理边界（维护者授权的单独变更除外）；
3. 不 push tag、不创建 GitHub Release、不发布 PyPI 包、不创建公开镜像。

## 修改前

1. 阅读 [`AGENTS.md`](AGENTS.md) 和受影响的架构、配置、工作流文档；
2. 明确允许修改的文件、禁止触碰的路径、验证命令和 rollback 方法；
3. 保持 App/Core 边界，不在 App 中复制 Core contract、canonical state 或科学判断；
4. 只使用 synthetic fixture，不读取真实 PDF、研究笔记、私有 workspace 或凭据；
5. 对行为变化先添加 focused test 或 characterization test。

## 修改与验证

- 使用项目现有的 Python、React、TypeScript、Vite 和 PowerShell 约定，不添加生产依赖；
- 按任务要求运行 focused checks，并记录每个命令的精确退出码；
- 不把命令成功、patch 生成或测试通过描述为发布、Gate 或 milestone acceptance；
- 失败时保留现状，说明未验证部分和可恢复的 rollback 路径。

## 交接内容

在 pull request 描述中说明：

- 变更文件清单和每个文件的目的；
- 关键设计取舍、已知限制和未解决问题；
- 精确验证命令、退出码、测试范围和未运行的检查；
- diff review 结果、隐私边界和 rollback 说明。

## 许可证与隐私

本项目采用 [`Apache License 2.0`](LICENSE)。提交内容必须适合在该许可证下审查，且
不得包含真实 PDF、解析文本、Evidence quote、研究笔记、workspace export、凭据、token、
绝对路径或机构专属信息。安全问题不要公开提交，按 [`SECURITY.md`](SECURITY.md) 处理；
普通使用问题按 [`SUPPORT.md`](SUPPORT.md) 的 beta 支持边界处理。
