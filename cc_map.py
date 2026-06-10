"""CC マッピング定数・正規化・seq コーデック（純粋関数）。

MIDI / pygame に依存しない純粋ロジック。ユニットテストの中心。
新 MIDI 仕様（docs/specs/midi-mapping.md）にコントローラ役（送信主体）の視点で対応する。
仕様は Unity 受信側の視点で書かれているため、本シミュレータでは送受信が反転する点に注意。
"""
from __future__ import annotations

# --- CC 番号 ---------------------------------------------------------------
# 送信（Sim -> Unity）: スティック軸 (MSB CC, LSB CC) 左X / 左Y / 右X / 右Y
CC_AXES: tuple[tuple[int, int], ...] = ((16, 48), (17, 49), (18, 50), (19, 51))
AXIS_NAMES: tuple[str, ...] = ("左X", "左Y", "右X", "右Y")

# 送信: ボタン 0–9
BUTTON_CCS: tuple[int, ...] = tuple(range(20, 30))

# 送信: 0–127 生値
PRESET_CC = 40
ERROR_CC = 41
STATE_CC = 42

# コマンド/イベント I/F
CMDRSP_STATUS_CC = 43   # 送信: 受信コマンドへの ACK (status + seqEcho)
EVT_ARG_CC = 44         # 送信: イベント引数
EVT_OP_CC = 45          # 送信: イベント opcode + seq (commit)
CMD_ARG1_CC = 50        # 受信: コマンド第1引数
CMD_ARG2_CC = 51        # 受信: コマンド第2引数
CMD_OP_CC = 52          # 受信: コマンド opcode + seq (commit)
EVTRSP_STATUS_CC = 53   # 受信: イベント送信への ACK (status + seqEcho)

# --- 値域 ------------------------------------------------------------------
CENTER_14BIT = 8192
MAX_14BIT = 16383
MAX_7BIT = 127
BUTTON_ON_THRESHOLD = 64

# --- seq コーデック（OP/STATUS 値の bit6 を seq ビットに） ------------------
SEQ_BIT = 64         # bit6 = 値 64
PAYLOAD_MASK = 0x3F  # bit0–5 = 0–63

# --- STATUS コード（bit0–5） -----------------------------------------------
STATUS_OK = 0
STATUS_UNKNOWN_OP = 1
STATUS_INVALID_ARG = 2
STATUS_REJECTED = 3

# --- コマンド opcode（受信側で解釈・bit0–5） -------------------------------
CMD_PING = 0
CMD_LED = 1
CMD_HAPTIC = 2
CMD_SET_PRESET = 4

# --- イベント opcode（送信側・bit0–5） -------------------------------------
EVT_HEARTBEAT = 0
EVT_BUTTON_COMBO = 1
EVT_SENSOR_TRIGGER = 2


def clamp(value: int, low: int, high: int) -> int:
    """value を [low, high] にクランプする。"""
    return max(low, min(high, value))


def split_14bit(value: int) -> tuple[int, int]:
    """14bit 値 (0–16383) を (MSB, LSB) の 7bit ペアに分割する。"""
    v = clamp(value, 0, MAX_14BIT)
    return (v >> 7) & 0x7F, v & 0x7F


def combine_14bit(msb: int, lsb: int) -> int:
    """(MSB, LSB) の 7bit ペアを 14bit 値 (0–16383) に再構成する。"""
    return ((msb & 0x7F) << 7) | (lsb & 0x7F)


def norm14_bipolar(value: int) -> float:
    """14bit 生値を中央 8192 基準で -1.0..+1.0 に正規化する（表示用）。

    仕様の式 n = (v - 8192) / 8192 を計算し、[-1.0, +1.0] にクランプする。
    """
    n = (value - CENTER_14BIT) / CENTER_14BIT
    return max(-1.0, min(1.0, n))


def pack_seq(payload: int, seq: int) -> int:
    """payload(0–63) と seq(0/1) を OP/STATUS 値に合成する。"""
    return (payload & PAYLOAD_MASK) + (SEQ_BIT if seq & 1 else 0)


def payload_of(value: int) -> int:
    """OP/STATUS 値から payload(bit0–5) を取り出す。"""
    return value & PAYLOAD_MASK


def seq_of(value: int) -> int:
    """OP/STATUS 値から seq(bit6) を取り出す。"""
    return (value >> 6) & 1
