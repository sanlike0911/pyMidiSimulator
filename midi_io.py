"""MIDI 入出力ラッパー（ポート列挙・CC 送信・受信ディスパッチ）。

rtmidi を薄くラップする。受信した Control Change を (cc, value) に分解して
コールバックへ渡す。送信は 7bit CC と 14bit CC（LSB 先・MSB 後）をサポート。
"""
from __future__ import annotations

from typing import Callable, List, Optional

import rtmidi

import cc_map

# MIDI チャンネル（0 = ch1）。送受信で共通。
MIDI_CHANNEL = 0
_CC_STATUS = 0xB0  # Control Change ステータスバイト（| channel）


def list_output_ports() -> List[str]:
    """利用可能な MIDI 出力ポート名の一覧を返す。"""
    return rtmidi.MidiOut().get_ports()


def list_input_ports() -> List[str]:
    """利用可能な MIDI 入力ポート名の一覧を返す。"""
    return rtmidi.MidiIn().get_ports()


class MidiIO:
    """MIDI 出力／入力ポートの保持と送受信を担う。"""

    def __init__(self) -> None:
        self._out: Optional[rtmidi.MidiOut] = None
        self._in: Optional[rtmidi.MidiIn] = None
        self._on_cc: Optional[Callable[[int, int], None]] = None

    # --- 出力 ---------------------------------------------------------------
    def open_output(self, port_index: int) -> None:
        """指定インデックスの出力ポートを開く。"""
        self._out = rtmidi.MidiOut()
        self._out.open_port(port_index)

    def send_cc(self, cc: int, value: int) -> None:
        """7bit Control Change を 1 メッセージ送信する。出力未接続なら no-op。"""
        if self._out is None:
            return
        self._out.send_message([_CC_STATUS | MIDI_CHANNEL, cc & 0x7F, value & 0x7F])

    def send_14bit(self, msb_cc: int, lsb_cc: int, value14: int) -> None:
        """14bit 値を LSB 先・MSB 後の 2 メッセージで送信する。"""
        msb, lsb = cc_map.split_14bit(value14)
        self.send_cc(lsb_cc, lsb)
        self.send_cc(msb_cc, msb)

    # --- 入力 ---------------------------------------------------------------
    def open_input(self, port_index: int, on_cc: Callable[[int, int], None]) -> None:
        """入力ポートを開き、受信した CC を on_cc(cc, value) へ渡す。"""
        self._on_cc = on_cc
        self._in = rtmidi.MidiIn()
        self._in.open_port(port_index)
        self._in.set_callback(self._callback)

    def _callback(self, message, data=None) -> None:
        """rtmidi 受信コールバック。送信と同一チャンネルの CC のみ抽出してディスパッチする（別スレッド）。

        ステータスの上位ニブル（CC 判定）に加え、下位ニブル（チャンネル）も `MIDI_CHANNEL` と
        一致するもののみ処理する。これにより、同一入力ポートに別チャンネルの CC#110–113 が
        流れても、コマンド引数・commit・イベント ACK として誤処理しない。
        """
        msg, _timestamp = message
        if (
            len(msg) >= 3
            and (msg[0] & 0xF0) == _CC_STATUS
            and (msg[0] & 0x0F) == MIDI_CHANNEL
            and self._on_cc is not None
        ):
            self._on_cc(msg[1], msg[2])

    def has_input(self) -> bool:
        """入力ポートが開かれているか。"""
        return self._in is not None

    # --- 後始末 -------------------------------------------------------------
    def close(self) -> None:
        """入出力ポートを穏当に閉じる。"""
        if self._in is not None:
            self._in.close_port()
            self._in = None
        if self._out is not None:
            self._out.close_port()
            self._out = None
