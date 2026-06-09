# MIDI Controller Simulator - Python版

キーボード操作で **MIDI コントローラ役（送信主体）** を演じ、Unity 側 `CkdGameController`（受信側）に対して新 MIDI 仕様の Control Change を送受信する Python アプリケーションです。実機 MIDI コントローラやゲームパッドが無くても、スティック / ボタン / Preset / Error / State の送信、コマンド受信＋ACK、イベント送信＋応答待ちを検証できます。

- 対象 MIDI 仕様: `docs/specs/midi-mapping.md`（Unity 受信側の視点で記述）
- 設計書: `docs/superpowers/specs/2026-06-09-controller-sim-new-midi-spec-design.md`

> **送受信の向き:** 仕様書は Unity（受信側）の視点で「IN / OUT」を定義しています。本アプリはコントローラ役なので**送受信が反転**します（仕様の「IN」= 本アプリの送信、「OUT」= 本アプリの受信）。

## 仕様

### スティック軸（14bit・送信）

| 軸 | MSB CC | LSB CC |
|---|--------|--------|
| 左スティック X | CC#16 | CC#48 |
| 左スティック Y | CC#17 | CC#49 |
| 右スティック X | CC#18 | CC#50 |
| 右スティック Y | CC#19 | CC#51 |

- **分解能**: 各軸 16,384 段階（2^14）、値域 0–16383、中央 8192
- **送信順序**: LSB（下位7bit）→ MSB（上位7bit）
- **解釈モード**（`M` キーで切替・同時併用不可）
  - **Stick**: 中央 8192 を原点に -1.0 … +1.0（双極）
  - **Slider**: 0 を原点に 0.0 … 1.0（単極）

### ボタン・Preset・Error・State（送信）

- **ボタン 0–9**: CC#20–29（押下=127 / 離上=0、受信側しきい値 64）
- **Preset**: CC#40（0–127 生値）
- **Error**: CC#41（0–127 生値）
- **State**: CC#42（0–127 生値）

### コマンド/イベント I/F（双方向）

仕様書セクション7に準拠（seq=bit6、ACK、タイムアウト、応答待ちステートマシン）。

- **コマンド受信**（Unity → 本アプリ）: `CMD_ARG1`=CC#50 / `CMD_ARG2`=CC#53 / `CMD_OP`=CC#51 を受信し、`CMDRSP_STATUS`=CC#43 で即 ACK。SetPreset(op4) は内部 Preset を更新。
- **イベント送信**（本アプリ → Unity）: `EVT_ARG`=CC#44 / `EVT_OP`=CC#45 を送信し、`EVTRSP_STATUS`=CC#52 で応答を受信。

## インストールと実行

### 仮想環境を使用（推奨）

```bash
# 1. 仮想環境の作成
python -m venv .venv

# 2. アクティベート（Windows）
.venv\Scripts\activate
#    アクティベート（macOS/Linux）
source .venv/bin/activate

# 3. 実行依存のインストール
pip install -r requirements.txt

# 4. アプリケーションの実行
python midi_simulator.py

# 5. 仮想環境の終了
deactivate
```

### 自動セットアップ

```bash
python setup.py
```

### テスト

```bash
pip install -r requirements-dev.txt
pytest
```

`cc_map` / `messaging` の純粋ロジックを対象にカバレッジを取得します（MIDI / pygame / 対話 UI はユニットテスト対象外）。

## キーボード操作

操作対象は起動時に開く小さなウィンドウです（**このウィンドウにフォーカス**してキー入力してください）。状態はコンソールに表示されます。

| キー | 動作 |
|------|------|
| `1`/`2` `3`/`4` `5`/`6` `7`/`8` | 左X / 左Y / 右X / 右Y の +/−（押下中ランプ） |
| `0` | 全軸を原点へ（Stick=8192 / Slider=0） |
| `Q W E F T Y U I O P` | ボタン 0–9（押下=ON / 離上=OFF） |
| `]`/`[` | Preset +1/−1（CC40） |
| `X`/`Z` | Error +1/−1（CC41） |
| `V`/`C` | State +1/−1（CC42） |
| `G` / `B` / `N` | イベント送信 HeartBeat / ButtonCombo / SensorTrigger |
| `M` | Stick ⇔ Slider 切替（軸を原点リセット） |
| `/` | ヘルプ再表示 ／ `ESC` 終了 |

> コマンド（SetPreset 等）は Unity から受信し自動で ACK します（キー操作不要）。

## 必要な環境

- Python 3.7 以降
- MIDI デバイスまたは仮想 MIDI ポート（**コマンド/イベント I/F を使う場合は IN/OUT 別ポート**）

## 依存パッケージ

- `pygame` - キーボード入力（KEYDOWN/KEYUP）
- `python-rtmidi` - MIDI 入出力
- `pytest` / `pytest-cov` - テスト（開発）

## 使用方法

