"""docs/specs/midi-mapping.md（外部正典のコピー）と実装定数の同期検証。

midi-mapping.md は別プロジェクト（Unity / コントローラ側）で更新され、本リポジトリへ
そのままコピーされる。本テストは仕様書の機械可読な部分（CC 早見表・コード値・規約値）を
パースして cc_map / messaging の定数と照合し、仕様更新への実装追従漏れを検出する。

仕様書更新後に pytest を実行して落ちたテストが、実装側で直すべき箇所を指す。
パースは仕様書の安定した構造（早見表・コード値表・太字の規約文）にのみ依存する。
フォーマット自体が変わってパースできなくなった場合も assert で明示的に失敗する。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import cc_map
import messaging

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "specs" / "midi-mapping.md"

# 早見表の用途欄ラベル -> (実装定数, 仕様上の方向)
# 方向は仕様（ゲーム視点）の表記: IN = コントローラ→ゲーム = 本シミュレータの送信。
NAMED_CCS: dict[str, tuple[int, str]] = {
    "CMDRSP_STATUS": (cc_map.CMDRSP_STATUS_CC, "IN"),
    "EVT_ARG": (cc_map.EVT_ARG_CC, "IN"),
    "EVT_OP": (cc_map.EVT_OP_CC, "IN"),
    "CMD_ARG1": (cc_map.CMD_ARG1_CC, "OUT"),
    "CMD_ARG2": (cc_map.CMD_ARG2_CC, "OUT"),
    "CMD_OP": (cc_map.CMD_OP_CC, "OUT"),
    "EVTRSP_STATUS": (cc_map.EVTRSP_STATUS_CC, "OUT"),
}

AXIS_LABELS = ("左スティック X", "左スティック Y", "右スティック X", "右スティック Y")


@pytest.fixture(scope="module")
def spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def _quick_table_rows(spec_text: str) -> list[tuple[str, str, str]]:
    """早見表セクションから (CC欄, 用途欄, 方向欄) の行を抽出する。"""
    assert "## 早見表（CC 一覧）" in spec_text, "仕様書に早見表セクションが見つからない"
    section = spec_text.split("## 早見表（CC 一覧）", 1)[1]
    rows = []
    for line in section.splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(IN|OUT)\s*\|", line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    assert rows, "早見表から CC 行をパースできない（フォーマット変更の可能性）"
    return rows


class TestQuickTable:
    """早見表（CC 一覧）と CC 定数の照合。"""

    def test_axis_cc_pairs(self, spec_text: str) -> None:
        rows = _quick_table_rows(spec_text)
        for axis_index, label in enumerate(AXIS_LABELS):
            matched = [r for r in rows if r[1].startswith(label)]
            assert len(matched) == 1, f"早見表に「{label}」の行が 1 行見つからない: {matched}"
            cc_text, _, direction = matched[0]
            m = re.match(r"^(\d+)\s*/\s*(\d+)$", cc_text)
            assert m, f"「{label}」の CC 欄 '{cc_text}' を MSB/LSB としてパースできない"
            assert cc_map.CC_AXES[axis_index] == (int(m.group(1)), int(m.group(2))), (
                f"{label} の CC ペアが仕様と不一致"
            )
            assert direction == "IN"

    def test_button_ccs(self, spec_text: str) -> None:
        rows = _quick_table_rows(spec_text)
        matched = [r for r in rows if re.match(r"^ボタン 0[–-]9\b", r[1])]
        assert len(matched) == 1, "早見表に「ボタン 0–9」の行が 1 行見つからない"
        cc_text, _, direction = matched[0]
        m = re.match(r"^(\d+)[–-](\d+)$", cc_text)
        assert m, f"ボタンの CC 欄 '{cc_text}' を範囲としてパースできない"
        assert cc_map.BUTTON_CCS == tuple(range(int(m.group(1)), int(m.group(2)) + 1)), (
            "ボタン CC 範囲が仕様と不一致"
        )
        assert direction == "IN"

    @pytest.mark.parametrize(
        ("label", "constant"),
        [("Preset", cc_map.PRESET_CC), ("Error", cc_map.ERROR_CC), ("State", cc_map.STATE_CC)],
    )
    def test_scalar_ccs(self, spec_text: str, label: str, constant: int) -> None:
        rows = _quick_table_rows(spec_text)
        matched = [r for r in rows if r[1].startswith(label)]
        assert len(matched) == 1, f"早見表に「{label}」の行が 1 行見つからない"
        cc_text, _, direction = matched[0]
        assert constant == int(cc_text), f"{label} の CC が仕様と不一致"
        assert direction == "IN"

    @pytest.mark.parametrize(("name", "expected"), list(NAMED_CCS.items()))
    def test_messaging_ccs(self, spec_text: str, name: str, expected: tuple[int, str]) -> None:
        constant, expected_direction = expected
        rows = _quick_table_rows(spec_text)
        # 用途欄の先頭トークン完全一致＋方向で識別する（CMD_OP と EVT_OP 等の部分一致を防ぐ）。
        matched = [
            r for r in rows
            if re.match(rf"^{re.escape(name)}\b", r[1]) and r[2] == expected_direction
        ]
        assert len(matched) == 1, f"早見表に {name}（{expected_direction}）の行が 1 行見つからない"
        assert constant == int(matched[0][0]), (
            f"{name} の CC が仕様と不一致（仕様={matched[0][0]} / 実装={constant}）"
        )


class TestCodeValues:
    """コード値（STATUS / opcode）の照合。"""

    def test_status_codes(self, spec_text: str) -> None:
        pairs = re.findall(
            r"^\|\s*(\d+)\s*\|\s*(OK|UNKNOWN_OP|INVALID_ARG|REJECTED)（", spec_text, re.M
        )
        parsed = {name: int(value) for value, name in pairs}
        assert parsed == {
            "OK": cc_map.STATUS_OK,
            "UNKNOWN_OP": cc_map.STATUS_UNKNOWN_OP,
            "INVALID_ARG": cc_map.STATUS_INVALID_ARG,
            "REJECTED": cc_map.STATUS_REJECTED,
        }, "STATUS コードが仕様と不一致（またはパース不能）"

    def test_set_preset_opcode(self, spec_text: str) -> None:
        m = re.search(r"^\|\s*(\d+)\s*\|\s*\*\*SetPreset\*\*", spec_text, re.M)
        assert m, "opcode 表から SetPreset の行をパースできない"
        assert cc_map.CMD_SET_PRESET == int(m.group(1)), "SetPreset opcode が仕様と不一致"

    def test_event_opcodes(self, spec_text: str) -> None:
        # 例示扱いだが実装はこの値に合わせているため、表が変わったら検出する。
        rows = re.findall(
            r"^\|\s*(\d+)\s*\|[^|]+\|\s*(HeartBeat|ButtonCombo|SensorTrigger)\s*\|", spec_text, re.M
        )
        parsed = {name: int(op) for op, name in rows}
        assert parsed == {
            "HeartBeat": cc_map.EVT_HEARTBEAT,
            "ButtonCombo": cc_map.EVT_BUTTON_COMBO,
            "SensorTrigger": cc_map.EVT_SENSOR_TRIGGER,
        }, "イベント opcode が仕様の例示と不一致（またはパース不能）"


class TestProtocolConstants:
    """規約値（しきい値・seq ビット・タイムアウト）の照合。"""

    def test_button_on_threshold(self, spec_text: str) -> None:
        m = re.search(r"しきい値\s*(\d+)\s*以上で\s*ON", spec_text)
        assert m, "仕様書からボタン ON しきい値をパースできない"
        assert cc_map.BUTTON_ON_THRESHOLD == int(m.group(1))

    def test_seq_bit(self, spec_text: str) -> None:
        m = re.search(r"bit6（値\s*(\d+)）をシーケンスビット", spec_text)
        assert m, "仕様書から seq ビット値をパースできない"
        assert cc_map.SEQ_BIT == int(m.group(1))

    def test_response_timeout(self, spec_text: str) -> None:
        m = re.search(r"タイムアウト既定は\s*\*\*(\d+)\s*フレーム", spec_text)
        assert m, "仕様書から応答タイムアウトをパースできない"
        assert messaging.RESPONSE_TIMEOUT_TICKS == int(m.group(1))
