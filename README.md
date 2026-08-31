# DaVinci 网关路由工具

本工具读取上游生成的标准网关路由配置表和旧版 DaVinci/MICROSAR 工程导出的完整 ARXML，一次处理直接报文与信号路由的 `ADD`/`DELETE`，生成新版完整 ARXML。配置表和基准 ARXML 始终只读。

## 可以做什么

- 校验配置表版本、工作表、字段、重复和替换对。
- 检查 ARXML 的 AUTOSAR Schema 以及 CanIf、Com、EcuC、PduR 四个必要模块。
- 新增或安全删除直接 CAN 报文路由和 Com 信号路由。
- 按配置表明确填写的组名，增量维护直接报文目标腿与既有 PduR RoutingGroup 的成员引用。
- 支持同一源端的一对多，只删除指定目标腿。
- 允许同一路由键各有一条 `DELETE` 和 `ADD`，以“先删后增”修改参数。
- 保护共享 CanIf/EcuC/PduR/Com 对象、HRH、TxBuffer 和无法确认归属的人工配置。
- 只在证据充分时清除源 ComSignal 超时；其他情况保守保留并说明原因。
- 在内存工作副本上完成 `DELETE → 投影 → ADD`，通过输出复核后才原子写出。

当前不支持直接报文 LIN 路由、诊断/CanTp 路由、多 ARXML 合并和 DaVinci GUI 自动操作。

## Windows 桌面版

第06轮提供 `davinci-gw-gui.exe` 免安装桌面程序。普通用户解压 ZIP 后直接双击 EXE，不需要安装 Python，也不需要配置 PATH。界面按“选择配置表 → 选择基准 ARXML → 预览 → 生成”工作：预览成功后复用同一份内存事务生成，不会再次解析约 81 MB 的基准文件。

界面支持浏览与拖放、自动建议不重名输出、动态路由统计、问题搜索/筛选/复制、协作式取消和安全关闭。输入文件始终只读，输出不能指向基准，也默认拒绝覆盖已有文件。运行日志位于 `%LOCALAPPDATA%\DaVinciGW\logs\gui.log`，只记录阶段、耗时、状态和异常类型，不记录 Excel/XML 内容。

完整操作、取消语义和常见问题见 [GUI 使用说明](docs/GUI.md)。开发环境可用 `python -m UI` 启动，发布包由 `scripts/build_gui_release.ps1` 构建。

## 本地 MCP 服务

第05轮提供 `davinci-gw-mcp.exe`：一个无窗口、纯本地、长生命周期的 STDIO MCP 服务，可由 Codex 等支持本地 STDIO MCP 的 AI 客户端启动。它不监听端口，不提供 HTTP/SSE/WebSocket，不登录云服务，不上传文件，也不包含自动更新或遥测。

服务只有四个工具：

- `get_gateway_capabilities`：查询版本、能力和安全策略。
- `validate_gateway_inputs`：只读校验配置表与基准 ARXML。
- `preview_gateway_update`：完整预览并返回当前进程内的一次性 `preparation_id`。
- `generate_gateway_arxml`：用户确认预览后，使用 `preparation_id` 生成一个新的 ARXML。

固定工作流是 `validate → preview → 用户确认 → generate`。生成工具不接受配置表或基准路径，不能绕过预览；`preparation_id` 不能跨 MCP 进程使用。所有路径必须是带盘符的本地 Windows 绝对路径，URL、UNC、设备路径、命名管道和网络共享会被拒绝。输出目录必须已存在，输出文件必须尚不存在，且不得通过大小写、规范化、符号链接或目录联接指向基准 ARXML。

MCP 只返回有界 JSON 摘要、问题定位和输出文件的大小/SHA-256，不返回 Excel 或 ARXML 内容。标准输出仅用于 MCP 协议；诊断日志位于 `%LOCALAPPDATA%\DaVinciGW\logs\mcp.log`。完整安装、Codex 配置、审批和卸载说明见 [MCP 本地安装与使用](docs/MCP本地安装与使用.md)。

