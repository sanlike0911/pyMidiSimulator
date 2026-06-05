# MIDI ボタン送信・状態送信の追加（pyMidiSimulator 送信側）設計書

- **日付**: 2026-06-05
- **ステータス**: 承認済み（実装計画 / writing-plans 待ち）
- **対象**: `midi_simulator.py`（pyMidiSimulator＝ゲームパッド → MIDI CC 送信ツール）
- **関連（CC 割当の取り決め元）**: 受信側 Unity プロジェクト `TestMIDIGameController` の全体設計書および機能設計書 `2026-06-04-midi-button-state-input-design.md`。本設計はそのアドレスマップ（CC#20–29 / CC#30）に**送信側を一致させる**もの。

---

## 1. 背景と目的

pyMidiSimulator は pygame でゲームパッドを読み、左右スティックのアナログ軸を 14bit MIDI CC（CC#16–19 / 48–51）に変換して python-rtmidi で送信する**送信側ツール**。受信側（Unity `MIDIInputProvider.cs`）は既にボタン（CC#20–29）と状態入力（CC#30）に対応済みだが、**送信側はスティックしか送っていない**（`process_input` にボタン・状態の送信処理が無い）。

本変更で送信側にボタン送信・状態送信を追加し、受信側と CC 割当を一致させる。

## 2. スコープ

### 2.1 やること
1. ゲームパッドのボタン（`get_button(0..9)`）→ **CC#20–29** 送信（押下 127 / 離上 0、変化時のみ）。
2. ショルダーボタン（LB/RB）での状態増減 → **CC#30** 送信（内部 0–16 を 0–127 にスケール、変化時のみ）。
3. `process_input` を `_process_sticks` / `_process_buttons` / `_process_state` に分割し、7bit CC 送信用 `send_cc` を共通化（アプローチ B）。

### 2.2 やらないこと（Non-goals）
- スティック処理（CC#16–19 / 48–51）の挙動変更（メソッドへ移動するのみ・挙動不変）。
- ボタン/CC 割当の設定 UI 化・設定ファイル化（定数で十分。YAGNI）。
- 状態入力の複数系統化（当面 1 系統）。
- 自動テスト基盤（pytest 等）の新規導入（プロジェクト方針＝手動検証を踏襲。受信側 Unity も手動検証方針）。

## 3. 設計判断のサマリ（ブレストの結論）

| 論点 | 決定 |
|------|------|
| 対象 | **Python 送信側**（`midi_simulator.py`）。受信側 Unity は実装済み |
| 範囲 | ボタン（CC#20–29）＋状態（CC#30）の両方 |
| 状態の入力ソース | **ショルダーボタン（LB/RB）で増減**（離散セレクタ。Unity 側キーシミュレーション `[`/`]` と同方式） |
| コード構造 | **案 B**：`process_input` をメソッド分割＋`send_cc` 共通化 |
| ショルダーの二重送信 | ショルダーは **CC#24/25 のボタンとしても送信**し、状態増減も兼ねる（提示マップ通り全 10 ボタン送信） |
| 検証 | 手動（実機ゲームパッド＋受信側目視）。pytest 追加なし |

## 4. アーキテクチャ（アプローチ B：メソッド分割）

`GamepadMidiController.process_input()` を分割し、各入力系統を独立メソッドに切り出す。`process_input` は順に呼ぶだけの薄い窓口にする。

```python
def process_input(self):
    pygame.event.pump()
    self._process_sticks()              # 既存スティック処理（移動のみ・挙動不変）
    current = self._read_buttons()      # list[bool]、joystick 無→[False]*BUTTON_COUNT
    self._process_buttons(current)      # prev_buttons と比較し変化分のみ CC#20-29 送信
    self._process_state(current)        # prev_buttons と比較しショルダーのエッジで状態増減→CC#30
    self.prev_buttons = current         # 末尾で前フレーム状態を一括更新
```

- `_process_buttons` と `_process_state` はいずれも **更新前の** `self.prev_buttons`（前フレーム値）を参照し、`process_input` 末尾で `self.prev_buttons` を一括更新する。これによりボタン変化検出とショルダーのエッジ検出が同じ前フレーム基準で整合する。
- 既存の `send_14bit_cc`（[midi_simulator.py:283]）はスティック専用のまま残す。ボタン/状態は新設の `send_cc`（7bit 単発）を使う。

## 5. CC 割当・定数（`__init__` に追加）

| 項目 | 定数名 | 値 | 備考 |
|------|--------|----|------|
| ボタン基準 CC | `CC_BUTTON_BASE` | 20 | ボタン i → CC#(20+i) |
| ボタン数 | `BUTTON_COUNT` | 10 | CC#20–29 |
| ボタン ON/OFF 送信値 | （リテラル） | 127 / 0 | 受信側しきい値 64 で判定 |
| 状態 CC | `CC_STATE` | 30 | |
| 状態最大段階 | `STATE_MAX` | 16 | 0..16 の 17 段階 |
| ショルダー（−1） | `SHOULDER_DOWN_BTN` | 4 | LB（XInput 一般値）。**機種依存のためコメントで明記** |
| ショルダー（+1） | `SHOULDER_UP_BTN` | 5 | RB（XInput 一般値）。同上 |

`__init__` に追加する内部状態：

```python
self.prev_buttons = [False] * self.BUTTON_COUNT  # 前フレームのボタン状態（変化検出用）
self.state_value = 0                             # 状態セレクタ値（0..STATE_MAX）
```

## 6. ボタン送信ロジック（`_read_buttons` / `_process_buttons`）

