---
name: remove-routed-tx-truncation
description: 删除既有 ARXML 中普通直接报文路由及 CanTp 诊断路由所用 CanIf Tx 的 CanIfTxPduTruncation 参数。按实际引用识别并输出新文件；保留不参与路由的 Tx、COM 信号路由及业务共享不明确的对象。
metadata:
  version: "1.0.0"
---

# 清理路由 Tx Truncation

对用户提供的完整本地 ARXML，删除普通直接报文路由和 CanTp 诊断路由所用 CanIf Tx 容器内的 `CanIfTxPduTruncation` 参数节点，无论原值为 true 还是 false。不参与路由的 CanIf/CanTp Tx 保持原样。输入始终只读，输出必须为尚不存在的新 `.arxml` 文件。

入口为 `scripts/remove_routed_tx_truncation.py`，使用 Python 3.12 及 lxml。脚本可独立运行，无须导入仓库应用。先执行只读预览：

```powershell
python <skill目录>/scripts/remove_routed_tx_truncation.py --input D:/工程/baseline.arxml
```

检查返回 JSON 中每个候选的路径、路由、删除数量和跳过原因。用户已明确要求清理时直接执行输出；只有调查请求时，先展示候选并取得修改授权：

```powershell
python <skill目录>/scripts/remove_routed_tx_truncation.py --input D:/工程/baseline.arxml --output D:/工程/cleaned.arxml
```

识别依据是 `CanIfRx → EcuC → PduRSrcPdu → COMMUNICATION_INTERFACE RoutingPath → PduRDestPdu → EcuC → CanIfTx`，并解析 PduR BSW 模块引用到实际 CanIf 模块。Rx 回调必须为 PduR，目标 Tx Confirmation UL 必须为 NONE。不凭 GWT 前缀、Diag 字样、CAN ID 或路由组名称判断业务类型。

CanTp Tx 必须通过其底层 NPdu 或流控 PDU、所属 N-SDU 和上层 EcuC PDU，关联到完整的 PduR TRANSPORT_PROTOCOL 路由。核对两端实际 CanIf 数据 PDU（或对端 DoIP PDU）及模块归属；缺少真实源接收端点时跳过。只有本地诊断配置而没有 PduR 路由证据时保留。普通直接路由和 CanTp 诊断按各自引用链分别判断，不因名称含 Diag 就改变分类。命令行 JSON 固定使用 UTF-8。

源链不完整、重复路径或多重引用导致身份不唯一、目标被 COM 或无法识别的其他业务共享时，脚本保留该目标并报告原因。多个已证明的普通直接路由可共享同一个 Tx。不要为增加命中数量而放宽条件或猜测。

返回 `deleted_parameter_count`、`changed`、`already_absent`、`skipped` 及输出路径。预览中的删除数量是预计数量，指定输出并成功返回后才是实际删除数量。只修改候选 Tx 中该参数节点，保留其他参数、引用、Rx 和无关 Tx。重复执行时缺失参数计入 `already_absent`，不会重新创建参数。输出不覆盖基准或已有文件，失败不得声称成功。

交付时报告删除数量、对象路径、跳过或冲突原因及新文件链接。参数节点缺失与显式 false 是不同配置；未在具体 DaVinci 工程验证时，不能声称界面已显示 false 或 Validate/Generate 已通过。
