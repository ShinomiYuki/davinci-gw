# MCP 本地安装与使用

## 产品边界

`davinci-gw-mcp.exe` 是 Windows x64 单文件控制台程序。MCP 客户端通过进程的 stdin/stdout 与它通信。服务不会监听 TCP/UDP 端口，不需要 Python、源码目录或 PATH，不下载依赖，不访问云端，也不会把配置表、ARXML 或完整请求写入日志。

安装和运行均不需要管理员权限。项目采用 MIT License；发布包包含 EXE、SHA-256、安装/卸载脚本、示例配置、版本和 MIT 许可证。

## 安装

先验证发布 ZIP 旁的 `.zip.sha256`，解压后在 PowerShell 7 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_mcp.ps1
```

默认安装位置为：

```text
%LOCALAPPDATA%\Programs\DaVinciGW\davinci-gw-mcp.exe
```

安装器在复制前核对包内 `SHA256SUMS.txt`。目标已有不同 EXE 时，会先复制到安装目录的 `backups`；相同版本可重复安装。Codex 配置有变化时会在原文件旁创建带 UTC 时间戳的 `.bak`。安装器只维护带以下标记的配置块，不会重写其他配置：

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
command = 'C:\Users\YOUR_NAME\AppData\Local\Programs\DaVinciGW\davinci-gw-mcp.exe'
startup_timeout_sec = 30
tool_timeout_sec = 3600
enabled = true
required = false
default_tools_approval_mode = "writes"

[mcp_servers.davinci_gateway.tools.generate_gateway_arxml]
approval_mode = "prompt"
```

`get_gateway_capabilities`、`validate_gateway_inputs` 和 `preview_gateway_update` 均标记为只读；`generate_gateway_arxml` 标记为 destructive，并显式配置为每次提示。客户端提示生成审批时，应核对预览摘要、`preparation_id` 和新输出路径后再批准。

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

信号 ADD 会同步把实际参与 Mapping 的 Rx、Tx `ComSignalAccess` 设置为 `ACCESS_NEEDED_BY_SWC_OR_COM`；已有 Mapping 重跑时也会补齐旧值。路由组非主成员属于基线 ARXML 警告，每个异常成员只返回一次，不绑定当前 Excel 行或当前目标 DestPdu。

Prepared Session 默认 15 分钟过期、进程内最多 8 个。成功生成后立即消费；失败、取消或进程退出后不能复用。输入文件在预览后发生变化时，生成会安全终止并要求重新预览。

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

卸载器只删除安装器管理的配置块、安装 EXE 和安装清单；修改配置前同样备份。它不会递归删除安装根目录，因此历史备份和任何非本产品文件都不会被误删。重复卸载是安全的。

## 从源码构建

在项目 Python 3.12 开发环境中安装 `.[dev]` 后：

```powershell
.\scripts\build_mcp_release.ps1 -Python "C:\path\to\python.exe"
```

构建产物保留在 `release/`，EXE、ZIP 和校验和均被 Git 忽略，不进入源码提交。
