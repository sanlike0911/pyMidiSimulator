# 自動デバッグ入力モード ＋ Stick/Slider 撤廃 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 手動キー操作なしで送信系の全 CC を巡回送信する「自動デバッグ入力モード」を追加し、実用価値の薄い Stick/Slider モードを撤廃して操作系を単純化する。

**Architecture:** 巡回シーケンス生成を MIDI/pygame 非依存の純粋ロジック `AutoSequencer`（新規 `auto_sequencer.py`）に閉じ込め、`tick(event_pending)` が「この Tick に送るアクション列」を返す。`midi_simulator.py` は返ったアクションを既存の `send_14bit`/`send_cc`/`send_event` に変換するだけ。Stick/Slider モードは撤廃し、軸基準を中心点 8192 に固定する。

**Tech Stack:** Python 3.7+ / pygame（キー入力）/ python-rtmidi（MIDI I/O）/ pytest（純粋ロジックのテスト）

**設計書:** [2026-06-09-auto-debug-input-mode-design.md](../specs/2026-06-09-auto-debug-input-mode-design.md)

---

## 事前準備（全タスク共通）

すべての `pytest` / `python` コマンドは **仮想環境を有効化してから**実行する（INSTRUCTIONS.md 準拠）:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (Git Bash) / macOS / Linux
source .venv/Scripts/activate   # Windows の場合
source .venv/bin/activate        # macOS/Linux の場合
```

プロンプトに `(.venv)` が出ていることを確認する。以降のコマンドは有効化済み前提で記載する。

---

## ファイル構成

| ファイル | 役割 | 変更 |
|----------|------|------|
| `auto_sequencer.py` | 巡回シーケンス生成（純粋ロジック）。`AutoSequencer` / `SendAction` / `ActionKind` / `Phase` | 新規 |
| `tests/test_auto_sequencer.py` | `AutoSequencer` のユニットテスト | 新規 |
| `midi_simulator.py` | 自動モード分岐・トグル・ディスパッチ。Stick/Slider 撤廃・中心点移動 | 変更 |
| `keyboard_map.py` | `M` を自動トグルへ再定義、`help_text()` 更新 | 変更 |
| `cc_map.py` | `norm_slider` 削除 | 変更 |
| `tests/test_cc_map.py` | slider テスト 3 件削除 | 変更 |
| `INSTRUCTIONS.md` | Stick/Slider 記述・キー表・`norm_slider` 言及を更新、自動モード追記 | 変更 |
| `docs/superpowers/specs/2026-06-05-demo-mode-design.md` | supersede 注記を冒頭に追加 | 変更 |

実装順序：`AutoSequencer`（Task 1–5）を完成 → Stick/Slider 撤廃（Task 6）→ 統合（Task 7）→ ドキュメント（Task 8）。

---

## Task 1: AutoSequencer の型定義と状態初期化

**Files:**
- Create: `auto_sequencer.py`
- Test: `tests/test_auto_sequencer.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_auto_sequencer.py`:

```python
"""AutoSequencer 純粋ロジックのユニットテスト。"""
import cc_map
from auto_sequencer import ActionKind, AutoSequencer, Phase, SendAction


def _make() -> AutoSequencer:
    """テスト用にステップを大きめにして少 Tick で各フェーズを通過させる。"""
    return AutoSequencer(stick_step=4096, button_hold_ticks=2, cc_step=64)


