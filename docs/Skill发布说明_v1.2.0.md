# 网关配置表 Skill 1.2.0

本次发布更新上游 `build-gateway-routing-config` Skill，工具应用版本仍为 1.1.4。新增 DoIP→CAN 映射表的提取、写入和校验，作为下一阶段诊断开发的输入；不新增 DoIP/SoAd 或跨协议 PduR 的 ARXML 生成。

## 模板变更

- 新增 `DoIP_to_CAN`，沿用同事表的七列：Tester、logicalAddress、RouterType、CANBus、RequestCanId、RespCanId、Functional。
- 删除“诊断报文路由参数”“Sheet1”“命名规则”。
- “直接报文路由需求描述”改名为“需求描述”，保留原有参数与说明的排版，按现有字段及 1.1.4 实际行为重写 CanIf、Com、EcuC、CanTp、PduR，另说明 DoIP 映射的使用边界。
- 保留直接报文、信号、CAN 诊断输入页及原有格式、引用数据与下拉校验。

## 映射提取

以 logicalAddress 为中心，合并 OBD ETH、OTA/设备管理/远程综合页与对应独立业务页中的实际 Tester 及 CAN 映射。必须明确全量、每页项目打点列或行号；不按文件名默认项目，不自动补齐四个 Tester，不把纯 DoIP 行中的 Tester 套用到 CAN 行。

物理寻址保留请求和响应 CAN ID，Functional 留空；明确功能寻址按 CANBus 展开，Functional=YES，响应留空。相同 logicalAddress/Tester/CANBus 的完全相同映射去重并保留内部来源记录，请求/响应或寻址类型冲突时阻止输出。新页是需求快照，不增加 ADD/DELETE 列。

Tester/RouterType 保持同事表兼容：0x0E80=OBD，0x0E81=OBD_Remote，0x0F00=OTA，0x0F01=OTA_Remote；0x0F01 在原始需求中对应设备管理。

## 使用与验证

下载发布 ZIP，将 `build-gateway-routing-config` 目录安装到个人 skills 目录。源码与模板同时保存在本仓库的 `skills/build-gateway-routing-config`，后续统一在仓库维护并同步安装副本。

按 [Skill 工作流](../skills/build-gateway-routing-config/SKILL.md) 创建选择 JSON，依次执行 `extract_doip_can_mappings.py`、`write_gateway_config.py`、`validate_output.py`。仅映射表的流程不要求 DBC、ARXML、请求源 CAN 通道或 CanTp 时间参数。

局部测试覆盖实际 Tester 数量、跨页去重、项目打点、合并功能寻址、地址与枚举校验、冲突阻止、写入及逐行复核。原始客户表仅用于本地验证，不包含在仓库或发布包中。

18 项局部测试通过，含数字单元格及合并区域的地址回归。原始需求全量提取、写入、复核得到 533 条映射及 124 个逻辑地址；按两张主表通用 SD 打点列选择得到 472 条映射及 108 个逻辑地址。两份输出均通过现有 1.1.4 读取校验。另以单页明确行号跑通三个 CLI 步骤，验证只保留该页实际存在的 Tester。

原生审查发现并修复本次数字单元格被文本化后误按十六进制解析的问题。另记录旧写表脚本既有的输出命名问题：同一秒重复解析已存在的输出路径时，时间戳候选未再次检查是否存在。本次按范围要求保留原行为；实际生成应使用尚不存在的明确输出路径。
