#!/usr/bin/env python3
"""MIDI Controller Simulator（コントローラ役・キーボード操作）。

新 MIDI 仕様（docs/specs/midi-mapping.md）に対応。実機 MIDI コントローラの代わりに、
キーボードから スティック / ボタン / Preset / Error / State を送信し、Unity が送る
コマンド（SetPreset 等）を受信して ACK を返し、イベントを送信して応答を待つ。

設計: docs/superpowers/specs/2026-06-09-controller-sim-new-midi-spec-design.md
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional

import pygame

import cc_map
import keyboard_map
import midi_io as midi_io_mod
from auto_sequencer import ActionKind, AutoSequencer, SendAction
from messaging import Messaging, MessagingState

TICK_INTERVAL = 1.0 / 60.0
# 押下中ランプの 1 Tick あたりの 14bit 変化量（約 0.5 秒でフルスケール）
STICK_STEP_PER_TICK = 550
# 軸の中心点（初期値・"R" キー・自動モード遷移時の移動先）
AXIS_CENTER = cc_map.CENTER_14BIT
# --- 自動デバッグ入力モードのパラメータ ---
AUTO_STICK_STEP = 550        # スティックスイープの 1 Tick あたり 14bit 変化量
AUTO_BUTTON_HOLD_TICKS = 15  # 各ボタンの ON 保持 Tick 数（≒0.25s @60fps）
AUTO_CC_STEP = 8             # Preset/Error/State スイープの刻み（0→127 を約16段）

# pygame ウィンドウ HUD 用フォント。デフォルト(SysFont(None))は日本語グリフを持たず
# 文字化けするため、日本語対応フォントを OS 横断の候補から探索する。
HUD_FONT_SIZE = 22
JP_FONT_CANDIDATES = (
    "Yu Gothic UI", "Meiryo", "MS Gothic",            # Windows
    "Hiragino Sans", "Hiragino Kaku Gothic Pro",      # macOS
    "Noto Sans CJK JP", "IPAGothic", "VL Gothic",     # Linux
)


def load_jp_font(size: int) -> pygame.font.Font:
    """日本語グリフを持つシステムフォントを探して返す。

    候補を順に試し、'あ' のグリフを持つ最初のフォントを採用する。どれも見つからなければ
    pygame デフォルト（日本語非対応）にフォールバックする。pygame.font.init() 済みが前提。
    """
    for name in JP_FONT_CANDIDATES:
        path = pygame.font.match_font(name)
        if not path:
            continue
        font = pygame.font.Font(path, size)
        metrics = font.metrics("あ")
        if metrics and metrics[0] is not None:
            return font
    return pygame.font.SysFont(None, size)


class ControllerSimulator:
    """コントローラ役の MIDI シミュレータ本体。"""

    def __init__(self) -> None:
        self._midi = midi_io_mod.MidiIO()
        self._messaging = Messaging(self._midi.send_cc)
        self._lock = threading.Lock()  # MIDI 出力と messaging を直列化（受信は別スレッド）
        self._running = True

        self._axis_raw: List[int] = [AXIS_CENTER] * 4
        self._axis_sent: List[int] = [-1] * 4  # -1 = 未送信（初回必ず送る）

        self._buttons = [False] * len(cc_map.BUTTON_CCS)
        self._preset = 0
        self._error = 0
        self._state = 0
        self._event_arg = 0

        self._help_requested = False
        self._auto_mode = False
        self._auto: Optional[AutoSequencer] = None
        self._prev_command = None
        self._prev_event_response = None

    # --- 起動シーケンス -----------------------------------------------------
    def run(self) -> None:
        """対話セットアップ後にメインループを回す。"""
        try:
            print("MIDI Controller Simulator - 新仕様対応（コントローラ役）")
            print("=" * 56)
            if not self._setup_ports():
                return
            self._init_window()
            print(keyboard_map.help_text())
            print("-" * 56)
            self._loop()
        except KeyboardInterrupt:
            print("\n終了します...")
        finally:
            self._cleanup()

    def _setup_ports(self) -> bool:
        """出力ポート（必須）と入力ポート（任意）を選択して開く。"""
        out_ports = midi_io_mod.list_output_ports()
        if not out_ports:
            print("利用可能な MIDI 出力ポートがありません")
            return False
        out_idx = self._select_port(out_ports, "MIDI 出力ポート", allow_skip=False)
        if out_idx is None:
            return False
        self._midi.open_output(out_idx)
        print(f"出力ポート '{out_ports[out_idx]}' に接続しました")

        in_ports = midi_io_mod.list_input_ports()
        in_idx = (
            self._select_port(in_ports, "MIDI 入力ポート", allow_skip=True)
            if in_ports
            else None
        )
        if in_idx is None:
            print("MIDI 入力なし: コマンド受信／イベント応答は無効です（送信のみ）")
            return True
        if in_ports[in_idx] == out_ports[out_idx]:
            print(
                "⚠ 警告: 入力と出力が同一ポートです。自分が送る右スティック LSB(CC50/51) 等が\n"
                "  コマンド引数(CMD_ARG1/ARG2)として自プロセスへ誤注入される恐れがあります。IN/OUT は別ポート推奨。"
            )
        self._midi.open_input(in_idx, self._on_cc_received)
        print(f"入力ポート '{in_ports[in_idx]}' に接続しました")
        return True

    def _select_port(self, ports: List[str], label: str, allow_skip: bool) -> Optional[int]:
        """ポート一覧を表示してインデックスを選ばせる。allow_skip なら Enter で None。"""
        print(f"\n利用可能な {label}:")
        for i, port in enumerate(ports):
            print(f"  {i}: {port}")
        suffix = ", Enter=スキップ): " if allow_skip else "): "
        while True:
            choice = input(f"{label} を選択 (0-{len(ports) - 1}{suffix}").strip()
            if allow_skip and choice == "":
                return None
            try:
                idx = int(choice)
            except ValueError:
                print("数字を入力してください。")
                continue
            if 0 <= idx < len(ports):
                return idx
            print(f"0-{len(ports) - 1} の範囲で入力してください。")

    def _init_window(self) -> None:
        """キー入力フォーカス用の小さな pygame ウィンドウを開く。"""
        pygame.init()
        screen = pygame.display.set_mode((520, 130))
        pygame.display.set_caption("MIDI Controller Simulator")
        font = load_jp_font(HUD_FONT_SIZE)
        screen.fill((18, 18, 26))
        lines = [
            "MIDI Controller Simulator (controller role)",
            "このウィンドウにフォーカスしてキー操作してください",
            "状態はコンソールに表示   '/'=ヘルプ   ESC=終了",
        ]
        for i, text in enumerate(lines):
            screen.blit(font.render(text, True, (225, 225, 235)), (14, 16 + i * 30))
        pygame.display.flip()

    # --- メインループ -------------------------------------------------------
    def _loop(self) -> None:
        """100Hz 弱の Tick でキー処理・軸ランプ・タイムアウト判定・表示を行う。"""
        while self._running:
            events = pygame.event.get()
            pressed = pygame.key.get_pressed()
            with self._lock:
                for event in events:
                    self._apply_event(event)
                if self._auto_mode:
                    self._tick_auto()
                else:
                    self._ramp_axes(pressed)
                self._messaging.tick()
                snapshot = self._messaging.snapshot()
            if self._help_requested:
                print(keyboard_map.help_text())
                self._help_requested = False
            self._log_incoming_changes(snapshot)
            time.sleep(TICK_INTERVAL)

    def _apply_event(self, event: pygame.event.Event) -> None:
        """1 つの pygame イベントを処理する（lock 内で呼ばれる）。"""
        if event.type == pygame.QUIT:
            self._running = False
        elif event.type == pygame.KEYDOWN:
            self._on_keydown(event.key)
        elif event.type == pygame.KEYUP:
            self._on_keyup(event.key)

    def _on_keydown(self, key: int) -> None:
        """KEYDOWN: 自動モード中は M/ヘルプ/終了のみ受理。手動時はボタン/離散/イベント/中心点移動。"""
        allowed_in_auto = (keyboard_map.AUTO_MODE_KEY, keyboard_map.HELP_KEY, keyboard_map.QUIT_KEY)
        if self._auto_mode and key not in allowed_in_auto:
            return
        if key in keyboard_map.BUTTON_KEYS:
            idx = keyboard_map.BUTTON_KEYS[key]
            self._buttons[idx] = True
            self._midi.send_cc(cc_map.BUTTON_CCS[idx], 127)
            print(f"ボタン{idx}: ON")
        elif key in keyboard_map.PRESET_KEYS:
            self._preset = cc_map.clamp(self._preset + keyboard_map.PRESET_KEYS[key], 0, cc_map.MAX_7BIT)
            self._midi.send_cc(cc_map.PRESET_CC, self._preset)
            print(f"Preset 送信: {self._preset}")
        elif key in keyboard_map.ERROR_KEYS:
            self._error = cc_map.clamp(self._error + keyboard_map.ERROR_KEYS[key], 0, cc_map.MAX_7BIT)
            self._midi.send_cc(cc_map.ERROR_CC, self._error)
            print(f"Error 送信: {self._error}")
        elif key in keyboard_map.STATE_KEYS:
            self._state = cc_map.clamp(self._state + keyboard_map.STATE_KEYS[key], 0, cc_map.MAX_7BIT)
            self._midi.send_cc(cc_map.STATE_CC, self._state)
            print(f"State 送信: {self._state}")
        elif key in keyboard_map.EVENT_KEYS:
            self._send_event(keyboard_map.EVENT_KEYS[key])
        elif key == keyboard_map.AXIS_RESET_KEY:
            self._center_axes()
        elif key == keyboard_map.AUTO_MODE_KEY:
            self._toggle_auto_mode()
        elif key == keyboard_map.HELP_KEY:
            self._help_requested = True
        elif key == keyboard_map.QUIT_KEY:
            self._running = False

    def _on_keyup(self, key: int) -> None:
        """KEYUP: 自動モード中は無視。手動時はボタン OFF と軸キー離上時の最終値ログ。"""
        if self._auto_mode:
            return
        if key in keyboard_map.BUTTON_KEYS:
            idx = keyboard_map.BUTTON_KEYS[key]
            self._buttons[idx] = False
            self._midi.send_cc(cc_map.BUTTON_CCS[idx], 0)
            print(f"ボタン{idx}: OFF")
        elif key in keyboard_map.AXIS_KEYS:
            self._log_axis(keyboard_map.AXIS_KEYS[key][0])

    def _send_event(self, opcode: int) -> None:
        """イベントを送信する（arg は送信ごとに増えるカウンタ）。"""
        arg = self._event_arg
        self._event_arg = (self._event_arg + 1) & 0x7F
        if self._messaging.send_event(opcode, arg):
            print(f"イベント送信: op={opcode} arg={arg}")
        else:
            print("イベント送信を抑止: 前のイベントが応答待ちです")

    def _ramp_axes(self, pressed) -> None:
        """押下中キーに応じて軸をランプし、変化した軸だけ 14bit CC を送信する。"""
        directions = [0, 0, 0, 0]
        for key, (axis, delta) in keyboard_map.AXIS_KEYS.items():
            if pressed[key]:
                directions[axis] += delta
        for axis in range(4):
            if directions[axis] != 0:
                self._axis_raw[axis] = cc_map.clamp(
                    self._axis_raw[axis] + directions[axis] * STICK_STEP_PER_TICK,
                    0,
                    cc_map.MAX_14BIT,
                )
            if self._axis_raw[axis] != self._axis_sent[axis]:
                msb_cc, lsb_cc = cc_map.CC_AXES[axis]
                self._midi.send_14bit(msb_cc, lsb_cc, self._axis_raw[axis])
                self._axis_sent[axis] = self._axis_raw[axis]

    def _toggle_auto_mode(self) -> None:
        """自動デバッグ入力モードを ON/OFF し、軸を中心点・全ボタン OFF に整える。"""
        self._auto_mode = not self._auto_mode
        if self._auto_mode:
            self._auto = AutoSequencer(AUTO_STICK_STEP, AUTO_BUTTON_HOLD_TICKS, AUTO_CC_STEP)
        self._all_buttons_off()
        self._center_axes()
        print(f"自動デバッグ入力: {'ON' if self._auto_mode else 'OFF'}")

    def _all_buttons_off(self) -> None:
        """押下中の全ボタンを OFF 送信する（自動/手動の残留点灯を解消）。"""
        for idx in range(len(cc_map.BUTTON_CCS)):
            if self._buttons[idx]:
                self._buttons[idx] = False
                self._midi.send_cc(cc_map.BUTTON_CCS[idx], 0)

    def _tick_auto(self) -> None:
        """自動シーケンスを 1 Tick 進め、返ったアクションを送信する。"""
        event_pending = self._messaging.snapshot().event_pending
        for action in self._auto.tick(event_pending):
            self._dispatch_auto_action(action)

    def _dispatch_auto_action(self, action: SendAction) -> None:
        """AutoSequencer の SendAction を実際の MIDI 送信に変換する。"""
        if action.kind is ActionKind.AXIS:
            msb_cc, lsb_cc = cc_map.CC_AXES[action.target]
            self._midi.send_14bit(msb_cc, lsb_cc, action.value)
            self._axis_raw[action.target] = action.value
            self._axis_sent[action.target] = action.value
        elif action.kind is ActionKind.BUTTON:
            self._buttons[action.target] = action.value > 0
            self._midi.send_cc(cc_map.BUTTON_CCS[action.target], action.value)
        elif action.kind is ActionKind.SCALAR:
            self._sync_scalar(action.target, action.value)
            self._midi.send_cc(action.target, action.value)
        elif action.kind is ActionKind.EVENT:
            self._messaging.send_event(action.target, action.value)
        if action.log:
            print(f"[AUTO] {action.log}")

    def _sync_scalar(self, cc: int, value: int) -> None:
        """自動送信したスカラー値を内部状態へ反映し、手動復帰時の整合を保つ。"""
        if cc == cc_map.PRESET_CC:
            self._preset = value
        elif cc == cc_map.ERROR_CC:
            self._error = value
        elif cc == cc_map.STATE_CC:
            self._state = value

    def _center_axes(self) -> None:
        """全軸を中心点(8192)へ移動して送信する。"""
        for axis in range(4):
            self._axis_raw[axis] = AXIS_CENTER
            msb_cc, lsb_cc = cc_map.CC_AXES[axis]
            self._midi.send_14bit(msb_cc, lsb_cc, AXIS_CENTER)
            self._axis_sent[axis] = AXIS_CENTER
        print(f"全軸を中心点 {AXIS_CENTER} へ移動")

    def _log_axis(self, axis: int) -> None:
        """軸の現在値と正規化値（双極 -1..+1）をログ出力する。"""
        raw = self._axis_raw[axis]
        norm = cc_map.norm14_bipolar(raw)
        print(f"{cc_map.AXIS_NAMES[axis]}: {raw} ({norm:+.3f})")

    # --- 受信処理（別スレッド → lock）-------------------------------------
    def _on_cc_received(self, cc: int, value: int) -> None:
        """MIDI 入力コールバック（別スレッド）。messaging へ委譲する。"""
        with self._lock:
            self._messaging.handle_incoming_cc(cc, value)

    def _log_incoming_changes(self, snapshot: MessagingState) -> None:
        """受信コマンド・イベント応答が更新されていればログ出力する。"""
        if snapshot.last_command is not None and snapshot.last_command is not self._prev_command:
            cmd = snapshot.last_command
            print(
                f"[受信コマンド] op={cmd.opcode} arg1={cmd.arg1} arg2={cmd.arg2}"
                f" -> status={cmd.status} (seqEcho={cmd.seq})"
            )
            # SetPreset(OK) 受信時は送信用 Preset も受信値へ同期する。
            # これにより以降の ]/[ が受信した Preset を基準に増減し、送信用と受信値の二重管理を防ぐ。
            if cmd.opcode == cc_map.CMD_SET_PRESET and cmd.status == cc_map.STATUS_OK:
                self._preset = cmd.arg1
                print(f"  → 送信用 Preset を {self._preset} に同期")
            self._prev_command = snapshot.last_command
        response = snapshot.last_event_response
        if response is not None and response is not self._prev_event_response:
            if response.timed_out:
                print(f"[イベント応答] op={response.opcode} seq={response.seq} -> タイムアウト")
            else:
                print(f"[イベント応答] op={response.opcode} seq={response.seq} -> status={response.status}")
            self._prev_event_response = response

    def _cleanup(self) -> None:
        """MIDI / pygame リソースを解放する。"""
        self._midi.close()
        if pygame.get_init():
            pygame.quit()
        print("リソースを解放しました")


def main() -> None:
    ControllerSimulator().run()


if __name__ == "__main__":
    main()