class TestInit:
    def test_send_action_defaults_log_to_none(self):
        action = SendAction(ActionKind.AXIS, 0, cc_map.CENTER_14BIT)
        assert action.kind is ActionKind.AXIS
        assert action.target == 0
        assert action.value == cc_map.CENTER_14BIT
        assert action.log is None

    def test_initial_phase_is_stick(self):
        seq = _make()
        assert seq._phase is Phase.STICK

    def test_initial_axis_starts_at_center(self):
        seq = _make()
        assert seq._axis_index == 0
        assert seq._axis_value == cc_map.CENTER_14BIT
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_auto_sequencer.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'auto_sequencer'`）

- [ ] **Step 3: 最小実装を書く**

`auto_sequencer.py`:

```python
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

    # 各フェーズのハンドラ（後続タスクで実装）
    def _tick_stick(self) -> List[SendAction]:
        return []

    def _tick_button(self) -> List[SendAction]:
        return []

    def _tick_scalar(self) -> List[SendAction]:
        return []

    def _tick_event(self, event_pending: bool) -> List[SendAction]:
        return []
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/test_auto_sequencer.py -v`
Expected: PASS（3 件）

- [ ] **Step 5: コミット**

```bash
git add auto_sequencer.py tests/test_auto_sequencer.py
git commit -m "feat: AutoSequencer の型定義と状態初期化を追加"
```

---

## Task 2: スティックスイープフェーズ

**Files:**
- Modify: `auto_sequencer.py`（`_tick_stick` を実装、`_advance_axis` を追加）
- Test: `tests/test_auto_sequencer.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_auto_sequencer.py` に追記:

```python
class TestStickPhase:
    def test_axis0_sweeps_center_max_min_center(self):
        # stick_step=4096: 8192→(12288→16383)→(12287→…→0)→(4096→8192) と往復
        seq = AutoSequencer(stick_step=4096, button_hold_ticks=2, cc_step=64)

        values = []
        # 軸0 が中心へ復帰する瞬間まで AXIS アクションを集める
        for _ in range(20):
            actions = seq.tick(event_pending=False)
            axis_actions = [a for a in actions if a.kind is ActionKind.AXIS and a.target == 0]
            values.extend(a.value for a in axis_actions)
            if seq._axis_index != 0:  # 軸0完了 → 次軸へ進んだ
                break

        assert values[0] == 12288            # 8192 + 4096（上昇開始）
        assert max(values) == cc_map.MAX_14BIT   # 16383 に到達
        assert min(values) == 0                  # 0 に到達
        assert values[-1] == cc_map.CENTER_14BIT # 8192 へ復帰して軸完了

    def test_processes_four_axes_then_enters_button_phase(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=2, cc_step=64)
        # stick_step を最大にすると 1 Tick で各 leg が端へ到達 → 軸あたり 3 Tick
        seen_axes = set()
        for _ in range(50):
            if seq._phase is Phase.BUTTON:
                break
            for a in seq.tick(event_pending=False):
                if a.kind is ActionKind.AXIS:
                    seen_axes.add(a.target)
        assert seen_axes == {0, 1, 2, 3}
        assert seq._phase is Phase.BUTTON

    def test_endpoints_carry_log_and_midpoints_do_not(self):
        seq = AutoSequencer(stick_step=4096, button_hold_ticks=2, cc_step=64)
        first = seq.tick(event_pending=False)[0]   # 12288（中間点）
        assert first.log is None
        second = seq.tick(event_pending=False)[0]   # 16383（上端）
        assert second.value == cc_map.MAX_14BIT
        assert second.log is not None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_auto_sequencer.py::TestStickPhase -v`
Expected: FAIL（`_tick_stick` がスタブで `[]` を返すため）

- [ ] **Step 3: `_tick_stick` と `_advance_axis` を実装**

`auto_sequencer.py` の `_tick_stick` スタブを置換し、直後に `_advance_axis` を追加:

```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/test_auto_sequencer.py -v`
Expected: PASS（Task1 分 + TestStickPhase 3 件）

- [ ] **Step 5: コミット**

```bash
git add auto_sequencer.py tests/test_auto_sequencer.py
git commit -m "feat: AutoSequencer スティックスイープフェーズを実装"
```

---

## Task 3: ボタン順次フェーズ

**Files:**
- Modify: `auto_sequencer.py`（`_tick_button` を実装）
- Test: `tests/test_auto_sequencer.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_auto_sequencer.py` に追記:

```python
def _advance_to_phase(seq: AutoSequencer, phase: Phase, limit: int = 2000):
    """目的フェーズに到達するまで tick を回す（応答待ちは即解決扱い）。"""
    for _ in range(limit):
        if seq._phase is phase:
            return
        seq.tick(event_pending=False)
    raise AssertionError(f"{phase} に到達しませんでした")