## 公共应用接口

第04轮提供了供未来 GUI 与本地 MCP 共同依赖的 `GatewayFacade`。它不依赖 Qt、MCP 协议或 lxml 类型，所有返回值都是冻结的公共 DTO，可通过 `to_dict()` 或 `to_json()` 得到稳定的 JSON 数据。

```python
from davinci_gw.application import GatewayFacade
from davinci_gw.contracts import UpdateRequestDto

facade = GatewayFacade()
request = UpdateRequestDto(
    config_path=r"input\网关路由配置表_v4.84.xlsx",
    baseline_path=r"input\825E0GA.arxml",
)
prepared = facade.preview(request)
if prepared.session_id:
    result = facade.commit_prepared(
        prepared.session_id,
        r"output\825E0GA_v4.84.arxml",
    )
```

公共入口包括能力查询、联合校验、安全预览、确认生成、一步生成、主动释放和过期清理。预览结果按当前支持的功能动态分组；增加新的路由能力时，未来界面不需要改变固定统计字段。

Prepared Session 只存在于当前进程：默认有效期 15 分钟、最多保存 8 个，以随机 UUID 标识。预览时完成一次解析、规划和内存应用，提交复用同一工作树，不再次解析基准或重新规划。配置表与基准 ARXML 使用“大小、纳秒修改时间、SHA-256”三重指纹；提交开始前和原子替换前均会复核。会话成功后一次性消费，失败或取消后失效；同一会话的并发提交最多一个成功。

公共接口接受可选的中立进度观察者和线程安全取消令牌。取消只在安全阶段边界生效；原子替换前取消会清理临时文件且不生成目标，替换完成后即视为已提交，不会误报取消。观察者自身异常被隔离，不改变核心操作结果。

现有 CLI 保持原内部应用链路，作为回归和开发入口，避免 MCP 改变已有输出文本、退出码和用户习惯。MCP 是可选适配层，只依赖 `GatewayFacade`、公共 DTO、进度观察者与取消令牌；核心 CLI 的运行依赖中不强制安装 MCP SDK。

## 需要准备什么

1. 上游流程生成的标准配置表，文件名为 `*_vX.x.xlsx`。
2. 旧版工程导出的完整 ARXML，且包含 CanIf、Com、EcuC、PduR。
3. 一个与基准文件不同的输出路径。

直接报文路由的每条 `ADD`/`DELETE` 还必须填写 `PduR路由组`。一个目标腿属于多个组时使用英文分号分隔，例如：

```text
PduRRoutingPathGroup_DCAN;PduRRoutingPathGroup_Diag
```

名称必须与基准 ARXML 中现有 `PduRRoutingPathGroup` 的 `SHORT-NAME` 完全一致。工具不会根据目标网段猜组名，也不会创建、删除或重命名路由组。

工具不读取原始 Communication Routing Table，不解析 History，也不从自然语言推导需求。默认不覆盖已有输出；只有显式使用 `--overwrite` 才允许替换指定输出，但永远禁止把输出指向基准 ARXML。

## 安装

在 Python 3.12 环境中进入工程目录：

```powershell
python -m pip install .
```

开发环境：

```powershell
python -m pip install -e ".[dev]"
```

只安装桌面 GUI 可选依赖：

```powershell
python -m pip install ".[gui]"
python -m UI
```

只安装 Python MCP 可选依赖：

```powershell
python -m pip install ".[mcp]"
python -m davinci_gw.mcp
```

## 校验、预览与生成

```powershell
davinci-gw validate `
  --config "input\网关路由配置表_v4.84.xlsx" `
  --baseline "input\825E0GA.arxml"

davinci-gw preview `
  --config "input\网关路由配置表_v4.84.xlsx" `
  --baseline "input\825E0GA.arxml"

davinci-gw generate `
  --config "input\网关路由配置表_v4.84.xlsx" `
  --baseline "input\825E0GA.arxml" `
  --output "output\825E0GA_v4.84.arxml"
