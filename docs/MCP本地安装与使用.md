# MCP 本地安装与使用

## 产品边界

`davinci-gw-mcp.exe` 是 Windows x64 `onedir` 控制台程序。MCP 客户端通过进程的 stdin/stdout 与它通信。多个会话共用安装目录中的 `_internal`，不会再按会话向 TEMP 解包 `_MEI*`。服务本身不需要系统 Python；普通校验、预览和生成不访问云端。只有用户主动调用故障诊断或经确认的自动修复时，才会通过用户现有 Codex 登录使用官方 `openai-codex` SDK。

安装和运行均不需要管理员权限。项目采用 MIT License；发布 ZIP 包含 EXE、完整 `_internal`、全目录 SHA-256、安装/卸载脚本、示例配置、版本和 MIT 许可证。不能只复制 EXE。

## 安装

先验证发布 ZIP 旁的 `.zip.sha256`，解压后在 PowerShell 7 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_mcp.ps1
```

默认安装位置为：

```text
%LOCALAPPDATA%\Programs\DaVinciGW\versions\1.1.1\davinci-gw-mcp.exe
```

安装器在复制前核对 `SHA256SUMS.txt` 中的全部文件。每个版本安装到独立目录，升级时 Codex 配置切换到新版本，因此正在运行的旧会话不会阻塞新版本安装；相同版本、相同内容可重复安装。Codex 配置有变化时会在原文件旁创建带 UTC 时间戳的 `.bak`。安装器只维护带以下标记的配置块，不会重写其他配置：

```text
# BEGIN DAVINCI_GW_MCP MANAGED BLOCK
# END DAVINCI_GW_MCP MANAGED BLOCK
```

若已有同名、但不是安装器管理的 `[mcp_servers.davinci_gateway]`，安装会停止并要求人工处理，不会覆盖。

需要先安装 EXE、暂不写 Codex 配置时：

```powershell
.\install_mcp.ps1 -ConfigureCodex $false
```

## Codex 配置与审批

[Codex 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)说明，本地 STDIO MCP 可配置在用户级 `~/.codex/config.toml`，`command` 用于启动服务，`startup_timeout_sec` 和 `tool_timeout_sec` 控制启动与工具超时，`default_tools_approval_mode` 及单工具 `approval_mode` 控制审批。

安装器写入的等价配置如下，实际路径由安装器生成并正确转义：

```toml
[mcp_servers.davinci_gateway]
command = 'C:\Users\YOUR_NAME\AppData\Local\Programs\DaVinciGW\versions\1.1.1\davinci-gw-mcp.exe'
startup_timeout_sec = 30
tool_timeout_sec = 3600
enabled = true
required = false
default_tools_approval_mode = "writes"

[mcp_servers.davinci_gateway.tools.generate_gateway_arxml]
approval_mode = "prompt"

[mcp_servers.davinci_gateway.tools.start_bug_repair]
approval_mode = "prompt"

[mcp_servers.davinci_gateway.tools.submit_bug_repair]
approval_mode = "prompt"

