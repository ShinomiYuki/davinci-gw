# CAN诊断路由规则

## 范围

- 只处理 `DIAG Message routing(OBD CAN)` 和 `DIAG Message routing(OBD ETH)` 中用户通过项目列或明确行号选中的 CAN 诊断需求；明确行号与操作优先于打点。
- 每条需求都是不可拆分的双向配置：请求端 → 网关 → 应答端，以及应答端 → 网关 → 请求端。ADD/DELETE 必须成对一致。
- OBD ETH 的 K/L/M/O/P 分别是目标网络、CAN 请求报文名、请求 CAN ID、CAN 应答报文名、应答 CAN ID。N 是 LIN 请求帧 ID，CAN 诊断范围内忽略。页名不决定请求侧为 DoIP；普通 CAN 诊断仍需明确的请求 CAN 通道和双向 PduR。
- 排除纯 DoIP 和 DoLIN 行，不生成以太网或 LIN 诊断配置。

## 项目打点与操作

- OBD CAN 项目列只允许 P/Q/R；OBD ETH 项目列只允许 T/U/V/W。
- 单列模式中 `●` 生成 `ADD`，空白不输出。
- 转换模式中，旧列 `●`、新列空白生成 `DELETE`；旧列空白、新列 `●` 生成 `ADD`；状态相同不输出。
- 只接受 `●` 和空白。其他符号属于输入冲突，应一次性列出全部问题后停止写入。

## 通道类型和长度

通道名称以模板“引用数据”为准。

| 通道 | Rx类型 | Tx类型 | Length |
|---|---|---|---|
| BDCAN、DMCAN、DGCAN | `STANDARD_NO_FD_CAN` | `STANDARD_CAN` | 8 |
| 其他CAN通道 | `STANDARD_FD_CAN` | `STANDARD_FD_CAN` | 64 |

该规则按最终 CAN 通道判断，不按需求表网络名称中是否含 `CANFD` 判断。例如映射到 DGCAN 后仍使用经典 CAN 8 字节。需求表协议列只用于识别并排除纯 DoIP/DoLIN 行，不覆盖最终通道规则。

## OBD CAN

- 请求报文名和请求 ID：F/G。
- 应答报文名和应答 ID：H/I。
- 应答端目标网络：L，映射为应答端 CAN 通道。
- 请求端 CAN 通道不在需求表中，由用户按本次运行明确提供。
- 请求端与应答端的 N_As、N_Bs、N_Cs、N_Ar、N_Br、N_Cr、BlockSize、STmin 都必须由用户提供。
- 输出中请求 ID 同时写入请求端 `CANID_REQ` 和应答端 `CANID_REQ`；应答 ID 同时写入请求端 `CANID_RES` 和应答端 `CANID_RES`。

## OBD ETH

- 请求报文名和请求 ID：L/M。
- 应答报文名和应答 ID：O/P。
- 应答端目标网络：K，映射为应答端 CAN 通道。
- 请求端 CAN 通道由用户或已确认的同一工作簿约定提供；K 仅指定目标通道。EEA5.1 Robotaxi OBD ETH 工作簿的请求通道已确认为 `DGCAN`，其他工作簿不可直接套用。
- 标准表两端都填写 M/P、各自通道的 Rx/Tx 类型与 Length，以及两端的八个传输参数。`诊断入口类型=OBD_ETH` 记录需求来源，不表示 DoIP。普通 CAN 诊断生成请求和应答两条 PduR RoutingPath。
- 用户给出基准 ARXML 并要求参考已有配置时，从同通道、同物理/功能寻址类型的既有 CAN 诊断配置推导时间参数；不一致或缺失时再询问。
- 用户明确要求 DoIP→CAN 时进入独立的映射输入工作流，见 `doip-can-routing-rules.md`；不把该页当作普通 CAN→CAN，也不生成 DoIP ARXML。

## 输出

- `诊断入口类型` 只允许 `OBD_CAN` 或 `OBD_ETH`。
- `操作类型` 只允许 `ADD` 或 `DELETE`。
- 报文 ID 保持十六进制文本，名称保持需求表原始拼写。
- 工具 1.1.4：同一 TP 路径的多个目标共用 Queue；既有路径复用既有队列并保留参数；独立新路径分别创建 Queue，含 PduRSharedBufferQueue，Depth=3、TpThreshold=0；7DF/功能寻址 Depth=5、TpThreshold=0。新建 PduRSrcPdu 短名带唯一身份，不得对不同 Handle ID 重复使用 `Source` 等通名。
- 缺字段、通道无法映射或同一诊断身份重复时，在写入前阻断；不使用模板示例值兜底。
