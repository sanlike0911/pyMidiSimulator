# MIDI Controller Simulator - Python版

キーボード操作で **MIDI コントローラ役（送信主体）** を演じ、Unity 側 `CkdGameController`（受信側）に対して新 MIDI 仕様の Control Change を送受信する Python アプリケーションです。実機 MIDI コントローラやゲームパッドが無くても、スティック / ボタン / State / Mode / Error / Preset の送信、コマンド受信＋ACK、イベント送信＋応答待ちを検証できます。

- 対象 MIDI 仕様: `docs/specs/midi-mapping.md`（Unity 受信側の視点で記述）
- 設計書: `docs/superpowers/specs/2026-06-10-cc-band-and-opcode-redesign-design.md`

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
- **解釈**: 中心点 8192 基準の双極値（-1.0 … +1.0）。`R` キーで全軸を中心点へ移動

### ボタン・パラメータ（送信）

- **ボタン 0–9**: CC#20–29（押下=127 / 離上=0、受信側しきい値 64）
- **パラメータ**（0–127 生値・接続直後の初期通知＋値の変化時のみ送信）
  - **State**: CC#102
  - **Mode**: CC#103（0=通常 / 110=バージョンアップ / 127=出荷検査。SetMode 起点・初期通知のほか `B` キーで手動巡回送信可）
  - **Error**: CC#104
  - **Preset**: CC#105

### コマンド/イベント I/F（双方向）

仕様書セクション5に準拠（seq=bit6、ACK、タイムアウト、応答待ちステートマシン）。

- **コマンド受信**（Unity → 本アプリ）: `CMD_ARG1`=CC#110 / `CMD_ARG2`=CC#111 / `CMD_OP`=CC#112 を受信し、「検証 → `CMDRSP_STATUS`=CC#114 で ACK → 実行」の順で処理。
  - 対応 opcode: Ping(0) / Reset(1) / SetMode(2) / SetZero(3) / SetPreset(4) / SetValve(5)
  - SetPreset は変化時のみ内部 Preset を更新し CC#105 で新値を通知。SetMode は変化時 CC#103 を通知（一方向遷移）
- **イベント送信**（本アプリ → Unity）: 確定イベントは **Ping(0) のみ**。`EVT_OP`=CC#116 を送信し（ARG 未使用のため `EVT_ARG`=CC#115 は省略）、`EVTRSP_STATUS`=CC#113 で応答を受信。

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

`cc_map` / `messaging` / `controller_state` / `auto_sequencer` の純粋ロジックを対象にカバレッジを取得します（MIDI / pygame / 対話 UI はユニットテスト対象外）。

## キーボード操作

操作対象は起動時に開く小さなウィンドウです（**このウィンドウにフォーカス**してキー入力してください）。状態はコンソールに表示されます。

| キー | 動作 |
|------|------|
| `W`/`A`/`S`/`D` | 左スティック 上/左/下/右（十字操作・押下中ランプ） |
| `↑`/`←`/`↓`/`→` | 右スティック 上/左/下/右（十字操作・押下中ランプ） |
| `R` | 全軸を中心点へ移動（8192） |
| `1 2 3 4 5 6 7 8 9 0` | ボタン 0–9（押下=ON / 離上=OFF） |
| `]`/`[` | Preset +1/−1（CC105・変化時のみ送信） |
| `X`/`Z` | Error +1/−1（CC104・変化時のみ送信） |
| `V`/`C` | State +1/−1（CC102・変化時のみ送信） |
| `B` | Mode 巡回切替 0→110→127→0（CC103・デバッグ用） |
| `G` | イベント送信 Ping（確定イベントは Ping のみ） |
| `M` | 自動デバッグ入力モード ON/OFF（全要素を巡回送信。Mode は有効 3 値のみ） |
| `/` | ヘルプ再表示 ／ `ESC` 終了 |

> コマンド（Ping/Reset/SetMode/SetZero/SetPreset/SetValve）は Unity から受信し自動で ACK します（キー操作不要）。

## 必要な環境

- Python 3.7 以降
- MIDI デバイスまたは仮想 MIDI ポート（Unity と対向する通常運用では IN/OUT に 2 ポート）

## 依存パッケージ

- `pygame` - キーボード入力（KEYDOWN/KEYUP）
- `python-rtmidi` - MIDI 入出力
- `pytest` / `pytest-cov` - テスト（開発）

## 使用方法

1. **仮想 MIDI ポートの準備**（下記「Windows での MIDI 設定」参照）。I/F を使うなら IN/OUT 2 ポート。
2. **起動**: `python midi_simulator.py`
3. **MIDI 出力ポート選択**: 送信先を選ぶ（必須・接続後にパラメータ現在値を初期通知）
4. **MIDI 入力ポート選択**: コマンド受信/イベント応答を使うなら選ぶ（任意・Enter でスキップ）
5. **キー操作**: 開いたウィンドウにフォーカスして上記キーで操作
6. **終了**: `ESC` または `Ctrl+C`

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

> **参考:** 新仕様は IN/OUT で CC 番号の重複が無いため、同一ポートのループバックでも
> 自分の送信が他入力として誤注入されることはありません（受信処理対象は CC#110–113 のみ）。

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
| 102 / 103 / 104 / 105 | State / Mode / Error / Preset（0–127） | 送信 |
| 110 / 111 | CMD_ARG1 / CMD_ARG2 | 受信 |
| 112 | CMD_OP（commit） | 受信 |
| 113 | EVTRSP_STATUS（イベント ACK） | 受信 |
| 114 | CMDRSP_STATUS（コマンド ACK） | 送信 |
| 115 / 116 | EVT_ARG / EVT_OP（イベント） | 送信 |

## 実行例

```
MIDI Controller Simulator - 新仕様対応（コントローラ役）
========================================================

利用可能な MIDI 出力ポート:
  0: Microsoft GS Wavetable Synth 0
  1: Sim Out
MIDI 出力ポート を選択 (0-1): 1
出力ポート 'Sim Out' に接続しました

利用可能な MIDI 入力ポート:
  0: Sim In
MIDI 入力ポート を選択 (0-0, Enter=スキップ): 0
入力ポート 'Sim In' に接続しました
初期通知: State=0 Mode=0 Error=0 Preset=0
--------------------------------------------------------
ボタン0: ON
ボタン0: OFF
Preset 送信: 1
左X: 12000 (+0.466)
[受信コマンド] SetPreset(op=4) arg1=100 arg2=0 -> status=0 (seqEcho=1)
[SetPreset] Preset=100（CC105 で新値を通知）
イベント送信: Ping(op=0)
[イベント応答] op=0 seq=0 -> status=0
```

## 動作環境

- Windows 10/11
- macOS 10.14 以降
- Linux (Ubuntu 18.04 以降推奨)
- Python 3.7 以降
