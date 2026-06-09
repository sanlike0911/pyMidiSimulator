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
from messaging import Messaging, MessagingState

TICK_INTERVAL = 1.0 / 60.0
# 押下中ランプの 1 Tick あたりの 14bit 変化量（約 0.5 秒でフルスケール）
STICK_STEP_PER_TICK = 550
MODE_ORIGIN = {"stick": cc_map.CENTER_14BIT, "slider": 0}


class ControllerSimulator:
    """コントローラ役の MIDI シミュレータ本体。"""

    def __init__(self) -> None:
        self._midi = midi_io_mod.MidiIO()
        self._messaging = Messaging(self._midi.send_cc)
        self._lock = threading.Lock()  # MIDI 出力と messaging を直列化（受信は別スレッド）
        self._running = True

        self._mode = "stick"
        origin = MODE_ORIGIN[self._mode]
        self._axis_raw: List[int] = [origin] * 4
        self._axis_sent: List[int] = [-1] * 4  # -1 = 未送信（初回必ず送る）

        self._buttons = [False] * len(cc_map.BUTTON_CCS)
        self._preset = 0
        self._error = 0
        self._state = 0
        self._event_arg = 0

        self._help_requested = False
        self._prev_command = None
        self._prev_event_response = None

    # --- 起動シーケンス -----------------------------------------------------
    def run(self) -> None:
        """対話セットアップ後にメインループを回す。"""
        try:
            print("MIDI Controller Simulator - 新仕様対応（コントローラ役）")
            print("=" * 56)
            self._select_mode()
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

    def _select_mode(self) -> None:
        """Stick / Slider のスティック解釈モードを選択する。"""
        print("\nスティック解釈モード:")
        print("  1: Stick （双極・中央 8192 基準・-1.0 … +1.0）")
        print("  2: Slider（単極・0 基準・0.0 … 1.0）")
        while True:
            choice = input("モードを選択してください (1-2): ").strip()
            if choice == "1":
                self._mode = "stick"
                break
            if choice == "2":
                self._mode = "slider"
                break
            print("1 または 2 を入力してください。")
        origin = MODE_ORIGIN[self._mode]
        self._axis_raw = [origin] * 4
        print(f"モード: {self._mode}（原点 {origin}）")

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
                "  コマンドとして自プロセスへ誤注入される恐れがあります。IN/OUT は別ポート推奨。"
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
        font = pygame.font.SysFont(None, 22)
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
        """KEYDOWN: ボタン ON・離散 ±1・イベント送信・モード切替などを処理する。"""
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
            self._reset_axes()
        elif key == keyboard_map.TOGGLE_MODE_KEY:
            self._toggle_mode()
        elif key == keyboard_map.HELP_KEY:
            self._help_requested = True
        elif key == keyboard_map.QUIT_KEY:
            self._running = False

    def _on_keyup(self, key: int) -> None:
        """KEYUP: ボタン OFF と、軸キー離上時の最終値ログを処理する。"""
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

    def _reset_axes(self) -> None:
        """全軸を現モードの原点へ戻して送信する。"""
        origin = MODE_ORIGIN[self._mode]
        for axis in range(4):
            self._axis_raw[axis] = origin
            msb_cc, lsb_cc = cc_map.CC_AXES[axis]
            self._midi.send_14bit(msb_cc, lsb_cc, origin)
            self._axis_sent[axis] = origin
        print(f"全軸を原点 {origin} へ")

    def _toggle_mode(self) -> None:
        """Stick ⇔ Slider を切り替え、軸を新原点へリセットする。"""
        self._mode = "slider" if self._mode == "stick" else "stick"
        print(f"モード切替: {self._mode}（原点 {MODE_ORIGIN[self._mode]}）")
        self._reset_axes()

    def _log_axis(self, axis: int) -> None:
        """軸の現在値と正規化値をログ出力する。"""
        raw = self._axis_raw[axis]
        norm = cc_map.norm14_bipolar(raw) if self._mode == "stick" else cc_map.norm_slider(raw)
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
