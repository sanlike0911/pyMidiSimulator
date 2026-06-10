# CC 帯再割り当て（102–119）と opcode 体系刷新への追従 設計書

- 日付: 2026-06-10
- 対象仕様: `docs/specs/midi-mapping.md`（2026-06-10 版・外部正典）
- 先行設計: [2026-06-09-controller-sim-new-midi-spec-design.md](2026-06-09-controller-sim-new-midi-spec-design.md)（CC 帯と opcode 体系は本書が更新する）

## 1. 仕様変更点サマリ

2026-06-10 版 `midi-mapping.md` の変更点（コントローラ役＝本シミュレータ視点）:

| 項目 | 旧 | 新 |
| --- | --- | --- |
| State 送信 | CC42 | **CC102** |
| Mode 送信 | （なし） | **CC103 新設**（0=通常 / 110=バージョンアップ / 127=出荷検査） |
| Error 送信 | CC41 | **CC104** |
| Preset 送信 | CC40 | **CC105** |
| CMD_ARG1 受信 | CC50 | **CC110** |
| CMD_ARG2 受信 | CC51 | **CC111**（現行確定 opcode ではすべて未使用） |
| CMD_OP 受信 | CC52 | **CC112** |
| EVTRSP_STATUS 受信 | CC53 | **CC113** |
| CMDRSP_STATUS 送信 | CC43 | **CC114** |
| EVT_ARG 送信 | CC44 | **CC115** |
| EVT_OP 送信 | CC45 | **CC116** |
| opcode 名前空間 | コマンド/イベントで別 | **共通番号空間＋方向（G→C / C→G / G⇄C）** |
| コマンド opcode | Ping(0)/LED(1)/Haptic(2)/SetPreset(4) | **Ping(0)/Reset(1)/SetMode(2)/SetZero(3)/SetPreset(4)/SetValve(5)** |
| イベント opcode | HeartBeat(0)/ButtonCombo(1)/SensorTrigger(2)（例示） | **Ping(0) のみ**（G⇄C 双方向。旧イベント 3 種は廃止） |
| SetPreset ARG2 | option 値 | **未使用**（送信省略可・受信側は検証しない） |
| IN/OUT CC 衝突 | 右スティック LSB と CMD_ARG が同番号（要警告） | **重複なし**（ループバックでも誤注入なし） |
| パラメータ送信契機 | 未規定 | **接続直後（初期通知）＋値の変化時** |
| SetPreset 成功時 | エコーバック未規定 | **変化時のみ CC105 で新値通知**（同値設定は通知なし） |
| Reset / SetMode | （なし） | **ACK（OK）送信後に実行/遷移** |

スティック（CC16–19/48–51）・ボタン（CC20–29）・STATUS コード（0–3）・seq ビット（64）・
タイムアウト（30 Tick）・ボタンしきい値（64）は変更なし。

## 2. アーキテクチャ変更: コマンド処理の層分離

### 動機

新仕様のコマンド群はシミュレータ全体の状態に作用する（Reset=全状態初期化、SetMode=Mode
更新＋CC103 通知、SetPreset=Preset 更新＋CC105 通知）。さらに「**検証 → ACK → 実行**」の
順序規約（Reset/SetMode は ACK 送信後に実行）が明文化された。opcode ビジネスロジックを
`messaging.py` に埋めたままでは、上位状態への作用とパラメータ現在値の二重管理
（旧: `Messaging._received_preset` と `ControllerSimulator._preset` の同期）が悪化する。

### 新しい責務分担

```
midi_simulator.py     配線・UI・物理層シミュレーション（軸・ボタン）・メインループ
controller_state.py   ★新規★ アプリ層: パラメータ現在値（State/Mode/Error/Preset/Valve）の
                      一元管理・変化時送信・初期通知・opcode 別の validate/execute
messaging.py          プロトコル層に純化: フレーミング（ARG バッファ・commit）・ACK 送信・
                      seq・イベント保留・タイムアウト。opcode の中身は知らない
cc_map.py             定数・純粋関数（CC 番号・opcode・Mode/Valve 値・seq コーデック）
```

