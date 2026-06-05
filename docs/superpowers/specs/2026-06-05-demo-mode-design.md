# デモモード（自動出力）の追加（pyMidiSimulator 送信側）設計書

- **日付**: 2026-06-05
- **ステータス**: 承認済み（実装計画 / writing-plans 待ち）
- **対象**: `midi_simulator.py`（pyMidiSimulator＝ゲームパッド → MIDI CC 送信ツール）
- **関連**: 同日の [2026-06-05-midi-button-state-send-design.md](2026-06-05-midi-button-state-send-design.md)（ボタン/状態送信）。本設計はその送信パス（`send_14bit_cc` / `send_cc`）を再利用する。実装計画は両設計を統合して 1 本にまとめる。

---

## 1. 背景と目的

pyMidiSimulator はゲームパッドを MIDI CC に変換して送信するツールだが、検証時に**実機ゲームパッドが必要**で、受信側（Unity）単体の動作確認がしづらい。

本変更で、起動時に選べる**デモモード**を追加する。デモモードはゲームパッドを使わず、経過時間に基づいて全 CC（スティック CC#16–19/48–51、ボタン CC#20–29、状態 CC#30）を**規則的なパターンで定期的に変化**させ送信する。受信側をゲームパッド無しで動作確認・デモできるようにする。

## 2. スコープ

### 2.1 やること
1. 起動時に「通常モード / デモモード」を選択する対話 UI を追加。
2. デモモードでは `init_gamepad()` をスキップし（MIDI 出力ポート選択は実施）、メインループで `_process_demo()` を呼ぶ。
3. `_process_demo()` が経過時間から全 CC を規則的パターンで生成し、**既存の `send_14bit_cc` / `send_cc` で送信**する。

### 2.2 やらないこと（Non-goals）
- 通常モード（ゲームパッド入力）の挙動変更。
- デモパターンの設定 UI 化・設定ファイル化（定数で十分。YAGNI）。
- デモ中のインタラクティブな操作（一時停止・パターン切替など）。`Ctrl+C` 終了のみ。
- 自動テスト基盤の導入（手動検証を踏襲）。

## 3. 設計判断のサマリ（ブレストの結論）

| 論点 | 決定 |
|------|------|
| 出力対象 | 全部（スティック＋ボタン＋状態） |
| 変化方式 | **規則的パターン**（スティック=円運動、ボタン=順次点灯、状態=0↔16 往復） |
| コード構造 | **案 A**：`run()` でモード分岐＋`_process_demo()`。既存送信パス再利用 |
| 選択方法 | 起動時の対話メニュー（`1`:通常 / `2`:デモ） |
| 検証 | 手動（受信側で円運動・順次点灯・往復を目視） |

## 4. アーキテクチャ（案 A：`run()` 分岐＋`_process_demo()`）

```python
def run(self):
    print("\n=== モード選択 ===")
    self.demo_mode = self.select_mode()        # True=デモ

    if not self.init_midi():                   # 出力ポートは両モードで必要
        print("MIDI出力デバイスの初期化に失敗しました"); return False
    if not self.init_midi_input():
        print("MIDI入力デバイスの初期化に失敗しました"); return False
    if not self.demo_mode:                      # デモ時はゲームパッド不要
        if not self.init_gamepad():
            print("ゲームパッドの初期化に失敗しました"); return False

    # 開始メッセージ（モードに応じて分岐）...
    try:
        while self.running:
            if self.demo_mode:
                self._process_demo()
            else:
                self.process_input()
            time.sleep(0.01)                    # 既存どおり 100Hz
    except KeyboardInterrupt:
        print("\n終了します...")
    finally:
        self.cleanup()
```

- デモモードでは `self.joystick` を一切触らないため、ゲームパッド未接続でもエラーにならない。
- 送信は既存 `send_14bit_cc`（スティック）/ `send_cc`（ボタン・状態）を再利用する。

## 5. 起動時の選択 UI（`select_mode`）

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

既存の `select_midi_port` 等と同じ対話スタイル。`run()` の冒頭（MIDI ポート選択より前）で呼ぶ。

## 6. `__init__` に追加する状態

```python
# デモモード
self.demo_mode = False
self.demo_start_time = None          # 初回 _process_demo で time.time() を記録
self.demo_last_stick_send = 0.0      # スティック送信の間引き用（前回送信からの経過）
self.demo_prev_button_idx = -1       # 直前に点灯していたボタン番号（-1=なし）
self.demo_state_value = 0            # デモ用の状態セレクタ値（0..STATE_MAX）
self.demo_prev_state_step = -1       # 直前の状態ステップ（変化検出用）
```

## 7. `_process_demo()` の規則的パターン

経過時間 `t = time.time() - self.demo_start_time` を基準に各出力を生成する。デモパターン用の定数（クラス定数 or `__init__`）:

| 対象 | 定数 | 既定値 | 意味 |
|------|------|:---:|------|
| スティック1周 | `DEMO_STICK_PERIOD` | 4.0 秒 | 円運動の周期 |
| スティック送信間隔 | `DEMO_STICK_INTERVAL` | 0.05 秒 | 100Hz ループでも 20Hz に間引いて送信 |
| ボタン送り | `DEMO_BUTTON_STEP` | 0.4 秒 | 点灯ボタンを次へ送る間隔 |
| 状態1段 | `DEMO_STATE_STEP` | 0.4 秒 | 状態 ±1 の間隔 |

