# CC 帯再割り当て（102–119）と opcode 体系刷新 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2026-06-10 版 `docs/specs/midi-mapping.md`（パラメータ帯 CC102–105・Mode 新設・コマンド/イベント帯 CC110–116・opcode 共通番号空間 Ping/Reset/SetMode/SetZero/SetPreset/SetValve）に実装を追従させる。

**Architecture:** `messaging.py` をプロトコル層（フレーミング・ACK・seq・タイムアウト）に純化し、opcode 別の validate/execute とパラメータ現在値（State/Mode/Error/Preset/Valve）の一元管理を新規 `controller_state.py` に分離する。「検証 → ACK → 実行」の 2 段階呼び出しで仕様の「ACK 送信後にリセット/モード遷移」を構造的に保証する。

**Tech Stack:** Python 3.7+ / pygame / python-rtmidi / pytest（純粋ロジックのテスト）

**設計書:** [2026-06-10-cc-band-and-opcode-redesign-design.md](../specs/2026-06-10-cc-band-and-opcode-redesign-design.md)（validate/execute の全表・定数一覧・設計判断はこちらが正）

---

## 事前準備（全タスク共通）

すべての `pytest` コマンドは**仮想環境を有効化してから**実行する（INSTRUCTIONS.md 準拠）:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

---

## ファイル構成

| ファイル | 役割 | 変更 |
|----------|------|------|
| `docs/specs/midi-mapping.md` | 外部正典（2026-06-10 版コピー済み） | 更新 |
| `cc_map.py` | CC 値刷新・Mode/Valve/opcode 定数追加・旧 opcode 定数削除 | 変更 |
| `controller_state.py` | ★新規★ パラメータ一元管理＋コマンド validate/execute | 新規 |
| `messaging.py` | validate/execute 注入・ARG 省略イベント・clear_pending | 変更 |
| `auto_sequencer.py` | SCALAR=State/Error/Preset（CC 昇順・Mode 除外）・EVENT=Ping のみ | 変更 |
| `keyboard_map.py` | EVENT_KEYS=G→Ping のみ・help_text 更新 | 変更 |
| `midi_simulator.py` | ControllerState 配線・初期通知・同一ポート警告削除 | 変更 |
| `tests/test_spec_sync.py` | 新表フォーマット対応・opcode 全照合・Mode/Valve 値照合 | 変更 |
| `tests/test_controller_state.py` | ★新規★ validate/execute・adjust・初期通知 | 新規 |
| `tests/test_messaging.py` | フェイク validate/execute 注入でプロトコル層のみ検証 | 変更 |
| `tests/test_auto_sequencer.py` | SCALAR 順序・EVENT Ping 化 | 変更 |
| `tests/test_cc_map.py` | `CMD_SET_PRESET` → `OP_SET_PRESET` 参照更新 | 変更 |
| `pyproject.toml` | `--cov=controller_state` 追加 | 変更 |
| `INSTRUCTIONS.md` / `README.md` | CC 一覧・キー表・モジュール構成・衝突注記削除 | 変更 |

コミット戦略: 各 Task 末尾でコミット。Task 1（仕様書）直後のみ `test_spec_sync` が RED
（＝追従漏れ検出機構の意図動作・前例 8f14c5e→938a722 と同じ流れ）。Task 2 以降は常に全 GREEN。

---

## Task 1: 仕様書・設計書・実装計画のコミット

**Files:** `docs/specs/midi-mapping.md`（コピー済み）/ `docs/superpowers/specs/2026-06-10-cc-band-and-opcode-redesign-design.md` / 本計画書

- [ ] **Step 1: spec_sync が追従漏れを検出することを確認**

Run: `pytest tests/test_spec_sync.py -q --no-cov`
Expected: FAIL（Preset 40≠105 など複数）— 検出機構が機能している証拠

- [ ] **Step 2: コミット**

```bash
git add docs/
git commit -m "docs: MIDI 仕様 2026-06-10 版（CC帯102-119・opcode刷新）を取り込み設計と計画を追加"
```

---

## Task 2: cc_map 刷新＋ test_spec_sync 全面改修（GREEN 回復）

**Files:** Modify: `cc_map.py` / `tests/test_spec_sync.py` / `tests/test_cc_map.py`

- [ ] **Step 1: cc_map.py の定数を刷新**