- `Messaging` は `validate_command` / `execute_command` の 2 つの callable を注入される。
  commit 時に `status = validate(...)` → ACK 送信 → `status == OK` なら `execute(...)`。
  これにより「ACK 送信後に実行」が全 opcode で構造的に保証される。
- パラメータの変更経路（手動キー・自動モード・SetPreset/SetMode コマンド）はすべて
  `ControllerState` のメソッドを通り、変化検出と CC 送信が一元化される。

## 3. cc_map.py の定数刷新

```python
# 送信: 0–127 生値（パラメータ帯 CC102–109・余りは予約）
STATE_CC = 102
MODE_CC = 103    # 新設: 動作モード通知
ERROR_CC = 104
PRESET_CC = 105

# コマンド/イベント I/F（CC110–119 帯）
CMD_ARG1_CC = 110       # 受信: コマンド第1引数
CMD_ARG2_CC = 111       # 受信: コマンド第2引数（現行確定 opcode では未使用）
CMD_OP_CC = 112         # 受信: コマンド opcode + seq (commit)
EVTRSP_STATUS_CC = 113  # 受信: イベント送信への ACK
CMDRSP_STATUS_CC = 114  # 送信: 受信コマンドへの ACK
EVT_ARG_CC = 115        # 送信: イベント引数（確定イベント Ping では未使用＝送信省略）
EVT_OP_CC = 116         # 送信: イベント opcode + seq (commit)

# opcode（コマンド/イベント共通番号空間・bit0–5）
OP_PING = 0        # G⇄C 双方向
OP_RESET = 1       # G→C
OP_SET_MODE = 2    # G→C
OP_SET_ZERO = 3    # G→C
OP_SET_PRESET = 4  # G→C
OP_SET_VALVE = 5   # G→C
OPCODE_NAMES = {0: "Ping", 1: "Reset", 2: "SetMode", 3: "SetZero", 4: "SetPreset", 5: "SetValve"}

# イベント経路（Sim → Unity）で送信できる確定 opcode（方向が C→G または G⇄C のもの）
EVENT_OPCODES = (OP_PING,)

# Mode 値（CC103 通知と SetMode ARG1 で共通）
MODE_NORMAL = 0
MODE_VERSION_UP = 110
MODE_FACTORY_INSPECTION = 127
MODE_VALUES = (MODE_NORMAL, MODE_VERSION_UP, MODE_FACTORY_INSPECTION)
MODE_NAMES = {0: "通常", 110: "バージョンアップ", 127: "出荷検査"}

# SetValve ARG1 値
VALVE_OPEN = 0
VALVE_CLOSE = 1
```

旧定数 `CMD_PING / CMD_LED / CMD_HAPTIC / CMD_SET_PRESET / EVT_HEARTBEAT /
EVT_BUTTON_COMBO / EVT_SENSOR_TRIGGER` は削除（参照箇所も全て更新）。

## 4. controller_state.py（新規・純粋ロジック）

```python
class ControllerState:
    def __init__(self, send_cc: Callable[[int, int], None],
                 on_reset: Callable[[], None],
                 on_log: Callable[[str], None]) -> None: ...

    # パラメータ現在値（読み取り用プロパティ）: state / mode / error / preset / valve
    # 初期値: state=0, mode=MODE_NORMAL, error=0, preset=0, valve=None（未指示）

    def notify_initial(self) -> None: ...
        # 接続直後の初期通知: State/Mode/Error/Preset の現在値を CC 昇順（102→103→104→105）で送信

    # 手動キー用: ±delta・0–127 クランプ・変化時のみ送信＋ログ。新値を返す
    def adjust_state(self, delta: int) -> int: ...
    def adjust_error(self, delta: int) -> int: ...
    def adjust_preset(self, delta: int) -> int: ...

    # 自動デバッグ入力用: CC 番号指定で設定。変化時のみ送信（ログは AutoSequencer 側が担う）
    def set_scalar(self, cc: int, value: int) -> None: ...

    # messaging から注入される 2 段階コマンド処理
    def validate_command(self, opcode: int, arg1: int, arg2: int) -> int: ...  # STATUS を返す・状態不変
    def execute_command(self, opcode: int, arg1: int, arg2: int) -> None: ...  # OK 時のみ・ACK 後に呼ばれる
```