class TestButtonPhase:
    def test_each_button_turns_on_then_off_in_order(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=2, cc_step=64)
        _advance_to_phase(seq, Phase.BUTTON)

        on_order, off_order = [], []
        for _ in range(200):
            if seq._phase is not Phase.BUTTON:
                break
            for a in seq.tick(event_pending=False):
                if a.kind is ActionKind.BUTTON and a.value == cc_map.MAX_7BIT:
                    on_order.append(a.target)
                elif a.kind is ActionKind.BUTTON and a.value == 0:
                    off_order.append(a.target)

        assert on_order == list(range(len(cc_map.BUTTON_CCS)))   # 0..9 を順に ON
        assert off_order == list(range(len(cc_map.BUTTON_CCS)))  # 0..9 を順に OFF

    def test_button_held_for_configured_ticks(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=3, cc_step=64)
        _advance_to_phase(seq, Phase.BUTTON)

        # ON の Tick
        on = seq.tick(event_pending=False)
        assert on[0].kind is ActionKind.BUTTON and on[0].value == cc_map.MAX_7BIT
        # 保持中（button_hold_ticks=3 未満）は何も出ない
        assert seq.tick(event_pending=False) == []
        assert seq.tick(event_pending=False) == []
        # 3 Tick 目で OFF
        off = seq.tick(event_pending=False)
        assert off[0].kind is ActionKind.BUTTON and off[0].value == 0

    def test_enters_scalar_phase_after_last_button(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=2, cc_step=64)
        _advance_to_phase(seq, Phase.SCALAR)
        assert seq._phase is Phase.SCALAR
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_auto_sequencer.py::TestButtonPhase -v`
Expected: FAIL（`_tick_button` がスタブのため `Phase.SCALAR` へ到達せず AssertionError）

- [ ] **Step 3: `_tick_button` を実装**

`auto_sequencer.py` の `_tick_button` スタブを置換:

```python
    def _tick_button(self) -> List[SendAction]:
        idx = self._button_index
        if not self._button_on:
            self._button_on = True
            self._hold_counter = 0
            return [SendAction(ActionKind.BUTTON, idx, cc_map.MAX_7BIT, f"ボタン{idx} ON")]
        self._hold_counter += 1
        if self._hold_counter >= self._button_hold_ticks:
            self._button_on = False
            self._button_index += 1
            if self._button_index >= len(cc_map.BUTTON_CCS):
                self._button_index = 0
                self._phase = Phase.SCALAR
            return [SendAction(ActionKind.BUTTON, idx, 0, f"ボタン{idx} OFF")]
        return []
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/test_auto_sequencer.py -v`
Expected: PASS（既存 + TestButtonPhase 3 件）

- [ ] **Step 5: コミット**

```bash
git add auto_sequencer.py tests/test_auto_sequencer.py
git commit -m "feat: AutoSequencer ボタン順次フェーズを実装"
```

---

## Task 4: スカラースイープフェーズ（Preset/Error/State）

**Files:**
- Modify: `auto_sequencer.py`（`_tick_scalar` を実装）
- Test: `tests/test_auto_sequencer.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_auto_sequencer.py` に追記:

```python
class TestScalarPhase:
    def test_sweeps_preset_error_state_in_order(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=64)
        _advance_to_phase(seq, Phase.SCALAR)

        by_cc = {cc_map.PRESET_CC: [], cc_map.ERROR_CC: [], cc_map.STATE_CC: []}
        order = []
        for _ in range(300):
            if seq._phase is not Phase.SCALAR:
                break
            for a in seq.tick(event_pending=False):
                if a.kind is ActionKind.SCALAR:
                    by_cc[a.target].append(a.value)
                    if a.target not in order:
                        order.append(a.target)

        # Preset(40) → Error(41) → State(42) の順で処理される
        assert order == [cc_map.PRESET_CC, cc_map.ERROR_CC, cc_map.STATE_CC]
        # 各スカラーは 0 から始まり 127 で終わる
        for cc in (cc_map.PRESET_CC, cc_map.ERROR_CC, cc_map.STATE_CC):
            assert by_cc[cc][0] == 0
            assert by_cc[cc][-1] == cc_map.MAX_7BIT
            assert max(by_cc[cc]) == cc_map.MAX_7BIT  # 127 を超えない

    def test_enters_event_phase_after_state(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=64)
        _advance_to_phase(seq, Phase.EVENT)
        assert seq._phase is Phase.EVENT
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_auto_sequencer.py::TestScalarPhase -v`
Expected: FAIL（`_tick_scalar` スタブのため `Phase.EVENT` へ到達せず）

- [ ] **Step 3: `_tick_scalar` を実装**

`auto_sequencer.py` の `_tick_scalar` スタブを置換:

```python
    def _tick_scalar(self) -> List[SendAction]:
        cc = _SCALAR_CCS[self._scalar_index]
        name = _SCALAR_NAMES[self._scalar_index]
        value = min(self._scalar_value, cc_map.MAX_7BIT)
        log = f"{name} = {value}" if value in (0, cc_map.MAX_7BIT) else None
        action = SendAction(ActionKind.SCALAR, cc, value, log)
        if self._scalar_value >= cc_map.MAX_7BIT:
            self._scalar_index += 1
            self._scalar_value = 0
            if self._scalar_index >= len(_SCALAR_CCS):
                self._scalar_index = 0
                self._phase = Phase.EVENT
        else:
            self._scalar_value += self._cc_step
        return [action]
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/test_auto_sequencer.py -v`
Expected: PASS（既存 + TestScalarPhase 2 件）

- [ ] **Step 5: コミット**

```bash
git add auto_sequencer.py tests/test_auto_sequencer.py
git commit -m "feat: AutoSequencer スカラースイープフェーズを実装"
```

---

## Task 5: イベント送信フェーズとサイクルループ

**Files:**
- Modify: `auto_sequencer.py`（`_tick_event` を実装）
- Test: `tests/test_auto_sequencer.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_auto_sequencer.py` に追記:

```python
class TestEventPhase:
    def test_sends_three_events_in_order_waiting_for_response(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)

        sent = []
        # event_pending を「送信直後の 1 Tick だけ True」に擬似制御する
        pending = False
        for _ in range(50):
            actions = seq.tick(event_pending=pending)
            events = [a for a in actions if a.kind is ActionKind.EVENT]
            if events:
                sent.append(events[0].target)
                pending = True   # 送信したので次 Tick は応答待ち
            else:
                pending = False  # 応答が来た（解決）とみなす
            if seq._phase is Phase.STICK:  # サイクル一巡
                break

        assert sent == [cc_map.EVT_HEARTBEAT, cc_map.EVT_BUTTON_COMBO, cc_map.EVT_SENSOR_TRIGGER]

    def test_does_not_advance_while_event_pending(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)
        first = seq.tick(event_pending=False)   # 1 件目送信
        assert first[0].kind is ActionKind.EVENT
        # 応答待ちの間は何も送らない
        assert seq.tick(event_pending=True) == []
        assert seq.tick(event_pending=True) == []

    def test_event_arg_increments_across_sends(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)
        a1 = seq.tick(event_pending=False)[0]
        seq.tick(event_pending=False)            # 解決して次へ
        a2 = seq.tick(event_pending=False)[0]
        assert a2.value == (a1.value + 1) & cc_map.MAX_7BIT

    def test_completing_event_phase_loops_back_to_stick(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)
        # 3 イベントを送信→解決で一巡し STICK に戻る
        for _ in range(20):
            seq.tick(event_pending=False)
            if seq._phase is Phase.STICK:
                break
        assert seq._phase is Phase.STICK
        assert seq._axis_index == 0
        assert seq._axis_value == cc_map.CENTER_14BIT
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_auto_sequencer.py::TestEventPhase -v`
Expected: FAIL（`_tick_event` スタブのためイベントが送られない）

- [ ] **Step 3: `_tick_event` を実装**

`auto_sequencer.py` の `_tick_event` スタブを置換:

```python
    def _tick_event(self, event_pending: bool) -> List[SendAction]:
        if not self._event_sent:
            opcode = _EVENT_OPCODES[self._event_index]
            name = _EVENT_NAMES[self._event_index]
            arg = self._event_arg
            self._event_arg = (self._event_arg + 1) & cc_map.MAX_7BIT
            self._event_sent = True
            return [SendAction(ActionKind.EVENT, opcode, arg, f"イベント送信 {name} arg={arg}")]
        if event_pending:
            return []  # 応答待ち
        # 応答 or タイムアウトで解決 → 次イベントへ
        self._event_index += 1
        self._event_sent = False
        if self._event_index >= len(_EVENT_OPCODES):
            self._reset_cycle()  # サイクル完了 → STICK へ（event_arg は保持）
        return []
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/test_auto_sequencer.py -v`
Expected: PASS（全クラス）

- [ ] **Step 5: 全体テストとカバレッジ確認**

Run: `pytest --cov=auto_sequencer --cov-report=term-missing tests/test_auto_sequencer.py -v`
Expected: PASS、`auto_sequencer` のカバレッジ 80% 以上

- [ ] **Step 6: コミット**

```bash
git add auto_sequencer.py tests/test_auto_sequencer.py
git commit -m "feat: AutoSequencer イベント送信フェーズとサイクルループを実装"
```

---

## Task 6: Stick/Slider モード撤廃と「中心点へ移動」統一

**Files:**
- Modify: `tests/test_cc_map.py`（slider テスト 3 件削除）
- Modify: `cc_map.py`（`norm_slider` 削除）
- Modify: `midi_simulator.py:26,38-39,59,71-87,248-262,264-268`（モード分岐撤去・`_center_axes`・`AXIS_CENTER`）
- Modify: `keyboard_map.py:44,49-62`（`help_text()` 更新。`M` の再定義は Task 7）

- [ ] **Step 1: slider テストを削除（先に期待値を消す）**

`tests/test_cc_map.py` の `TestNormalization` クラスから次の 3 メソッドを削除する:

```python
    def test_slider_zero(self):
        assert cc_map.norm_slider(0) == 0.0

    def test_slider_max(self):
        assert cc_map.norm_slider(16383) == 1.0

    def test_slider_mid(self):
        assert cc_map.norm_slider(8192) == pytest.approx(0.5, abs=1e-3)
