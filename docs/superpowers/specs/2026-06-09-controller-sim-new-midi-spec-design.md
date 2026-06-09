# コントローラ役シミュレータ 新MIDI仕様対応 設計書

- 日付: 2026-06-09
- 対象仕様: `docs/specs/midi-mapping.md`（Unity `CkdGameController` 受信表・2026-06-09 版）
- 対象ファイル: `midi_simulator.py` を全面刷新（旧ゲームパッド/デモ/CC#30 仕様は廃止）

## 1. 目的と役割

ゲームパッド入力を MIDI に変換していた旧シミュレータを、新 MIDI 仕様に対応した
**コントローラ役（送信主体）シミュレータ**へ全面刷新する。実機 MIDI コントローラの代わりに、
キーボード操作でスティック/ボタン/Preset/Error/State を Unity（受信側）へ送信し、
Unity が送るコマンド（SetPreset 等）を受信して ACK を返し、イベントを送信して応答を受け取る。

確定した設計判断:

| 論点 | 決定 |
| --- | --- |
| 役割 | コントローラ役（送信主体） |
| 入力 | キーボード操作モード（pygame KEYDOWN/KEYUP） |
| ファイル | `midi_simulator.py` 全面刷新 + モジュール分割 |
| コマンド/イベント I/F | フル実装（seq・ACK・タイムアウト・応答待ち） |
| スティック | Stick / Slider 両モード切替 |
| キー入力 | pygame（押下/離上エッジ取得） |

## 2. 送受信 CC 方向（コントローラ役視点）

仕様書は Unity 視点のため、シミュレータでは送受信が反転する。

### 送信（Sim → Unity / 仕様表の「IN」）

| CC | 用途 | 値 |
| --- | --- | --- |
| 16/48・17/49・18/50・19/51 | 左X / 左Y / 右X / 右Y（MSB/LSB） | 14bit 0–16383 |
| 20–29 | ボタン 0–9 | 127=ON / 0=OFF |
| 40 | Preset | 0–127 生値 |
| 41 | Error | 0–127 生値 |
| 42 | State | 0–127 生値 |
| 43 | CMDRSP_STATUS（受信コマンドへの ACK） | status + seqEcho×64 |
| 44 / 45 | EVT_ARG / EVT_OP（イベント送信） | arg / op + seq×64 |

### 受信（Unity → Sim / 仕様表の「OUT」）

| CC | 用途 | 値 |
| --- | --- | --- |
| 50 / 53 | CMD_ARG1 / CMD_ARG2 | 0–127 |
| 51 | CMD_OP（commit） | op + seq×64 |
| 52 | EVTRSP_STATUS（イベント送信への ACK） | status + seqEcho×64 |

### CC 番号衝突と対策

送信側の右スティック X/Y LSB（CC50/51）と、受信側の CMD_ARG1/CMD_OP（CC50/51）が同番号。
MIDI は IN/OUT が独立エンドポイントなので**実機（物理 IN/OUT 分離）では無害**だが、
単一仮想ポート/ループバックでは自分が送った右スティック LSB が CMD として自プロセスへ誤注入される。

対策:
- **IN/OUT を別ポートに分離する前提**（出力ポートと入力ポートを別々に選択）。
- 出力ポート == 入力ポートが選ばれた場合は**警告を表示**。
- 送信側で使う MIDI 出力チャンネルと受信側チャンネルは共通定数 `MIDI_CHANNEL`（既定 0 = ch1）。

## 3. モジュール構成

```
midi_simulator.py   エントリ・メインループ・モード/ポート選択UI・状態オーケストレーション
cc_map.py           CC定数・正規化・14bit分割/再構成・seqコーデック・STATUS/OPCODE定数（純粋関数）
messaging.py        コマンド/イベントI/Fステートマシン（送信・受信・ACK・seq・タイムアウト）
midi_io.py          MIDI出力/入力ラッパー（ポート選択・CC送信・受信ディスパッチ）
keyboard_map.py     pygameキー → セマンティックアクションのマッピング + ヘルプテキスト
tests/
  test_cc_map.py
  test_messaging.py
pyproject.toml      pytest設定（testpaths=tests）
requirements.txt        実行依存（pygame, python-rtmidi）
requirements-dev.txt    開発依存（pytest, pytest-cov）
```

`cc_map.py` と `messaging.py` は MIDI/pygame に依存しない純粋ロジックとし、ユニットテストの中心にする。

### 3.1 cc_map.py インターフェース

