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
SLIDER_LABELS = ("Slider1", "Slider2", "Slider3", "Slider4")

# opcode 表の名称（英字部分）-> 実装定数
OPCODE_CONSTANTS: dict[str, int] = {
    "Ping": cc_map.OP_PING,
    "Reset": cc_map.OP_RESET,
    "SetMode": cc_map.OP_SET_MODE,
    "SetZero": cc_map.OP_SET_ZERO,
    "SetPreset": cc_map.OP_SET_PRESET,
    "SetValve": cc_map.OP_SET_VALVE,
}

OPCODE_SECTION_MARKER = "**opcode（bit0–5・コマンド/イベント共通の番号空間）**"


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


def _opcode_table_rows(spec_text: str) -> list[tuple[int, str, str, str]]:
    """コード値セクションの opcode 表から (op, 名称, 方向欄, ARG1欄) を抽出する。"""
    assert OPCODE_SECTION_MARKER in spec_text, "仕様書に opcode 表セクションが見つからない"
    section = spec_text.split(OPCODE_SECTION_MARKER, 1)[1]
    section = section.split("###", 1)[0]  # 次の見出しまで（早見表等への誤マッチを防ぐ）
    rows = []
    for line in section.splitlines():
        m = re.match(
            r"^\|\s*(\d+)\s*\|\s*([A-Za-z]+)[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line
        )
        if m:
            rows.append((int(m.group(1)), m.group(2), m.group(3), m.group(4)))
    assert rows, "opcode 表から行をパースできない（フォーマット変更の可能性）"
    return rows