```

`TestNormalization` には bipolar 系 4 メソッドが残る。

- [ ] **Step 2: `cc_map.norm_slider` を削除**

`cc_map.py` の次の関数全体（`def norm_slider` ～ return 行）を削除する:

```python
def norm_slider(value: int) -> float:
    """14bit 生値を 0.0..1.0 に線形正規化する（表示用・クランプ）。"""
    return max(0.0, min(1.0, value / MAX_14BIT))
```

- [ ] **Step 3: `midi_simulator.py` の Stick/Slider 撤廃**

3-1. 定数を変更（`midi_simulator.py:23-26` 付近）。`MODE_ORIGIN` を削除し `AXIS_CENTER` と自動モード定数を追加:

```python
TICK_INTERVAL = 1.0 / 60.0
# 押下中ランプの 1 Tick あたりの 14bit 変化量（約 0.5 秒でフルスケール）
STICK_STEP_PER_TICK = 550
# 軸の中心点（初期値・"0" キー・自動モード遷移時の移動先）
AXIS_CENTER = cc_map.CENTER_14BIT
# --- 自動デバッグ入力モードのパラメータ ---
AUTO_STICK_STEP = 550        # スティックスイープの 1 Tick あたり 14bit 変化量
AUTO_BUTTON_HOLD_TICKS = 15  # 各ボタンの ON 保持 Tick 数（≒0.25s @60fps）
AUTO_CC_STEP = 8             # Preset/Error/State スイープの刻み（0→127 を約16段）
```

3-2. `__init__` の軸初期化（`midi_simulator.py:38-39`）を置換:

```python
        self._axis_raw: List[int] = [AXIS_CENTER] * 4