設計書 §3 のとおり。要点:
- CC 値変更: `STATE_CC=102` / `ERROR_CC=104` / `PRESET_CC=105` / `CMD_ARG1_CC=110` /
  `CMD_ARG2_CC=111` / `CMD_OP_CC=112` / `EVTRSP_STATUS_CC=113` / `CMDRSP_STATUS_CC=114` /
  `EVT_ARG_CC=115` / `EVT_OP_CC=116`
- 新定数: `MODE_CC=103`、`OP_PING/OP_RESET/OP_SET_MODE/OP_SET_ZERO/OP_SET_PRESET/OP_SET_VALVE`
  （0–5）、`OPCODE_NAMES`、`EVENT_OPCODES=(OP_PING,)`、`MODE_NORMAL=0` /
  `MODE_VERSION_UP=110` / `MODE_FACTORY_INSPECTION=127` / `MODE_VALUES` / `MODE_NAMES`、
  `VALVE_OPEN=0` / `VALVE_CLOSE=1`
- 旧 opcode 定数（`CMD_PING/CMD_LED/CMD_HAPTIC/CMD_SET_PRESET/EVT_*`）は参照側更新まで**併存**
  （`CMD_SET_PRESET = OP_SET_PRESET` のようにエイリアス化し、Task 4 で削除）

- [ ] **Step 2: test_spec_sync.py を新仕様フォーマットへ改修**

- 早見表: `test_scalar_ccs` のパラメトライズへ `("Mode", cc_map.MODE_CC)` を追加
- STATUS 表: 正規表現を `^\|\s*(\d+)\s*\|\s*(OK|UNKNOWN_OP|INVALID_ARG|REJECTED)\s*\|` へ
- opcode 表（新形式 `| 0 | Ping | **G⇄C（双方向）** | …`）: 全 6 opcode の番号照合
  `{"Ping": OP_PING, "Reset": OP_RESET, "SetMode": OP_SET_MODE, "SetZero": OP_SET_ZERO,
  "SetPreset": OP_SET_PRESET, "SetValve": OP_SET_VALVE}`
- イベント方向: 方向列が `C→G` または `G⇄C` の opcode 集合 == `set(EVENT_OPCODES)` を検証
- SetMode ARG1 値 `**0 = 通常モード / 110 = バージョンアップモード / 127 = 出荷検査モード**`
  → `MODE_VALUES` と照合
- SetValve ARG1 値 `**0 = open / 1 = close**` → `VALVE_OPEN`/`VALVE_CLOSE` と照合
- 旧 `test_event_opcodes`（HeartBeat 等）と旧 `test_set_preset_opcode` の太字前提を削除/置換

- [ ] **Step 3: test_cc_map.py の参照更新**

`test_pack_seq_known_values` の `cc_map.CMD_SET_PRESET` → `cc_map.OP_SET_PRESET`

- [ ] **Step 4: 全テスト GREEN 確認**

Run: `pytest -q`
Expected: PASS（messaging/auto_sequencer は定数参照のため CC 値変更に自動追従）

- [ ] **Step 5: コミット**

```bash
git commit -am "feat: CC帯102-119とopcode共通番号空間へcc_mapを刷新し仕様同期テストを追従"
```

---

## Task 3: controller_state.py 新設（TDD）

**Files:** Create: `controller_state.py` / `tests/test_controller_state.py`

- [ ] **Step 1: 失敗するテストを書く**（フェイク `send_cc`（記録）・`on_reset`（カウンタ）・`on_log`（記録）を注入）

