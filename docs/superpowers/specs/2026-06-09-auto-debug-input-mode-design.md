# 自動デバッグ入力モードの追加 ＋ Stick/Slider モード撤廃 設計書

- **日付**: 2026-06-09
- **ステータス**: ブレスト完了 / ユーザーレビュー待ち（→ writing-plans）
- **対象**: `midi_simulator.py`（新仕様・コントローラ役シミュレータ）、新規 `auto_sequencer.py`、`keyboard_map.py`、`cc_map.py`、`tests/`
- **関連 / 後継関係**:
  - [2026-06-09-controller-sim-new-midi-spec-design.md](2026-06-09-controller-sim-new-midi-spec-design.md)（現行アーキテクチャの母体）
  - [2026-06-05-demo-mode-design.md](2026-06-05-demo-mode-design.md) を **supersede（後継）**。旧設計は「ゲームパッド → MIDI 送信ツール」向けで `init_gamepad` / `process_input` / `convert_to_midi_value` / `CC_STATE=CC#30` 等を前提とし、現コード（新仕様・コントローラ役）には存在せず**未実装のまま陳腐化**している。本設計はその目的（手動入力なしで送信系を自動確認）を新仕様で再実現する。

---

## 1. 背景と目的

本シミュレータは MIDI コントローラ役として、キーボードからスティック / ボタン / Preset / Error / State / イベントを送信する。検証時に**人手でキーを操作し続ける必要があり**、受信側（Unity）の送信受け取りを通しで確認しづらい。

本変更で **自動デバッグ入力モード** を追加する。手動キー操作なしに、送信系の全 CC を**既知の巡回シーケンス**で自動的に流し続け、Unity 側の受信マッピングを目視・ログで一括確認できるようにする。

あわせて、実用価値の薄い **Stick/Slider モード**（送信バイト列に影響せず、原点と表示正規化のみを変える）を撤廃して操作系を単純化し、空いた `M` キーを自動モードのトグルへ転用する。

## 2. スコープ

### 2.1 やること
1. 新規純粋ロジック `auto_sequencer.py`（`AutoSequencer`）を追加。MIDI/pygame 非依存で巡回シーケンスを生成（pytest 対象）。
2. `midi_simulator.py` のメインループに自動モード分岐を追加。`M` キーで ON/OFF トグル。
3. 自動モード中は手動入力（軸 / ボタン / Preset / Error / State / イベント各キー）を無視。`M`・`/`（ヘルプ）・`ESC`（終了）のみ有効。
4. Stick/Slider モードを撤廃（起動時選択 UI・`M` 切替・`MODE_ORIGIN`・`_mode` 分岐・`norm_slider`）。軸の基準を中心点 `8192` 固定に統一。
5. 「原点へ戻す / 原点リセット」表現を **「中心点へ移動」** に統一（`AXIS_CENTER` / `_center_axes`）。`0` キーの全軸移動機能は維持。
6. テスト・ドキュメント追従（`tests/`・`INSTRUCTIONS.md`・旧設計書の注記）。

### 2.2 やらないこと（Non-goals / YAGNI）
- 自動パターンの設定可能化（GUI / 設定ファイル）。定数で十分。
- 案B（並行連続変化型）・案C（受信モニタ強化）。受信生 MIDI 表示は既存 `test_midi_debug.py` が担う。
- 自動 E2E テスト基盤の導入（手動検証を踏襲。純粋ロジックのみ自動テスト）。
- 受信側（コマンド受信 → ACK、イベント応答）の挙動変更。自動モードと独立に従来どおり動く。

## 3. 設計判断サマリ（ブレストの結論）

| 論点 | 決定 |
|------|------|
| モードの目的 | 手動キー操作なしで送信系（全 CC）を自動確認するデバッグ用モード |
| 動かし方 | **巡回シーケンス型**：スティック軸スイープ → ボタン順次 ON/OFF → Preset/Error/State スイープ → イベント送信 → ループ |
| トグル | `M` キー（Stick/Slider 切替の跡地） |
| 自動中の手動入力 | 無視（`M`・`/`・`ESC` のみ有効） |
| Stick/Slider モード | **完全撤廃**。軸基準は中心点 `8192` 固定、表示は `norm14_bipolar` 一本化 |
| 「原点へ戻す」表現 | **「中心点へ移動」** に統一（`AXIS_CENTER` / `_center_axes`） |
| `norm_slider` | 削除（テスト・ドキュメント追従） |
| 純粋ロジック分離 | `auto_sequencer.py` の `AutoSequencer`（MIDI/pygame 非依存・pytest 対象） |
| 検証 | `AutoSequencer` のユニットテスト ＋ 手動（受信側で巡回を目視） |

