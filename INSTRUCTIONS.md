# プロジェクト指示書 (INSTRUCTIONS.md)

このファイルは、このリポジトリで作業する AI コーディングツール（Claude Code / Codex 等 / Gemini CLI）共通の指示書（唯一のソース）です。各ツールの設定ファイル（CLAUDE.md / AGENTS.md / GEMINI.md）はこのファイルを参照します。

## プロジェクト概要

キーボード操作で **MIDI コントローラ役（送信主体）** を演じ、Unity 側 `CkdGameController`（受信側）に対して新 MIDI 仕様の Control Change を送受信する Python シミュレータです。実機 MIDI コントローラやゲームパッドが無くても、スティック / スライダー / ボタン / State / Mode / Error / Preset の送信、コマンド受信＋ACK、イベント送信＋応答待ちを検証できます。

- 対象 MIDI 仕様: `docs/specs/midi-mapping.md`（ゲーム＝Unity 側の視点で IN/OUT を記述）
- 設計書: `docs/superpowers/specs/2026-06-12-cc-remap-slider-and-12-buttons-design.md`（CC 再配置・Slider 専用帯・ボタン 12 個。基盤は `2026-06-10-cc-band-and-opcode-redesign-design.md` / `2026-06-09-controller-sim-new-midi-spec-design.md`）

> **仕様書の同期運用:** `docs/specs/midi-mapping.md` は**別プロジェクト（Unity / コントローラ側）で更新され、本リポジトリへそのままコピーされる外部正典**。本ファイルには pyMidiSimulator 固有の注記を書き込まない（上書きで消えるため）。仕様書を更新したら `pytest` を実行すること — `tests/test_spec_sync.py` が仕様書の CC 早見表・コード値（STATUS / opcode）・規約値（しきい値 / seq ビット / タイムアウト）をパースして実装定数（`cc_map` / `messaging`）と照合し、実装の追従漏れを検出する。

> **重要な視点:** 仕様書は Unity（受信側）の視点で「IN / OUT」を定義しています。本シミュレータはコントローラ役なので**送受信が反転**します（仕様の「IN」= 本アプリの送信、「OUT」= 本アプリの受信）。

## アーキテクチャ

### モジュール構成

- **`midi_simulator.py`** - `ControllerSimulator` クラス（オーケストレーション）。ポート選択 UI、pygame ウィンドウ、メインループ、キー入力処理、HUD ログ、リソース解放。
- **`cc_map.py`** - CC 番号定数・opcode/Mode/Valve 定数・正規化（`norm14_bipolar` / `norm14_unipolar`）・14bit 分割/再構成（`split_14bit` / `combine_14bit`）・seq コーデック（`pack_seq` / `payload_of` / `seq_of`）。MIDI/pygame 非依存の純粋関数。
- **`controller_state.py`** - アプリ層。パラメータ現在値（State/Mode/Error/Preset/Valve）の一元管理・接続直後の初期通知・変化時のみ送信・コマンドの opcode 別 validate/execute。`send_cc` / `on_reset` / `on_log` の注入で動作する純粋ロジック。
- **`auto_sequencer.py`** - 自動デバッグ入力モードの巡回シーケンス生成（`AutoSequencer` / `SendAction` / `ActionKind` / `Phase`）。`tick(event_pending)` がアクション列を返す MIDI/pygame 非依存の純粋ロジック。
- **`messaging.py`** - コマンド/イベント I/F のプロトコル層（フレーミング・ACK・seqEcho・イベント保留・タイムアウト）。opcode の中身は知らず、注入された `validate_command` / `execute_command`（controller_state）へ「検証 → ACK → 実行」の順で委譲する。
- **`midi_io.py`** - python-rtmidi のラッパー（ポート列挙・7bit/14bit CC 送信・受信ディスパッチ）。
- **`keyboard_map.py`** - pygame キー → セマンティックアクションのマッピングとヘルプテキスト。
- **`tests/`** - pytest（`cc_map` / `messaging` / `controller_state` / `auto_sequencer` の純粋ロジックを網羅）。`test_spec_sync.py` は仕様書 `docs/specs/midi-mapping.md` と実装定数の同期を検証する。
- **`setup.py`** - 依存確認・インストール・起動を行う補助スクリプト。

