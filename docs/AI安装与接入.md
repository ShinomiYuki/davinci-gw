# AI 安装与客户端接入

本入口供用户和 AI 安装网关 MCP。默认使用 Windows x64 发布包，不要求 Python，也不要求 Codex 登录；正常配置表校验、预览和 ARXML 生成都在本机执行。故障自动修复是独立可选功能，仍需要源码和既有 Codex 登录。

在 Codex 或 DSH Desktop 中打开源码仓库后，只需告诉 AI：“安装这个工程并接入当前客户端。”根目录 `AGENTS.md` 提供执行流程。客户端没有自动读取该文件时，改为：“读取根目录 AGENTS.md 并安装。”

## 一条命令安装

在 PowerShell 7 中进入源码根目录，选择实际使用的客户端：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness
.\scripts\setup.ps1 -Client Codex
```

只运行其中一条。默认 `Auto` 优先使用本地包；从公司镜像克隆且没有本地包时选择本地构建，其余情况下载对应版本的 GitHub Release MCP ZIP。脚本校验、安装到用户目录、更新指定客户端并检查 MCP 初始化、工具列表和版本。它不修改模型、密钥或其他插件。完成后重新加载 MCP 或重启客户端，调用 `get_gateway_capabilities` 确认当前会话可用。

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

## 公司镜像与本地构建

公司内网不能访问 GitHub 时，从镜像克隆源码后执行 `-Check`。如果需要准备环境，AI 必须先让用户选择以下一种方式：

- `Direct`：复用现有 64 位 Python；没有时直装到本产品用户目录，并为工程创建 `.venv`，避免改动其他工程依赖。
- `Miniconda`：使用指定 Miniconda，或安装到本产品用户目录，然后创建名为 `davinci-gw` 的独立 Conda 环境。

选择后执行，例如：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Mode Build -Environment Miniconda
# 用户选择直装时，将 Environment 改为 Direct。
```

`Build` 只使用本地源码构建 MCP，再安装和接入客户端，不请求 GitHub Release。不需要管理员权限，不修改系统 PATH、全局 pip 配置或 `.condarc`。需要公司内网源时显式指定：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Mode Build -Environment Miniconda `
  -CondaRoot 'D:\Miniconda3' -PipIndexUrl 'https://your-pypi-mirror/simple' `
  -CondaChannel 'https://your-conda-mirror/conda-forge'
```

未传 `-Environment` 时不会擅自安装 Python 或 Miniconda，而是报告需要用户选择。`-Check` 不要求本机已经有 Python。

## 国内镜像与依赖

镜像列表集中在 `packaging/python-mirrors.json`：Python 安装包提供华为云和 npmmirror 候选，Miniconda/Conda 提供清华、北外、中科大、华为云候选，pip 提供清华、阿里云、华为云、腾讯云、北外候选。脚本按可达性、安装包签名及实际安装结果切换；候选地址不代表每个网络都可用，某些站点也可能重定向到同一后端。

安装包下载失败会切换下一个候选；签名无效的文件不执行。依赖安装失败也会换源，全部失败时说明失败并接受用户指定的内网源或已安装解释器，不关闭证书校验、不回退到 GitHub。使用 Miniconda 时通过命令参数选择国内 conda-forge 社区频道，不改用户全局频道。

地址和使用方式依据镜像维护方说明核对：[清华 PyPI 镜像](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)、[清华 Anaconda/Miniconda 镜像](https://mirrors.tuna.tsinghua.edu.cn/help/anaconda/)、[华为 Python 安装包目录](https://mirrors.huaweicloud.com/python/3.13.15/)。

## 源码运行

开发用户可以用现有的 64 位 Python 3.12 或更新版本：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Mode Source -Environment Direct -Python 'C:\Python312\python.exe'
```

脚本安装 `.[mcp]`，检查 MCP，然后将客户端配置为所选环境中的 Python 加 `-m davinci_gw.mcp`。源码更新后再次执行同一命令即可更新环境中的安装。`Direct` 使用工程 `.venv`，`Miniconda` 使用 `davinci-gw` 环境。

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