## 4. アーキテクチャ — 純粋ロジック分離

INSTRUCTIONS.md の方針「純粋ロジックを MIDI/pygame から分離してユニットテスト可能にする」に従い、シーケンス生成を `AutoSequencer` に閉じ込める。`midi_simulator.py` は返ったアクションを送信するだけの薄い層に保つ。

```
AutoSequencer.tick(event_pending) ──▶ list[SendAction]
        │  （MIDI/pygame 非依存・状態機械）
        ▼
ControllerSimulator._dispatch_auto_action(action)
        │  axis  → self._midi.send_14bit(...)
        │  button→ self._midi.send_cc(BUTTON_CCS[i], 0/127)
        │  scalar→ self._midi.send_cc(PRESET/ERROR/STATE_CC, v)
        │  event → self._messaging.send_event(opcode, arg)
        └  action.log があれば print（間引きは AutoSequencer 側で制御）
```

## 5. `auto_sequencer.py`（新規・純粋ロジック）

### 5.1 アクション型

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class ActionKind(Enum):
    AXIS = "axis"       # target=軸index(0-3),   value=14bit raw
    BUTTON = "button"   # target=ボタンindex(0-9), value=127|0
    SCALAR = "scalar"   # target=CC番号,          value=0-127
    EVENT = "event"     # target=opcode,          value=arg(0-127)

@dataclass(frozen=True)
class SendAction:
    kind: ActionKind
    target: int
    value: int
    log: Optional[str] = None   # None の Tick は HUD に出さない（ランプ中間など）
```

### 5.2 フェーズと内部状態

```python
class Phase(Enum):
    STICK = 0       # 4軸を順にスイープ
    BUTTON = 1      # 0→9 を順次 ON/OFF
    SCALAR = 2      # Preset→Error→State を 0→127 スイープ
    EVENT = 3       # HeartBeat→ButtonCombo→SensorTrigger 送信（応答待ち）
```

保持する状態（すべて `tick` で決定的に更新）:
- `phase: Phase`
- スティック: `axis_index(0-3)`, `axis_value(int)`, `axis_leg`（`TO_MAX`/`TO_MIN`/`TO_CENTER`）
- ボタン: `button_index(0-9)`, `button_on(bool)`, `hold_counter(int)`
- スカラー: `scalar_index(0-2)`, `scalar_value(int)`（対象 CC は `[PRESET_CC, ERROR_CC, STATE_CC]`）
- イベント: `event_index(0-2)`, `event_sent(bool)`, `event_arg(int)`（送信ごとに `+1 & 0x7F`）

イベント opcode 列: `[EVT_HEARTBEAT, EVT_BUTTON_COMBO, EVT_SENSOR_TRIGGER]`。

### 5.3 `tick(event_pending: bool) -> list[SendAction]` の契約

1 Tick で「1 アクション」または「待機（空リスト）」を返す。各フェーズの規則:

- **STICK**: 現在軸を中心点 `8192` 起点に `8192 →(TO_MAX)→ 16383 →(TO_MIN)→ 0 →(TO_CENTER)→ 8192` と `AUTO_STICK_STEP` ずつ往復。leg 端点（16383 / 0 / 8192）に到達した Tick で `log` を付与（中間は `log=None`）。中心点復帰で次の軸へ。`axis_index==4` で `BUTTON` へ遷移（軸状態リセット）。他軸は触らない（モード開始時に全軸 8192 送信済み）。
- **BUTTON**: `button_on=False` の Tick で当該ボタンを ON（`value=127`, log 付）。以降 `hold_counter` を `AUTO_BUTTON_HOLD_TICKS` まで数え、到達 Tick で OFF（`value=0`, log 付）し次のボタンへ。`button_index==10` で `SCALAR` へ。常に 1 個ずつ点灯。
- **SCALAR**: 現在スカラー（Preset→Error→State）を `0→127` まで `AUTO_CC_STEP` 刻みで送信（`min(value,127)`、端点で log）。`127` 到達で次スカラーへ（`value=0` 再開）。`scalar_index==3` で `EVENT` へ。
- **EVENT**: `event_pending` を尊重する。
  - `not event_sent` → 当該イベントを送信（`EventAction`, log 付）。`event_sent=True`、`event_arg` を進める。
  - `event_sent and event_pending` → 待機（`[]`）。
  - `event_sent and not event_pending`（応答 or タイムアウトで解決）→ 次イベントへ。`event_index==3` で **1 サイクル完了**：`STICK` へ戻り全状態をサイクル初期化。

決定性：同じ `event_pending` 入力列に対し同じアクション列を返す（`time`/`random` 非使用）→ ユニットテスト可能。

## 6. `midi_simulator.py` 統合

### 6.1 メインループ分岐

```python
def _loop(self):
    while self._running:
        events = pygame.event.get()
        pressed = pygame.key.get_pressed()
        with self._lock:
            for event in events:
                self._apply_event(event)
            if self._auto_mode:
                self._tick_auto()          # 自動シーケンス
            else:
                self._ramp_axes(pressed)   # 手動ランプ
            self._messaging.tick()
            snapshot = self._messaging.snapshot()
        if self._help_requested:
            print(keyboard_map.help_text()); self._help_requested = False
        self._log_incoming_changes(snapshot)
        time.sleep(TICK_INTERVAL)