### 送受信 CC（コントローラ役視点）

**送信（Sim → Unity / 仕様表の「IN」）**
- スティック: 左X→CC#20/52、左Y→CC#21/53、右X→CC#22/54、右Y→CC#23/55（MSB/LSB、14bit 0–16383、LSB 先・MSB 後）
- スライダー 1–4: CC#24/56–27/59（MSB/LSB、14bit 0–16383。帯 24–31/56–63 のうち 5–8 は予約＝未使用）
- ボタン 0–11: CC#102–113（127=押下 / 0=離上、受信側しきい値 64。12 本固定・予約帯なし）
- パラメータ（0–127 生値・**接続直後の初期通知＋値の変化時のみ送信**）: State=CC#114 ／ Mode=CC#115（0=通常 / 110=バージョンアップ / 127=出荷検査）／ Error=CC#116 ／ Preset=CC#117
- CMDRSP_STATUS: CC#90（受信コマンドへの ACK）
- イベント: EVT_ARG=CC#118（確定イベント Ping は未使用＝送信省略）/ EVT_OP=CC#119

**受信（Unity → Sim / 仕様表の「OUT」）**
- コマンド: CMD_ARG1=CC#85 / CMD_ARG2=CC#86（現行確定 opcode では未使用）/ CMD_OP=CC#87（commit）
- EVTRSP_STATUS: CC#89（イベント送信への ACK）

> **IN/OUT で CC 番号の重複なし**（仕様に明記）。単一仮想ポート/ループバックで疎通試験しても、自分の送信が他入力として誤注入されることはない（受信処理対象は CC#85–87/89 のみ）。CC#88 は High Resolution Velocity Prefix のため割り当て禁止。Unity と対向する通常運用では IN/OUT に loopMIDI 等の 2 ポートを使う。

### スティック/スライダー解釈

- **スティック**（CC 20/52・21/53・22/54・23/55）: 中心点 8192 を基準とする双極値（-1.0 … +1.0）。表示は `norm14_bipolar` による双極正規化。
- **スライダー**（CC 24/56–27/59）: 0 を原点とする単極値（0.0 … 1.0）。表示は `norm14_unipolar` による単極正規化。Stick とは専用 CC ペア帯で独立しており**同時併用可**。
- `R` キーでスティックは中心点 8192、スライダーは原点 0 へ移動できる。

> 旧 Stick/Slider「解釈モード切替」（同一 CC を共有しワイヤ外合意で解釈を変える方式）は撤廃済みで、新仕様の Slider は専用帯を持つ独立入力として実装する。経緯は [docs/superpowers/specs/2026-06-09-auto-debug-input-mode-design.md](docs/superpowers/specs/2026-06-09-auto-debug-input-mode-design.md) と [docs/superpowers/specs/2026-06-12-cc-remap-slider-and-12-buttons-design.md](docs/superpowers/specs/2026-06-12-cc-remap-slider-and-12-buttons-design.md) を参照。

### コマンド/イベント I/F

