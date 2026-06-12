# CC 再配置・Slider 専用帯・ボタン 12 個化への追従設計

- 日付: 2026-06-12
- 対象仕様: `docs/specs/midi-mapping.md`（最終更新 2026-06-12）
- 基盤設計: `2026-06-10-cc-band-and-opcode-redesign-design.md` / `2026-06-09-controller-sim-new-midi-spec-design.md`

## 背景

仕様書の 2026-06-12 改訂で CC 割り当てが全面再配置された。プロトコル規約
（14bit 構成・しきい値 64・seq=bit6・STATUS / opcode 体系・タイムアウト 30 Tick・
送信契機「初期通知＋変化時」）は不変で、変わるのは **CC 番号と入力要素の構成**のみ。

| 項目 | 旧 | 新 |
| --- | --- | --- |
| スティック 4 軸 (MSB/LSB) | 16–19 / 48–51 | **20–23 / 52–55** |
| Slider 1–4 (MSB/LSB) | （Stick と CC 共有・解釈モード切替） | **24–27 / 56–59（専用帯・同時併用可）** |
| Slider 5–8 | — | 28–31 / 60–63（予約・未使用） |
| ボタン | 0–9 = CC 20–29（予約 30–39） | **0–11 = CC 102–113（12 本固定・予約なし）** |
| State / Mode / Error / Preset | 102 / 103 / 104 / 105 | **114 / 115 / 116 / 117** |
| CMD_ARG1 / ARG2 / OP | 110 / 111 / 112 | **85 / 86 / 87** |
| EVTRSP_STATUS | 113 | **89** |
| CMDRSP_STATUS | 114 | **90** |
| EVT_ARG / EVT_OP | 115 / 116 | **118 / 119** |
| CC 88 | — | High Resolution Velocity Prefix のため**割り当て禁止** |

## 設計判断

### 1. CC 定数は `cc_map.py` の更新のみで全層へ波及させる

実装は CC 番号を `cc_map.py` に集約済みで、`controller_state` / `messaging` /
`auto_sequencer` / `midi_simulator` は定数参照・`len(cc_map.BUTTON_CCS)` 連動のため
定数更新が構造的に全層へ波及する。ハードコード箇所は追加しない。

### 2. Slider は「送信機能」として対応する（解釈モードの復活ではない）

旧仕様の Stick/Slider は同一 CC の解釈モード切替（ワイヤ外合意）であり、本シミュレータ
では撤廃済み（2026-06-09-auto-debug-input-mode-design.md）。新仕様の Slider は
**専用 CC ペア帯を持つ独立した入力要素**（Stick と同時併用可）なので、撤廃したモード
切替を戻すのではなく、スティックと並ぶ送信対象として新規追加する:

- `cc_map.SLIDER_CCS = ((24,56),(25,57),(26,58),(27,59))`（使用中の 1–4 のみ。5–8 は
  予約＝未使用のため定数化しない）
- 値は単極（0–16383・初期値 0）。表示正規化に `norm14_unipolar`（`clamp(v/16383, 0..1)`）
  を追加（既存の双極 `norm14_bipolar` と対）
- 手動操作: 押下中ランプ（スティックと同方式）。キーは上段/下段の縦対応で
  **U/J=Slider1・I/K=Slider2・O/L=Slider3・P/;=Slider4**（上段=増 / 下段=減）
- `R` キーと Reset コマンドは「スティック中心化（8192）＋スライダー 0 復帰」に拡張
- 自動デバッグ入力: `Phase.SLIDER` を STICK の直後に追加
  （STICK→**SLIDER**→BUTTON→SCALAR→EVENT）。スイープは単極のため
  0→16383→0 の 2 区間（中心復帰なし）。`ActionKind.SLIDER` を新設

### 3. ボタン 10/11 のキーは数字キー列の右隣 2 キー

ボタン 0–9 は数字キー 1–0 のまま。10/11 は数字列の延長として
**`-`（K_MINUS）= ボタン 10、`^`（K_CARET・JIS）/ `=`（K_EQUALS・US）= ボタン 11**
を割り当てる（JIS / US どちらの配列でも「0 の右隣 2 キー」になるよう両キーコードを
同じボタンへマップ）。

### 4. test_spec_sync は新フォーマットへパーサを追従させる

- ボタン行: ラベル「ボタン 0–9」固定 → 「ボタン 0–N」の N をパースして
  `BUTTON_CCS` の本数・範囲と照合（個数変更も検出対象にする）
- Slider1–4 行の照合テストを追加（`SLIDER_CCS` と照合。予約行 5–8 は未使用のため対象外）
- IN/OUT 重複なし検証の送信集合に Slider CC を追加

## 影響ファイル

| ファイル | 変更 |
| --- | --- |
| `cc_map.py` | CC 定数全面更新・`SLIDER_CCS`/`SLIDER_NAMES`/`norm14_unipolar` 追加 |
| `auto_sequencer.py` | `Phase.SLIDER`・`ActionKind.SLIDER`・単極スイープ追加 |
| `keyboard_map.py` | ボタン 10/11 キー・`SLIDER_KEYS`・ヘルプ更新 |
| `midi_simulator.py` | スライダー状態/ランプ/ログ・R/Reset の 0 復帰・自動アクション分岐 |
| `controller_state.py` | docstring の CC 番号表記のみ（ロジック不変） |
| `tests/test_spec_sync.py` | ボタン 0–N パース・Slider 照合・重複検証の拡張 |
| `tests/test_cc_map.py` | `norm14_unipolar` テスト追加 |
| `tests/test_auto_sequencer.py` | SLIDER フェーズのテスト追加・遷移 assert 更新 |
| `INSTRUCTIONS.md` / `README.md` | CC 表記・キー操作表・自動モード説明の更新 |

## 不変条件（変更しないこと）

- プロトコル規約値: しきい値 64 / seq=bit6（値 64）/ タイムアウト 30 Tick / opcode・STATUS 体系
- 「検証 → ACK → 実行」の処理順・変化時のみ送信・初期通知
- Mode 巡回（B キー・自動モードの有効 3 値送信）の挙動
- スティックの双極解釈・中心点 8192
