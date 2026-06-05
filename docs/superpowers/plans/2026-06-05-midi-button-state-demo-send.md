# MIDI ボタン/状態送信＋デモモード 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pyMidiSimulator（`midi_simulator.py`）に、ゲームパッドのボタン送信（CC#20–29）・状態送信（CC#30）と、ゲームパッド不要のデモモード（自動出力）を追加する。

**Architecture:** `process_input` をスティック/ボタン/状態の 3 メソッドに分割し、7bit 送信 `send_cc` を共通化（設計①）。`run()` にモード分岐を足し、デモモードは `_process_demo()` が経過時間から全 CC を規則的パターンで生成して既存送信パスで送る（設計②）。

**Tech Stack:** Python 3.7+ / pygame / python-rtmidi。

**確定済み設計:** [2026-06-05-midi-button-state-send-design.md](../specs/2026-06-05-midi-button-state-send-design.md)（ボタン/状態）・[2026-06-05-demo-mode-design.md](../specs/2026-06-05-demo-mode-design.md)（デモモード）。

---

## 検証方針（重要）

- 本プロジェクトに自動テスト基盤は無い（`requirements.txt` は pygame / python-rtmidi のみ、設計で手動検証と確定）。各タスクの「検証」は次のいずれか：
  - **構文確認**: venv を有効化し `python -m py_compile midi_simulator.py` がエラー無く通ること（import を走らせず構文のみ検査するので、ゲームパッド/MIDI 機器が無くても実行可）。
  - **手動動作確認**: 実機ゲームパッドまたはデモモードで起動し、受信側（`test_midi_debug.py` / Unity HUD）で目視。
- **常時実行可能性の順序保証**: 各タスクは「追加 → 既存と共存 → 構文確認可」の順に並べてある。Phase A（ボタン/状態）→ Phase B（デモ）→ Phase C（ドキュメント）の順に実施すること。
- venv の有効化（Windows）: `.venv\Scripts\activate`。各検証コマンドは venv 有効化後に実行する。

---

## File Structure

| ファイル | 操作 | 責務 |
|----------|------|------|
| `midi_simulator.py` | 変更 | 送信ロジック本体。ボタン/状態送信、メソッド分割、デモモードを追加 |
| `CLAUDE.md` | 変更 | MIDI Mapping にボタン（CC#20–29）・状態（CC#30）・デモモードを追記 |

`midi_simulator.py` 内の追加・変更箇所（`GamepadMidiController` クラス）:

- `__init__`: ボタン/状態/ショルダー定数、`prev_buttons` / `state_value`、デモ定数 `DEMO_*`、デモ用フィールド
- 新規メソッド: `send_cc` / `_process_sticks` / `_read_buttons` / `_process_buttons` / `_process_state` / `select_mode` / `_process_demo` / `_demo_sticks` / `_demo_buttons` / `_demo_state`
- 変更メソッド: `process_input`（分割）/ `run`（モード分岐）
- ファイル先頭: `import math` を追加

---

# Phase A — ボタン/状態送信（設計①）

## Task 1: ボタン/状態の定数とフィールドを追加

**Files:**
- Modify: `midi_simulator.py`（`__init__`）

- [ ] **Step 1: CC 定数群を追加**

`__init__` 内の `self.CC_RIGHT_Y_LSB = 51` の**直後**に追記:

```python
        # ボタン設定（CC#20-29）
        self.CC_BUTTON_BASE = 20    # ボタン i → CC#(20+i)
        self.BUTTON_COUNT = 10

        # 状態入力設定（CC#30）
        self.CC_STATE = 30
        self.STATE_MAX = 16         # 0..16 の17段階

        # ショルダー（状態増減）: XInput 一般値 LB=4 / RB=5。機種により異なるため必要なら変更
        self.SHOULDER_DOWN_BTN = 4  # 押下で状態 -1
        self.SHOULDER_UP_BTN = 5    # 押下で状態 +1
```

- [ ] **Step 2: 内部状態フィールドを追加**

`__init__` 内の `self.prev_right_stick = (0.0, 0.0)` の**直後**に追記:

```python
        # ボタン状態（変化検出用）と状態セレクタ値
        self.prev_buttons = [False] * self.BUTTON_COUNT
        self.state_value = 0
```