1. **仮想 MIDI ポートの準備**（下記「Windows での MIDI 設定」参照）。I/F を使うなら IN/OUT 2 ポート。
2. **起動**: `python midi_simulator.py`
3. **モード選択**: Stick / Slider を選択
4. **MIDI 出力ポート選択**: 送信先を選ぶ（必須）
5. **MIDI 入力ポート選択**: コマンド受信/イベント応答を使うなら選ぶ（任意・Enter でスキップ）
6. **キー操作**: 開いたウィンドウにフォーカスして上記キーで操作
7. **終了**: `ESC` または `Ctrl+C`

## 設定項目

コード内（定数）で調整可能：

- `TICK_INTERVAL`: メインループ周期（既定 1/60 秒）
- `STICK_STEP_PER_TICK`: 押下中ランプの 1 Tick あたり 14bit 変化量（既定 550）
- `RESPONSE_TIMEOUT_TICKS`: イベント応答タイムアウト（既定 30 Tick ≈ 0.5s）
- `MIDI_CHANNEL`: 送受信チャンネル（既定 0 = ch1）
- CC 番号: `cc_map.py` の定数

## Windows での MIDI デバイス認識設定

### 仮想 MIDI ポートの作成（loopMIDI 推奨）

1. [Tobias Erichsen's loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) をダウンロード・インストール
2. loopMIDI を起動し、ポートを作成
   - **送信用**（例「Sim Out」）と**受信用**（例「Sim In」）の **2 ポート**を作成（I/F を使う場合）
   - 単方向（送信のみ）なら 1 ポートで可
3. 本アプリ起動時に、出力＝「Sim Out」、入力＝「Sim In」を選択
4. Unity / DAW 側では「Sim Out」を入力、「Sim In」を出力として設定

> **同一ポートの注意:** 出力と入力に同じポートを選ぶと、自分が送る右スティック LSB（CC#50/51）が
> コマンド（CMD_ARG1/CMD_OP）として自プロセスへ誤注入される恐れがあります（起動時に警告）。IN/OUT は別ポート推奨。

### DAW での設定例

#### Reaper
1. Options > Preferences > Audio > MIDI Devices
2. Input devices で送信用ポートを有効化
3. 新規トラックの入力を送信用ポートに設定

#### FL Studio
1. Options > MIDI Settings
2. Input で送信用ポートを選択して Enable

#### Ableton Live
1. Options > Preferences > Link/Tempo/MIDI
2. MIDI Ports で送信用ポートの Track / Remote を有効化

## トラブルシューティング

### キー入力が効かない場合
1. 起動時に開く pygame ウィンドウにフォーカスしているか確認
2. 別アプリがキーを奪っていないか確認

### MIDI が送信されない場合
1. 仮想 MIDI ポート（loopMIDI）が作成・起動されているか確認
2. 出力ポートの選択が正しいか確認
3. 受信側（Unity/DAW）の MIDI 入力設定を確認

### コマンド受信／イベント応答が来ない場合
1. MIDI 入力ポートを選択したか確認（スキップすると受信無効）
2. IN/OUT が別ポートになっているか確認

### インストールエラーの場合
1. Python バージョンを確認（3.7 以降）
2. Visual C++ Build Tools のインストール（Windows・python-rtmidi のビルド用）

## MIDI CC 一覧

| CC | 用途 | 方向（本アプリ視点） |
|----|------|----------------------|
| 16/48・17/49・18/50・19/51 | 左X / 左Y / 右X / 右Y（MSB/LSB） | 送信 |
| 20–29 | ボタン 0–9 | 送信 |
| 40 / 41 / 42 | Preset / Error / State（0–127） | 送信 |
| 43 | CMDRSP_STATUS（コマンド ACK） | 送信 |
| 44 / 45 | EVT_ARG / EVT_OP（イベント） | 送信 |
| 50 / 53 | CMD_ARG1 / CMD_ARG2 | 受信 |
| 51 | CMD_OP（commit） | 受信 |
| 52 | EVTRSP_STATUS（イベント ACK） | 受信 |

## 実行例

```
MIDI Controller Simulator - 新仕様対応（コントローラ役）
========================================================

スティック解釈モード:
  1: Stick （双極・中央 8192 基準・-1.0 … +1.0）
  2: Slider（単極・0 基準・0.0 … 1.0）
モードを選択してください (1-2): 1
モード: stick（原点 8192）

利用可能な MIDI 出力ポート:
  0: Microsoft GS Wavetable Synth 0
  1: Sim Out
MIDI 出力ポート を選択 (0-1): 1
出力ポート 'Sim Out' に接続しました

利用可能な MIDI 入力ポート:
  0: Sim In
MIDI 入力ポート を選択 (0-0, Enter=スキップ): 0
入力ポート 'Sim In' に接続しました
--------------------------------------------------------
ボタン0: ON
ボタン0: OFF
Preset 送信: 1
左X: 12000 (+0.466)
[受信コマンド] op=4 arg1=100 arg2=0 -> status=0 (seqEcho=1)
イベント送信: op=0 arg=0
[イベント応答] op=0 seq=0 -> status=0
```

## 動作環境

- Windows 10/11
- macOS 10.14 以降
- Linux (Ubuntu 18.04 以降推奨)
- Python 3.7 以降
