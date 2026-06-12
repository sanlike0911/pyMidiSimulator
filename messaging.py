"""コマンド/イベント I/F ステートマシン（コントローラ役・プロトコル層）。

仕様: docs/specs/midi-mapping.md セクション5。
- コマンド（Unity -> Sim）: フレーミング（ARG バッファ -> OP commit）して
  「検証 -> ACK -> 実行」の順で処理する（本シミュレータは受信者）。
  opcode 別の検証/実行は注入された validate_command / execute_command（controller_state）
  に委譲し、本モジュールは ACK・seqEcho・引数消費のみ担う。
- イベント（Sim -> Unity）: 送信して応答（ACK）を待つ（本シミュレータは送信者）。
- ACK 注入（デバッグ）: コマンド ACK の遅延・無応答・強制エラー status を設定でき、
  対向（ゲーム側）のタイムアウト・エラー処理の試験に使う
  （設計: docs/superpowers/specs/2026-06-13-ack-response-debug-injection-design.md）。

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
    status: Optional[int]  # ACK で返した STATUS 値。無応答（dropped）時は None
    seq: int
    dropped: bool = False  # ACK 注入「無応答」で黙殺した（ACK も実行もしていない）
    forced: bool = False   # ACK 注入「強制 status」で検証結果を上書きした


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


@dataclass
class _PendingAck:
    """ACK 注入「遅延」で保留中のコマンド（FIFO・remaining を tick で減算するため非 frozen）。"""

    opcode: int
    arg1: int
    arg2: int
    seq: int
    remaining: int  # 残り Tick。0 以下かつ先頭で発火（追い越しなし）


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
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._send_cc = send_cc
        self._validate_command = validate_command  # (opcode, arg1, arg2) -> STATUS（ACK 前）
        self._execute_command = execute_command    # OK の場合のみ ACK 後に呼ぶ
        self._log = on_log or (lambda _msg: None)  # ACK 注入の動作通知（既定は無音）

        # 受信コマンドの引数バッファ（OP commit 後にクリア）
        self._arg1: Optional[int] = None
        self._arg2: Optional[int] = None

        # イベント送信の保留（同一チャンネルの次送信のみ抑止）
        self._pending: Optional[_PendingEvent] = None
        self._pending_ticks = 0
        self._next_event_seq = 0  # 送信ごとに 0↔1 反転

        # ACK 注入（コマンド ACK のデバッグ設定）。Reset でも解除しない（操作者所有の設定）
        self._ack_delay: Optional[int] = 0             # 0=即時 / N>0=N Tick 遅延 / None=無応答
        self._ack_forced_status: Optional[int] = None  # None=通常（検証結果） / 1–63=強制 NG
        self._ack_queue: list[_PendingAck] = []        # 遅延発火待ちの FIFO

        # 表示用状態
        self._last_command: Optional[CommandRecord] = None
        self._last_event_response: Optional[EventResponse] = None

    # --- ACK 注入（デバッグ設定） --------------------------------------------
    def set_ack_delay(self, delay: Optional[int]) -> None:
        """コマンド ACK の応答タイミングを設定する（デバッグ注入）。

        0=即時（既定・従来挙動）/ N>0=commit から N Tick 後に「検証→ACK→実行」を一体で
        実行 / None=無応答（ACK も実行もしない）。設定変更は保留済みの遅延 ACK に影響しない。
        """
        if delay is not None and delay < 0:
            raise ValueError(f"ACK 遅延は 0 以上の Tick 数または None（無応答）: {delay}")
        self._ack_delay = delay

    def set_ack_forced_status(self, status: Optional[int]) -> None:
        """コマンド ACK の status を強制する（デバッグ注入）。

        None=通常（検証結果どおり）/ 1–63=強制 NG（実行も抑止する）。
        OK(0) の強制は検証 NG コマンドの実行を招くため禁止。発火時点の設定を参照する
        ため、保留済みの遅延 ACK にも即時反映される（set_ack_delay とは異なる）。
        """
        if status is not None and not (1 <= status <= cc_map.PAYLOAD_MASK):
            raise ValueError(
                f"強制 status は 1–{cc_map.PAYLOAD_MASK} または None（通常）: {status}"
            )
        self._ack_forced_status = status

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
        """CMD_OP 到着で 1 件のコマンドを確定する。

        ARG1/ARG2 はこの時点で束縛・消費する（仕様: 次の要求で送られなかった引数は 0。
        保留中に届く次コマンドの引数とも混ざらない）。処理本体（検証 → ACK → 実行）は
        ACK 注入の設定に従い、即時実行・遅延キュー投入・無応答（黙殺）に分岐する。
        """
        opcode = cc_map.payload_of(op_value)
        seq = cc_map.seq_of(op_value)
        arg1 = self._arg1 if self._arg1 is not None else 0
        arg2 = self._arg2 if self._arg2 is not None else 0
        self._arg1 = None
        self._arg2 = None

        if self._ack_delay is None:
            # 無応答: ACK も実行もしない（コマンドを受信しなかった相当）。記録のみ残す
            self._last_command = CommandRecord(opcode, arg1, arg2, None, seq, dropped=True)
            self._log(f"[ACK 注入] 無応答: {self._opcode_name(opcode)} (seq={seq}) を黙殺")
            return
        if self._ack_delay > 0:
            self._ack_queue.append(_PendingAck(opcode, arg1, arg2, seq, self._ack_delay))
            self._log(
                f"[ACK 注入] {self._opcode_name(opcode)} (seq={seq}) の処理を"
                f" {self._ack_delay} Tick 保留"
            )
            return
        self._process_command(opcode, arg1, arg2, seq)

    def _process_command(self, opcode: int, arg1: int, arg2: int, seq: int) -> None:
        """「検証 -> ACK -> 実行」の順で 1 件のコマンドを処理する。

        仕様: Reset / SetMode は「ACK 送信後に実行/遷移」。実行を ACK の後に置くことで
        全 opcode でこの規約を構造的に満たす。検証は発火時（＝ACK 時点）の状態に対して
        行う。強制 status（ACK 注入）中は検証結果を上書きして NG を返し、実行しない
        （ワイヤ上の応答と内部状態の整合を保つ）。
        """
        status = self._validate_command(opcode, arg1, arg2)
        forced = self._ack_forced_status is not None
        if forced:
            status = self._ack_forced_status
            self._log(
                f"[ACK 注入] {self._opcode_name(opcode)} (seq={seq}) に"
                f" 強制 status={status} を応答（実行抑止）"
            )

        # 受信側へ ACK（status + seqEcho）。seqEcho は受信コマンドの seq をそのまま返す。
        self._send_cc(cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(status, seq))
        if status == cc_map.STATUS_OK:
            self._execute_command(opcode, arg1, arg2)
        self._last_command = CommandRecord(opcode, arg1, arg2, status, seq, forced=forced)

    @staticmethod
    def _opcode_name(opcode: int) -> str:
        """ログ表示用の opcode 名（未知は番号付きで明示）。"""
        return cc_map.OPCODE_NAMES.get(opcode, f"未知op{opcode}")

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
        """メインループの各反復で呼ぶ。イベントのタイムアウトと遅延 ACK の発火を進める。"""
        self._tick_event_timeout()
        self._tick_ack_queue()

    def _tick_event_timeout(self) -> None:
        """保留イベントのタイムアウトを判定する。"""
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

    def _tick_ack_queue(self) -> None:
        """遅延 ACK のカウントダウンと発火（FIFO・先頭から順に・追い越しなし）。"""
        if not self._ack_queue:
            return
        for entry in self._ack_queue:
            entry.remaining -= 1
        while self._ack_queue and self._ack_queue[0].remaining <= 0:
            entry = self._ack_queue.pop(0)
            self._process_command(entry.opcode, entry.arg1, entry.arg2, entry.seq)

    # --- 表示用 -------------------------------------------------------------
    def snapshot(self) -> MessagingState:
        """現在の表示用状態を返す。"""
        return MessagingState(
            last_command=self._last_command,
            last_event_response=self._last_event_response,
            event_pending=self._pending is not None,
        )
