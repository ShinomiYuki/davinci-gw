# AI 安装与客户端接入

本入口供用户和 AI 安装网关 MCP。默认使用 Windows x64 发布包，不要求 Python，也不要求 Codex 登录；正常配置表校验、预览和 ARXML 生成都在本机执行。故障自动修复是独立可选功能，仍需要源码和既有 Codex 登录。

在 Codex 或 DSH Desktop 中打开源码仓库后，只需告诉 AI：“安装这个工程并接入当前客户端。”根目录 `AGENTS.md` 提供执行流程。客户端没有自动读取该文件时，改为：“读取根目录 AGENTS.md 并安装。”

## 一条命令安装

在 PowerShell 7 中进入源码根目录，选择实际使用的客户端：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness
.\scripts\setup.ps1 -Client Codex
```

只运行其中一条。脚本下载与源码版本相同的 GitHub Release MCP ZIP，校验、安装到用户目录、更新指定客户端并检查 MCP 初始化、工具列表和版本。它不修改模型、密钥或其他插件。完成后重新加载 MCP 或重启客户端，调用 `get_gateway_capabilities` 确认当前会话可用。

在解压的 MCP 发布包中使用 `./setup.ps1`。也可以提前查看计划，或使用已经解压的本地包：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Check
.\scripts\setup.ps1 -Client DeepSeekHarness -PackageRoot 'D:\Downloads\davinci-gw-mcp-1.1.5-win-x64'
```

`-Check` 不下载或修改配置。网络受限时使用既有代理，或从 GitHub Release 下载 ZIP 后解压并指定 `-PackageRoot`。需要 PowerShell 7 时安装后再执行；脚本不修改系统执行策略。若系统执行策略阻止脚本，可在本次进程使用 `pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Client DeepSeekHarness`。

## DSH Desktop 与命令行 Harness

已核对 DSH Desktop 0.9.1 搭载的 Harness `0.1.5-rc.2`：它读取项目 `AGENTS.md`，并通过 `mcp-client` 插件连接 STDIO MCP。

客户端目录按显式 `-DshHome`、`DSH_HOME`、已存在的 `%APPDATA%\dsh-desktop\harness`、`~/.dsh` 顺序选择。配置写入所选目录的 `cordis.patch.yml`，使用独立托管块。已有配置会先备份；相同配置重复安装不重复添加。自定义目录必须显式传入：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -DshHome 'D:\HarnessData'
```

生成的插件配置包含 `transport: stdio`、`serverName: davinci_gateway`、程序绝对路径、启动参数和一小时工具调用超时。它不是 `settings.yaml`，也不是 `mcpServers` JSON。安装后出现的工具名称形如 `mcp__davinci_gateway__get_gateway_capabilities`。

客户端配置成功但工具暂时未出现时，先重新加载客户端，再调用能力查询；不要反复重新安装依赖。客户端格式变化或出现损坏、同名非托管条目时，脚本会停止并指出配置文件，保留其他设置。

## 源码运行

开发用户可以用现有的 64 位 Python 3.12 或更新版本：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Mode Source -Python 'C:\Python312\python.exe'
```

脚本在源码根目录创建 `.venv`，安装 `.[mcp]`，检查 MCP，然后将客户端配置为虚拟环境中的 Python 加 `-m davinci_gw.mcp`。不修改系统 Python 或用户已有环境。源码更新后再次执行同一命令即可更新虚拟环境中的安装。

只安装和检查运行时、暂不接入客户端时使用 `-Client None`。

## 卸载

发布包安装后，使用包内卸载脚本并选择安装时的客户端和自定义目录：

```powershell
.\uninstall_mcp.ps1 -Client DeepSeekHarness
# Codex 使用 -Client Codex；自定义 DSH_HOME 继续传 -DshHome。
```

它只移除指定客户端的托管配置和清单内的 MCP 运行时。若两个客户端共用该运行时，先分别移除两边配置，再卸载运行时。

源码模式只移除客户端接入时，在源码根目录执行：

```powershell
.\scripts\configure_mcp_client.ps1 -Client DeepSeekHarness -Remove
```

虚拟环境保留在工程目录。标准路由工作流和工具参数见 [MCP 本地安装与使用](MCP本地安装与使用.md)。