```

（`self._mode = "stick"` 行と `origin = MODE_ORIGIN[self._mode]` 行を削除。）

3-3. `run()` から `self._select_mode()` 呼び出し（`midi_simulator.py:59`）を削除する。

3-4. `_select_mode` メソッド全体（`midi_simulator.py:71-87`）を削除する。

3-5. `_reset_axes`（`midi_simulator.py:248-256`）を `_center_axes` に改名・改文言:

```python
    def _center_axes(self) -> None:
        """全軸を中心点(8192)へ移動して送信する。"""
        for axis in range(4):
            self._axis_raw[axis] = AXIS_CENTER
            msb_cc, lsb_cc = cc_map.CC_AXES[axis]
            self._midi.send_14bit(msb_cc, lsb_cc, AXIS_CENTER)
            self._axis_sent[axis] = AXIS_CENTER
        print(f"全軸を中心点 {AXIS_CENTER} へ移動")
```

3-6. `_toggle_mode` メソッド全体（`midi_simulator.py:258-262`）を削除する（自動トグルは Task 7 で追加）。

3-7. `_on_keydown` 内の `_reset_axes` 呼び出し（`midi_simulator.py:203`）を `_center_axes` に変更:

```python
        elif key == keyboard_map.AXIS_RESET_KEY:
            self._center_axes()
```

3-8. `_on_keydown` 内の `TOGGLE_MODE_KEY` 分岐（`midi_simulator.py:204-205`）を**この Task では削除**する（Task 7 で自動トグルとして再追加）:

```python
# 削除する 2 行:
#        elif key == keyboard_map.TOGGLE_MODE_KEY:
#            self._toggle_mode()
```

3-9. `_log_axis`（`midi_simulator.py:264-268`）を bipolar 一本化:

```python
    def _log_axis(self, axis: int) -> None:
        """軸の現在値と正規化値（双極 -1..+1）をログ出力する。"""
        raw = self._axis_raw[axis]
        norm = cc_map.norm14_bipolar(raw)
        print(f"{cc_map.AXIS_NAMES[axis]}: {raw} ({norm:+.3f})")
```

- [ ] **Step 4: `keyboard_map.help_text()` の `0` 行を更新**

`keyboard_map.py` の `help_text()` 内、`0=全軸原点へ` を `0=全軸を中心点へ移動` に変更:

```python
        "  スティック: 1/2=左X±  3/4=左Y±  5/6=右X±  7/8=右Y±（押下中ランプ）  0=全軸を中心点へ移動\n"
```

（`M` 行はこの Task では一旦そのまま。Task 7 で自動モード用に差し替える。）

- [ ] **Step 5: テストとインポートを確認**

```bash
pytest -v
python -c "import midi_simulator; print('import OK')"
```

Expected: pytest 全 PASS（cc_map の slider テストが消え、残りグリーン）。`import OK` が表示され、`midi_simulator` が `norm_slider`/`MODE_ORIGIN`/`_select_mode`/`_toggle_mode` 参照を残していないこと（`AttributeError`/`ImportError` が出ない）。

- [ ] **Step 6: コミット**

```bash
git add cc_map.py tests/test_cc_map.py midi_simulator.py keyboard_map.py
git commit -m "refactor: Stick/Slider モードを撤廃し中心点移動に統一"
```

---

## Task 7: 自動デバッグ入力モードを midi_simulator に統合

**Files:**
- Modify: `keyboard_map.py:44`（`TOGGLE_MODE_KEY` → `AUTO_MODE_KEY`、`help_text()` の `M` 行）
- Modify: `midi_simulator.py`（import・`__init__`・`_loop`・`_on_keydown`/`_on_keyup`・`_toggle_auto_mode`・`_tick_auto`・`_dispatch_auto_action`・`_sync_scalar`・`_all_buttons_off`）

このフェーズは pygame/MIDI 依存のためユニットテスト対象外（INSTRUCTIONS.md 準拠）。`AutoSequencer` の純粋ロジックは Task 1–5 でテスト済み。ここでは import 健全性と手動検証で担保する。

- [ ] **Step 1: `keyboard_map.py` を自動トグル用に更新**

1-1. `keyboard_map.py:44` の定数名と意味を変更:

```python
AUTO_MODE_KEY = pygame.K_m
```

1-2. `help_text()` の `モード切替: M（...）` 行を自動モード用に差し替え:

```python
        "  自動入力  : M（自動デバッグ入力 ON/OFF・全要素を巡回送信）\n"