[mcp_servers.davinci_gateway.tools.cancel_bug_repair]
approval_mode = "prompt"
```

`get_gateway_capabilities`、`validate_gateway_inputs`、`preview_gateway_update`、`diagnose_generation_failure` 和 `get_bug_repair_status` 标记为只读；生成、开始修复、提交修复和取消修复均标记为写操作并显式提示。

安装后可执行：

```powershell
codex mcp list
```

本项目的自动验证只使用临时 `CODEX_HOME`，不会修改真实用户配置。

## 标准调用流程

1. 调用 `get_gateway_capabilities` 确认版本和功能。
2. 调用 `validate_gateway_inputs`，传入 `.xlsx` 与 `.arxml` 的本地绝对路径。
3. 调用 `preview_gateway_update`，检查动态功能摘要、问题、目标版本和输入指纹。
4. 把预览展示给用户，并取得明确确认。
5. 调用 `generate_gateway_arxml`，传入同一进程预览返回的 `preparation_id` 和尚不存在的新 `.arxml` 绝对路径。

MCP 与 GUI、CLI 使用同一份标准 18 列直接报文输入，不增加路由组字段。`GatewayFacade` 会根据目标通道和基准 ARXML 自动解析唯一应用路由组；无法解析、存在歧义或实际成员归组异常时，validate/preview 返回带工作表行、报文名称、CAN ID、通道、组名和完整对象路径的问题。

既有直接报文路由按 CanIf、EcuC、PduR 和 RoutingGroup 的完整引用链识别，不依赖 `Gw`、`GWT`、`GWH` 等名称前缀。只有精确源 RoutingPath 下的唯一目标腿才会返回“已存在”；路径外的本地自发 Tx 不会成为网关证据。仅有本地端点、部分链或重复完整链时，MCP 与公共 Facade 一样返回阻断问题和候选完整路径，不会继续创建或自动清理。

信号 ADD 会同步把实际参与 Mapping 的 Rx、Tx `ComSignalAccess` 设置为 `ACCESS_NEEDED_BY_SWC_OR_COM`；已有 Mapping 重跑时也会补齐旧值。路由组非主成员属于基线 ARXML 警告，每个异常成员只返回一次，不绑定当前 Excel 行或当前目标 DestPdu。

Prepared Session 默认 15 分钟过期、进程内最多 8 个。成功生成后立即消费；失败、取消或进程退出后不能复用。输入文件在预览后发生变化时，生成会安全终止并要求重新预览。

## 失败诊断与自动修复

自动修复只面向保留了完整 `davinci-gw` 源码和 Git 仓库的开发机，还需要传入该开发环境的 Python 路径。发布包内记录了准确构建 commit，热修复从该 commit 创建独立 `hotfix/mcp-auto-*` 分支和 worktree，不会切换或清理主工作区。

失败先调用 `diagnose_generation_failure`。结论严格分为 DBC 缺失、输入问题、基线问题、工具 BUG 和无法确定：DBC 缺失行继续跳过，其他路由仍可生成；输入和基线问题只报告定位；无法确定时继续询问。只有稳定复现且有具体代码证据的工具 BUG 才具有修复资格。

开始修复必须原样确认：

```text
确认开始工具BUG自动修复
```

修复会依次完成定位、最小修改、针对性测试、完整差异审查、审查修复、一次最终全量回归、原输入复验、候选包构建和 STDIO/多进程冒烟。成功后停在等待提交状态。正式提交、推送、PR、合并、打标和发布之前，还必须原样确认：

```text
确认提交工具BUG修复
```

维护者可以选择合并并发布；外部用户只能推送自己的 fork 并创建 Draft PR。PR 不得包含真实 Excel、ARXML、DBC、LDF、BLF 或其他项目数据。修复失败或取消时会保留 worktree 和报告，主工作区不会被自动 stash、reset 或清理。

## 路径与输出安全

- 只接受 `C:\...` 形式的本地绝对路径。
- 拒绝相对路径、URL、UNC、扩展 UNC、设备路径、命名管道、网络共享、备用数据流和 Windows 保留设备名。
- 配置表必须存在且为 `.xlsx`；基准必须存在且为 `.arxml`。
- 输出父目录必须已存在；服务不会自动创建目录。
- 输出必须是新 `.arxml`，不允许覆盖；也不允许大小写、规范化、符号链接或目录联接绕过基准保护。
- 响应最多返回 200 个问题，错误优先；总 JSON 响应上限为 512000 字节。

## 日志与故障排查

默认日志：

```text
%LOCALAPPDATA%\DaVinciGW\logs\mcp.log
```

日志按 1 MB 滚动、保留 3 个备份。日志只记录操作 ID、阶段、进度和适配器异常类型，不记录异常消息、请求正文、XML 内容或完整用户参数。无法创建日志目录时仅回退到 stderr；stdout 永远留给 MCP JSON-RPC。

常见状态：

- `VALIDATION_FAILED`：路径、输入契约或业务校验未通过。
- `SESSION_MISSING/EXPIRED/CONSUMED`：重新执行预览并使用新的 `preparation_id`。
- `INPUT_CHANGED`：配置表或基准在预览后变化，重新预览。
- `CANCELLED`：客户端取消或关闭，未报告成功的输出不得视为有效。
- `INTERNAL_FAILURE`：查看本地滚动日志；协议响应不会暴露异常、堆栈或内部对象。

## 卸载

在解压后的发布包中执行：

```powershell
.\uninstall_mcp.ps1
```

卸载器会按安装清单逐个校验并删除安装器管理的所有 MCP 版本、配置块和安装清单；修改配置前同样备份。它不会递归删除安装根目录，因此历史备份和任何非本产品文件都不会被误删。完成卸载后重复执行是安全的；清单缺失但 `versions` 仍有文件时会拒绝猜测删除。

## 从源码构建

在项目 Python 3.12 开发环境中安装 `.[dev]` 后：

```powershell
.\scripts\build_mcp_release.ps1 -Python "C:\path\to\python.exe"
```

构建产物保留在 `release/`，EXE、ZIP 和校验和均被 Git 忽略，不进入源码提交。