仕様書セクション5に準拠（seq=bit6、ACK、タイムアウト、応答待ちステートマシン）：
- 受信コマンド: `CMD_ARG1/ARG2` をバッファ → `CMD_OP` 到着で commit →「**検証 → ACK（seqEcho 付）→ 実行**」の順で処理（仕様の「Reset/SetMode は ACK 送信後に実行」を全 opcode で構造的に保証）→ arg 消費。
- opcode（共通番号空間・全 6 種）: Ping(0)=OK ／ Reset(1)=全状態初期化＋再初期通知 ／ SetMode(2)=ARG1∈{0,110,127} を検証し変化時 CC#115 通知（**一方向遷移**: 非通常モードからは REJECTED）／ SetZero(3)=受領のみ ／ SetPreset(4)=変化時のみ Preset 更新＋CC#117 通知（ARG2 未使用）／ SetValve(5)=ARG1∈{0=open,1=close} を検証。未知 opcode=UNKNOWN_OP。未使用 ARG は検証しない。
- イベント送信: 確定イベントは **Ping(0) のみ**（方向 G⇄C。ARG 未使用のため `EVT_ARG` を省略し `EVT_OP` のみ送信）→ `EVTRSP_STATUS` 受信で解決、seq 不一致/保留なしは破棄、30 Tick でタイムアウト・再送可。
- 受信は常時処理（応答待ち中もコマンド受信→即 ACK）。クロスしてもデッドロックしない。

### 自動デバッグ入力モード

`M` キーで ON/OFF する。手動操作なしに送信系の CC を巡回送信し、受信側（Unity）の動作確認に使う。1 サイクルは「スティック各軸スイープ → スライダー 1–4 スイープ（単極 0→16383→0）→ ボタン 0–11 順次 ON/OFF → State/Mode/Error/Preset 巡回（CC 昇順）→ Ping イベント送信」で、終了後ループする。**Mode（CC#115）はスイープせず有効 3 値（110→127→0）のみを送信**し、最後に必ず通常(0)へ復帰する（無効値の大量送信と、非通常モードのまま終わる誤認を防ぐ。全送信をモード名付きでログ）。生成ロジックは `auto_sequencer.py` の `AutoSequencer`（純粋関数・テスト済み）。自動モード中は手動入力を無視し、`M`・`/`・`ESC` のみ有効。MIDI 入力が無い場合、イベントは応答タイムアウト（30 Tick）で次へ進む。

## 開発環境

**重要: このプロジェクトは Python 仮想環境 (venv) を使用します。Python コマンドを実行する前に必ず仮想環境を有効化してください。**

### 仮想環境のセットアップ（必須）
```bash
# 仮想環境を作成
python -m venv .venv

# 有効化 (Windows)
.venv\Scripts\activate

# 有効化 (macOS/Linux)
source .venv/bin/activate

# 無効化
deactivate
```

### 仮想環境の利用ルール
- Python コマンド実行前に**必ず** `.venv` を有効化する
- パッケージをグローバルに**インストールしない** - 仮想環境を使う
- コマンドプロンプトに `(.venv)` が表示されているかで有効状態を確認する
- 開発・テストはすべて仮想環境内で行う

### パッケージ管理
```bash
# 先に仮想環境を有効化 (Windows)
.venv\Scripts\activate

# 実行依存をインストール
pip install -r requirements.txt

# 開発依存（テスト）をインストール
pip install -r requirements-dev.txt

# 不足している依存関係を確認
python setup.py
```

### アプリケーションの実行
```bash
# 先に仮想環境を有効化 (Windows)
.venv\Scripts\activate

# 直接実行
python midi_simulator.py

# セットアップスクリプト経由（インタラクティブ）
python setup.py
```

### テスト
```bash
# 仮想環境内で
pytest
```
`cc_map` / `messaging` / `controller_state` / `auto_sequencer` の純粋ロジックを対象にカバレッジを取得する（`pyproject.toml` で `testpaths=tests`）。MIDI / pygame / 対話 UI はユニットテスト対象外。

## キーボード操作

