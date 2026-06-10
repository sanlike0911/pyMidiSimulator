"""コントローラのアプリ状態（パラメータ現在値・コマンド実行）。

仕様: docs/specs/midi-mapping.md セクション3（パラメータ）/ 5（コード値・共通規約）。
- パラメータ（State/Mode/Error/Preset）の現在値を一元管理し、接続直後の初期通知と
  「値の変化時のみ送信」（仕様 §3 の送信契機）を実装する。
- コマンドの opcode 別処理を「検証（validate・ACK 前・状態不変）」と
  「実行（execute・ACK 後・OK 時のみ）」の 2 段階で提供する。
  messaging（プロトコル層）から注入呼び出しされ、仕様の「ACK 送信後に
  リセット/モード遷移」を構造的に保証する。

MIDI / pygame に依存せず、send_cc / on_reset / on_log の注入だけで動作する（テスト可能）。
"""
from __future__ import annotations

from typing import Callable, Optional

import cc_map


class ControllerState:
    """パラメータ現在値の一元管理とコマンド validate/execute。"""

    def __init__(
        self,
        send_cc: Callable[[int, int], None],
        on_reset: Callable[[], None],
        on_log: Callable[[str], None],
    ) -> None:
        self._send_cc = send_cc
        self._on_reset = on_reset  # Reset 実行時の外部初期化（軸中心化・ボタン OFF 等）
        self._on_log = on_log
        self._state = 0
        self._mode = cc_map.MODE_NORMAL
        self._error = 0
        self._preset = 0
        self._valve: Optional[int] = None  # None = 未指示

    # --- 参照（表示・テスト用） ---------------------------------------------
    @property
    def state(self) -> int:
        return self._state

    @property
    def mode(self) -> int:
        return self._mode

    @property
    def error(self) -> int:
        return self._error

    @property
    def preset(self) -> int:
        return self._preset

    @property
    def valve(self) -> Optional[int]:
        return self._valve

    # --- パラメータ送信（仕様 §3: 接続直後＋値の変化時） ---------------------
    def notify_initial(self) -> None:
        """接続直後の初期通知。State/Mode/Error/Preset の現在値を CC 昇順で送る。"""
        self._send_cc(cc_map.STATE_CC, self._state)
        self._send_cc(cc_map.MODE_CC, self._mode)
        self._send_cc(cc_map.ERROR_CC, self._error)
        self._send_cc(cc_map.PRESET_CC, self._preset)
        self._on_log(
            f"初期通知: State={self._state} Mode={self._mode} "
            f"Error={self._error} Preset={self._preset}"
        )

    def adjust_state(self, delta: int) -> int:
        self._state = self._adjusted("State", cc_map.STATE_CC, self._state, delta)
        return self._state

    def adjust_error(self, delta: int) -> int:
        self._error = self._adjusted("Error", cc_map.ERROR_CC, self._error, delta)
        return self._error

    def adjust_preset(self, delta: int) -> int:
        self._preset = self._adjusted("Preset", cc_map.PRESET_CC, self._preset, delta)
        return self._preset

    def _adjusted(self, label: str, cc: int, current: int, delta: int) -> int:
        """±delta を 0–127 にクランプし、変化時のみ送信＋ログして新値を返す。"""
        new = cc_map.clamp(current + delta, 0, cc_map.MAX_7BIT)
        if new != current:
            self._send_cc(cc, new)
            self._on_log(f"{label} 送信: {new}")
        return new

    def set_scalar(self, cc: int, value: int) -> None:
        """自動デバッグ入力からのスカラー設定。変化時のみ送信し内部状態を同期する。"""
        if cc == cc_map.STATE_CC and value != self._state:
            self._state = value
        elif cc == cc_map.MODE_CC and value != self._mode:
            self._mode = value
        elif cc == cc_map.ERROR_CC and value != self._error:
            self._error = value
        elif cc == cc_map.PRESET_CC and value != self._preset:
            self._preset = value
        else:
            return  # 対象外 CC または変化なし -> 送信しない
        self._send_cc(cc, value)

    def cycle_mode(self) -> int:
        """Mode を有効値の並び（通常→バージョンアップ→出荷検査→通常）で巡回し CC103 を送信する。

        手動デバッグ用に「コントローラ自身のモード遷移」を模擬する。SetMode コマンドの
        一方向遷移はゲーム側からの制約であり、本キー操作はその制約を受けずに巡回できる
        （非通常モード中に SetMode が REJECTED になる状況も本操作で再現できる）。
        """
        current = self._mode if self._mode in cc_map.MODE_VALUES else cc_map.MODE_NORMAL
        idx = cc_map.MODE_VALUES.index(current)
        new = cc_map.MODE_VALUES[(idx + 1) % len(cc_map.MODE_VALUES)]
        self._mode = new
        self._send_cc(cc_map.MODE_CC, new)
        self._on_log(f"Mode 送信: {new}（{cc_map.MODE_NAMES[new]}）")
        return new

    # --- コマンド処理（messaging から注入呼び出し） --------------------------
    def validate_command(self, opcode: int, arg1: int, _arg2: int) -> int:
        """ACK 前の検証。STATUS を返し、状態は変更しない。

        共通規約: 未使用 ARG は検証しない（現行確定 opcode の arg2 はすべて未使用）。
        """
        if opcode in (cc_map.OP_PING, cc_map.OP_RESET, cc_map.OP_SET_ZERO):
            return cc_map.STATUS_OK
        if opcode == cc_map.OP_SET_MODE:
            if arg1 not in cc_map.MODE_VALUES:
                return cc_map.STATUS_INVALID_ARG
            if self._mode != cc_map.MODE_NORMAL:
                return cc_map.STATUS_REJECTED  # 一方向遷移: 非通常モードからは戻れない
            return cc_map.STATUS_OK
        if opcode == cc_map.OP_SET_PRESET:
            return cc_map.STATUS_OK  # 7bit 全域 0–127 を対応範囲とする
        if opcode == cc_map.OP_SET_VALVE:
            if arg1 not in (cc_map.VALVE_OPEN, cc_map.VALVE_CLOSE):
                return cc_map.STATUS_INVALID_ARG
            return cc_map.STATUS_OK
        return cc_map.STATUS_UNKNOWN_OP

    def execute_command(self, opcode: int, arg1: int, _arg2: int) -> None:
        """ACK 後の実行。validate が OK を返したコマンドのみ呼ばれる。"""
        if opcode == cc_map.OP_RESET:
            self._execute_reset()
        elif opcode == cc_map.OP_SET_MODE:
            self._execute_set_mode(arg1)
        elif opcode == cc_map.OP_SET_ZERO:
            self._on_log("[SetZero] 零点設定を受領しました")
        elif opcode == cc_map.OP_SET_PRESET:
            self._execute_set_preset(arg1)
        elif opcode == cc_map.OP_SET_VALVE:
            self._execute_set_valve(arg1)
        # OP_PING は実行処理なし（validate で OK 応答済み）

    def _execute_reset(self) -> None:
        """仕様: ACK 送信後にリセット実行。全パラメータを初期値へ戻し再度初期通知する。"""
        self._state = 0
        self._mode = cc_map.MODE_NORMAL
        self._error = 0
        self._preset = 0
        self._valve = None
        self._on_reset()  # 軸中心化・全ボタン OFF・イベント保留破棄（呼び出し側）
        self._on_log("[Reset] コントローラを初期化しました")
        self.notify_initial()  # 接続直後相当の現在値通知

    def _execute_set_mode(self, value: int) -> None:
        """変化時のみ mode を更新し CC103 で新モードを通知する（仕様 §3 送信契機）。"""
        if value == self._mode:
            return  # 通常→通常の同値設定: 変化なし・通知なし
        old = self._mode
        self._mode = value
        self._send_cc(cc_map.MODE_CC, value)
        self._on_log(
            f"[SetMode] モード遷移: {cc_map.MODE_NAMES[old]}({old}) → "
            f"{cc_map.MODE_NAMES[value]}({value})"
        )

    def _execute_set_preset(self, value: int) -> None:
        """変化時のみ preset を更新し CC105 で新値を通知する（同値設定は通知なし）。"""
        if value == self._preset:
            self._on_log(f"[SetPreset] Preset={value}（変化なし・通知省略）")
            return
        self._preset = value
        self._send_cc(cc_map.PRESET_CC, value)
        self._on_log(f"[SetPreset] Preset={value}（CC{cc_map.PRESET_CC} で新値を通知）")

    def _execute_set_valve(self, value: int) -> None:
        self._valve = value
        label = "open" if value == cc_map.VALVE_OPEN else "close"
        self._on_log(f"[SetValve] バルブ {label}")
