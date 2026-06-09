"""pygame キー → セマンティックアクションのマッピングとヘルプテキスト。

仕様書セクション8（Unity 側キーボードシミュレーション）のキー文字を踏襲しつつ、
コントローラ役（送信主体）の意味に再定義する。
"""
from __future__ import annotations

from typing import Dict, Tuple

import pygame

import cc_map

# 軸キー: key -> (軸インデックス, 方向)。押下中ランプ（pygame.key.get_pressed で参照）。
# 軸インデックス: 0=左X 1=左Y 2=右X 3=右Y（cc_map.CC_AXES と対応）
AXIS_KEYS: Dict[int, Tuple[int, int]] = {
    pygame.K_1: (0, +1), pygame.K_2: (0, -1),
    pygame.K_3: (1, +1), pygame.K_4: (1, -1),
    pygame.K_5: (2, +1), pygame.K_6: (2, -1),
    pygame.K_7: (3, +1), pygame.K_8: (3, -1),
}

# 全軸を原点へ戻す
AXIS_RESET_KEY = pygame.K_0

# ボタンキー: key -> ボタンインデックス（0–9）。KEYDOWN=ON / KEYUP=OFF。
BUTTON_KEYS: Dict[int, int] = {
    pygame.K_q: 0, pygame.K_w: 1, pygame.K_e: 2, pygame.K_f: 3, pygame.K_t: 4,
    pygame.K_y: 5, pygame.K_u: 6, pygame.K_i: 7, pygame.K_o: 8, pygame.K_p: 9,
}

# 離散 ±1（KEYDOWN）: key -> delta
PRESET_KEYS: Dict[int, int] = {pygame.K_RIGHTBRACKET: +1, pygame.K_LEFTBRACKET: -1}
ERROR_KEYS: Dict[int, int] = {pygame.K_x: +1, pygame.K_z: -1}
STATE_KEYS: Dict[int, int] = {pygame.K_v: +1, pygame.K_c: -1}

# イベント送信（KEYDOWN）: key -> opcode（イベント名前空間）
EVENT_KEYS: Dict[int, int] = {
    pygame.K_g: cc_map.EVT_HEARTBEAT,
    pygame.K_b: cc_map.EVT_BUTTON_COMBO,
    pygame.K_n: cc_map.EVT_SENSOR_TRIGGER,
}

TOGGLE_MODE_KEY = pygame.K_m
HELP_KEY = pygame.K_SLASH
QUIT_KEY = pygame.K_ESCAPE


def help_text() -> str:
    """キー操作の一覧を返す。"""
    return (
        "操作キー一覧（このウィンドウにフォーカスして操作）:\n"
        "  スティック: 1/2=左X±  3/4=左Y±  5/6=右X±  7/8=右Y±（押下中ランプ）  0=全軸原点へ\n"
        "  ボタン0-9 : Q W E F T Y U I O P（押下=ON / 離上=OFF）\n"
        "  Preset    : ]=+1  [=-1（CC40 0-127）\n"
        "  Error     : X=+1  Z=-1（CC41 0-127）\n"
        "  State     : V=+1  C=-1（CC42 0-127）\n"
        "  イベント  : G=HeartBeat  B=ButtonCombo  N=SensorTrigger（送信→応答待ち）\n"
        "  モード切替: M（Stick ⇔ Slider・軸は原点リセット）\n"
        "  その他    : /=ヘルプ再表示  ESC=終了\n"
        "  ※ コマンド(SetPreset 等)は Unity から受信し自動 ACK します"
    )