```

- [ ] **Step 2: `midi_simulator.py` の import を追加**

`midi_simulator.py:21`（`from messaging import ...` の前後）に追加:

```python
from auto_sequencer import ActionKind, AutoSequencer, SendAction
```

- [ ] **Step 3: `__init__` に自動モード状態を追加**

`midi_simulator.py` の `__init__`、`self._help_requested = False` の近くに追加:

```python
        self._auto_mode = False
        self._auto: Optional[AutoSequencer] = None
```

- [ ] **Step 4: `_loop` に自動分岐を追加**

`midi_simulator.py` の `_loop`、`self._ramp_axes(pressed)` を呼んでいる箇所（`midi_simulator.py:163` 付近）を分岐に置換:

```python
                if self._auto_mode:
                    self._tick_auto()
                else:
                    self._ramp_axes(pressed)
```

- [ ] **Step 5: `_on_keydown` / `_on_keyup` に自動モードガードとトグルを追加**

5-1. `_on_keydown`（Task 6 で `_center_axes` 化・`TOGGLE_MODE_KEY` 分岐削除済み）を、以下の**最終形で全置換**する。冒頭に手動入力ガード、`AXIS_RESET_KEY` の次に `AUTO_MODE_KEY` 分岐を持つ:

```python
    def _on_keydown(self, key: int) -> None:
        """KEYDOWN: 自動モード中は M/ヘルプ/終了のみ受理。手動時はボタン/離散/イベント/中心点移動。"""
        allowed_in_auto = (keyboard_map.AUTO_MODE_KEY, keyboard_map.HELP_KEY, keyboard_map.QUIT_KEY)
        if self._auto_mode and key not in allowed_in_auto:
            return
        if key in keyboard_map.BUTTON_KEYS:
            idx = keyboard_map.BUTTON_KEYS[key]
            self._buttons[idx] = True
            self._midi.send_cc(cc_map.BUTTON_CCS[idx], 127)
            print(f"ボタン{idx}: ON")
        elif key in keyboard_map.PRESET_KEYS:
            self._preset = cc_map.clamp(self._preset + keyboard_map.PRESET_KEYS[key], 0, cc_map.MAX_7BIT)
            self._midi.send_cc(cc_map.PRESET_CC, self._preset)
            print(f"Preset 送信: {self._preset}")
        elif key in keyboard_map.ERROR_KEYS:
            self._error = cc_map.clamp(self._error + keyboard_map.ERROR_KEYS[key], 0, cc_map.MAX_7BIT)
            self._midi.send_cc(cc_map.ERROR_CC, self._error)
            print(f"Error 送信: {self._error}")
        elif key in keyboard_map.STATE_KEYS:
            self._state = cc_map.clamp(self._state + keyboard_map.STATE_KEYS[key], 0, cc_map.MAX_7BIT)
            self._midi.send_cc(cc_map.STATE_CC, self._state)
            print(f"State 送信: {self._state}")
        elif key in keyboard_map.EVENT_KEYS:
            self._send_event(keyboard_map.EVENT_KEYS[key])
        elif key == keyboard_map.AXIS_RESET_KEY:
            self._center_axes()
        elif key == keyboard_map.AUTO_MODE_KEY:
            self._toggle_auto_mode()
        elif key == keyboard_map.HELP_KEY:
            self._help_requested = True
        elif key == keyboard_map.QUIT_KEY:
            self._running = False
```

5-2. `_on_keyup` を、以下の**最終形で全置換**する（冒頭に自動モードガードを追加、既存のボタン OFF・軸ログは維持）:

```python
    def _on_keyup(self, key: int) -> None:
        """KEYUP: 自動モード中は無視。手動時はボタン OFF と軸キー離上時の最終値ログ。"""
        if self._auto_mode:
            return
        if key in keyboard_map.BUTTON_KEYS:
            idx = keyboard_map.BUTTON_KEYS[key]
            self._buttons[idx] = False
            self._midi.send_cc(cc_map.BUTTON_CCS[idx], 0)
            print(f"ボタン{idx}: OFF")
        elif key in keyboard_map.AXIS_KEYS:
            self._log_axis(keyboard_map.AXIS_KEYS[key][0])