```python
# CC 定数
CC_AXES = [(16, 48), (17, 49), (18, 50), (19, 51)]  # (MSB, LSB) 左X/左Y/右X/右Y
BUTTON_CCS = list(range(20, 30))                      # ボタン 0–9
PRESET_CC = 40
ERROR_CC = 41
STATE_CC = 42
CMDRSP_STATUS_CC = 43
EVT_ARG_CC = 44
EVT_OP_CC = 45
CMD_ARG1_CC = 50
CMD_ARG2_CC = 53
CMD_OP_CC = 51
EVTRSP_STATUS_CC = 52

CENTER_14BIT = 8192
MAX_14BIT = 16383
BUTTON_ON_THRESHOLD = 64
SEQ_BIT = 64           # bit6
PAYLOAD_MASK = 0x3F    # bit0–5

# STATUS
STATUS_OK, STATUS_UNKNOWN_OP, STATUS_INVALID_ARG, STATUS_REJECTED = 0, 1, 2, 3
# コマンド opcode（受信側で解釈）
CMD_PING, CMD_LED, CMD_HAPTIC, CMD_SET_PRESET = 0, 1, 2, 4
# イベント opcode（送信側）
EVT_HEARTBEAT, EVT_BUTTON_COMBO, EVT_SENSOR_TRIGGER = 0, 1, 2

def split_14bit(value: int) -> tuple[int, int]:      # -> (msb, lsb)
def combine_14bit(msb: int, lsb: int) -> int:
def norm14_bipolar(value: int) -> float:             # 中央8192基準 -1.0..+1.0（表示用）
def norm_slider(value: int) -> float:                # 0..1（表示用）
def pack_seq(payload: int, seq: int) -> int:         # payload + seq*64
def payload_of(value: int) -> int:                   # value & 0x3F
def seq_of(value: int) -> int:                       # (value>>6) & 1
```

### 3.2 messaging.py インターフェース

```python
class Messaging:
    def __init__(self, send_cc: Callable[[int, int], None]): ...
    # 受信ディスパッチ（midi_io から CC 受信のたびに呼ばれる）
    def handle_incoming_cc(self, cc: int, value: int) -> None: ...
    # イベント送信（キー操作から）
    def send_event(self, opcode: int, arg: int) -> bool: ...   # 保留中は False
    # 毎 Tick 呼ぶ（タイムアウト判定）
    def tick(self) -> None: ...
    # 表示用スナップショット
    def snapshot(self) -> MessagingState: ...
```

内部状態:
- 受信コマンドバッファ: `arg1`, `arg2`（OP commit 後にクリア）
- イベント保留: `pending = None | PendingEvent(opcode, seq, arg, ticks_waited)`
- イベント seq: `next_event_seq`（送信ごとに 0↔1 反転）
- 表示用: `last_command`, `received_preset`, `last_event_response`

依存は `send_cc` 関数のみ（DI）。`tick()` がクロック。MIDI/pygame 非依存でテスト可能。

## 4. コマンド/イベント I/F ステートマシン詳細

### 4.1 受信コマンド処理（Sim = コマンドの受信者）

1. `CMD_ARG1(50)` 受信 → `arg1` に保持
2. `CMD_ARG2(53)` 受信 → `arg2` に保持
3. `CMD_OP(51)` 受信 → commit。`opcode = payload_of(value)`, `seq = seq_of(value)`
   - `CMD_PING(0)` → `STATUS_OK`
   - `CMD_SET_PRESET(4)` → `arg1`(0–127) を `received_preset` に設定、`option=arg2`。範囲内 → `STATUS_OK`、範囲外 → `STATUS_INVALID_ARG`
   - `CMD_LED(1)` / `CMD_HAPTIC(2)` → 受領し `STATUS_OK`（物理動作なし）
   - その他 → `STATUS_UNKNOWN_OP`
   - `CMDRSP_STATUS(43) = pack_seq(status, seq)` を送信
   - `arg1`, `arg2` をクリア（次コマンドで送られなかった引数は 0）

### 4.2 イベント送信処理（Sim = イベントの送信者）

- `send_event(opcode, arg)`:
  - `pending is not None` なら抑止（`False` 返却、ログ「前イベント応答待ち」）
  - `seq = next_event_seq`、送信後 `next_event_seq ^= 1`
  - `EVT_ARG(44) = arg` → `EVT_OP(45) = pack_seq(opcode, seq)` の順で送信
  - `pending = PendingEvent(opcode, seq, arg, ticks_waited=0)`
- `EVTRSP_STATUS(52)` 受信:
  - `pending` あり かつ `seq_of(value) == pending.seq` → 解決（`last_event_response` 更新）、`pending=None`
  - seq 不一致 or 保留なし → 破棄（ログ）
- `tick()`:
  - `pending` あり → `ticks_waited += 1`。`> RESPONSE_TIMEOUT_TICKS(30)` で失敗扱い、`pending=None`（再送可）

### 4.3 クロス安全性

受信ディスパッチ（`handle_incoming_cc`）は常時実行。イベント応答待ち中でもコマンド受信→即 ACK。
イベント保留はコマンド処理と独立。別 CC・別方向のためデッドロックしない（last-write-wins）。

## 5. キーボード操作マッピング