### validate_command（ACK 前・状態を変更しない）

| opcode | 検証 | STATUS |
| --- | --- | --- |
| Ping(0) | なし | OK |
| Reset(1) | なし | OK |
| SetMode(2) | `arg1 not in MODE_VALUES` → INVALID_ARG。現在モードが非通常 → REJECTED（一方向遷移・復帰不可） | OK / INVALID_ARG / REJECTED |
| SetZero(3) | なし（シミュレータは常に零点設定可能） | OK |
| SetPreset(4) | なし（7bit 全域 0–127 を対応範囲とする。仕様: 対応範囲はコントローラ実装依存） | OK |
| SetValve(5) | `arg1 not in (VALVE_OPEN, VALVE_CLOSE)` → INVALID_ARG | OK / INVALID_ARG |
| その他 | 未実装 opcode | UNKNOWN_OP |

共通規約の実装対応:
- **未使用 ARG は検証しない**（仕様: 0 以外が届いても無視し INVALID_ARG にしない）→ arg2 はどの opcode でも見ない。
- **INVALID_ARG / REJECTED 時は状態を変更しない** → validate は読み取りのみ、execute は OK 時のみ呼ばれる構造で保証。

### execute_command（ACK 後・OK 時のみ）

| opcode | 実行内容 |
| --- | --- |
| Ping | なし |
| Reset | パラメータを初期値へ（state=0, mode=通常, error=0, preset=0, valve=None）→ `on_reset()`（軸中心化・全ボタン OFF・イベント保留破棄は simulator 側）→ `notify_initial()`（接続直後相当の再通知）→ ログ |
| SetMode | `arg1 == mode`（通常→通常の同値）なら変化なし・通知なし。変化時は mode 更新 → **CC103 送信** → ログ（旧→新モード名） |
| SetZero | ログのみ（物理センサなし・受領） |
| SetPreset | `arg1 == preset` なら変化なし・**通知なし**（仕様）。変化時は preset 更新 → **CC105 送信** → ログ |
| SetValve | valve 更新 → ログ（open/close）。バルブ状態を通知する CC は仕様にないため送信なし |

> SetMode 補足: validate で非通常モードからの SetMode は REJECTED になるため、execute に到達
> するのは現在モード＝通常のときのみ。バージョンアップ／出荷検査モードへの遷移後も本シミュ
> レータは動作を継続する（仕様上は遷移後の動作未規定。デバッグツールとして寛容に振る舞う）。

## 5. messaging.py の変更（プロトコル層へ純化）

```python
class Messaging:
    def __init__(self, send_cc, validate_command, execute_command) -> None: ...

    def _commit_command(self, op_value: int) -> None:
        # opcode/seq 分解・引数確定（未送は 0）
        status = self._validate_command(opcode, arg1, arg2)
        self._send_cc(CMDRSP_STATUS_CC, pack_seq(status, seq))   # ① ACK 先行
        if status == STATUS_OK:
            self._execute_command(opcode, arg1, arg2)            # ② ACK 後に実行
        # CommandRecord 記録・引数消費（従来どおり）

    def send_event(self, opcode: int, arg: Optional[int] = None) -> bool: ...
        # arg=None: ARG 未使用イベント（Ping）→ EVT_ARG 送信を省略し EVT_OP のみ（仕様の正規例に一致）
        # arg=int : 従来どおり EVT_ARG → EVT_OP の順

    def clear_pending(self) -> None: ...
        # 保留中イベントを応答記録なしで破棄（Reset 実行時に simulator から呼ぶ）
```

- `_process_command` と `_received_preset` / `_preset_option` を削除（アプリ層へ移動）。
- `MessagingState` から `received_preset` / `preset_option` を削除。
- `_PendingEvent.arg` は未参照のため削除。
- CC 番号は `cc_map` 定数参照のため受信ディスパッチのコード形は不変。
- タイムアウト・seq・クロス挙動は変更なし。

## 6. midi_simulator.py / keyboard_map.py の変更

### 配線

```python
self._params = ControllerState(send_cc=self._midi.send_cc,
                               on_reset=self._on_controller_reset, on_log=print)
self._messaging = Messaging(self._midi.send_cc,
                            self._params.validate_command, self._params.execute_command)
```

