# 安装说明

> **适用版本：** `research-kb-app==0.1.1b2`，Windows-only 公开 beta。
> 本文只描述从 PyPI 安装已发布的 wheel 并启动产品；不授权 migration、cutover 或
> 生产 workspace 切换。

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | 64 位 Windows（当前验证平台） |
| Python | CPython 3.11 或 3.12（`>=3.11,<3.13`） |
| Core | `research-kb-core[pdf]==0.1.1`，由 App 元数据固定并自动安装 |
| 磁盘 | App 管理的目录位于本机 NTFS，并通过当前用户、`SYSTEM` 和本机 Administrators 的受保护 ACL 检查 |
| 网络 | 安装依赖需要 PyPI；运行产品只绑定 `127.0.0.1` |

32 位 Python、其他操作系统或本机 NTFS/ACL 检查不通过的环境不受支持。

## 全新安装

推荐在全新 virtualenv 中安装，避免与旧候选版或其他研究工具混用：

```powershell
python -m venv .venv-rkb
.\.venv-rkb\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install research-kb-app==0.1.1b2
```

因为命令显式指定了 `0.1.1b2`，pip 会直接解析这个 pre-release 版本；不要在同一个
环境里再混装其他 App 版本。

## 安装后验证

```powershell
pip check
research-kb-app --help
```

- `pip check` 必须报告无冲突依赖（退出码 0）。
- `research-kb-app --help` 必须打印用法并返回退出码 0。
- 如果安装或验证失败，不要手工编辑 `core-compatibility.json`、依赖 pin 或本地 receipt
  来“绕开”检查；先按下方故障排查确认环境，仍无法解决时按
  [`SUPPORT.md`](../SUPPORT.md) 提交最小复现。

## 启动

```powershell
research-kb-app
```

启动器会：

1. 选择可用的 `127.0.0.1` 端口；
2. 在控制台输出 URL、一次性 startup token 和日志路径；
3. 在服务就绪后打开默认浏览器。

startup token 只出现在控制台，不写入 URL、日志、浏览器存储或 App 配置。第一次启动
输入 token 后进入受管 setup；已配置的 profile 会直接进入 workspace 选择。多 profile
可用 `--profile <profile-id>`；已有自动化可用 `--config <absolute-config-path>` 兼容
入口，但普通用户不需要手写配置。

完整操作说明见 [`docs/r1-operator-guide.md`](r1-operator-guide.md)，配置契约见
[`docs/configuration.md`](configuration.md)。

## 从 0.1.1b1 升级到 0.1.1b2

`0.1.1b1` 已被 `0.1.1b2` 取代，不应继续使用。升级采用“新环境安装”而不是原位覆盖：

```powershell
python -m venv .venv-rkb-b2
.\.venv-rkb-b2\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install research-kb-app==0.1.1b2
pip check
research-kb-app --help
```

b1 创建的用户数据属于本地 workspace；b2 不执行数据 migration。已经通过检查的
Shared Core workspace 可在 b2 的 setup 中重新选择，但不要复制 b1 的 App state、
profile 或 log 目录到 b2，也不要修改 b1 的已发布 package 文件。正式 source of truth
仍是 legacy CLI workspace，切换生产 workspace 不属于 beta 支持范围。

## 故障排查

- **`pip install` 无法解析 0.1.1b2**：确认命令写的是 `==0.1.1b2`，且没有旧 index、
  mirror 或 `--only-binary`/`--no-deps` 限制。
- **`pip check` 报冲突**：说明环境中混入了其他研究工具或旧 App；换一个全新 virtualenv
  重装，不要把 b1 与 b2 装进同一个环境。
- **启动时 Core compatibility 拒绝**：不要修改 packaged compatibility 文件。确认
  `pip check` 通过、Python 是 3.11/3.12、安装的是完整 wheel 而不是源码树子集。
- **NTFS/ACL/reparse 拒绝**：把 workspace 和 App state 放在本机 NTFS，移除 junction、
  symlink 或不安全 ACL，不要用 UNC/网络路径或 exFAT。
- **首次启动后浏览器未打开**：先按控制台 URL 手工打开 `127.0.0.1` 地址；token 仍在
  控制台，不会在浏览器或日志中重复出现。

以上任一检查失败都应 fail closed。不要绕过 Core compatibility、ACL、profile-instance
或 clipboard-policy 检查；不能确定时保留原状并提交脱敏最小复现。
