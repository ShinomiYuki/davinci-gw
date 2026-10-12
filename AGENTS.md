# DaVinci 网关路由工程的 AI 入口

本工程通过标准配置表更新完整 ARXML，支持整帧 CAN、COM 信号和 CAN 诊断路由。用户要求安装、接入或配置环境时，按下面流程执行，不需要先搜索整套文档。普通路由配置和源码修改任务不自动触发安装。

## 安装或接入客户端

1. 使用 Windows 和 PowerShell 7。依据当前会话选择 `Codex` 或 `DeepSeekHarness`，不要仅凭电脑安装了哪个应用来猜。客户端仍不明确时，只询问这一项。
2. 仓库源码中的安装入口是 `scripts/setup.ps1`；解压 MCP 发布包后的入口是 `setup.ps1`。先执行 `-Check` 查看版本、安装位置和客户端配置位置；此步骤不下载、不安装、不改配置。
3. 默认 `Auto` 模式优先使用本地发布包。从公司镜像克隆且没有本地包时自动选择 `Build`，从源码本地构建 MCP，不访问 GitHub；GitHub 主仓库则下载对应版本发布包。根据实际远端 URL 识别仓库，不仅凭 `origin` 名字判断。网络明确禁止 GitHub 时显式传 `-Mode Build`，已有包则传 `-PackageRoot`。
4. `-Check` 返回 `NeedsEnvironmentChoice=true` 时，必须先询问用户选择“直装 Python，使用工程 .venv”或“Miniconda，创建 davinci-gw 环境”。不要替用户默认选择。取得选择后传 `-Environment Direct` 或 `Miniconda`。脚本会检查现有解释器，缺失时从多个国内镜像下载，依赖源不可用会切换候选。用户已有内网源通过 `-PipIndexUrl`、`-CondaChannel` 指定，不改全局配置。只有明确要求源码运行时才选 `-Mode Source`。
5. 安装入口会验证 MCP 初始化、工具列表和版本。客户端重新加载后，调用 `get_gateway_capabilities` 确认连接；不要在连接尚未验证时声称客户端已经能调用工具。重启应由用户执行，不强制关闭正在运行的客户端。

源码仓库中的常用命令：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Check
# 镜像克隆需要本地构建；取得用户选择后，使用 Direct 或 Miniconda。
.\scripts\setup.ps1 -Client DeepSeekHarness -Mode Build -Environment Miniconda
# 当前客户端是 Codex 时，将 Client 改为 Codex。
```

DSH Desktop 使用 `%APPDATA%\dsh-desktop\harness\cordis.patch.yml`，命令行 Harness 使用 `DSH_HOME` 或 `~/.dsh`；自定义目录传 `-DshHome`。它使用 `mcp-client` YAML 插件补丁，不能写成 Codex TOML 或通用 `mcpServers` JSON。Codex 自定义目录传 `-CodexHome`。安装器只修改本产品的托管块，保留其他插件、模型、密钥和客户端设置。发现同名非托管配置或损坏标记时停止该配置步骤，并报告具体位置，不删除整个配置文件。

内网不能访问 GitHub 时保持本地构建，不要求用户去 GitHub 下载文件或登录 Codex。标准表校验、预览和 ARXML 生成均在本机执行；可选的 Codex 自动修复需要另行具备其服务访问条件，不作为安装成功或正常使用的前提。使用用户的既有代理或镜像源，不关闭证书验证。详细参数及卸载见 [安装说明](docs/AI安装与接入.md)；发布包中的同名说明位于根目录。

## 路由配置

- 原始需求表先读取仓库的 `skills/build-gateway-routing-config/SKILL.md`，生成标准配置表；不凭工作表名称推断 DoIP 架构，不编造缺失端点。
- 标准配置表通过 MCP 执行 `validate_gateway_inputs → preview_gateway_update → 展示预览并确认 → generate_gateway_arxml`。生成必须使用当前进程的 `preparation_id`。
- 输入文件保持只读，输出使用新的绝对路径。工具的 ARXML 校验不能代替 DaVinci 实际 Validate/Generate。

## 开发和验证

只改请求所需的代码。先运行受影响测试；原生审查发现的问题按实际影响处理。GUI、MCP、核心、Windows 版本资源和发布说明使用同一版本。用户要求发布时，依据远端 URL 区分 GitHub 主仓库与公司镜像；二进制放 GitHub Release，不提交客户工作簿、ARXML 或构建目录。`docs` 下的开发日志只保留本地，不再跟踪或推送；其他文档正常提交。提交信息使用中文约定式提交，并选择实际作用域。
