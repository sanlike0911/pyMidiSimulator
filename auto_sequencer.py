"""自動デバッグ入力モードの巡回シーケンス生成（純粋ロジック）。

MIDI / pygame に依存しない。tick() が「この Tick に送るべきアクション列」を返し、
midi_simulator がそれを実際の MIDI 送信に変換する。これによりユニットテスト可能。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import cc_map


class ActionKind(Enum):
    """送信アクションの種別。target/value の意味が種別ごとに変わる。"""

    AXIS = "axis"      # target=軸index(0-3),    value=14bit raw(0-16383)
    BUTTON = "button"  # target=ボタンindex(0-9), value=127|0
    SCALAR = "scalar"  # target=CC番号,           value=0-127
    EVENT = "event"    # target=opcode,           value=arg(0-127)


@dataclass(frozen=True)
class SendAction:
    """1 件の送信指示。log が非 None の Tick のみ HUD に出す。"""

    kind: ActionKind
    target: int
    value: int
    log: Optional[str] = None


class Phase(Enum):
    """巡回シーケンスのフェーズ。STICK→BUTTON→SCALAR→EVENT→(STICK) と循環する。"""

    STICK = 0
    BUTTON = 1
    SCALAR = 2
    EVENT = 3


class _Leg(Enum):
    """スティック軸の往復区間。"""

    TO_MAX = 0      # 中心 8192 → 上端 16383
    TO_MIN = 1      # 上端 16383 → 下端 0
    TO_CENTER = 2   # 下端 0 → 中心 8192（到達で軸完了）


# スカラーフェーズ対象（Preset, Error, State）
_SCALAR_CCS = (cc_map.PRESET_CC, cc_map.ERROR_CC, cc_map.STATE_CC)
_SCALAR_NAMES = ("Preset", "Error", "State")

# イベントフェーズ対象 opcode
_EVENT_OPCODES = (cc_map.EVT_HEARTBEAT, cc_map.EVT_BUTTON_COMBO, cc_map.EVT_SENSOR_TRIGGER)
_EVENT_NAMES = ("HeartBeat", "ButtonCombo", "SensorTrigger")


class AutoSequencer:
    """巡回シーケンスを生成する決定的な状態機械。MIDI/pygame 非依存。"""

    def __init__(self, stick_step: int, button_hold_ticks: int, cc_step: int) -> None:
        self._stick_step = stick_step
        self._button_hold_ticks = button_hold_ticks
        self._cc_step = cc_step
        self._event_arg = 0  # サイクルをまたいで連続インクリメント（cycle reset では触らない）
        self._reset_cycle()

    def _reset_cycle(self) -> None:
        """1 サイクル分のフェーズ状態を初期化する（event_arg は保持）。"""
        self._phase = Phase.STICK
        self._axis_index = 0
        self._axis_value = cc_map.CENTER_14BIT
        self._axis_leg = _Leg.TO_MAX
        self._button_index = 0
        self._button_on = False
        self._hold_counter = 0
        self._scalar_index = 0
        self._scalar_value = 0
        self._event_index = 0
        self._event_sent = False

    def tick(self, event_pending: bool) -> List[SendAction]:
        """1 Tick 進め、その Tick に送るべきアクション列を返す。"""
        if self._phase is Phase.STICK:
            return self._tick_stick()
        if self._phase is Phase.BUTTON:
            return self._tick_button()
        if self._phase is Phase.SCALAR:
            return self._tick_scalar()
        return self._tick_event(event_pending)

    # 各フェーズのハンドラ
    def _tick_stick(self) -> List[SendAction]:
        axis = self._axis_index
        axis_done = False
        log = None
        if self._axis_leg is _Leg.TO_MAX:
            self._axis_value = min(self._axis_value + self._stick_step, cc_map.MAX_14BIT)
            if self._axis_value >= cc_map.MAX_14BIT:
                self._axis_leg = _Leg.TO_MIN
                log = f"スティック {cc_map.AXIS_NAMES[axis]} 上端 {self._axis_value}"
        elif self._axis_leg is _Leg.TO_MIN:
            self._axis_value = max(self._axis_value - self._stick_step, 0)
            if self._axis_value <= 0:
                self._axis_leg = _Leg.TO_CENTER
                log = f"スティック {cc_map.AXIS_NAMES[axis]} 下端 {self._axis_value}"
        else:  # TO_CENTER
            self._axis_value = min(self._axis_value + self._stick_step, cc_map.CENTER_14BIT)
            if self._axis_value >= cc_map.CENTER_14BIT:
                axis_done = True
                log = f"スティック {cc_map.AXIS_NAMES[axis]} 中心 {self._axis_value}"
        action = SendAction(ActionKind.AXIS, axis, self._axis_value, log)
        if axis_done:
            self._advance_axis()
        return [action]

    def _advance_axis(self) -> None:
        """現在の軸を終え、次の軸へ。4 軸完了で BUTTON フェーズへ。"""
        self._axis_index += 1
        if self._axis_index >= len(cc_map.CC_AXES):
            self._axis_index = 0
            self._phase = Phase.BUTTON
        else:
            self._axis_value = cc_map.CENTER_14BIT
            self._axis_leg = _Leg.TO_MAX

    def _tick_button(self) -> List[SendAction]:
        return []

    def _tick_scalar(self) -> List[SendAction]:
        return []

    def _tick_event(self, event_pending: bool) -> List[SendAction]:
        return []