```python
def _process_demo(self):
    if self.demo_start_time is None:
        self.demo_start_time = time.time()
    t = time.time() - self.demo_start_time
    self._demo_sticks(t)
    self._demo_buttons(t)
    self._demo_state(t)
```

### 7.1 スティック（円運動・送信間引き）

```python
def _demo_sticks(self, t):
    # 100Hz ループでの過剰送信を避け DEMO_STICK_INTERVAL ごとに送る
    if t - self.demo_last_stick_send < self.DEMO_STICK_INTERVAL:
        return
    self.demo_last_stick_send = t

    import math
    theta = 2 * math.pi * (t / self.DEMO_STICK_PERIOD)
    left = (math.sin(theta), math.cos(theta))
    right = (math.sin(theta + math.pi), math.cos(theta + math.pi))  # 位相を π ずらす

    lx = self.convert_to_midi_value(left[0]); ly = self.convert_to_midi_value(left[1])
    rx = self.convert_to_midi_value(right[0]); ry = self.convert_to_midi_value(right[1])
    self.send_14bit_cc(self.CC_LEFT_X_LSB, self.CC_LEFT_X_MSB, lx)
    self.send_14bit_cc(self.CC_LEFT_Y_LSB, self.CC_LEFT_Y_MSB, ly)
    self.send_14bit_cc(self.CC_RIGHT_X_LSB, self.CC_RIGHT_X_MSB, rx)
    self.send_14bit_cc(self.CC_RIGHT_Y_LSB, self.CC_RIGHT_Y_MSB, ry)
```

- `convert_to_midi_value` はデッドゾーン 0.1 を含むため、中央付近では 8192 付近にスナップする（既存挙動のまま）。デモの円運動としては問題ない。

### 7.2 ボタン（順次点灯・変化時のみ送信）

```python
def _demo_buttons(self, t):
    idx = int(t / self.DEMO_BUTTON_STEP) % self.BUTTON_COUNT
    if idx == self.demo_prev_button_idx:
        return
    if self.demo_prev_button_idx >= 0:                       # 前のボタンを OFF
        self.send_cc(self.CC_BUTTON_BASE + self.demo_prev_button_idx, 0)
    self.send_cc(self.CC_BUTTON_BASE + idx, 127)             # 新しいボタンを ON
    self.demo_prev_button_idx = idx
```

常に 1 個だけ点灯し、`DEMO_BUTTON_STEP` ごとに次へ移る（受信側 HUD でランプが流れる）。

### 7.3 状態（0↔16 往復・三角波・変化時のみ送信）

```python
def _demo_state(self, t):
    step = int(t / self.DEMO_STATE_STEP)                     # 0,1,2,...
    period = 2 * self.STATE_MAX                              # 0..16..0 の全長（32ステップ）
    phase = step % period
    value = phase if phase <= self.STATE_MAX else period - phase   # 三角波 0..16..0
    if value == self.demo_prev_state_step:
        return
    self.demo_prev_state_step = value
    self.demo_state_value = value
    cc_value = round(value / self.STATE_MAX * 127)
    self.send_cc(self.CC_STATE, cc_value)
```

`DEMO_STATE_STEP=0.4` で 0→16→0 を約 12.8 秒周期で往復する。

## 8. エラー処理・エッジケース

- **`Ctrl+C`**: 既存の `KeyboardInterrupt` 捕捉 ＋ `cleanup()` で安全終了。`select_mode` 中の中断は通常モード扱い（`False`）で抜ける。
- **`midi_out` 未接続**: `send_14bit_cc` / `send_cc` がそれぞれ冒頭でガードし無送信。
- **デモモードでのゲームパッド**: 参照しないため未接続でもエラーなし。`cleanup()` の `self.joystick` 解放は `if self.joystick:` ガード済み（デモ時は `None` のまま）。

## 9. 検証（手動）

1. 起動 → `2`（デモモード）選択 → MIDI 出力ポート選択 → **ゲームパッド無しで** CC が周期送信されること。
2. 受信側（[test_midi_debug.py](test_midi_debug.py) または Unity HUD）で確認：
   - スティック：左右が円を描く（位相反転）。
   - ボタン：CC#20→29 が順番に 1 個ずつ点灯。
   - 状態：CC#30 が 0→16→0 を往復。
3. 通常モード（`1`）が従来どおりゲームパッドで動くこと（回帰）。
4. デモ中・通常中とも `Ctrl+C` で正常終了すること。

## 10. リスクと留意点

- **送信レート**: スティックは `DEMO_STICK_INTERVAL`（20Hz）に間引く。ボタン/状態は変化時のみ。100Hz ループでも MIDI トラフィックは過剰にならない。
- **`math` インポート**: ファイル先頭に `import math` を追加（関数内 import でも可だが先頭が望ましい）。
- **デモ用状態の分離**: デモの状態セレクタ（`demo_state_value`）は通常モードの `state_value` と排他運用（同時に動かない）だが、変数を分けて混線を防ぐ。
- **定数の所在**: デモ定数（`DEMO_*`）はクラス定数として配置し、ボタン/状態の定数（`CC_BUTTON_BASE` 等）と同じ並びにまとめる。