```python
def _read_buttons(self):
    """現在のボタン状態を BUTTON_COUNT 個ぶん取得（未接続/不足は False）"""
    if not self.joystick:
        return [False] * self.BUTTON_COUNT
    n = self.joystick.get_numbuttons()
    return [self.joystick.get_button(i) if i < n else False
            for i in range(self.BUTTON_COUNT)]

def _process_buttons(self, current):
    """変化したボタンだけ CC#20-29 を送信（押下127 / 離上0）"""
    for i in range(self.BUTTON_COUNT):
        if current[i] != self.prev_buttons[i]:
            self.send_cc(self.CC_BUTTON_BASE + i, 127 if current[i] else 0)
            print(f"ボタン{i}: {'ON' if current[i] else 'OFF'}")
```

- ショルダー（ボタン4/5）も `current` に含まれるため、ここで CC#24/25 として送信される（二重送信）。

## 7. 状態送信ロジック（`_process_state`）

```python
def _process_state(self, current):
    """ショルダーの押下エッジで状態を増減し、変化時に CC#30 を送信"""
    down_edge = current[self.SHOULDER_DOWN_BTN] and not self.prev_buttons[self.SHOULDER_DOWN_BTN]
    up_edge   = current[self.SHOULDER_UP_BTN]   and not self.prev_buttons[self.SHOULDER_UP_BTN]

    new_state = self.state_value
    if up_edge:
        new_state = min(self.STATE_MAX, new_state + 1)
    if down_edge:
        new_state = max(0, new_state - 1)

    if new_state != self.state_value:
        self.state_value = new_state
        cc_value = round(new_state / self.STATE_MAX * 127)   # 0..16 → 0..127
        self.send_cc(self.CC_STATE, cc_value)
        print(f"状態: {new_state}/{self.STATE_MAX} (CC#30={cc_value})")
```

- `SHOULDER_DOWN_BTN` / `SHOULDER_UP_BTN` が `BUTTON_COUNT` 範囲内である前提（4/5 < 10）。`_read_buttons` が範囲外を False で埋めるため、ショルダー非搭載パッドではエッジが立たず安全にスキップされる。

### 値変換の往復整合
送信側 `round(state/16*127)` と受信側 `round(value/127*16)` は対称：

| 状態 (送信側) | 送信 CC 値 | 受信側 復元 |
|:---:|:---:|:---:|
| 0 | 0 | 0 |
| 8 | 64 | 8 |
| 16 | 127 | 16 |

中間値は丸めにより最大 ±1 段ズレうるが、状態セレクタとして実用上問題ない。送信側の `state_value` が真値。

## 8. `send_cc`（7bit CC 単発送信・共通）

```python
def send_cc(self, cc: int, value: int) -> None:
    """7bit CC を 1 メッセージ送信（ボタン/状態で共用）"""
    if not self.midi_out:
        return
    self.midi_out.send_message([0xB0, cc, value & 0x7F])
    print(f"    MIDI: CC#{cc}={value & 0x7F}")
```

- チャンネルは既存実装と同じ `0xB0`（ch.1 固定）。受信側 Unity は全チャンネル受理のため問題なし。

## 9. エラー処理・エッジケース

- **ゲームパッド未接続**：`_read_buttons` が `[False]*BUTTON_COUNT` を返し、変化が無いので何も送らない。
- **ボタン数 < 10**：存在しないボタンは False 固定（範囲ガード）。
- **ショルダー番号が範囲外/非搭載**：エッジが立たず状態増減はスキップ。
- **`midi_out` 未接続**：`send_cc` 冒頭で return（既存 `send_14bit_cc` と同じガード方針）。

## 10. 検証・テスト方針

自動テスト基盤が無い（`requirements.txt` は pygame / python-rtmidi のみ、`test_midi_debug.py` は受信目視用の手動スクリプト）ため、**手動検証**を中心とする。

1. **回帰**：起動後スティックを動かし、CC#16–19 / 48–51 が従来どおり送信されること（挙動不変）。
2. **ボタン**：各ボタン押下/離上で対応する CC#20–29 が 127/0 で送信されること（変化時のみ）。
3. **状態**：RB 押下で状態 +1（CC#30 増）、LB 押下で −1、0–16 でクランプされること。
4. **受信側突き合わせ**：`test_midi_debug.py` または Unity 受信側 HUD（10 ボタンランプ・状態値）で受信を確認。
5. **二重送信の確認**：ショルダー押下時に CC#24/25（ボタン）と CC#30（状態）の双方が出ること。

（任意）将来 pytest を導入する場合は、値変換（状態 0–16 ↔ CC 0–127）を純粋関数に切り出して単体テスト可能。今回は実施しない。

## 11. ドキュメント更新

- プロジェクトの `CLAUDE.md` の「MIDI Mapping」節に、ボタン（CC#20–29）と状態入力（CC#30）の記述を追記する（現状はスティックのみ記載）。

## 12. リスクと留意点

- **ショルダーのボタン番号は機種依存**（XInput 想定で 4/5）。定数化し、環境に応じて変更可能とする旨をコメントで明記。
- **`prev_buttons` の更新順序**：`_process_buttons` と `_process_state` が前フレーム値を参照し、`process_input` 末尾で一括更新する設計を崩さないこと。
- **ボタンの二重送信**（ショルダー）：受信側でボタン4/5 が現状未使用のため無害。将来ボタン4/5 にゲームアクションを割り当てる場合は、状態セレクタと同時発火する点に留意。
- **送信レート**：ボタン/状態とも変化時のみ送信のため、100Hz ループでも MIDI トラフィックは増えない。