def _normalize_direction(cell: str) -> str:
    """方向欄（例: '**G⇄C（双方向）**', 'G→C'）から方向トークンを取り出す。"""
    return cell.replace("*", "").split("（", 1)[0].strip()


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

    def test_slider_cc_pairs(self, spec_text: str) -> None:
        """使用中の Slider1–4 の CC ペア照合（予約帯 5–8 は未使用のため対象外）。"""
        rows = _quick_table_rows(spec_text)
        for slider_index, label in enumerate(SLIDER_LABELS):
            matched = [r for r in rows if re.match(rf"^{label}\b", r[1])]
            assert len(matched) == 1, f"早見表に「{label}」の行が 1 行見つからない: {matched}"
            cc_text, _, direction = matched[0]
            m = re.match(r"^(\d+)\s*/\s*(\d+)$", cc_text)
            assert m, f"「{label}」の CC 欄 '{cc_text}' を MSB/LSB としてパースできない"
            assert cc_map.SLIDER_CCS[slider_index] == (int(m.group(1)), int(m.group(2))), (
                f"{label} の CC ペアが仕様と不一致"
            )
            assert direction == "IN"

    def test_button_ccs(self, spec_text: str) -> None:
        """ボタン行「ボタン 0–N」をパースし、本数（個数変更含む）と CC 範囲を照合する。"""
        rows = _quick_table_rows(spec_text)
        matched = [r for r in rows if re.match(r"^ボタン 0[–-]\d+\b", r[1])]
        assert len(matched) == 1, "早見表に「ボタン 0–N」の行が 1 行見つからない"
        cc_text, label, direction = matched[0]
        label_m = re.match(r"^ボタン 0[–-](\d+)\b", label)
        m = re.match(r"^(\d+)[–-](\d+)$", cc_text)
        assert m, f"ボタンの CC 欄 '{cc_text}' を範囲としてパースできない"
        assert len(cc_map.BUTTON_CCS) == int(label_m.group(1)) + 1, "ボタン個数が仕様と不一致"
        assert cc_map.BUTTON_CCS == tuple(range(int(m.group(1)), int(m.group(2)) + 1)), (
            "ボタン CC 範囲が仕様と不一致"
        )
        assert direction == "IN"

    @pytest.mark.parametrize(
        ("label", "constant"),
        [
            ("State", cc_map.STATE_CC),
            ("Mode", cc_map.MODE_CC),
            ("Error", cc_map.ERROR_CC),
            ("Preset", cc_map.PRESET_CC),
        ],
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

    def test_no_cc_number_shared_between_in_and_out(self, spec_text: str) -> None:
        """仕様の規約「IN / OUT で CC 番号の重複なし」を実装定数側でも検証する。"""
        sent = {msb for msb, _ in cc_map.CC_AXES} | {lsb for _, lsb in cc_map.CC_AXES}
        sent |= {msb for msb, _ in cc_map.SLIDER_CCS} | {lsb for _, lsb in cc_map.SLIDER_CCS}
        sent |= set(cc_map.BUTTON_CCS)
        sent |= {cc_map.STATE_CC, cc_map.MODE_CC, cc_map.ERROR_CC, cc_map.PRESET_CC}
        sent |= {cc_map.CMDRSP_STATUS_CC, cc_map.EVT_ARG_CC, cc_map.EVT_OP_CC}
        received = {
            cc_map.CMD_ARG1_CC, cc_map.CMD_ARG2_CC, cc_map.CMD_OP_CC, cc_map.EVTRSP_STATUS_CC
        }
        assert not (sent & received), f"送信 CC と受信 CC が重複: {sorted(sent & received)}"


class TestCodeValues:
    """コード値（STATUS / opcode / ARG1 値体系）の照合。"""

    def test_status_codes(self, spec_text: str) -> None:
        pairs = re.findall(
            r"^\|\s*(\d+)\s*\|\s*(OK|UNKNOWN_OP|INVALID_ARG|REJECTED)\s*\|", spec_text, re.M
        )
        parsed = {name: int(value) for value, name in pairs}
        assert parsed == {
            "OK": cc_map.STATUS_OK,
            "UNKNOWN_OP": cc_map.STATUS_UNKNOWN_OP,
            "INVALID_ARG": cc_map.STATUS_INVALID_ARG,
            "REJECTED": cc_map.STATUS_REJECTED,
        }, "STATUS コードが仕様と不一致（またはパース不能）"

    @pytest.mark.parametrize(("name", "constant"), list(OPCODE_CONSTANTS.items()))
    def test_opcode_numbers(self, spec_text: str, name: str, constant: int) -> None:
        rows = _opcode_table_rows(spec_text)
        matched = [r for r in rows if r[1] == name]
        assert len(matched) == 1, f"opcode 表に {name} の行が 1 行見つからない"
        assert constant == matched[0][0], (
            f"{name} の opcode が仕様と不一致（仕様={matched[0][0]} / 実装={constant}）"
        )

    def test_all_spec_opcodes_are_implemented(self, spec_text: str) -> None:
        """仕様に確定 opcode が追加されたら実装側の定数表も追従させる。"""
        spec_names = {name for _op, name, _d, _a in _opcode_table_rows(spec_text)}
        assert spec_names == set(OPCODE_CONSTANTS), (
            "仕様の opcode 表と実装の対応表が不一致（opcode の追加/削除に追従すること）"
        )

    def test_event_opcodes_match_directions(self, spec_text: str) -> None:
        """イベント経路（Sim 送信）で使える opcode（方向 C→G / G⇄C）と実装定数の照合。"""
        rows = _opcode_table_rows(spec_text)
        event_ops = {
            op for op, _name, direction, _arg1 in rows
            if _normalize_direction(direction) in ("C→G", "G⇄C")
        }
        assert event_ops == set(cc_map.EVENT_OPCODES), (
            "イベント経路で送信可能な opcode が実装の EVENT_OPCODES と不一致"
        )

    def test_set_mode_arg_values(self, spec_text: str) -> None:
        rows = _opcode_table_rows(spec_text)
        arg1_cell = next(r[3] for r in rows if r[1] == "SetMode")
        values = tuple(int(v) for v in re.findall(r"(\d+)\s*=", arg1_cell))
        assert values == cc_map.MODE_VALUES, "SetMode の ARG1 値（モード値体系）が仕様と不一致"

    def test_set_valve_arg_values(self, spec_text: str) -> None:
        rows = _opcode_table_rows(spec_text)
        arg1_cell = next(r[3] for r in rows if r[1] == "SetValve")
        open_m = re.search(r"(\d+)\s*=\s*open", arg1_cell)
        close_m = re.search(r"(\d+)\s*=\s*close", arg1_cell)
        assert open_m and close_m, "SetValve の ARG1 欄から open/close をパースできない"
        assert cc_map.VALVE_OPEN == int(open_m.group(1)), "VALVE_OPEN が仕様と不一致"
        assert cc_map.VALVE_CLOSE == int(close_m.group(1)), "VALVE_CLOSE が仕様と不一致"


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
