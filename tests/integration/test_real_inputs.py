"""使用本地真实输入执行的慢速集成测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from davinci_gw.application.preview import preview_inputs
from davinci_gw.application.validate import inspect_baseline, write_roundtrip_copy


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "input" / "网关路由配置表_v4.84.xlsx"
BASELINE = ROOT / "input" / "825E0GA.arxml"


def fingerprint(path: Path) -> tuple[int, int, str]:
    """计算基线大小、纳秒时间戳和 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


@pytest.mark.slow
@pytest.mark.skipif(not CONFIG.exists() or not BASELINE.exists(), reason="本地真实输入不存在")
def test_real_inputs_and_roundtrip(tmp_path: Path) -> None:
    before = fingerprint(BASELINE)
    preview = preview_inputs(CONFIG, BASELINE)
    assert preview.is_valid, [issue.message for issue in preview.validation.issues]
    assert preview.target_version == "4.84"
    assert preview.reference_count == 12
    assert (preview.direct_add_count, preview.direct_delete_count) == (9, 1)
    assert (preview.signal_add_count, preview.signal_delete_count) == (2, 2)

    inspection = inspect_baseline(BASELINE)
    assert inspection.namespace_uri == "http://autosar.org/schema/r4.0"
    assert inspection.schema_filename == "AUTOSAR_00049.xsd"
    assert {name: info.definition_ref for name, info in inspection.modules.items()} == {
        "CanIf": "/MICROSAR/CanIf", "Com": "/MICROSAR/Com",
        "EcuC": "/MICROSAR/EcuC", "PduR": "/MICROSAR/PduR",
    }
    output = tmp_path / "roundtrip.arxml"
    write_roundtrip_copy(BASELINE, output)
    assert inspect_baseline(output).schema_filename == "AUTOSAR_00049.xsd"
    assert fingerprint(BASELINE) == before