- [ ] **Step 3: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし（終了コード 0）

- [ ] **Step 4: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(send): ボタン/状態送信の定数とフィールドを追加"
```

---

## Task 2: 7bit CC 送信 `send_cc` を追加

**Files:**
- Modify: `midi_simulator.py`（`send_14bit_cc` の直後）

- [ ] **Step 1: `send_cc` メソッドを追加**

`send_14bit_cc` メソッドの最終行 `print(f"    MIDI: CC#{cc_msb}(MSB)={msb} [14bit={value}]")` で終わるメソッドの**直後**（`get_stick_values` の定義の前）に追記:

```python
    def send_cc(self, cc: int, value: int):
        """7ビットCCを1メッセージ送信（ボタン/状態で共用）"""
        if not self.midi_out:
            return

        self.midi_out.send_message([0xB0, cc, value & 0x7F])
        print(f"    MIDI: CC#{cc}={value & 0x7F}")
```

- [ ] **Step 2: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 3: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(send): 7bit CC送信 send_cc を追加"
```

---

## Task 3: `process_input` をスティック処理メソッドに分離

**Files:**
- Modify: `midi_simulator.py`（`process_input` 全体）

既存のスティック処理を `_process_sticks()` に移し、`process_input()` はそれを呼ぶだけにする（この時点では挙動不変）。

- [ ] **Step 1: `process_input` を次の2メソッドに置き換え**

既存の `process_input` メソッド全体（`def process_input(self):` から右スティック処理の `self.prev_right_stick = right_stick` まで）を、次に**置き換え**:

```python
    def process_input(self):
        """入力処理とMIDI送信（通常モード）"""
        pygame.event.pump()
        self._process_sticks()

    def _process_sticks(self):
        """左右スティックの変化を14bit CCで送信"""
        left_stick, right_stick = self.get_stick_values()

        # 左スティック処理
        if abs(left_stick[0] - self.prev_left_stick[0]) > 0.001 or \
           abs(left_stick[1] - self.prev_left_stick[1]) > 0.001:

            x_value = self.convert_to_midi_value(left_stick[0])
            y_value = self.convert_to_midi_value(left_stick[1])

            self.send_14bit_cc(self.CC_LEFT_X_LSB, self.CC_LEFT_X_MSB, x_value)
            self.send_14bit_cc(self.CC_LEFT_Y_LSB, self.CC_LEFT_Y_MSB, y_value)

            print(f"左スティック X:{left_stick[0]:6.3f}→{x_value:5d} Y:{left_stick[1]:6.3f}→{y_value:5d}")
            self.prev_left_stick = left_stick

        # 右スティック処理
        if abs(right_stick[0] - self.prev_right_stick[0]) > 0.001 or \
           abs(right_stick[1] - self.prev_right_stick[1]) > 0.001:

            x_value = self.convert_to_midi_value(right_stick[0])
            y_value = self.convert_to_midi_value(right_stick[1])

            self.send_14bit_cc(self.CC_RIGHT_X_LSB, self.CC_RIGHT_X_MSB, x_value)
            self.send_14bit_cc(self.CC_RIGHT_Y_LSB, self.CC_RIGHT_Y_MSB, y_value)

            print(f"右スティック X:{right_stick[0]:6.3f}→{x_value:5d} Y:{right_stick[1]:6.3f}→{y_value:5d}")
            self.prev_right_stick = right_stick
```

- [ ] **Step 2: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 3: コミット**

```bash
git add midi_simulator.py
git commit -m "refactor(send): process_input をスティック処理メソッドに分離"
```

---

## Task 4: ボタン送信（CC#20–29）を追加

**Files:**
- Modify: `midi_simulator.py`（`process_input` と `_process_sticks` の後）

- [ ] **Step 1: `process_input` にボタン読み取り・送信の呼び出しを追加**

`process_input` を次に**置き換え**（`_process_sticks` の呼び出しの後にボタン処理を追加）:

```python
    def process_input(self):
        """入力処理とMIDI送信（通常モード）"""
        pygame.event.pump()
        self._process_sticks()
        current = self._read_buttons()
        self._process_buttons(current)
        self.prev_buttons = current
```

- [ ] **Step 2: `_read_buttons` と `_process_buttons` を追加**

`_process_sticks` メソッドの**直後**に追記:

