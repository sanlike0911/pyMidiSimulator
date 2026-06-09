# プロジェクト指示書 (INSTRUCTIONS.md)

このファイルは、このリポジトリで作業する AI コーディングツール（Claude Code / Codex 等 / Gemini CLI）共通の指示書（唯一のソース）です。各ツールの設定ファイル（CLAUDE.md / AGENTS.md / GEMINI.md）はこのファイルを参照します。

## プロジェクト概要

キーボード操作で **MIDI コントローラ役（送信主体）** を演じ、Unity 側 `CkdGameController`（受信側）に対して新 MIDI 仕様の Control Change を送受信する Python シミュレータです。実機 MIDI コントローラやゲームパッドが無くても、スティック / ボタン / Preset / Error / State の送信、コマンド受信＋ACK、イベント送信＋応答待ちを検証できます。

- 対象 MIDI 仕様: `docs/specs/midi-mapping.md`（Unity 受信側の視点で記述）
- 設計書: `docs/superpowers/specs/2026-06-09-controller-sim-new-midi-spec-design.md`

> **重要な視点:** 仕様書は Unity（受信側）の視点で「IN / OUT」を定義しています。本シミュレータはコントローラ役なので**送受信が反転**します（仕様の「IN」= 本アプリの送信、「OUT」= 本アプリの受信）。

## アーキテクチャ

### モジュール構成

- **`midi_simulator.py`** - `ControllerSimulator` クラス（オーケストレーション）。モード/ポート選択 UI、pygame ウィンドウ、メインループ、キー入力処理、HUD ログ、リソース解放。
- **`cc_map.py`** - CC 番号定数・正規化（`norm14_bipolar` / `norm_slider`）・14bit 分割/再構成（`split_14bit` / `combine_14bit`）・seq コーデック（`pack_seq` / `payload_of` / `seq_of`）。MIDI/pygame 非依存の純粋関数。
- **`messaging.py`** - コマンド/イベント I/F ステートマシン（受信コマンド→ACK、イベント送信→応答待ち、seq、タイムアウト）。`send_cc` の注入と `tick()` 駆動で動作。
- **`midi_io.py`** - python-rtmidi のラッパー（ポート列挙・7bit/14bit CC 送信・受信ディスパッチ）。
- **`keyboard_map.py`** - pygame キー → セマンティックアクションのマッピングとヘルプテキスト。
- **`tests/`** - pytest（`cc_map` / `messaging` の純粋ロジックを網羅）。
- **`setup.py`** - 依存確認・インストール・起動を行う補助スクリプト。

### 送受信 CC（コントローラ役視点）

**送信（Sim → Unity / 仕様表の「IN」）**
- スティック: 左X→CC#16/48、左Y→CC#17/49、右X→CC#18/50、右Y→CC#19/51（MSB/LSB、14bit 0–16383、LSB 先・MSB 後）
- ボタン 0–9: CC#20–29（127=押下 / 0=離上、受信側しきい値 64）
- Preset: CC#40（0–127 生値）／ Error: CC#41（0–127 生値）／ State: CC#42（0–127 生値）
- CMDRSP_STATUS: CC#43（受信コマンドへの ACK）
- イベント: EVT_ARG=CC#44 / EVT_OP=CC#45

**受信（Unity → Sim / 仕様表の「OUT」）**
- コマンド: CMD_ARG1=CC#50 / CMD_ARG2=CC#53 / CMD_OP=CC#51（commit）
- EVTRSP_STATUS: CC#52（イベント送信への ACK）

> ⚠️ **CC 番号衝突:** 送信側の右スティック X/Y の LSB（CC#50/51）と、受信側の CMD_ARG1/CMD_OP（CC#50/51）が同番号。MIDI は IN/OUT が独立エンドポイントのため実機（物理 IN/OUT 分離）では無害だが、**単一仮想ポート/ループバックでは自分の送信が誤注入される**。IN/OUT は別ポートにすること（同一選択時は警告）。

### スティック解釈（Stick / Slider）

同じ CC ペア（16/48・17/49・18/50・19/51）を 2 モードで使用（同時併用不可・`M` キーで切替）：
- **Stick**: 中央 8192 を原点に -1.0 … +1.0（双極）
- **Slider**: 0 を原点に 0.0 … 1.0（単極）

送信する 14bit バイト列はモード非依存。モードは原点（リセット先）・表示の正規化・初期値にのみ影響する。

### コマンド/イベント I/F

仕様書セクション7に準拠（seq=bit6、ACK、タイムアウト、応答待ちステートマシン）：
- 受信コマンド: `CMD_ARG1/ARG2` をバッファ → `CMD_OP` 到着で commit、opcode 別処理（Ping/LED/Haptic=OK、SetPreset(4)=内部 Preset 更新、未知=UNKNOWN_OP）→ `CMDRSP_STATUS` で即 ACK（seqEcho 付）→ arg 消費。
- イベント送信: `EVT_ARG`→`EVT_OP`（seq 0↔1 反転）→ `EVTRSP_STATUS` 受信で解決、seq 不一致/保留なしは破棄、30 Tick でタイムアウト・再送可。
- 受信は常時処理（応答待ち中もコマンド受信→即 ACK）。クロスしてもデッドロックしない。

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
`cc_map` / `messaging` の純粋ロジックを対象にカバレッジを取得する（`pyproject.toml` で `testpaths=tests`）。MIDI / pygame / 対話 UI はユニットテスト対象外。

## キーボード操作

| キー | 動作 |
| --- | --- |
| `1`/`2` `3`/`4` `5`/`6` `7`/`8` | 左X / 左Y / 右X / 右Y の +/−（押下中ランプ） |
| `0` | 全軸を原点へ（Stick=8192 / Slider=0） |
| `Q W E F T Y U I O P` | ボタン 0–9（押下=ON / 離上=OFF） |
| `]`/`[` | Preset +1/−1（CC40） |
| `X`/`Z` | Error +1/−1（CC41） |
| `V`/`C` | State +1/−1（CC42） |
| `G` / `B` / `N` | イベント送信 HeartBeat / ButtonCombo / SensorTrigger |
| `M` | Stick ⇔ Slider 切替（軸を原点リセット） |
| `/` | ヘルプ再表示 ／ `ESC` 終了 |

> コマンド（SetPreset 等）は Unity から受信し自動で ACK する（キー操作不要）。

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

## UI 機能

起動時にインタラクティブな選択を提供します：
- **モード選択**: Stick / Slider を選択（`M` キーで動的切替も可）
- **MIDI 出力ポート選択**: 必須
- **MIDI 入力ポート選択**: 任意（スキップ時はコマンド受信／イベント応答が無効・送信のみ）。出力と同一ポート選択時は自己エコー誤注入の警告
- **エラー処理**: ポート未検出・接続失敗を穏当にハンドリング

## MIDI デバイスのセットアップ

アプリケーションは既存の MIDI ポートに接続します。コマンド/イベント I/F を使う場合は **IN/OUT を別ポート**にしてください（loopMIDI で 2 ポート作成等）。loopMIDI と DAW の詳細なセットアップ手順は README.md を参照してください。

## コードパターン

- 入力処理は変化検出を用いて MIDI トラフィックを最小化する
- 14bit 値は 7bit の MSB/LSB ペアに分割する（LSB 先・MSB 後）
- 純粋ロジック（`cc_map` / `messaging`）を MIDI/pygame から分離してユニットテスト可能にする
- 受信は別スレッドのため、MIDI 出力と messaging 操作は 1 つのロックで直列化する
- pygame と MIDI リソースを穏当にクリーンアップする
