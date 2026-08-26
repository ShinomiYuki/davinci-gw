# DaVinci 网关路由工具

本工具读取标准化网关路由配置表和旧版本 DaVinci/MICROSAR 工程导出的完整 ARXML，生成包含本轮 ADD 路由的新版完整 ARXML。输入 Excel 和基准 ARXML 始终只读，未受影响的模块、节点、注释和属性会原样保留。

## 当前能力

- 校验配置表版本、工作表、表头、字段、重复和冲突。
- 检查 ARXML 的 AUTOSAR Schema 以及 CanIf、Com、EcuC、PduR 四个必要模块。
- 预览直接报文和信号路由的 ADD/DELETE 数量。
- 新增 EcuC PDU、CanIf Rx/Tx PDU、PduR RoutingPath/SrcPdu/DestPdu。
- 新增 ComGwMapping、ComGwSource 和一个或多个 ComGwDestination。
- 将配置表中已经标准化的“超时时间”和“超时值”写到源 ComSignal；空值不写零。
- 支持同一源报文或源信号的一对多路由、完整语义幂等检查和冲突阻止。
- 原子写出并重新解析输出，复核新增对象、内部引用、UUID 和 Handle ID。

当前不支持 DELETE、诊断/CanTp 路由、DaVinci GUI 自动操作和图形界面。

## 输入与输出

需要准备：

1. 上游流程生成的标准配置表，文件名为 `*_vX.x.xlsx`。
2. 旧版本工程导出的完整 ARXML，且包含 CanIf、Com、EcuC、PduR。
3. 一个与基准文件不同的输出路径。

工具不会修改或覆盖输入文件。默认也不会覆盖已有输出；只有显式使用 `--overwrite` 才允许替换指定输出，但仍禁止把输出指向基准 ARXML。

如果配置表含有任何 DELETE，`generate` 会停止。原因是当前版本只实现 ADD，忽略 DELETE 会生成一个看似成功、实际不完整的目标版本。`validate` 和 `preview` 仍可正常读取并显示这些 DELETE。

## 安装

在 Python 3.12 环境中进入工程目录：

```powershell
python -m pip install .
```

开发环境：

```powershell
python -m pip install -e ".[dev]"
```

## 生成新版 ARXML

```powershell
davinci-gw generate `
  --config "input\网关路由配置表_v4.84.xlsx" `
  --baseline "input\825E0GA.arxml" `
  --output "output\825E0GA_v4.84.arxml"
```

成功输出类似：

```text
目标版本：4.84

直接报文路由：
  新增：9
  已存在并跳过：0
  缺失或不支持并跳过：0

信号路由：
  新增：0
  已存在并跳过：0
  缺失或不支持并跳过：2

输出文件：...\825E0GA_v4.84.arxml
输出验证：通过
```

缺少单条路由依赖的 DBC 对象时，工具会显示带工作表行号和对象名称的警告，跳过该路由并继续处理其他有效 ADD。请务必阅读“缺失或不支持并跳过”计数；成功写出不代表所有输入行都已生成。

## 校验与预览

```powershell
davinci-gw validate --config "input\网关路由配置表_v4.84.xlsx" --baseline "input\825E0GA.arxml"

davinci-gw preview --config "input\网关路由配置表_v4.84.xlsx" --baseline "input\825E0GA.arxml"
```

退出码：成功为 `0`；契约、业务或配置冲突为 `2`；文件损坏、I/O 或系统错误为 `3`。普通模式不显示 Python 堆栈；需要诊断时可在子命令前加 `--debug`。

## 常见问题

- 找不到 HRH：检查“引用数据”的 `CanIfHrh名称`，并先在 DaVinci 中导入或配置对应源 CAN 通道对象。该路由会被明确警告并跳过。
- 找不到 TxBuffer：检查 `CanIfTxBuffer名称` 是否与基准 ARXML 一致。只有受影响的目标腿会跳过。
- 找不到 ComSignal/ComIPdu：通常表示目标版本 DBC 尚未导入，或报文、信号、网段名称不匹配。先在 DaVinci 中补齐 DBC 配置后重试；其他可解析路由仍会继续。
- 已有同名对象但参数或引用不同：这是阻断冲突。请根据提示核对 CAN ID、Length/DLC、类型、HRH/TxBuffer、PduR 或 Com 引用，修复基准或配置表后再生成。
- 配置表包含 DELETE：当前版本拒绝正式生成，避免遗漏删除。可先用 `preview` 查看数量，等待 DELETE 功能实现或提供不含 DELETE 的正式输入。
- 输出文件已存在：更换路径，或确认目标正确后显式增加 `--overwrite`。

完整字段规则见 [输入契约](docs/输入契约.md)，实现与验证记录见 [第02轮开发日志](docs/第02轮开发日志.md)。