```

- [ ] **Step 6: 自動モードのトグルとディスパッチを実装**

`midi_simulator.py` の `_ramp_axes` の直後あたりに追加:

```python
    def _toggle_auto_mode(self) -> None:
        """自動デバッグ入力モードを ON/OFF し、軸を中心点・全ボタン OFF に整える。"""
        self._auto_mode = not self._auto_mode
        if self._auto_mode:
            self._auto = AutoSequencer(AUTO_STICK_STEP, AUTO_BUTTON_HOLD_TICKS, AUTO_CC_STEP)
        self._all_buttons_off()
        self._center_axes()
        print(f"自動デバッグ入力: {'ON' if self._auto_mode else 'OFF'}")

    def _all_buttons_off(self) -> None:
        """押下中の全ボタンを OFF 送信する（自動/手動の残留点灯を解消）。"""
        for idx in range(len(cc_map.BUTTON_CCS)):
            if self._buttons[idx]:
                self._buttons[idx] = False
                self._midi.send_cc(cc_map.BUTTON_CCS[idx], 0)

    def _tick_auto(self) -> None:
        """自動シーケンスを 1 Tick 進め、返ったアクションを送信する。"""
        event_pending = self._messaging.snapshot().event_pending
        for action in self._auto.tick(event_pending):
            self._dispatch_auto_action(action)

    def _dispatch_auto_action(self, action: SendAction) -> None:
        """AutoSequencer の SendAction を実際の MIDI 送信に変換する。"""
        if action.kind is ActionKind.AXIS:
            msb_cc, lsb_cc = cc_map.CC_AXES[action.target]
            self._midi.send_14bit(msb_cc, lsb_cc, action.value)
            self._axis_raw[action.target] = action.value
            self._axis_sent[action.target] = action.value
        elif action.kind is ActionKind.BUTTON:
            self._buttons[action.target] = action.value > 0
            self._midi.send_cc(cc_map.BUTTON_CCS[action.target], action.value)
        elif action.kind is ActionKind.SCALAR:
            self._sync_scalar(action.target, action.value)
            self._midi.send_cc(action.target, action.value)
        elif action.kind is ActionKind.EVENT:
            self._messaging.send_event(action.target, action.value)
        if action.log:
            print(f"[AUTO] {action.log}")

    def _sync_scalar(self, cc: int, value: int) -> None:
        """自動送信したスカラー値を内部状態へ反映し、手動復帰時の整合を保つ。"""
        if cc == cc_map.PRESET_CC:
            self._preset = value
        elif cc == cc_map.ERROR_CC:
            self._error = value
        elif cc == cc_map.STATE_CC:
            self._state = value
```

- [ ] **Step 7: import と既存テストの健全性を確認**

```bash
python -c "import midi_simulator; print('import OK')"
pytest -v
```

Expected: `import OK`、pytest 全 PASS（純粋ロジック側に回帰なし）。

- [ ] **Step 8: 手動検証（MIDI 出力ポートが必要）**

1. `python midi_simulator.py` を起動 → モード選択 UI が**出ない**こと（Stick/Slider 撤廃の確認）。出力ポートを選択。
2. pygame ウィンドウにフォーカスし `M` を押下 → コンソールに `自動デバッグ入力: ON`、続いて `[AUTO] スティック ...`→`[AUTO] ボタン..`→`[AUTO] Preset/Error/State..`→`[AUTO] イベント送信..` が巡回表示されること。
3. 自動中に手動キー（`Q`・`1` など）を押しても送信されない（無視される）こと。
4. もう一度 `M` → `自動デバッグ入力: OFF`、全軸が中心点へ移動し、手動操作が復帰すること。
5. `0` キーで `全軸を中心点 8192 へ移動` と表示されること。
6. `ESC` で正常終了すること。

- [ ] **Step 9: コミット**

```bash
git add midi_simulator.py keyboard_map.py
git commit -m "feat: 自動デバッグ入力モードを統合し M キーで切替"
```

---

## Task 8: ドキュメント追従

**Files:**
- Modify: `INSTRUCTIONS.md`
- Modify: `docs/superpowers/specs/2026-06-05-demo-mode-design.md`

- [ ] **Step 1: `INSTRUCTIONS.md` のアーキテクチャに `auto_sequencer.py` を追記**

`- **`cc_map.py`** - ...` の項目の直後に追加:

```markdown
- **`auto_sequencer.py`** - 自動デバッグ入力モードの巡回シーケンス生成（`AutoSequencer` / `SendAction` / `ActionKind` / `Phase`）。`tick(event_pending)` がアクション列を返す MIDI/pygame 非依存の純粋ロジック。
```

`cc_map.py` の項目内の `正規化（`norm14_bipolar` / `norm_slider`）` を `正規化（`norm14_bipolar`）` に修正する。

- [ ] **Step 2: 「スティック解釈」セクションを書き換え**

`### スティック解釈（Stick / Slider）` セクション全体を次に置換:

