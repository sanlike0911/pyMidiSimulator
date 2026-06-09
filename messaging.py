"""コマンド/イベント I/F ステートマシン（コントローラ役）。

仕様: docs/specs/midi-mapping.md セクション7。
- コマンド（Unity -> Sim）: 受信して即 ACK を返す（本シミュレータは受信者）。
- イベント（Sim -> Unity）: 送信して応答（ACK）を待つ（本シミュレータは送信者）。

MIDI / pygame に依存せず、CC 送信関数（send_cc）の注入とフレーム駆動（tick）だけで動作する。
これによりユニットテスト可能。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import cc_map

# タイムアウト: 仕様の「30 フレーム（≒0.5s @60fps）」。Tick(メインループ反復) 計数で判定する。
RESPONSE_TIMEOUT_TICKS = 30


@dataclass(frozen=True)
class CommandRecord:
    """受信・処理したコマンドの記録（表示用）。"""

    opcode: int
    arg1: int
    arg2: int
    status: int
    seq: int


@dataclass(frozen=True)
class EventResponse:
    """送信イベントへの応答結果（表示用）。"""

    opcode: int
    seq: int
    status: Optional[int]  # 解決時は STATUS 値、タイムアウト時は None
    timed_out: bool


@dataclass(frozen=True)
class _PendingEvent:
    """応答待ちのイベント（イベントチャンネルの保留）。"""

    opcode: int
    seq: int
    arg: int


@dataclass(frozen=True)
class MessagingState:
    """表示用スナップショット。"""

    received_preset: Optional[int]
    preset_option: int
    last_command: Optional[CommandRecord]
    last_event_response: Optional[EventResponse]
    event_pending: bool


class Messaging:
    """コマンド受信＋ACK と、イベント送信＋応答待ちを担うステートマシン。"""

    def __init__(self, send_cc: Callable[[int, int], None]) -> None:
        self._send_cc = send_cc

        # 受信コマンドの引数バッファ（OP commit 後にクリア）
        self._arg1: Optional[int] = None
        self._arg2: Optional[int] = None

        # イベント送信の保留（同一チャンネルの次送信のみ抑止）
        self._pending: Optional[_PendingEvent] = None
        self._pending_ticks = 0
        self._next_event_seq = 0  # 送信ごとに 0↔1 反転

        # 表示用状態
        self._received_preset: Optional[int] = None
        self._preset_option = 0
        self._last_command: Optional[CommandRecord] = None
        self._last_event_response: Optional[EventResponse] = None

    # --- 受信ディスパッチ ---------------------------------------------------
    def handle_incoming_cc(self, cc: int, value: int) -> None:
        """MIDI 入力から CC を受信するたびに呼ぶ。該当しない CC は無視する。"""
        if cc == cc_map.CMD_ARG1_CC:
            self._arg1 = value
        elif cc == cc_map.CMD_ARG2_CC:
            self._arg2 = value
        elif cc == cc_map.CMD_OP_CC:
            self._commit_command(value)
        elif cc == cc_map.EVTRSP_STATUS_CC:
            self._handle_event_response(value)

    def _commit_command(self, op_value: int) -> None:
        """CMD_OP 到着で直前の引数とあわせて 1 件のコマンドを実行し、ACK を返す。"""
        opcode = cc_map.payload_of(op_value)
        seq = cc_map.seq_of(op_value)
        arg1 = self._arg1 if self._arg1 is not None else 0
        arg2 = self._arg2 if self._arg2 is not None else 0

        status = self._process_command(opcode, arg1, arg2)

        # 受信側へ ACK（status + seqEcho）。seqEcho は受信コマンドの seq をそのまま返す。
        self._send_cc(cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(status, seq))
        self._last_command = CommandRecord(opcode, arg1, arg2, status, seq)

        # 実行後に引数を消費（次コマンドで送られなかった引数は 0 とするため）
        self._arg1 = None
        self._arg2 = None

    def _process_command(self, opcode: int, arg1: int, arg2: int) -> int:
        """opcode 別にコマンドを処理し、STATUS を返す。"""
        if opcode == cc_map.CMD_PING:
            return cc_map.STATUS_OK
        if opcode == cc_map.CMD_SET_PRESET:
            if 0 <= arg1 <= cc_map.MAX_7BIT:
                self._received_preset = arg1
                self._preset_option = arg2
                return cc_map.STATUS_OK
            return cc_map.STATUS_INVALID_ARG
        if opcode in (cc_map.CMD_LED, cc_map.CMD_HAPTIC):
            return cc_map.STATUS_OK
        return cc_map.STATUS_UNKNOWN_OP

    # --- イベント送信 -------------------------------------------------------
    def send_event(self, opcode: int, arg: int) -> bool:
        """イベントを送信する。保留中（応答待ち）なら抑止して False を返す。"""
        if self._pending is not None:
            return False

        seq = self._next_event_seq
        arg7 = arg & 0x7F
        # ARG -> OP の順。OP の到着が commit。
        self._send_cc(cc_map.EVT_ARG_CC, arg7)
        self._send_cc(cc_map.EVT_OP_CC, cc_map.pack_seq(opcode, seq))

        self._pending = _PendingEvent(opcode=opcode, seq=seq, arg=arg7)
        self._pending_ticks = 0
        self._next_event_seq ^= 1
        return True

    def _handle_event_response(self, value: int) -> None:
        """EVTRSP_STATUS 受信。保留があり seq 一致なら解決、それ以外は破棄。"""
        if self._pending is None:
            return  # 保留なし -> 破棄
        if cc_map.seq_of(value) != self._pending.seq:
            return  # seq 不一致（遅延した古い応答など）-> 破棄

        self._last_event_response = EventResponse(
            opcode=self._pending.opcode,
            seq=self._pending.seq,
            status=cc_map.payload_of(value),
            timed_out=False,
        )
        self._pending = None

    # --- フレーム駆動 -------------------------------------------------------
    def tick(self) -> None:
        """メインループの各反復で呼ぶ。保留イベントのタイムアウトを判定する。"""
        if self._pending is None:
            return
        self._pending_ticks += 1
        if self._pending_ticks > RESPONSE_TIMEOUT_TICKS:
            self._last_event_response = EventResponse(
                opcode=self._pending.opcode,
                seq=self._pending.seq,
                status=None,
                timed_out=True,
            )
            self._pending = None  # 失敗扱い・再送可

    # --- 表示用 -------------------------------------------------------------
    def snapshot(self) -> MessagingState:
        """現在の表示用状態を返す。"""
        return MessagingState(
            received_preset=self._received_preset,
            preset_option=self._preset_option,
            last_command=self._last_command,
            last_event_response=self._last_event_response,
            event_pending=self._pending is not None,
        )
