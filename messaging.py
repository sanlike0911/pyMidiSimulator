"""コマンド/イベント I/F ステートマシン（コントローラ役・プロトコル層）。

仕様: docs/specs/midi-mapping.md セクション5。
- コマンド（Unity -> Sim）: フレーミング（ARG バッファ -> OP commit）して
  「検証 -> ACK -> 実行」の順で処理する（本シミュレータは受信者）。
  opcode 別の検証/実行は注入された validate_command / execute_command（controller_state）
  に委譲し、本モジュールは ACK・seqEcho・引数消費のみ担う。
- イベント（Sim -> Unity）: 送信して応答（ACK）を待つ（本シミュレータは送信者）。

MIDI / pygame に依存せず、CC 送信関数（send_cc）とコマンドハンドラの注入、
フレーム駆動（tick）だけで動作する。これによりユニットテスト可能。
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


@dataclass(frozen=True)
class MessagingState:
    """表示用スナップショット。"""

    last_command: Optional[CommandRecord]
    last_event_response: Optional[EventResponse]
    event_pending: bool


class Messaging:
    """コマンド受信＋ACK と、イベント送信＋応答待ちを担うステートマシン。"""

    def __init__(
        self,
        send_cc: Callable[[int, int], None],
        validate_command: Callable[[int, int, int], int],
        execute_command: Callable[[int, int, int], None],
    ) -> None:
        self._send_cc = send_cc
        self._validate_command = validate_command  # (opcode, arg1, arg2) -> STATUS（ACK 前）
        self._execute_command = execute_command    # OK の場合のみ ACK 後に呼ぶ

        # 受信コマンドの引数バッファ（OP commit 後にクリア）
        self._arg1: Optional[int] = None
        self._arg2: Optional[int] = None

        # イベント送信の保留（同一チャンネルの次送信のみ抑止）
        self._pending: Optional[_PendingEvent] = None
        self._pending_ticks = 0
        self._next_event_seq = 0  # 送信ごとに 0↔1 反転

        # 表示用状態
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
        """CMD_OP 到着で 1 件のコマンドを確定し「検証 -> ACK -> 実行」の順で処理する。

        仕様: Reset / SetMode は「ACK 送信後に実行/遷移」。実行を ACK の後に置くことで
        全 opcode でこの規約を構造的に満たす。
        """
        opcode = cc_map.payload_of(op_value)
        seq = cc_map.seq_of(op_value)
        arg1 = self._arg1 if self._arg1 is not None else 0
        arg2 = self._arg2 if self._arg2 is not None else 0

        status = self._validate_command(opcode, arg1, arg2)

        # 受信側へ ACK（status + seqEcho）。seqEcho は受信コマンドの seq をそのまま返す。
        self._send_cc(cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(status, seq))
        if status == cc_map.STATUS_OK:
            self._execute_command(opcode, arg1, arg2)
        self._last_command = CommandRecord(opcode, arg1, arg2, status, seq)

        # 実行後に引数を消費（次コマンドで送られなかった引数は 0 とするため）
        self._arg1 = None
        self._arg2 = None

    # --- イベント送信 -------------------------------------------------------
    def send_event(self, opcode: int, arg: Optional[int] = None) -> bool:
        """イベントを送信する。保留中（応答待ち）なら抑止して False を返す。

        arg=None は ARG 未使用イベント（確定仕様では Ping）で、仕様の正規例に合わせて
        EVT_ARG の送信自体を省略する（受信側は省略時 0 と解釈する）。
        """
        if self._pending is not None:
            return False

        seq = self._next_event_seq
        # ARG -> OP の順。OP の到着が commit。
        if arg is not None:
            self._send_cc(cc_map.EVT_ARG_CC, arg & 0x7F)
        self._send_cc(cc_map.EVT_OP_CC, cc_map.pack_seq(opcode, seq))

        self._pending = _PendingEvent(opcode=opcode, seq=seq)
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

    def clear_pending(self) -> None:
        """保留中イベントを応答記録なしで破棄する（Reset コマンド実行時に呼ぶ）。"""
        self._pending = None
        self._pending_ticks = 0

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
            last_command=self._last_command,
            last_event_response=self._last_event_response,
            event_pending=self._pending is not None,
        )
