"""路由对象命名必须编码完整身份：同一报文名在同一通道上可以有多个 CAN ID。"""

from davinci_gw.application.generate import generate_inputs
from davinci_gw.arxml.document import ArxmlDocument
from davinci_gw.modules import definitions as defs
from tests.conftest import direct_row


def _short_names(output, definition: str, namespace: str) -> list[str]:
    document = ArxmlDocument.load(output)
    index = document.build_index()
    names = []
    for node in index.find_by_definition_ref(definition):
        short = node.find(f"{{{namespace}}}SHORT-NAME")
        names.append(short.text if short is not None else "")
    return sorted(names)


def test_same_message_on_same_channel_keeps_distinct_can_id_objects(
    workbook_factory, arxml_factory, tmp_path,
):
    """同一源报文名与源通道、不同 CAN ID 的两条合法路由必须生成两组不同名的对象。

    名字里少了 CAN ID，两条路由会撞成同名对象：规划阶段按「同路径不同内容」
    阻断（用户在配置表侧无法自行修复），或者后写的覆盖先写的。
    """
    config = workbook_factory(
        filename="naming_distinct_canid_v4.84.xlsx",
        direct_rows=(
            direct_row(),
            direct_row(**{
                "源网段报文CANID": "0x300",
                "目标网段报文名称": "DST_MSG2",
                "目标网段报文CANID": 0x400,
            }),
        ),
        signal_rows=(),
    )
    output = tmp_path / "naming_distinct_canid_output.arxml"

    report = generate_inputs(config, arxml_factory(), output)

    assert report.is_success, [(i.code, i.message) for i in report.all_issues]
    assert output.exists()
    namespace = ArxmlDocument.load(output).namespace
    rx_names = _short_names(output, defs.CANIF_RX, namespace)
    tx_names = _short_names(output, defs.CANIF_TX, namespace)
    ecuc_names = _short_names(output, defs.ECUC_PDU, namespace)

    assert "GWT_CanIf_SRC_MSG_100_SRC_CAN_Rx" in rx_names
    assert "GWT_CanIf_SRC_MSG_300_SRC_CAN_Rx" in rx_names
    assert "GWT_EcuC_SRC_MSG_100_SRC_CAN_Rx" in ecuc_names
    assert "GWT_EcuC_SRC_MSG_300_SRC_CAN_Rx" in ecuc_names
    assert "GWT_CanIf_DST_MSG_200_DST_CAN_Tx" in tx_names
    assert "GWT_CanIf_DST_MSG2_400_DST_CAN_Tx" in tx_names
    # 名字是唯一身份：不得出现重复的短名。
    assert len(rx_names) == len(set(rx_names))
    assert len(tx_names) == len(set(tx_names))
    assert len(ecuc_names) == len(set(ecuc_names))