| キー | 動作 | 送信 |
| --- | --- | --- |
| `1`/`2` | 左X +/− | CC16/48 |
| `3`/`4` | 左Y +/− | CC17/49 |
| `5`/`6` | 右X +/− | CC18/50 |
| `7`/`8` | 右Y +/− | CC19/51 |
| `0` | 全軸を原点へ | 上記 |
| `Q W E F T Y U I O P` | ボタン 0–9（KEYDOWN=127 / KEYUP=0） | CC20–29 |
| `]`/`[` | Preset +1/−1（0–127） | CC40 |
| `X`/`Z` | Error +1/−1（0–127） | CC41 |
| `V`/`C` | State +1/−1（0–127） | CC42 |
| `G` | HeartBeat イベント送信（op0） | CC44/45 |
| `B` | ButtonCombo イベント送信（op1） | CC44/45 |
| `N` | SensorTrigger イベント送信（op2） | CC44/45 |
| `M` | Stick ⇔ Slider 切替（軸を原点リセット） | — |
| `/` | ヘルプ再表示 | — |
| `ESC` | 終了 | — |

### スティック挙動

- 軸の真値は raw 14bit（0–16383）で保持。押下中のキーに応じて毎 Tick `STICK_STEP_PER_TICK` ずつランプ、離上で現在値保持。
- Stick モード: 原点 `CENTER_14BIT(8192)`。`+` で 16383 方向、`−` で 0 方向、クランプ。
- Slider モード: 原点 `0`。`+` で 16383 方向、`−` で 0 方向、クランプ。
- 送信する MSB/LSB バイト列はモード非依存（同じ raw 値の分割）。モードは原点・表示正規化・初期値にのみ影響。
- raw 値が前回送信値から変化したときのみ 14bit CC を送信（変化検出でトラフィック抑制）。

### Preset / Error / State

- キー押下ごとに ±1（0–127 クランプ）、変化時に該当 CC を送信。

## 6. メインループとタイミング

- `TICK_INTERVAL = 1/60`（≈16.7ms）。各反復で:
  1. `pygame.event.get()` で KEYDOWN/KEYUP を処理（ボタンエッジ、離散 +/− 操作、イベント送信、モード切替）
  2. 押下中キーから軸をランプし、変化があれば 14bit CC 送信
  3. `messaging.tick()`（タイムアウト判定）
  4. HUD を変化時に更新表示
  5. 次 Tick まで sleep
- 受信は `midi_io` のコールバックから `messaging.handle_incoming_cc()` を随時呼ぶ（別スレッド）。共有状態は最小限・短時間ロックで保護。

## 7. エラー処理

- MIDI 出力ポート未選択/開放失敗 → メッセージ表示し終了。
- MIDI 入力ポート未選択 → I/F 受信不可の警告を出して送信のみ継続。
- 出力 == 入力ポート → 自己エコー誤注入の警告。
- 受信メッセージ: CC（0xB0）以外は無視。値は 0–127 にクランプ。
- pygame/MIDI リソースは finally で穏当にクリーンアップ。

## 8. テスト方針（pytest, 80% 目標）

`pyproject.toml` で `testpaths=["tests"]`（既存 `test_midi_debug.py` の誤収集を防止）。

### test_cc_map.py
- `split_14bit` / `combine_14bit` の往復（0, 8192, 16383）
- `norm14_bipolar`: 0→−1.0, 8192→0.0, 16383→≈+1.0、クランプ
- `norm_slider`: 0→0.0, 16383→1.0
- `pack_seq` / `payload_of` / `seq_of`: payload 0–63 と seq 0/1 の合成・分解

### test_messaging.py（フェイク send_cc で送信メッセージを記録）
- SetPreset 受信 → `received_preset` 更新 & `CMDRSP_STATUS = pack_seq(OK, seq)` 送信
- 受信 seq が ACK の seqEcho に反映される（seq=0 と seq=1 両方）
- arg 消費: 1 回目に arg1/arg2 を送り commit、2 回目 arg 無し commit で arg=0 になる
- 未知 opcode → `STATUS_UNKNOWN_OP`
- イベント送信: `EVT_ARG`→`EVT_OP` の順序と seq 反転（連続送信で 0,1,0…）
- イベント応答: seq 一致で解決、seq 不一致で破棄、保留なし応答で破棄
- タイムアウト: 30 Tick 超過で保留クリア・再送可
- クロス: イベント応答待ち中にコマンド受信 → 即 ACK（保留は維持）

MIDI 送受信・pygame・対話 UI はユニットテスト対象外（純粋ロジックでカバレッジ確保）。

## 9. 実装手順

1. `cc_map.py`（定数・純粋関数）
2. `messaging.py`（ステートマシン）
3. `midi_io.py`（MIDI ラッパー・ポート選択）
4. `keyboard_map.py`（キー → アクション）
5. `midi_simulator.py`（オーケストレーション・メインループ）
6. `tests/`・`pyproject.toml`・`requirements-dev.txt`
7. venv 作成・依存導入・`pytest` 実行（80% 確認）
8. `INSTRUCTIONS.md` / `README.md` を新仕様に更新
9. コミット → PR 作成 → Codex レビュー対応

## 10. 廃止・非対象（YAGNI）

- ゲームパッド入力（pygame joystick）
- デモモード（自動パターン送信）
- 旧 CC#30 状態セレクタ（0–16 量子化）
- マッチ再スタート（`R`）など Unity 側専用機能