```

`validate` 只检查输入契约和 ARXML 基础结构。`preview` 在可丢弃工作树上完整规划 DELETE 和 ADD，显示新增、删除、已存在、已不存在、共享保留、超时清理和冲突，但不写文件。`generate` 执行同一份事务规划并生成输出。

成功报告会显示每类路由的实际新增/删除/跳过数、共享对象或超时的保留原因、输出路径和输出验证结果。

退出码：成功为 `0`；契约、业务或配置冲突为 `2`；文件损坏、I/O 或系统错误为 `3`。普通模式不显示 Python 堆栈；需要诊断时可在子命令前加 `--debug`。

## 如何理解结果

- “已存在”：ADD 目标的完整语义与现有对象一致，幂等跳过。
- “已不存在”：DELETE 目标腿在基准中已经没有，视为幂等成功，不是致命错误。
- “保留共享对象”：目标仍被其他路由引用，或内含人工子容器；工具只删除能唯一确认的路由腿。
- “保留超时”：源信号仍有其他目标/Mapping/ADD 使用，或 DELETE 超时证据不足。这是正常的安全决策。
- “缺失或不支持并跳过”：单条 ADD 缺少 DBC、HRH 或 TxBuffer 等依赖；其他可解析 ADD 仍可继续。
- “候选不唯一”或“语义冲突”：工具无法证明对象归属，会阻止整份输出。请按错误中的工作表、行号和 ARXML 路径在 DaVinci 中清理重复或修复引用。
- “路由组不存在/成员重复/悬空引用/删除后为空组”：工具会停止输出。DELETE 还会检查目标腿是否属于未在该行声明的其他组，避免误删仍被其他 RoutingGroup 使用的 DestPdu。

LIN 信号端点不使用 `PSMM ↔ LIN04` 之类的固定项目映射。当配置表的逻辑网段不出现在 ComIPdu 名称中时，工具只根据报文名、信号名、方向和 `ComIPduSignalRef` 关系接受全局唯一的 LIN 结构候选；若 CAN/LIN 或多个 LIN 中有同名候选，则阻止输出。

## 安全保证

- DELETE 会核对报文/信号完整身份、方向、CAN ID、通道和引用关系，不只凭 SHORT-NAME 删除。
- 直接报文 ADD 在 DestPdu 就绪后最后添加组成员；DELETE 先删除声明的成员引用，再基于删除投影判断 DestPdu 是否仍被引用。
- 不修改 RoutingGroup 的名称、GroupId、初始化状态和无关成员，也不自动创建或删除 RoutingGroup。
- HRH、TxBuffer、CanIf 控制器、硬件对象和 PduR BSW 模块配置永不在删除计划中。
- 任一规划、应用、序列化或临时输出验证失败，整个工作副本丢弃，不修改基准，不留目标或临时半成品。
- 输出会重新解析，复核四模块、新增/删除/保留语义、参数删除、内部引用、UUID 和 Handle ID。

完整字段规则见 [输入契约](docs/输入契约.md)，删除设计见 [第03轮开发日志](docs/第03轮开发日志.md)，公共接口与扩展架构见 [第04轮开发日志](docs/第04轮开发日志.md)，MCP 的交付证据见 [第05轮开发日志](docs/第05轮开发日志.md)，桌面版交付证据见 [第06轮开发日志](docs/第06轮开发日志.md)，PduR 路由组成员设计与验证见 [第07轮开发日志](docs/第07轮开发日志.md)。

## 许可证

本项目自身使用 [MIT License](LICENSE)。桌面发布包还随附 [第三方软件许可说明](THIRD_PARTY_NOTICES.md) 和相应许可证原文；正式对外交付前仍应完成公司合规或法务审核。