```

```python
def _tick_auto(self):
    event_pending = self._messaging.snapshot().event_pending
    for action in self._auto.tick(event_pending):
        self._dispatch_auto_action(action)

def _dispatch_auto_action(self, action):
    if action.kind is ActionKind.AXIS:
        msb_cc, lsb_cc = cc_map.CC_AXES[action.target]
        self._midi.send_14bit(msb_cc, lsb_cc, action.value)
    elif action.kind is ActionKind.BUTTON:
        self._buttons[action.target] = action.value > 0
        self._midi.send_cc(cc_map.BUTTON_CCS[action.target], action.value)
    elif action.kind is ActionKind.SCALAR:
        self._sync_scalar(action.target, action.value)   # self._preset/_error/_state を更新
        self._midi.send_cc(action.target, action.value)
    elif action.kind is ActionKind.EVENT:
        self._messaging.send_event(action.target, action.value)
    if action.log:
        print(f"[AUTO] {action.log}")
```

### 6.2 トグルと手動無視

- `M` キー（KEYDOWN）→ `_toggle_auto_mode()`:
  - 反転。ON 時は `self._auto = AutoSequencer()` で状態初期化し、`_center_axes()` ＋ 全ボタン OFF を送ってクリーンな起点にする。OFF 時も `_center_axes()` ＋ 全ボタン OFF（進行中の点灯を残さない）。
  - ログ：`自動デバッグ入力: ON / OFF`。
- `_on_keydown` / `_on_keyup` の冒頭で「自動モード中、かつキーが `M`/`/`/`ESC` 以外」なら早期 return（手動入力を無視）。

### 6.3 受信との独立性

受信コマンド（Unity → Sim）は別スレッドで `messaging` が ACK を返す既存経路のまま。自動モード中も受信 → 即 ACK は動作する。自動イベント送信は `event_pending` を見て待つため、受信とクロスしてもデッドロックしない。

## 7. Stick/Slider 撤廃と「中心点へ移動」

- 削除: `_select_mode`、`_toggle_mode`、`MODE_ORIGIN`、`self._mode` 関連分岐、`cc_map.norm_slider`。
- 軸基準: `AXIS_CENTER = cc_map.CENTER_14BIT`（= 8192）に固定。初期値・`0` キー・モードトグル時の移動先はすべて `AXIS_CENTER`。
- 旧 `_reset_axes` → `_center_axes` に改名。ログは「全軸を中心点(8192)へ移動」。
- 表示正規化は `_log_axis` で常に `cc_map.norm14_bipolar` を使用（`_mode` 分岐を撤去）。
- `keyboard_map.py`: `TOGGLE_MODE_KEY`（`K_m`）の意味を「自動デバッグ入力モード トグル」に再定義。`help_text()` を更新（モード切替行を「`M`=自動デバッグ入力 ON/OFF」、`0` 行を「中心点へ移動」に）。
- 起動シーケンス `run()` から `_select_mode()` 呼び出しを削除。

## 8. 定数（マジックナンバー回避・`midi_simulator.py` 上部に集約）

| 定数 | 既定値 | 意味 |
|------|:---:|------|
| `AXIS_CENTER` | `cc_map.CENTER_14BIT`(8192) | 軸の中心点（初期 / `0` キー / モード遷移の移動先） |
| `AUTO_STICK_STEP` | `550` | スティックスイープの 1 Tick あたり 14bit 変化量（既存ランプ速度と同等） |
| `AUTO_BUTTON_HOLD_TICKS` | `15` | 各ボタンの ON 保持 Tick 数（≒0.25s @60fps） |
| `AUTO_CC_STEP` | `8` | Preset/Error/State スイープの刻み（0→127 を約 16 段） |

（イベントは `event_pending` 駆動のため間隔定数は不要。MIDI 入力無し時は `RESPONSE_TIMEOUT_TICKS=30`≒0.5s のタイムアウトで次へ進む。）参考：1 サイクルは入力無し時で約 9 秒。

## 9. エラー処理・エッジケース

- **MIDI 入力なし（送信のみ）**: イベントフェーズで応答が来ないが、30 Tick のタイムアウトで `event_pending=False` に戻り次イベントへ。デッドロックしない。
- **モード OFF 時の残留**: 進行中のボタン ON / 非中心の軸を、`_toggle_auto_mode` の全ボタン OFF ＋ `_center_axes` で必ず解消する。
- **`event_pending` 競合**: `tick` は `event_pending=False` を確認してからイベントを発行するため、`send_event` が抑止（`False`）される状況は通常起きない。万一 `False` でも次 Tick の再評価で吸収（lock 内で直列化）。
- **`ESC` / `Ctrl+C`**: 既存どおり安全終了（`_cleanup`）。

## 10. テスト計画

新規 `tests/test_auto_sequencer.py`（純粋ロジック）:
- スティック: 各軸が `8192→16383→0→8192` を辿る／4 軸を順に処理／他軸は中心維持／端点で `log` 付与。
- ボタン: `0→9` を順に ON→（`AUTO_BUTTON_HOLD_TICKS` 保持）→OFF、常に 1 個。
- スカラー: Preset→Error→State を正しい CC（40/41/42）で `0→127` スイープ。
- イベント: `event_pending=True` の間は空アクション、`False` で次イベント、3 種を順次、`event_arg` 増加。
- サイクル: EVENT 完了後に STICK へループし状態が初期化される。
- 決定性: 同一入力列で同一出力列。

既存テスト追従:
- `tests/test_cc_map.py`: `norm_slider` 関連テスト 3 件（`test_slider_zero`/`test_slider_max`/`test_slider_mid`）を削除。

カバレッジは `AutoSequencer` を中心に 80% 以上を維持。

## 11. 影響を受けるファイル

| ファイル | 変更 |
|----------|------|
| `auto_sequencer.py` | **新規**（`AutoSequencer` / `SendAction` / `ActionKind` / `Phase`） |
| `tests/test_auto_sequencer.py` | **新規** |
| `midi_simulator.py` | モード分岐・トグル・ディスパッチ追加、Stick/Slider 撤廃、`_center_axes` 改名、定数追加 |
| `keyboard_map.py` | `TOGGLE_MODE_KEY` を自動トグルへ再定義、`help_text()` 更新 |
| `cc_map.py` | `norm_slider` 削除 |
| `tests/test_cc_map.py` | slider テスト 3 件削除 |
| `INSTRUCTIONS.md` | Stick/Slider 記述・`M`/`0` キー表・`norm_slider` 言及を更新、自動デバッグ入力モードを追記 |
| `docs/superpowers/specs/2026-06-05-demo-mode-design.md` | 冒頭に「本仕様は 2026-06-09 設計に supersede（旧仕様向け・未実装）」の注記 |

## 12. 旧設計との関係（要確認事項）

- `2026-06-05-demo-mode-design.md` と `docs/superpowers/plans/2026-06-05-midi-button-state-demo-send.md` は旧アーキテクチャ向け。本設計が目的を引き継ぐため、**冒頭に supersede 注記を付けて温存**する想定（履歴として残す）。削除を希望する場合はレビュー時に指示する。
