#!/usr/bin/env python3
"""
MIDI Simulator - 14-bit CC Gamepad Controller
ゲームパッドの入力を14ビットMIDI CCメッセージに変換
"""

import pygame
import rtmidi
import time
import math
import sys
from typing import Tuple, Optional

class GamepadMidiController:
    def __init__(self):
        self.midi_out = None
        self.midi_in = None
        self.joystick = None
        self.running = True

        # MIDI CC番号定義
        self.CC_LEFT_X_MSB = 16
        self.CC_LEFT_X_LSB = 48
        self.CC_LEFT_Y_MSB = 17
        self.CC_LEFT_Y_LSB = 49
        self.CC_RIGHT_X_MSB = 18
        self.CC_RIGHT_X_LSB = 50
        self.CC_RIGHT_Y_MSB = 19
        self.CC_RIGHT_Y_LSB = 51

        # ボタン設定（CC#20-29）
        self.CC_BUTTON_BASE = 20    # ボタン i → CC#(20+i)
        self.BUTTON_COUNT = 10

        # 状態入力設定（CC#30）
        self.CC_STATE = 30
        self.STATE_MAX = 16         # 0..16 の17段階

        # ショルダー（状態増減）: XInput 一般値 LB=4 / RB=5。機種により異なるため必要なら変更
        self.SHOULDER_DOWN_BTN = 4  # 押下で状態 -1
        self.SHOULDER_UP_BTN = 5    # 押下で状態 +1

        # デモモード設定（自動出力パターン）
        self.DEMO_STICK_PERIOD = 4.0    # スティック円運動の周期（秒）
        self.DEMO_STICK_INTERVAL = 0.05 # スティック送信間隔（秒）= 約20Hz
        self.DEMO_BUTTON_STEP = 0.4     # 点灯ボタンを次へ送る間隔（秒）
        self.DEMO_STATE_STEP = 0.4      # 状態 ±1 の間隔（秒）

        # 14ビット値の範囲
        self.NEUTRAL_POSITION = 8192
        self.MAX_14BIT_VALUE = 16383
        self.DEADZONE = 0.1

        # 前回の値を保存（変化時のみ送信）
        self.prev_left_stick = (0.0, 0.0)
        self.prev_right_stick = (0.0, 0.0)

        # ボタン状態（変化検出用）と状態セレクタ値
        self.prev_buttons = [False] * self.BUTTON_COUNT
        self.state_value = 0

        # デモモード状態
        self.demo_mode = False
        self.demo_start_time = None
        self.demo_last_stick_send = -self.DEMO_STICK_INTERVAL  # デモ開始時に即送信（初回間引き回避）
        self.demo_prev_button_idx = -1
        self.demo_prev_state_value = -1
        self.demo_state_value = 0

        print("MIDI Simulator - 14-bit CC Gamepad Controller")
        print("=" * 50)

    def select_midi_port(self) -> Optional[int]:
        """利用可能なMIDIポートを表示し、ユーザーに選択させる"""
        try:
            self.midi_out = rtmidi.MidiOut()
            available_ports = self.midi_out.get_ports()

            if not available_ports:
                print("利用可能なMIDIポートがありません")
                return None

            print("\n利用可能なMIDIポート:")
            for i, port in enumerate(available_ports):
                print(f"  {i}: {port}")

            while True:
                try:
                    choice = input(f"\nMIDIポートを選択してください (0-{len(available_ports)-1}): ")
                    port_index = int(choice)

                    if 0 <= port_index < len(available_ports):
                        return port_index
                    else:
                        print(f"無効な選択です。0-{len(available_ports)-1}の範囲で入力してください。")

                except ValueError:
                    print("数字を入力してください。")
                except KeyboardInterrupt:
                    return None

        except Exception as e:
            print(f"MIDIポート検索エラー: {e}")
            return None

    def select_midi_input_port(self) -> Optional[int]:
        """利用可能なMIDI入力ポートを表示し、ユーザーに選択させる"""
        try:
            temp_midi_in = rtmidi.MidiIn()
            available_ports = temp_midi_in.get_ports()

            if not available_ports:
                print("利用可能なMIDI入力ポートがありません")
                return None

            print("\n利用可能なMIDI入力ポート:")
            for i, port in enumerate(available_ports):
                print(f"  {i}: {port}")

            while True:
                try:
                    choice = input(f"\nMIDI入力ポートを選択してください (0-{len(available_ports)-1}, スキップする場合はEnter): ")

                    if choice.strip() == "":
                        return None

                    port_index = int(choice)

                    if 0 <= port_index < len(available_ports):
                        return port_index
                    else:
                        print(f"無効な選択です。0-{len(available_ports)-1}の範囲で入力してください。")

                except ValueError:
                    print("数字を入力してください。")
                except KeyboardInterrupt:
                    return None

        except Exception as e:
            print(f"MIDI入力ポート検索エラー: {e}")
            return None

    def init_midi(self, port_index: Optional[int] = None) -> bool:
        """MIDI出力を初期化"""
        try:
            if port_index is None:
                port_index = self.select_midi_port()

            if port_index is None:
                return False

            if not self.midi_out:
                self.midi_out = rtmidi.MidiOut()

            available_ports = self.midi_out.get_ports()
            if port_index < len(available_ports):
                self.midi_out.open_port(port_index)
                print(f"MIDIポート '{available_ports[port_index]}' に接続しました")
            else:
                print("選択されたポートが利用できません")
                return False

            return True

        except Exception as e:
            print(f"MIDI初期化エラー: {e}")
            return False

    def init_midi_input(self, port_index: Optional[int] = None) -> bool:
        """MIDI入力を初期化"""
        try:
            if port_index is None:
                port_index = self.select_midi_input_port()

            if port_index is None:
                print("MIDI入力をスキップします")
                return True

            self.midi_in = rtmidi.MidiIn()
            available_ports = self.midi_in.get_ports()

            if port_index < len(available_ports):
                self.midi_in.open_port(port_index)
                self.midi_in.set_callback(self.midi_input_callback)
                print(f"MIDI入力ポート '{available_ports[port_index]}' に接続しました")
            else:
                print("選択されたMIDI入力ポートが利用できません")
                return False

            return True

        except Exception as e:
            print(f"MIDI入力初期化エラー: {e}")
            return False

    def midi_input_callback(self, message, data=None):
        """MIDI入力コールバック - 受信データをデバッグ出力"""
        msg, timestamp = message

        if len(msg) >= 2:
            status = msg[0]

            if status & 0xF0 == 0xB0:  # Control Change
                cc_number = msg[1]
                cc_value = msg[2] if len(msg) > 2 else 0
                channel = (status & 0x0F) + 1

                print(f"[MIDI入力] Ch.{channel} CC#{cc_number} = {cc_value} (0x{cc_value:02X}) [{' '.join(f'0x{b:02X}' for b in msg)}]")

            elif status & 0xF0 == 0x90:  # Note On
                note = msg[1]
                velocity = msg[2] if len(msg) > 2 else 0
                channel = (status & 0x0F) + 1

                print(f"[MIDI入力] Ch.{channel} Note On: {note} vel={velocity} [{' '.join(f'0x{b:02X}' for b in msg)}]")

            elif status & 0xF0 == 0x80:  # Note Off
                note = msg[1]
                velocity = msg[2] if len(msg) > 2 else 0
                channel = (status & 0x0F) + 1

                print(f"[MIDI入力] Ch.{channel} Note Off: {note} vel={velocity} [{' '.join(f'0x{b:02X}' for b in msg)}]")

            else:
                print(f"[MIDI入力] Raw: [{' '.join(f'0x{b:02X}' for b in msg)}]")
        else:
            print(f"[MIDI入力] Raw: [{' '.join(f'0x{b:02X}' for b in msg)}]")

    def select_gamepad(self) -> Optional[int]:
        """利用可能なゲームパッドを表示し、ユーザーに選択させる"""
        try:
            pygame.init()
            pygame.joystick.init()

            gamepad_count = pygame.joystick.get_count()

            if gamepad_count == 0:
                print("ゲームパッドが接続されていません")
                return None

            if gamepad_count == 1:
                # ゲームパッドが1つだけの場合は自動選択
                joystick = pygame.joystick.Joystick(0)
                print(f"\nゲームパッドを検出しました: {joystick.get_name()}")
                return 0

            print(f"\n{gamepad_count}個のゲームパッドが見つかりました:")
            for i in range(gamepad_count):
                joystick = pygame.joystick.Joystick(i)
                print(f"  {i}: {joystick.get_name()}")

            while True:
                try:
                    choice = input(f"\nゲームパッドを選択してください (0-{gamepad_count-1}): ")
                    gamepad_index = int(choice)

                    if 0 <= gamepad_index < gamepad_count:
                        return gamepad_index
                    else:
                        print(f"無効な選択です。0-{gamepad_count-1}の範囲で入力してください。")

                except ValueError:
                    print("数字を入力してください。")
                except KeyboardInterrupt:
                    return None

        except Exception as e:
            print(f"ゲームパッド検索エラー: {e}")
            return None

    def init_gamepad(self, gamepad_index: Optional[int] = None) -> bool:
        """ゲームパッドを初期化"""
        try:
            if gamepad_index is None:
                gamepad_index = self.select_gamepad()

            if gamepad_index is None:
                return False

            if not pygame.get_init():
                pygame.init()
                pygame.joystick.init()

            if gamepad_index >= pygame.joystick.get_count():
                print("選択されたゲームパッドが利用できません")
                return False

            self.joystick = pygame.joystick.Joystick(gamepad_index)
            self.joystick.init()

            print(f"ゲームパッド接続: {self.joystick.get_name()}")
            print(f"軸数: {self.joystick.get_numaxes()}")
            print(f"ボタン数: {self.joystick.get_numbuttons()}")

            return True

        except Exception as e:
            print(f"ゲームパッド初期化エラー: {e}")
            return False

    def select_mode(self) -> bool:
        """通常/デモのモード選択。True=デモモード"""
        print("\n動作モード:")
        print("  1: 通常モード（ゲームパッド）")
        print("  2: デモモード（自動出力・ゲームパッド不要）")
        while True:
            try:
                choice = input("モードを選択してください (1-2): ").strip()
            except KeyboardInterrupt:
                return False
            if choice == "1":
                return False
            if choice == "2":
                return True
            print("1 または 2 を入力してください。")

    def convert_to_midi_value(self, analog_value: float) -> int:
        """アナログ値(-1.0～1.0)を14ビットMIDI値(0-16383)に変換"""
        # デッドゾーン適用
        if abs(analog_value) < self.DEADZONE:
            analog_value = 0.0

        # -1.0～1.0 を 0.0～1.0 に正規化
        normalized = (analog_value + 1.0) * 0.5

        # 0-16383の範囲にスケール
        midi_value = int(normalized * self.MAX_14BIT_VALUE)
        return max(0, min(self.MAX_14BIT_VALUE, midi_value))

    def send_14bit_cc(self, cc_lsb: int, cc_msb: int, value: int):
        """14ビットCC送信（LSB→MSB順）"""
        if not self.midi_out:
            return

        # 14ビット値を7ビットずつに分割
        lsb = value & 0x7F  # 下位7ビット
        msb = (value >> 7) & 0x7F  # 上位7ビット

        # LSBを先に送信
        cc_lsb_msg = [0xB0, cc_lsb, lsb]
        self.midi_out.send_message(cc_lsb_msg)
        print(f"    MIDI: CC#{cc_lsb}(LSB)={lsb}")

        # MSBを後から送信
        cc_msb_msg = [0xB0, cc_msb, msb]
        self.midi_out.send_message(cc_msb_msg)
        print(f"    MIDI: CC#{cc_msb}(MSB)={msb} [14bit={value}]")

    def send_cc(self, cc: int, value: int):
        """7ビットCCを1メッセージ送信（ボタン/状態で共用）"""
        if not self.midi_out:
            return

        self.midi_out.send_message([0xB0, cc, value & 0x7F])
        print(f"    MIDI: CC#{cc}={value & 0x7F}")

    def get_stick_values(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """スティック値を取得"""
        if not self.joystick:
            return (0.0, 0.0), (0.0, 0.0)

        # 左スティック (通常は軸0,1)
        left_x = self.joystick.get_axis(0) if self.joystick.get_numaxes() > 0 else 0.0
        left_y = -self.joystick.get_axis(1) if self.joystick.get_numaxes() > 1 else 0.0  # Y軸反転

        # 右スティック (通常は軸2,3または4,5)
        right_x = self.joystick.get_axis(2) if self.joystick.get_numaxes() > 2 else 0.0
        right_y = -self.joystick.get_axis(3) if self.joystick.get_numaxes() > 3 else 0.0  # Y軸反転

        return (left_x, left_y), (right_x, right_y)

    def process_input(self):
        """入力処理とMIDI送信（通常モード）"""
        pygame.event.pump()
        self._process_sticks()
        current = self._read_buttons()
        self._process_buttons(current)
        self._process_state(current)
        self.prev_buttons = current

    def _process_sticks(self):
        """左右スティックの変化を14bit CCで送信"""
        left_stick, right_stick = self.get_stick_values()

        # 左スティック処理
        if abs(left_stick[0] - self.prev_left_stick[0]) > 0.001 or \
           abs(left_stick[1] - self.prev_left_stick[1]) > 0.001:

            x_value = self.convert_to_midi_value(left_stick[0])
            y_value = self.convert_to_midi_value(left_stick[1])

            self.send_14bit_cc(self.CC_LEFT_X_LSB, self.CC_LEFT_X_MSB, x_value)
            self.send_14bit_cc(self.CC_LEFT_Y_LSB, self.CC_LEFT_Y_MSB, y_value)

            print(f"左スティック X:{left_stick[0]:6.3f}→{x_value:5d} Y:{left_stick[1]:6.3f}→{y_value:5d}")
            self.prev_left_stick = left_stick

        # 右スティック処理
        if abs(right_stick[0] - self.prev_right_stick[0]) > 0.001 or \
           abs(right_stick[1] - self.prev_right_stick[1]) > 0.001:

            x_value = self.convert_to_midi_value(right_stick[0])
            y_value = self.convert_to_midi_value(right_stick[1])

            self.send_14bit_cc(self.CC_RIGHT_X_LSB, self.CC_RIGHT_X_MSB, x_value)
            self.send_14bit_cc(self.CC_RIGHT_Y_LSB, self.CC_RIGHT_Y_MSB, y_value)

            print(f"右スティック X:{right_stick[0]:6.3f}→{x_value:5d} Y:{right_stick[1]:6.3f}→{y_value:5d}")
            self.prev_right_stick = right_stick

    def _read_buttons(self):
        """現在のボタン状態を BUTTON_COUNT 個ぶん取得（未接続/不足は False）"""
        if not self.joystick:
            return [False] * self.BUTTON_COUNT
        n = self.joystick.get_numbuttons()
        return [bool(self.joystick.get_button(i)) if i < n else False
                for i in range(self.BUTTON_COUNT)]

    def _process_buttons(self, current):
        """変化したボタンだけ CC#20-29 を送信（押下127 / 離上0）"""
        for i in range(self.BUTTON_COUNT):
            if current[i] != self.prev_buttons[i]:
                self.send_cc(self.CC_BUTTON_BASE + i, 127 if current[i] else 0)
                print(f"ボタン{i}: {'ON' if current[i] else 'OFF'}")

    def _process_state(self, current):
        """ショルダーの押下エッジで状態を増減し、変化時に CC#30 を送信"""
        down_edge = current[self.SHOULDER_DOWN_BTN] and not self.prev_buttons[self.SHOULDER_DOWN_BTN]
        up_edge = current[self.SHOULDER_UP_BTN] and not self.prev_buttons[self.SHOULDER_UP_BTN]

        new_state = self.state_value
        if up_edge:
            new_state = min(self.STATE_MAX, new_state + 1)
        if down_edge:
            new_state = max(0, new_state - 1)

        if new_state != self.state_value:
            self.state_value = new_state
            cc_value = round(new_state / self.STATE_MAX * 127)
            self.send_cc(self.CC_STATE, cc_value)
            print(f"状態: {new_state}/{self.STATE_MAX} (CC#30={cc_value})")

    def _process_demo(self):
        """デモモード: 経過時間から全CCを規則的パターンで生成・送信"""
        if self.demo_start_time is None:
            self.demo_start_time = time.time()
        t = time.time() - self.demo_start_time
        self._demo_sticks(t)
        self._demo_buttons(t)
        self._demo_state(t)

    def _demo_sticks(self, t):
        """スティックを円運動（左右で位相反転）。DEMO_STICK_INTERVAL ごとに送信"""
        if t - self.demo_last_stick_send < self.DEMO_STICK_INTERVAL:
            return
        self.demo_last_stick_send = t

        theta = 2 * math.pi * (t / self.DEMO_STICK_PERIOD)
        lx = self.convert_to_midi_value(math.sin(theta))
        ly = self.convert_to_midi_value(math.cos(theta))
        rx = self.convert_to_midi_value(math.sin(theta + math.pi))
        ry = self.convert_to_midi_value(math.cos(theta + math.pi))

        self.send_14bit_cc(self.CC_LEFT_X_LSB, self.CC_LEFT_X_MSB, lx)
        self.send_14bit_cc(self.CC_LEFT_Y_LSB, self.CC_LEFT_Y_MSB, ly)
        self.send_14bit_cc(self.CC_RIGHT_X_LSB, self.CC_RIGHT_X_MSB, rx)
        self.send_14bit_cc(self.CC_RIGHT_Y_LSB, self.CC_RIGHT_Y_MSB, ry)

    def _demo_buttons(self, t):
        """ボタンを順次点灯（常に1個ON）。変化時のみ送信"""
        idx = int(t / self.DEMO_BUTTON_STEP) % self.BUTTON_COUNT
        if idx == self.demo_prev_button_idx:
            return
        if self.demo_prev_button_idx >= 0:
            self.send_cc(self.CC_BUTTON_BASE + self.demo_prev_button_idx, 0)
        self.send_cc(self.CC_BUTTON_BASE + idx, 127)
        print(f"デモ ボタン{idx} ON")
        self.demo_prev_button_idx = idx

    def _demo_state(self, t):
        """状態を 0→16→0 で往復（三角波）。変化時のみ送信"""
        step = int(t / self.DEMO_STATE_STEP)
        period = 2 * self.STATE_MAX
        phase = step % period
        value = phase if phase <= self.STATE_MAX else period - phase
        if value == self.demo_prev_state_value:
            return
        self.demo_prev_state_value = value
        self.demo_state_value = value
        cc_value = round(value / self.STATE_MAX * 127)
        self.send_cc(self.CC_STATE, cc_value)
        print(f"デモ 状態: {value}/{self.STATE_MAX} (CC#30={cc_value})")

    def run(self):
        """メインループ"""
        print("\n=== モード選択 ===")
        self.demo_mode = self.select_mode()

        print("\n=== デバイス選択 ===")
        if not self.init_midi():
            print("MIDI出力デバイスの初期化に失敗しました")
            return False

        if not self.init_midi_input():
            print("MIDI入力デバイスの初期化に失敗しました")
            return False

        if not self.demo_mode:
            if not self.init_gamepad():
                print("ゲームパッドの初期化に失敗しました")
                return False

        print("\n動作開始 (Ctrl+Cで終了)")
        if self.demo_mode:
            print("デモモード: ゲームパッド不要で自動的にMIDI CCを送信します")
        else:
            print("スティック/ボタン/ショルダーを操作してMIDI CCを送信してください")
        if self.midi_in:
            print("MIDI入力データも受信・表示します")
        print("-" * 50)

        try:
            while self.running:
                if self.demo_mode:
                    self._process_demo()
                else:
                    self.process_input()
                time.sleep(0.01)  # 100Hz更新

        except KeyboardInterrupt:
            print("\n終了します...")
        finally:
            self.cleanup()

    def cleanup(self):
        """リソースの解放"""
        if self.joystick:
            self.joystick.quit()

        if self.midi_in:
            self.midi_in.close_port()

        if self.midi_out:
            self.midi_out.close_port()

        if pygame.get_init():
            pygame.quit()
        print("リソースを解放しました")

def main():
    controller = GamepadMidiController()
    controller.run()

if __name__ == "__main__":
    main()