- `_preset` / `_error` / `_state` / `_event_arg` フィールドと `_sync_scalar()` を削除し
  `ControllerState` へ一元化。
- 手動キー（`]`/`[`・`X`/`Z`・`V`/`C`）は `adjust_preset/error/state` 呼び出しに変更
  （クランプ・変化時のみ送信・ログは ControllerState 側）。
- ポートセットアップ成功後・メインループ前に `self._params.notify_initial()`（初期通知）。
- `_on_controller_reset()`: `messaging.clear_pending()` → 全ボタン OFF → 軸中心化。
  （パラメータ初期化と再初期通知は ControllerState.execute_command 内で実施）
- `_log_incoming_changes()`: SetPreset 受信時の `_preset` 同期コードを削除
  （一元管理で不要）。受信コマンドのログに `OPCODE_NAMES` による名称を追加。
- **同一ポート警告を削除**: 新仕様は IN/OUT で CC 重複なし（仕様に明記）。自己エコーが
  あっても受信処理対象（CC110–113）に送信 CC は含まれないため無害。
- `_dispatch_auto_action`: SCALAR は `params.set_scalar(cc, value)` 経由（直接 send_cc しない）、
  EVENT は `messaging.send_event(opcode)`（arg なし）。

### keyboard_map.py

- `EVENT_KEYS = {pygame.K_g: cc_map.OP_PING}`（`B`/`N` キー削除。確定イベントは Ping のみ）。
- `help_text()` の CC 番号・イベント行・コマンド注記を更新。

## 7. auto_sequencer.py の変更

- SCALAR フェーズ対象: `(STATE_CC, ERROR_CC, PRESET_CC)`＝CC 昇順 102→104→105。
  **Mode（CC103）は対象外**: Mode は「現在の動作モード」の意味を持つ通知で、スイープや
  巡回送信で 110/127 を流すと受信側が実際にモード遷移したと誤認しうるため
  （遷移後の動作は仕様未規定）。Mode 通知は SetMode コマンド起点と初期通知に限定する。
  **→ 本判断は動作確認フィードバックにより §12 で変更（Mode も有効 3 値のみ巡回対象に）**。
- EVENT フェーズ対象: `EVENT_OPCODES`＝Ping のみ。ARG 未使用のため `_event_arg`
  カウンタを削除し、`SendAction(EVENT, opcode, 0)`（value は未使用）を 1 件送って
  応答待ち→サイクル完了とする。
- `ActionKind.EVENT` の注記を「target=opcode, value=未使用(0)」に更新。

## 8. test_spec_sync.py の更新（仕様パーサ追従）

新フォーマットへの追従と検証強化:

- **早見表**: スカラー照合に Mode（CC103）を追加。messaging 系 CC は定数参照のため
  パラメトライズ定義は不変（値は cc_map 更新で追従）。
- **STATUS 表**: 新形式 `| 0 | OK | …` （名称が独立列）に正規表現を更新。
- **opcode 表**: 新形式 `| 0 | Ping | **G⇄C（双方向）** | …` をパースし、
  全 6 opcode（Ping/Reset/SetMode/SetZero/SetPreset/SetValve）の番号を実装定数と照合。
- **イベント方向**: opcode 表の方向列から「C→G または G⇄C」の opcode 集合を抽出し、
  実装の `EVENT_OPCODES` と一致することを検証（仕様でイベントが増えたら検出）。
- **SetMode ARG1 値**: 表中の太字 `**0 = 通常モード / 110 = バージョンアップモード /
  127 = 出荷検査モード**` から (0, 110, 127) を抽出し `MODE_VALUES` と照合。
- **SetValve ARG1 値**: `**0 = open / 1 = close**` から `VALVE_OPEN` / `VALVE_CLOSE` を照合。
- 規約値（ボタンしきい値 64・seq ビット 64・タイムアウト 30）は仕様文言不変のため既存のまま。

## 9. テスト方針

