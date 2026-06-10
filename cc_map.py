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

# 送信: 0–127 生値（パラメータ帯 CC102–109・106–109 は予約）
STATE_CC = 102
MODE_CC = 103   # 動作モード通知（値体系は SetMode と共通）
ERROR_CC = 104
PRESET_CC = 105

# コマンド/イベント I/F（CC110–119 帯・117–119 は予約）
CMD_ARG1_CC = 110       # 受信: コマンド第1引数
CMD_ARG2_CC = 111       # 受信: コマンド第2引数（現行確定 opcode ではすべて未使用）
CMD_OP_CC = 112         # 受信: コマンド opcode + seq (commit)
EVTRSP_STATUS_CC = 113  # 受信: イベント送信への ACK (status + seqEcho)
CMDRSP_STATUS_CC = 114  # 送信: 受信コマンドへの ACK (status + seqEcho)
EVT_ARG_CC = 115        # 送信: イベント引数（確定イベント Ping では未使用＝送信省略）
EVT_OP_CC = 116         # 送信: イベント opcode + seq (commit)

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

# --- opcode（コマンド/イベント共通番号空間・bit0–5） -----------------------
# 各 opcode に方向（G→C / C→G / G⇄C）が定義され、方向で経路（コマンド/イベント）が決まる。
OP_PING = 0        # G⇄C 双方向: 疎通確認
OP_RESET = 1       # G→C: コントローラ再起動・初期化（ACK 送信後に実行）
OP_SET_MODE = 2    # G→C: 動作モード切替（ACK 送信後に遷移・一方向）
OP_SET_ZERO = 3    # G→C: センサ零点を現在値で設定
OP_SET_PRESET = 4  # G→C: プリセット設定（arg1=preset / arg2 未使用）
OP_SET_VALVE = 5   # G→C: バルブ開閉指示
OPCODE_NAMES: dict[int, str] = {
    OP_PING: "Ping",
    OP_RESET: "Reset",
    OP_SET_MODE: "SetMode",
    OP_SET_ZERO: "SetZero",
    OP_SET_PRESET: "SetPreset",
    OP_SET_VALVE: "SetValve",
}

# イベント経路（Sim → Unity）で送信できる確定 opcode（方向が C→G または G⇄C のもの）
EVENT_OPCODES: tuple[int, ...] = (OP_PING,)

# --- Mode 値（CC103 通知と SetMode ARG1 で共通） ----------------------------
MODE_NORMAL = 0
MODE_VERSION_UP = 110
MODE_FACTORY_INSPECTION = 127
MODE_VALUES: tuple[int, ...] = (MODE_NORMAL, MODE_VERSION_UP, MODE_FACTORY_INSPECTION)
MODE_NAMES: dict[int, str] = {
    MODE_NORMAL: "通常",
    MODE_VERSION_UP: "バージョンアップ",
    MODE_FACTORY_INSPECTION: "出荷検査",
}

# --- SetValve ARG1 値 -------------------------------------------------------
VALVE_OPEN = 0
VALVE_CLOSE = 1


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