```markdown
### スティック解釈（中心点固定）

スティック軸（CC 16/48・17/49・18/50・19/51）は中心点 8192 を基準とする双極値（-1.0 … +1.0）として扱う。`0` キーで全軸を中心点 8192 へ移動できる。表示は `norm14_bipolar` による双極正規化。

> 旧 Stick/Slider モード切替は撤廃済み（送信バイト列に影響せず、原点と表示正規化のみを変えるため）。経緯は [docs/superpowers/specs/2026-06-09-auto-debug-input-mode-design.md](docs/superpowers/specs/2026-06-09-auto-debug-input-mode-design.md) を参照。
```

- [ ] **Step 3: 「自動デバッグ入力モード」セクションを追記**

「### コマンド/イベント I/F」セクションの直後に追加:

```markdown
### 自動デバッグ入力モード

`M` キーで ON/OFF する。手動操作なしに送信系の全 CC を巡回送信し、受信側（Unity）の動作確認に使う。1 サイクルは「スティック各軸スイープ → ボタン 0–9 順次 ON/OFF → Preset/Error/State スイープ → イベント送信」で、終了後ループする。生成ロジックは `auto_sequencer.py` の `AutoSequencer`（純粋関数・テスト済み）。自動モード中は手動入力を無視し、`M`・`/`・`ESC` のみ有効。MIDI 入力が無い場合、イベントは応答タイムアウト（30 Tick）で次へ進む。
```

- [ ] **Step 4: キーボード操作テーブルを更新**

`## キーボード操作` のテーブルで 2 行を変更:

`| `0` | 全軸を原点へ（Stick=8192 / Slider=0） |` を:

```markdown
| `0` | 全軸を中心点へ移動（8192） |
```

`| `M` | Stick ⇔ Slider 切替（軸を原点リセット） |` を:

```markdown
| `M` | 自動デバッグ入力モード ON/OFF（全要素を巡回送信） |
```

- [ ] **Step 5: 「主要な設定」と「UI 機能」を更新**

5-1. `## 主要な設定` に追記:

```markdown
- **`AXIS_CENTER`**: 軸の中心点（既定 8192 = `cc_map.CENTER_14BIT`）
- **`AUTO_STICK_STEP` / `AUTO_BUTTON_HOLD_TICKS` / `AUTO_CC_STEP`**: 自動デバッグ入力モードのスイープ速度・ボタン保持・スカラー刻み（既定 550 / 15 / 8）
```

5-2. `## UI 機能` の `- **モード選択**: Stick / Slider ...` 行を削除し、次に置換:

```markdown
- **自動デバッグ入力**: `M` キーで全 CC を巡回送信するデバッグモードを ON/OFF（起動時の選択 UI は無し）
```

- [ ] **Step 6: 旧デモモード設計書に supersede 注記を付ける**

`docs/superpowers/specs/2026-06-05-demo-mode-design.md` の 1 行目見出し直後に追加:

```markdown
> **⚠ SUPERSEDED（2026-06-09）:** 本設計は旧アーキテクチャ（ゲームパッド → MIDI 送信ツール）向けで未実装のまま陳腐化した。同等の目的（手動入力なしの自動送信確認）は新仕様で [2026-06-09-auto-debug-input-mode-design.md](2026-06-09-auto-debug-input-mode-design.md) が引き継ぐ。本書は履歴として温存する。
```

- [ ] **Step 7: ドキュメントの整合を目視確認**

`INSTRUCTIONS.md` に `Stick ⇔ Slider` / `norm_slider` / `Slider=0` の残存がないこと、`auto_sequencer.py` と自動デバッグ入力モードが記載されていることを確認する。

```bash
grep -n -E "Slider|norm_slider" INSTRUCTIONS.md
```

Expected: 「撤廃済み」の文脈以外でヒットしない（旧記述の残存なし）。

- [ ] **Step 8: コミット**

```bash
git add INSTRUCTIONS.md docs/superpowers/specs/2026-06-05-demo-mode-design.md
git commit -m "docs: 自動デバッグ入力モードと中心点移動に追従、旧デモ設計に supersede 注記"
```

---

## 完了基準

- [ ] `pytest -v` が全 PASS（`AutoSequencer` の全フェーズ + 既存 cc_map/messaging）
- [ ] `auto_sequencer` のカバレッジ 80% 以上
- [ ] `python midi_simulator.py` で起動時モード選択が出ず、`M` で自動巡回送信が ON/OFF できる（手動検証）
- [ ] `0` キー・自動トグルで「中心点へ移動」と表示される
- [ ] `INSTRUCTIONS.md` から Stick/Slider 記述が一掃され、自動デバッグ入力モードが記載されている
- [ ] 旧デモモード設計書に supersede 注記がある