| テスト | 検証内容 |
| --- | --- |
| `test_initial_values` | state=0 / mode=MODE_NORMAL / error=0 / preset=0 / valve=None |
| `test_notify_initial_sends_all_params_in_cc_order` | CC102→103→104→105 の順で現在値送信 |
| `test_adjust_preset_sends_on_change` | +1 で CC105=1 送信・戻り値 1 |
| `test_adjust_preset_clamps_at_max_without_send` | 127 で +1 → 送信なし・127 のまま |
| `test_adjust_error_and_state_use_own_cc` | X/Z→CC104、V/C→CC102 |
| `test_set_scalar_skips_unchanged_value` | 同値 set_scalar → 送信なし |
| `test_validate_ping_reset_zero_preset_ok` | Ping/Reset/SetZero/SetPreset → OK |
| `test_validate_set_mode_rejects_invalid_value` | arg1=50 → INVALID_ARG・mode 不変 |
| `test_validate_set_mode_rejected_after_transition` | 110 遷移後の SetMode → REJECTED |
| `test_validate_set_valve_rejects_invalid_value` | arg1=2 → INVALID_ARG |
| `test_validate_unknown_opcode` | opcode=6 → UNKNOWN_OP |
| `test_validate_ignores_arg2` | SetPreset arg2=99 でも OK（未使用 ARG 不検証） |
| `test_execute_set_preset_updates_and_notifies` | preset=100 → CC105=100 送信 |
| `test_execute_set_preset_same_value_no_notify` | 同値 → 送信なし（仕様: 変化なし通知なし） |
| `test_execute_set_mode_updates_and_notifies` | 110 → mode=110・CC103=110 送信 |
| `test_execute_set_mode_same_value_no_notify` | 通常→通常(0) → 送信なし |
| `test_execute_reset_restores_initial_and_renotifies` | 変更済み状態から Reset → on_reset 1 回・全値初期化・CC102–105 再通知 |
| `test_execute_set_valve_updates_state` | 1 → valve=VALVE_CLOSE |
| `test_execute_ping_no_side_effect` | 送信なし・状態不変 |

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/test_controller_state.py -q --no-cov`
Expected: FAIL（`ModuleNotFoundError: No module named 'controller_state'`）

- [ ] **Step 3: controller_state.py を実装**（設計書 §4 の validate/execute 表に従う）

- [ ] **Step 4: テスト PASS 確認**

Run: `pytest tests/test_controller_state.py -q --no-cov` → PASS、`pytest -q` → 全 PASS

- [ ] **Step 5: コミット**

```bash
git add controller_state.py tests/test_controller_state.py
git commit -m "feat: パラメータ一元管理とコマンドvalidate/executeを担うcontroller_stateを新設"
```

---

## Task 4: messaging プロトコル層化＋参照側追従＋旧定数削除

**Files:** Modify: `messaging.py` / `auto_sequencer.py` / `keyboard_map.py` / `midi_simulator.py` / `cc_map.py`（旧定数削除）/ `tests/test_messaging.py` / `tests/test_auto_sequencer.py` / `pyproject.toml`

- [ ] **Step 1: test_messaging.py をフェイク validate/execute 注入形へ改修**

`make()` を `Messaging(sender, validate, execute)` 構築に変更（既定フェイク: validate は
呼び出しを記録し設定 status を返す / execute は呼び出しを記録）。追加・変更テスト:

| テスト | 検証内容 |
| --- | --- |
| `test_ack_echoes_seq`（既存改修） | ACK = CC114・pack_seq(status, seq) |
| `test_validate_then_ack_then_execute_order` | 呼び出し順序が validate → ACK 送信 → execute |
| `test_execute_skipped_when_not_ok` | validate が INVALID_ARG → execute 呼ばれず・ACK は INVALID_ARG |
| `test_args_consumed_after_commit`（既存維持） | 2 回目 commit で arg1/arg2=0 |
| `test_send_event_without_arg_omits_evt_arg` | `send_event(OP_PING)` → CC116 のみ（CC115 なし） |
| `test_send_event_with_arg_sends_arg_first`（既存改修） | arg 指定時 CC115→CC116 の順 |
| `test_clear_pending_discards_silently` | 保留中 clear_pending → event_pending=False・last_event_response 変化なし・再送可 |
| seq 反転・応答解決/不一致破棄/保留なし破棄・タイムアウト・クロス（既存維持・opcode 定数のみ OP_PING へ） | |

- [ ] **Step 2: messaging.py を改修**（設計書 §5: コンストラクタ 3 引数・`_process_command` と
  `received_preset`/`preset_option` 削除・`_commit_command` を validate→ACK→execute 化・
  `send_event(opcode, arg=None)` の ARG 省略・`clear_pending()` 追加・`_PendingEvent.arg` 削除）

Run: `pytest tests/test_messaging.py -q --no-cov` → PASS

- [ ] **Step 3: test_auto_sequencer.py を改修**

- SCALAR 順序を `[STATE_CC, ERROR_CC, PRESET_CC]`（CC 昇順 102→104→105）に変更
- EVENT: `test_sends_three_events_in_order_waiting_for_response` →
  `test_sends_single_ping_then_loops`（Ping 1 件送信→応答待ち→解決でサイクル先頭へ）
- `test_event_arg_increments_across_sends` / `test_event_arg_wraps_at_max` を削除
- Mode（CC103）が SCALAR に**含まれない**ことを明示するテストを追加

- [ ] **Step 4: auto_sequencer.py を改修**（設計書 §7: `_SCALAR_CCS=(STATE_CC, ERROR_CC,
  PRESET_CC)`・`_EVENT_OPCODES=cc_map.EVENT_OPCODES`・`_event_arg` 削除・EVENT の value=0 固定）

Run: `pytest tests/test_auto_sequencer.py -q --no-cov` → PASS

- [ ] **Step 5: keyboard_map.py / midi_simulator.py を配線**（設計書 §6）

- keyboard_map: `EVENT_KEYS = {pygame.K_g: cc_map.OP_PING}`・help_text 更新（CC105/104/102・
  G=Ping・B/N 削除・コマンド 6 種注記）
- midi_simulator: `ControllerState` 構築と `Messaging(send_cc, validate, execute)` 配線・
  `_preset/_error/_state/_event_arg/_sync_scalar` 削除・adjust 系呼び出し・
  `notify_initial()`（ポート接続後）・`_on_controller_reset`（clear_pending→全ボタン OFF→軸中心化）・
  同一ポート警告削除・`_send_event` の arg 廃止・`_log_incoming_changes` の preset 同期削除＋
  OPCODE_NAMES 表示・`_dispatch_auto_action` の SCALAR を `set_scalar` 経由へ

- [ ] **Step 6: cc_map.py の旧定数（エイリアス）削除・pyproject に `--cov=controller_state` 追加**

Run: `grep -rn "CMD_PING\|CMD_LED\|CMD_HAPTIC\|CMD_SET_PRESET\|EVT_HEARTBEAT\|EVT_BUTTON_COMBO\|EVT_SENSOR_TRIGGER" --include="*.py"` → ヒットなし

- [ ] **Step 7: 全テスト＋カバレッジ確認**

Run: `pytest`
Expected: 全 PASS・カバレッジ 80% 以上（cc_map / messaging / auto_sequencer / controller_state）

- [ ] **Step 8: コミット**

```bash
git commit -am "feat: messagingをプロトコル層へ純化し新opcode体系・初期通知・Pingイベントへ追従"
```

---

## Task 5: ドキュメント更新

**Files:** Modify: `INSTRUCTIONS.md` / `README.md`

- [ ] **Step 1: INSTRUCTIONS.md 更新**

- モジュール構成へ `controller_state.py` を追加・`messaging.py` の説明をプロトコル層に更新
- 送受信 CC 一覧（送信: State=102/Mode=103/Error=104/Preset=105・CMDRSP=114・EVT=115/116、
  受信: CMD=110/111/112・EVTRSP=113）
- 「CC 番号衝突」注記を削除し「IN/OUT で CC 重複なし」へ差し替え（ポート選択 UI の警告記述も削除）
- コマンド/イベント I/F 節: opcode 6 種・ACK 後実行（Reset/SetMode）・SetPreset 変化時 CC105 通知・
  イベント=Ping のみ・初期通知＋変化時送信
- キー表: Preset/Error/State の CC 更新・イベント行を `G`=Ping に・設計書参照を追加

- [ ] **Step 2: README.md 更新**

- 残存していた旧 Stick/Slider 記述（解釈モード切替・起動時モード選択・実行例）を削除
  （撤廃は 2026-06-09 設計で確定済み・今回まとめて追従）
- CC 一覧・キー表・コマンド/イベント説明・同一ポート注意の削除・実行例を新仕様へ
- 「IN/OUT 別ポート」要件を「CC 重複はないが運用上 2 ポート推奨」へ緩和

- [ ] **Step 3: 検証＋コミット**

Run: `pytest -q` → PASS（spec_sync がドキュメントではなく仕様書を見ることを再確認）

```bash
git commit -am "docs: INSTRUCTIONS/READMEをCC帯102-119・opcode6種・Pingイベントへ更新"
```

---

## Task 6: レビュー・PR・マージ

- [ ] **Step 1:** code-reviewer エージェントによるレビュー（CRITICAL/HIGH は修正）
- [ ] **Step 2:** `git push -u origin claude/hungry-nash-a9b3b1` → `gh pr create`（変更概要・テスト計画）
- [ ] **Step 3:** CI/チェック確認後 `gh pr merge --squash`（または履歴方針に合わせ merge）
- [ ] **Step 4:** メインリポジトリ側の未コミット仕様書変更が PR 内容と同一であることを確認し、後始末を案内