- `tests/test_controller_state.py`（新規）: §4 の表のとおり validate/execute を網羅
  （初期通知の順序、adjust 系の変化検出・クランプ、SetMode の一方向遷移・同値・
  INVALID_ARG 時の状態不変、SetPreset の変化時のみ通知、SetValve、Reset の
  on_reset 呼び出しと再通知、未知 opcode）。
- `tests/test_messaging.py`: フェイク validate/execute を注入し、プロトコル層のみ検証
  （ACK の seqEcho、validate→ACK→execute の呼び出し順序、非 OK で execute 抑止、
  引数消費、ARG 省略イベント送信、seq 反転、応答解決/破棄、タイムアウト、clear_pending、クロス）。
- `tests/test_auto_sequencer.py`: SCALAR 順序（State→Error→Preset）と EVENT（Ping 1 件で
  サイクル完了）へ更新。`_event_arg` 関連テストを削除。
- `pyproject.toml`: `--cov=controller_state` を追加（カバレッジ対象 4 モジュール・80% 以上）。

## 10. ドキュメント更新

- `INSTRUCTIONS.md`: モジュール構成（controller_state.py 追加）、送受信 CC 一覧、
  CC 衝突注記の削除（IN/OUT 重複なしへ）、コマンド一覧（6 opcode）、イベント＝Ping のみ、
  キー表（B/N 削除）、初期通知・変化時送信、自動デバッグの巡回内容（Mode 対象外）。
- `README.md`: 該当する CC 番号・キー操作・コマンド説明を同様に更新。
- 本設計書を「対象 MIDI 仕様」の現行設計として `INSTRUCTIONS.md` から参照。

## 11. 非対象（YAGNI）

- ~~Mode の手動操作キー（仕様にコントローラ自発のモード遷移の規定なし。SetMode 起点で十分）~~ **→ §12 で撤回（`B` キーを追加）**
- ~~自動デバッグ巡回への Mode 追加（§7 のとおり意味論を壊すため）~~ **→ §12 で撤回（有効 3 値のみ巡回）**
- バージョンアップ／出荷検査モード中の動作制限（仕様未規定。通常動作を継続）
- EVT_ARG 付きイベント送信の UI（確定イベント Ping は ARG 未使用。`send_event(opcode, arg)`
  API 自体は将来の ARG 付きイベントに対応済み）
- CC106–109 / 117–119 予約帯・ボタン 10–19 予約帯の実装

## 12. 追記（2026-06-10 動作確認フィードバック）: Mode（CC103）のデバッグ送信

実機動作確認で「Mode（CC103）のデバッグが考慮されていない＝CC103 を能動的に送信して
受信側の処理を確認する手段がない」とのフィードバックを受け、§7・§11 の判断を変更した。
初期通知（Mode=0 固定）と SetMode 起点（Unity 側に送信実装が必要）だけでは、
受信側 CC103 ハンドリングの単体デバッグができないため。

- **手動キー `B`（Mode 巡回）**: Mode を有効値の並び（通常 0 → バージョンアップ 110 →
  出荷検査 127 → 通常 0）で巡回し、CC103 で送信する（`ControllerState.cycle_mode()`）。
  これは「コントローラ自身のモード遷移」の模擬であり、SetMode コマンドの一方向遷移は
  **ゲーム側からの制約**のため本操作には適用しない。非通常モードへ巡回した状態では
  SetMode が REJECTED になる状況も再現できる（テストで仕様化済み）。
- **自動デバッグ巡回に Mode を追加**: SCALAR フェーズを State(102)→Mode(103)→
  Error(104)→Preset(105) の CC 昇順とする。ただし Mode はスイープせず**有効 3 値
  （110→127→0）のみ**を送信し、最後に必ず通常(0)へ復帰する（無効値の大量送信と、
  非通常モードのまま巡回を終える誤認を防ぐ）。Mode の送信は全件モード名付きでログする。
- `ControllerState.set_scalar` は `MODE_CC` も扱う（変化時のみ送信・内部 mode 同期。
  これにより自動巡回中も SetMode の REJECTED 判定が実状態と整合する）。
- `AutoSequencer._tick_scalar` は「CC ごとの送信値列」（Mode=3 値・他=0〜127 スイープ）に
  一般化した（送信タイミング・他 CC の値列は従来と同一）。
