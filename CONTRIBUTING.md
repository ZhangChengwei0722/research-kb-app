# 贡献指南

> **发布状态：** App `0.1.1b1` 是未发布的本地 Windows beta 候选版。当前没有公开
> pull request、issue 或下载入口；所有贡献都必须经过受控协作和维护者明确的范围确认。

## 当前贡献入口

请先在已经存在的受控协作渠道中确认任务范围，再提交 focused patch 或任务交接材料。
不要添加 Git remote、push 到公共仓库、创建公开镜像、发布候选包或引入新的公开 URL。
如果没有获得受控入口，不要把仓库内容复制到公开位置。

## 修改前

1. 阅读 [`AGENTS.md`](AGENTS.md) 和受影响的架构、配置、工作流文档；
2. 明确允许修改的文件、禁止触碰的路径、验证命令和 rollback 方法；
3. 保持 App/Core 边界，不在 App 中复制 Core contract、canonical state 或科学判断；
4. 只使用 synthetic fixture，不读取真实 PDF、研究笔记、私有 workspace 或凭据；
5. 对行为变化先添加 focused test 或 characterization test。

## 修改与验证

- 保持变更小、可审查、可回退，不修改 CI、fixture/public-source、锁文件、权限或远程配置；
- 使用项目现有的 Python、React、TypeScript、Vite 和 PowerShell 约定，不添加生产依赖；
- 按任务要求运行 focused checks，并记录每个命令的精确退出码；
- 不把命令成功、patch 生成或测试通过描述为发布、Gate 或 milestone acceptance；
- 失败时保留现状，说明未验证部分和可恢复的 rollback 路径。

## 交接内容

通过受控协作渠道提交：

- 变更文件清单和每个文件的目的；
- 关键设计取舍、已知限制和未解决问题；
- 精确验证命令、退出码、测试范围和未运行的检查；
- diff review 结果、隐私边界和 rollback 说明。

## 许可证与隐私

本项目采用 [`Apache License 2.0`](LICENSE)。提交内容必须适合在该许可证下审查，且
不得包含真实 PDF、解析文本、Evidence quote、研究笔记、workspace export、凭据、token、
绝对路径或机构专属信息。安全问题不要公开提交，按 [`SECURITY.md`](SECURITY.md) 处理；
普通使用问题按 [`SUPPORT.md`](SUPPORT.md) 的未发布候选边界处理。
