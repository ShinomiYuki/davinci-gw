# DaVinci 网关路由工程的 AI 入口

本工程通过标准配置表更新完整 ARXML，支持整帧 CAN、COM 信号和 CAN 诊断路由。用户要求安装、接入或配置环境时，按下面流程执行，不需要先搜索整套文档。普通路由配置和源码修改任务不自动触发安装。

## 安装或接入客户端

1. 使用 Windows 和 PowerShell 7。依据当前会话选择 `Codex` 或 `DeepSeekHarness`，不要仅凭电脑安装了哪个应用来猜。客户端仍不明确时，只询问这一项。
2. 仓库源码中的安装入口是 `scripts/setup.ps1`；解压 MCP 发布包后的入口是 `setup.ps1`。先执行 `-Check` 查看版本、安装位置和客户端配置位置；此步骤不下载、不安装、不改配置。
3. 用户要求安装即授权运行安装入口。默认使用免 Python 的发布包，自动下载与当前工程一致的版本、校验、安装并配置客户端。已提供本地发布包时传入 `-PackageRoot`，不再下载。只有明确要求源码运行或开发时才使用 `-Mode Source -Python <已安装的64位Python路径>`。
4. 安装入口会验证 MCP 初始化、工具列表和版本。客户端重新加载后，调用 `get_gateway_capabilities` 确认连接；不要在连接尚未验证时声称客户端已经能调用工具。重启应由用户执行，不强制关闭正在运行的客户端。

源码仓库中的常用命令：

```powershell
.\scripts\setup.ps1 -Client DeepSeekHarness -Check
.\scripts\setup.ps1 -Client DeepSeekHarness
# 当前客户端是 Codex 时，将 Client 改为 Codex。
```

DSH Desktop 使用 `%APPDATA%\dsh-desktop\harness\cordis.patch.yml`，命令行 Harness 使用 `DSH_HOME` 或 `~/.dsh`；自定义目录传 `-DshHome`。它使用 `mcp-client` YAML 插件补丁，不能写成 Codex TOML 或通用 `mcpServers` JSON。Codex 自定义目录传 `-CodexHome`。安装器只修改本产品的托管块，保留其他插件、模型、密钥和客户端设置。发现同名非托管配置或损坏标记时停止该配置步骤，并报告具体位置，不删除整个配置文件。

网络受限时使用用户的既有代理或本地发布包，不关闭证书验证。详细参数及卸载见 [安装说明](docs/AI安装与接入.md)；发布包中的同名说明位于根目录。

## 路由配置

- 原始需求表先读取仓库的 `skills/build-gateway-routing-config/SKILL.md`，生成标准配置表；不凭工作表名称推断 DoIP 架构，不编造缺失端点。
- 标准配置表通过 MCP 执行 `validate_gateway_inputs → preview_gateway_update → 展示预览并确认 → generate_gateway_arxml`。生成必须使用当前进程的 `preparation_id`。
- 输入文件保持只读，输出使用新的绝对路径。工具的 ARXML 校验不能代替 DaVinci 实际 Validate/Generate。

## 开发和验证

只改请求所需的代码。先运行受影响测试；原生审查发现的问题按实际影响处理。GUI、MCP、核心、Windows 版本资源和发布说明使用同一版本。用户要求发布时，GitHub `origin` 是主仓库，`company-mirror` 是公司镜像；二进制放 GitHub Release，不提交客户工作簿、ARXML 或构建目录。提交信息使用中文约定式提交，并选择实际作用域。
