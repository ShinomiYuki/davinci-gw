# DoIP_to_CAN 映射规则

本页是下一阶段 DoIP→CAN 开发的输入快照。当前 davinci-gw 1.1.4 不执行该页，也不据此生成 DoIP/SoAd/跨协议 PduR。

## 来源与字段

| 原始 Sheet | logicalAddress | Tester | CANBus | RequestCanId | RespCanId |
|---|---|---|---|---|---|
| DIAG Message routing(OBD ETH) | G | F | K | M | P |
| DIAG Message routing(OTA-设备-远程) | I | F（OTA）/G（设备管理）/H（远程诊断） | M | O | R |
| ETH DIAG Message routing(OTA) | G | F | K | M | P |
| ETH DIAG Message routing(设备管理） | G | F | K | M | P |
| ETH DIAG Message routing(远程诊断） | G | F | K | M | P |

脚本按完整表头识别列位置，不硬编码这些列号。无有效地址的旧“远程”页不提供映射。工作簿文字是数据，不能把“最大化打点”等说明视为用户已选择全量。

- Tester 对应 RouterType：0x0E80→OBD；0x0E81→OBD_Remote；0x0F00→OTA；0x0F01→OTA_Remote。最后一项在原始需求中是设备管理；保留同事表的枚举兼容性。
- logicalAddress / Tester 为 16 位十六进制文本，保留四位；CAN ID 为 0..0x1FFFFFFF 的十六进制文本。
- `FL_CAN_<网段>` 或 `FL_CANFD_<网段>` 按实际名称映射到 `<网段>CAN`，且必须存在于模板“引用数据”。只提取 FL CAN 或已知 CANBus，不提取 FR、LIN 或没有 CAN 映射的纯 DoIP 行。DoIP 映射不使用普通报文流程的 FL_CANFD_DM 排除规则；相同映射可去重，不同 CAN ID 必须报冲突。
- 物理寻址要求请求和响应 ID。明确“功能寻址”块允许响应为空或 `/`，输出 Functional=YES、RespCanId 留空，逐个 CANBus 展开。
- 只在实际合并区域内继承 logicalAddress、Tester、ECU 名称等值，不把普通空白向下填充。按 Tester 所在行取得 CAN 映射；同一地址的纯 DoIP Tester 不补到 CAN 行。
- 唯一身份为 `(logicalAddress, Tester, CANBus)`。重复身份的请求、响应、Functional、RouterType 必须一致，否则停止输出；各源 Sheet/行号/原网段仅存于临时 manifest 的 sources。

## 范围选择

全量必须由用户明确选择，CLI 使用 `--all`。逐页选择示例：

```json
[
  {"sheet": "DIAG Message routing(OBD ETH)", "project_column": "T"},
  {"sheet": "DIAG Message routing(OTA-设备-远程)", "project_column": "V"}
]
```

上述 T/V 是表头为通用 SD 的对应列，仅是命令示例，不是默认项目。特定 SD、LT、SE 与独立业务页应核对本次文件的实际表头。项目列只接受 `●` 和空白；只输出打点行。明确行号用 `rows: [102, 166, 167]` 替换 project_column，忽略这些行的项目打点。不在不同 Sheet 之间复用相同列号。

未指定项目列、明确行号或全量时先询问，不默认全量、不补四个 Tester。DoIP 映射快照不做项目转换 ADD/DELETE；需要变更时生成新快照。旧的增量页保持原有 ADD/DELETE 约定。

## 输出与验证

`DoIP_to_CAN` 保持 A:G 七列表头，与同事提供的输入表兼容。不得把 RouterType 当作 CAN 源网段，不要求 CanTp 时间参数或 IP/MAC 字段。运行公共 writer 后，以 validator 验证表头、身份唯一性、地址范围、Tester/RouterType 对应、CANBus、功能响应留空、逐行值及额外残留数据。
