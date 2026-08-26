# DaVinci 网关路由工具

本工具面向 DaVinci/MICROSAR 网关工程：读取标准化网关路由配置表和旧版本工程导出的完整 ARXML，为后续安全更新 CanIf、EcuC、PduR、Com 配置做准备。

> 第01轮暂不写入路由配置，只用于检查标准配置表和基准ARXML是否可以被后续流程安全处理。

## 当前可以做什么

- 检查配置表文件名中的目标版本，例如 `网关路由配置表_v4.84.xlsx`。
- 按表头读取“引用数据”“直接报文路由”“信号路由”，不依赖固定列号。
- 校验 ADD、DELETE、CAN ID、报文长度、超时、必填项、重复和冲突。
- 安全解析大型 ARXML，识别 AUTOSAR 命名空间、Schema 以及 CanIf、Com、EcuC、PduR 四个模块。
- 预览直接报文路由和信号路由的新增/删除数量。
- 通过内部接口无修改地原子写出 ARXML 副本，用于验证基线能否被安全处理。

## 需要准备的文件

1. 上游流程生成的标准配置表，文件名应为 `*_vX.x.xlsx`。
2. 旧版本 DaVinci 工程导出的完整 ARXML，且包含 CanIf、Com、EcuC、PduR 四个模块。

工具不会修改这两个输入文件。建议将它们放在本地 `input` 目录；该目录中的 `.xlsx` 和 `.arxml` 已被 Git 忽略。

## 安装

在 Python 3.12 环境中进入工程目录后运行：

```powershell
python -m pip install .
```

开发和测试环境可使用：

```powershell
python -m pip install -e ".[dev]"
```

## 校验输入

```powershell
davinci-gw validate `
  --config "input\网关路由配置表_v4.84.xlsx" `
  --baseline "input\825E0GA.arxml"
```

校验成功时会显示目标版本、AUTOSAR Schema、四模块检查结果，并明确提示尚未执行路由写入。

## 预览变更

```powershell
davinci-gw preview `
  --config "input\网关路由配置表_v4.84.xlsx" `
  --baseline "input\825E0GA.arxml"
```

预览会显示直接报文路由和信号路由的 ADD/DELETE 数量、引用数据中的 CAN 通道数量，以及基准 ARXML 检查结果。命令不会生成新版 ARXML。

成功退出码为 `0`；配置表契约或必要模块错误为 `2`；文件损坏、Excel 无法读取或 XML 无法解析等系统类错误为 `3`。普通模式只显示中文处理建议，如需诊断意外异常，可在子命令前增加 `--debug`。

## 常见错误

- “无法从配置表文件名识别目标版本”：将文件改为 `*_vX.x.xlsx`，例如 `网关路由配置表_v4.84.xlsx`。
- “缺少必需表头/工作表”：不要依赖列位置，请补回提示中点名的工作表或第一行表头。
- “仅支持ADD或DELETE”：将操作类型修改为 `ADD` 或 `DELETE`，大小写不限。
- “路由重复/冲突”：同一完整路由键只能保留一个操作，不能同时 ADD 和 DELETE。
- “未找到某模块”：重新从工程导出包含 CanIf、Com、EcuC、PduR 的完整 ARXML。
- “输出文件已存在”：无修改往返接口默认拒绝覆盖，请选择新路径。

## 当前尚不能做什么

第01轮不新增或删除 CanIf、EcuC、PduR、Com 节点，不写入超时参数，不处理诊断/CanTp 路由，不合并多个 ARXML，不调用 DaVinci 自动导入或 Validate，也不提供 GUI、`generate` 命令或安装包。实际路由写入将在后续开发轮次实现。

更详细的字段规则见 [输入契约](docs/输入契约.md)，本轮实现和验证记录见 [第01轮开发日志](docs/第01轮开发日志.md)。