```python
    def _read_buttons(self):
        """現在のボタン状態を BUTTON_COUNT 個ぶん取得（未接続/不足は False）"""
        if not self.joystick:
            return [False] * self.BUTTON_COUNT
        n = self.joystick.get_numbuttons()
        return [bool(self.joystick.get_button(i)) if i < n else False
                for i in range(self.BUTTON_COUNT)]

    def _process_buttons(self, current):
        """変化したボタンだけ CC#20-29 を送信（押下127 / 離上0）"""
        for i in range(self.BUTTON_COUNT):
            if current[i] != self.prev_buttons[i]:
                self.send_cc(self.CC_BUTTON_BASE + i, 127 if current[i] else 0)
                print(f"ボタン{i}: {'ON' if current[i] else 'OFF'}")
```

- [ ] **Step 3: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 4: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(send): ボタン送信(CC#20-29)を追加"
```

---

## Task 5: 状態送信（CC#30・ショルダー増減）を追加

**Files:**
- Modify: `midi_simulator.py`（`process_input` と `_process_buttons` の後）

- [ ] **Step 1: `process_input` に状態処理の呼び出しを追加**

`process_input` を次に**置き換え**（`prev_buttons` 更新の前に状態処理を挟む）:

```python
    def process_input(self):
        """入力処理とMIDI送信（通常モード）"""
        pygame.event.pump()
        self._process_sticks()
        current = self._read_buttons()
        self._process_buttons(current)
        self._process_state(current)
        self.prev_buttons = current
```

- [ ] **Step 2: `_process_state` を追加**

`_process_buttons` メソッドの**直後**に追記:

```python
    def _process_state(self, current):
        """ショルダーの押下エッジで状態を増減し、変化時に CC#30 を送信"""
        down_edge = current[self.SHOULDER_DOWN_BTN] and not self.prev_buttons[self.SHOULDER_DOWN_BTN]
        up_edge = current[self.SHOULDER_UP_BTN] and not self.prev_buttons[self.SHOULDER_UP_BTN]

        new_state = self.state_value
        if up_edge:
            new_state = min(self.STATE_MAX, new_state + 1)
        if down_edge:
            new_state = max(0, new_state - 1)

        if new_state != self.state_value:
            self.state_value = new_state
            cc_value = round(new_state / self.STATE_MAX * 127)
            self.send_cc(self.CC_STATE, cc_value)
            print(f"状態: {new_state}/{self.STATE_MAX} (CC#30={cc_value})")
```

- [ ] **Step 3: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 4: 手動動作確認（実機ゲームパッドがある場合・任意）**

`python midi_simulator.py` → `1`（通常モード）→ MIDI 出力ポート選択 → ゲームパッド選択。
- ボタン押下/離上で `ボタンN: ON/OFF` と `CC#(20+N)=127/0` が出る。
- LB/RB 押下で `状態: ...` と `CC#30=...` が増減する。
- スティックが従来どおり送信される（回帰）。

- [ ] **Step 5: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(send): 状態送信(CC#30、ショルダー増減)を追加"
```

### ✅ Checkpoint A

ボタン（CC#20–29）と状態（CC#30）が通常モードで送信され、スティックは従来どおり。Phase B へ進む。

---

# Phase B — デモモード（設計②）

## Task 6: デモモードの定数とフィールドを追加

**Files:**
- Modify: `midi_simulator.py`（ファイル先頭の import、`__init__`）

- [ ] **Step 1: `import math` を追加**

ファイル先頭の `import time` の**直後**（現 9 行付近）に追記:

```python
import math
```

- [ ] **Step 2: デモ用の定数を追加**

`__init__` 内、Task 1 で追加したショルダー定数（`self.SHOULDER_UP_BTN = 5`）の**直後**に追記:

```python
        # デモモード設定（自動出力パターン）
        self.DEMO_STICK_PERIOD = 4.0    # スティック円運動の周期（秒）
        self.DEMO_STICK_INTERVAL = 0.05 # スティック送信間隔（秒）= 約20Hz
        self.DEMO_BUTTON_STEP = 0.4     # 点灯ボタンを次へ送る間隔（秒）
        self.DEMO_STATE_STEP = 0.4      # 状態 ±1 の間隔（秒）
```

- [ ] **Step 3: デモ用の内部状態フィールドを追加**

`__init__` 内、Task 2 で追加した `self.state_value = 0` の**直後**に追記:

```python
        # デモモード状態
        self.demo_mode = False
        self.demo_start_time = None
        self.demo_last_stick_send = 0.0
        self.demo_prev_button_idx = -1
        self.demo_prev_state_value = -1
```

- [ ] **Step 4: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 5: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(demo): デモモードの定数とフィールドを追加"
```

---

## Task 7: モード選択 UI `select_mode` を追加

**Files:**
- Modify: `midi_simulator.py`（`init_gamepad` の後あたり）

- [ ] **Step 1: `select_mode` メソッドを追加**

`convert_to_midi_value` メソッドの定義の**直前**（`init_gamepad` の最後の `return False` で終わる except ブロックの後）に追記:

```python
    def select_mode(self) -> bool:
        """通常/デモのモード選択。True=デモモード"""
        print("\n動作モード:")
        print("  1: 通常モード（ゲームパッド）")
        print("  2: デモモード（自動出力・ゲームパッド不要）")
        while True:
            try:
                choice = input("モードを選択してください (1-2): ").strip()
            except KeyboardInterrupt:
                return False
            if choice == "1":
                return False
            if choice == "2":
                return True
            print("1 または 2 を入力してください。")
```

- [ ] **Step 2: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 3: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(demo): モード選択UI select_mode を追加"
```

---

## Task 8: `run()` にモード分岐を追加

**Files:**
- Modify: `midi_simulator.py`（`run` 全体）

- [ ] **Step 1: `run` を次に置き換え**

既存の `run` メソッド全体（`def run(self):` から `finally: self.cleanup()` まで）を、次に**置き換え**:

```python
    def run(self):
        """メインループ"""
        print("\n=== モード選択 ===")
        self.demo_mode = self.select_mode()

        print("\n=== デバイス選択 ===")
        if not self.init_midi():
            print("MIDI出力デバイスの初期化に失敗しました")
            return False

        if not self.init_midi_input():
            print("MIDI入力デバイスの初期化に失敗しました")
            return False

        if not self.demo_mode:
            if not self.init_gamepad():
                print("ゲームパッドの初期化に失敗しました")
                return False

        print("\n動作開始 (Ctrl+Cで終了)")
        if self.demo_mode:
            print("デモモード: ゲームパッド不要で自動的にMIDI CCを送信します")
        else:
            print("スティック/ボタン/ショルダーを操作してMIDI CCを送信してください")
        if self.midi_in:
            print("MIDI入力データも受信・表示します")
        print("-" * 50)

        try:
            while self.running:
                if self.demo_mode:
                    self._process_demo()
                else:
                    self.process_input()
                time.sleep(0.01)  # 100Hz更新

        except KeyboardInterrupt:
            print("\n終了します...")
        finally:
            self.cleanup()
```

- [ ] **Step 2: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 3: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(demo): run() にモード分岐を追加"
```

---

## Task 9: `_process_demo` と各デモ生成メソッドを追加

**Files:**
- Modify: `midi_simulator.py`（`_process_state` の後）

- [ ] **Step 1: デモ生成メソッド群を追加**

`_process_state` メソッドの**直後**に追記:

```python
    def _process_demo(self):
        """デモモード: 経過時間から全CCを規則的パターンで生成・送信"""
        if self.demo_start_time is None:
            self.demo_start_time = time.time()
        t = time.time() - self.demo_start_time
        self._demo_sticks(t)
        self._demo_buttons(t)
        self._demo_state(t)

    def _demo_sticks(self, t):
        """スティックを円運動（左右で位相反転）。DEMO_STICK_INTERVAL ごとに送信"""
        if t - self.demo_last_stick_send < self.DEMO_STICK_INTERVAL:
            return
        self.demo_last_stick_send = t

        theta = 2 * math.pi * (t / self.DEMO_STICK_PERIOD)
        lx = self.convert_to_midi_value(math.sin(theta))
        ly = self.convert_to_midi_value(math.cos(theta))
        rx = self.convert_to_midi_value(math.sin(theta + math.pi))
        ry = self.convert_to_midi_value(math.cos(theta + math.pi))

        self.send_14bit_cc(self.CC_LEFT_X_LSB, self.CC_LEFT_X_MSB, lx)
        self.send_14bit_cc(self.CC_LEFT_Y_LSB, self.CC_LEFT_Y_MSB, ly)
        self.send_14bit_cc(self.CC_RIGHT_X_LSB, self.CC_RIGHT_X_MSB, rx)
        self.send_14bit_cc(self.CC_RIGHT_Y_LSB, self.CC_RIGHT_Y_MSB, ry)

    def _demo_buttons(self, t):
        """ボタンを順次点灯（常に1個ON）。変化時のみ送信"""
        idx = int(t / self.DEMO_BUTTON_STEP) % self.BUTTON_COUNT
        if idx == self.demo_prev_button_idx:
            return
        if self.demo_prev_button_idx >= 0:
            self.send_cc(self.CC_BUTTON_BASE + self.demo_prev_button_idx, 0)
        self.send_cc(self.CC_BUTTON_BASE + idx, 127)
        print(f"デモ ボタン{idx} ON")
        self.demo_prev_button_idx = idx

    def _demo_state(self, t):
        """状態を 0→16→0 で往復（三角波）。変化時のみ送信"""
        step = int(t / self.DEMO_STATE_STEP)
        period = 2 * self.STATE_MAX
        phase = step % period
        value = phase if phase <= self.STATE_MAX else period - phase
        if value == self.demo_prev_state_value:
            return
        self.demo_prev_state_value = value
        self.state_value = value
        cc_value = round(value / self.STATE_MAX * 127)
        self.send_cc(self.CC_STATE, cc_value)
        print(f"デモ 状態: {value}/{self.STATE_MAX} (CC#30={cc_value})")
```

- [ ] **Step 2: 構文確認**

Run: `python -m py_compile midi_simulator.py`
Expected: エラー出力なし

- [ ] **Step 3: 手動動作確認**

`python midi_simulator.py` → `2`（デモモード）→ MIDI 出力ポート選択（ゲームパッドは選択不要）。
- 受信側（`test_midi_debug.py` または Unity HUD）で確認:
  - スティック（CC#16–19/48–51）が円運動（左右が位相反転）。
  - ボタン（CC#20–29）が順番に 1 個ずつ点灯。
  - 状態（CC#30）が 0→16→0 を往復。
- `Ctrl+C` で正常終了すること。

- [ ] **Step 4: コミット**

```bash
git add midi_simulator.py
git commit -m "feat(demo): _process_demo(円運動/順次点灯/状態往復)を追加"
```

### ✅ Checkpoint B

デモモードがゲームパッド無しで全 CC を自動送信。通常モードも従来どおり動作。

---

# Phase C — ドキュメント

## Task 10: CLAUDE.md に MIDI マッピングとデモモードを追記

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: MIDI Mapping 節にボタン/状態を追記**

`CLAUDE.md` の「### MIDI Mapping」節（スティックの CC 記述がある箇所）に、ボタンと状態の行を追記:

```markdown
- Buttons: gamepad button i → CC#(20+i), i=0..9 (127=press / 0=release, threshold 64 on receiver)
- State selector: shoulder buttons (LB/RB) increment/decrement 0..16 → CC#30 (scaled to 0-127)
```

- [ ] **Step 2: デモモードの記述を追記**

`CLAUDE.md` の「## User Interface Features」節に追記:

```markdown
- **Mode Selection**: At startup choose Normal mode (gamepad) or Demo mode. Demo mode needs no gamepad and continuously sends sticks (circular motion), buttons (sequential), and state (0↔16 sweep) for receiver-side testing.
```

- [ ] **Step 3: コミット**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md にボタン/状態送信とデモモードを追記"
```

---

## 完了の定義（Definition of Done）

- [ ] 通常モードで、ボタン（CC#20–29）と状態（CC#30・ショルダー増減）が送信される。
- [ ] 既存のスティック送信（CC#16–19/48–51）が回帰していない。
- [ ] 起動時に通常/デモを選択でき、デモモードはゲームパッド無しで全 CC を規則的パターンで自動送信する。
- [ ] デモ・通常とも `Ctrl+C` で正常終了する。
- [ ] `python -m py_compile midi_simulator.py` がエラー無く通る。
- [ ] CLAUDE.md がボタン/状態/デモモードを反映している。