| キー | 動作 |
| --- | --- |
| `W`/`A`/`S`/`D` | 左スティック 上/左/下/右（十字操作・押下中ランプ） |
| `↑`/`←`/`↓`/`→` | 右スティック 上/左/下/右（十字操作・押下中ランプ） |
| `U`/`J` `I`/`K` `O`/`L` `P`/`;` | スライダー 1–4 増/減（上段=増・下段=減・押下中ランプ） |
| `R` | 全軸を原点へ移動（スティック=8192 / スライダー=0） |
| `1 2 3 4 5 6 7 8 9 0 - ^(JIS)/=(US)` | ボタン 0–11（押下=ON / 離上=OFF） |
| `]`/`[` | Preset +1/−1（CC117・変化時のみ送信） |
| `X`/`Z` | Error +1/−1（CC116・変化時のみ送信） |
| `V`/`C` | State +1/−1（CC114・変化時のみ送信） |
| `B` | Mode 巡回切替 0→110→127→0（CC115・デバッグ用） |
| `G` | イベント送信 Ping（確定イベントは Ping のみ） |
| `M` | 自動デバッグ入力モード ON/OFF（全要素を巡回送信） |
| `/` | ヘルプ再表示 ／ `ESC` 終了 |

> コマンド（Ping/Reset/SetMode/SetZero/SetPreset/SetValve）は Unity から受信し自動で ACK する（キー操作不要）。Mode（CC115）は SetMode コマンド起点・初期通知に加え、`B` キーの手動巡回でも送信できる（コントローラ自身のモード遷移の模擬。非通常モードへ巡回中は SetMode が一方向遷移の制約で REJECTED になる）。

## 依存関係

- **pygame>=2.0.0** - キーボード入力（KEYDOWN/KEYUP）
- **python-rtmidi>=1.4.0** - MIDI 入出力
- **pytest / pytest-cov**（開発・`requirements-dev.txt`）
- **Python 3.7+** - 実行要件

## 主要な設定

- **`TICK_INTERVAL`**: メインループ周期（既定 1/60 秒）
- **`STICK_STEP_PER_TICK`**: 押下中ランプの 1 Tick あたり 14bit 変化量（既定 550）
- **`RESPONSE_TIMEOUT_TICKS`**: イベント応答タイムアウト（既定 30 Tick ≈ 0.5s）
- **`MIDI_CHANNEL`**: 送受信チャンネル（既定 0 = ch1）
- **14bit 範囲**: 0–16383（中央 8192）
- **`AXIS_CENTER`**: 軸の中心点（既定 8192 = `cc_map.CENTER_14BIT`）
- **`AUTO_STICK_STEP` / `AUTO_BUTTON_HOLD_TICKS` / `AUTO_CC_STEP`**: 自動デバッグ入力モードのスイープ速度・ボタン保持・スカラー刻み（既定 550 / 15 / 8）

## UI 機能

起動時にインタラクティブな選択を提供します：
- **自動デバッグ入力**: `M` キーで全 CC を巡回送信するデバッグモードを ON/OFF（起動時の選択 UI は無し）
- **MIDI 出力ポート選択**: 必須（接続後にパラメータ現在値の初期通知を送信）
- **MIDI 入力ポート選択**: 任意（スキップ時はコマンド受信／イベント応答が無効・送信のみ）
- **エラー処理**: ポート未検出・接続失敗を穏当にハンドリング

## MIDI デバイスのセットアップ

アプリケーションは既存の MIDI ポートに接続します。Unity と対向する通常運用では **IN/OUT に 2 ポート**を使います（loopMIDI で 2 ポート作成等。新仕様は IN/OUT で CC 重複が無いため、同一ポートのループバックでも誤注入は起きません）。loopMIDI と DAW の詳細なセットアップ手順は README.md を参照してください。

## コードパターン

- 入力処理は変化検出を用いて MIDI トラフィックを最小化する（パラメータは仕様 §3 の「変化時のみ送信」）
- 14bit 値は 7bit の MSB/LSB ペアに分割する（LSB 先・MSB 後）
- 純粋ロジック（`cc_map` / `messaging` / `controller_state`）を MIDI/pygame から分離してユニットテスト可能にする
- プロトコル層（messaging）とアプリ層（controller_state）を分離し、opcode 処理は validate（ACK 前）/ execute（ACK 後）の 2 段階で注入する
- 受信は別スレッドのため、MIDI 出力と messaging 操作は 1 つのロックで直列化する
- pygame と MIDI リソースを穏当にクリーンアップする
