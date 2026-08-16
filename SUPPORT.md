# 支持说明

> **发布状态：** App `0.1.1b1` 是未发布的本地 Windows beta 候选版。支持仅限于
> 受控协作和本地候选验证，不构成公开产品支持、可用性承诺或服务级别协议。

## 当前支持边界

可以在受控协作渠道中提交以下内容：

- Windows 64-bit、CPython 3.11/3.12、固定 Core `0.1.1` pin 下的可复现 App 问题；
- documented localhost workflow、配置校验、bootstrap、打包和本地日志诊断问题；
- 使用 synthetic fixture 的最小复现和与当前文档不一致的行为；
- 对隐私、路径隔离、Core compatibility 或数据完整性边界的疑问。

当前没有公开 issue tracker、公开下载入口或固定支持邮箱。请通过维护者已经指定的
受控协作渠道提交；若尚未指定入口，先在现有受控协作中请求入口，不要自行创建 public
issue、公开镜像或下载页。

## 如何提交可复现问题

请提供：

1. App 版本、Core 版本、Windows 版本和 CPython 版本；
2. 使用的命令、配置 contract version 和 synthetic fixture 名称；
3. 预期结果、实际结果、最小复现步骤和退出码；
4. 脱敏后的错误类型、相关日志片段和已尝试的排查动作；
5. 是否涉及真实 workspace、PDF、个人数据或外部 Agent 内容。

日志和配置只保留诊断所需的最小内容。删除或替换 token、凭据、绝对路径、源文本、
Evidence quote、研究笔记、workspace export、机构信息和外部访问链接。安全问题不要
按普通支持问题提交，改按 [`SECURITY.md`](SECURITY.md) 的私下报告边界处理。

## 不在当前支持承诺内

- public release、PyPI/其他 registry 发布、桌面安装包或远程部署；
- legacy workspace migration、write freeze、cutover 或真实生产 workspace 切换；
- embedded LLM、App 内部 Agent 执行或新的外部 provider；
- hostile-PDF sandbox、进程/网络隔离和物理 sleep/resume；
- 科学结论、论文解释、引用判断或 Agent 语义结果的正确性保证；
- 私有研究材料恢复、迁移、删除或任何需要访问真实 PDF/Research KB 的操作。

所有支持均为 best-effort；当前候选版不承诺响应、解决或发布时限。
