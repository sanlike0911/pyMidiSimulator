"""pygame キー → セマンティックアクションのマッピングとヘルプテキスト。

スティックは WASD（左）/ 矢印キー（右）の十字配置、ボタンは数字キー 1–0 に割り当て、
コントローラ役（送信主体）の意味で定義する。
"""
from __future__ import annotations

from typing import Dict, Tuple

import pygame

import cc_map

# 軸キー: key -> (軸インデックス, 方向)。押下中ランプ（pygame.key.get_pressed で参照）。
# 軸インデックス: 0=左X 1=左Y 2=右X 3=右Y（cc_map.CC_AXES と対応）
# 左スティック=WASD / 右スティック=矢印キー（上=Y+ 下=Y− 左=X− 右=X+ の十字配置）
AXIS_KEYS: Dict[int, Tuple[int, int]] = {
    pygame.K_d: (0, +1), pygame.K_a: (0, -1),
    pygame.K_w: (1, +1), pygame.K_s: (1, -1),
    pygame.K_RIGHT: (2, +1), pygame.K_LEFT: (2, -1),
    pygame.K_UP: (3, +1), pygame.K_DOWN: (3, -1),
}

# 全軸を原点へ戻す
AXIS_RESET_KEY = pygame.K_r

# ボタンキー: key -> ボタンインデックス（0–9）。KEYDOWN=ON / KEYUP=OFF。
BUTTON_KEYS: Dict[int, int] = {
    pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2, pygame.K_4: 3, pygame.K_5: 4,
    pygame.K_6: 5, pygame.K_7: 6, pygame.K_8: 7, pygame.K_9: 8, pygame.K_0: 9,
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

AUTO_MODE_KEY = pygame.K_m
HELP_KEY = pygame.K_SLASH
QUIT_KEY = pygame.K_ESCAPE


def help_text() -> str:
    """キー操作の一覧を返す。"""
    return (
        "操作キー一覧（このウィンドウにフォーカスして操作）:\n"
        "  左スティック: W=上(Y+)  S=下(Y-)  A=左(X-)  D=右(X+)（押下中ランプ）\n"
        "  右スティック: ↑=上(Y+)  ↓=下(Y-)  ←=左(X-)  →=右(X+)（押下中ランプ）\n"
        "  R          : 全軸を中心点へ移動\n"
        "  ボタン0-9 : 1 2 3 4 5 6 7 8 9 0（押下=ON / 離上=OFF）\n"
        "  Preset    : ]=+1  [=-1（CC40 0-127）\n"
        "  Error     : X=+1  Z=-1（CC41 0-127）\n"
        "  State     : V=+1  C=-1（CC42 0-127）\n"
        "  イベント  : G=HeartBeat  B=ButtonCombo  N=SensorTrigger（送信→応答待ち）\n"
        "  自動入力  : M（自動デバッグ入力 ON/OFF・全要素を巡回送信）\n"
        "  その他    : /=ヘルプ再表示  ESC=終了\n"
        "  ※ コマンド(SetPreset 等)は Unity から受信し自動 ACK します"
    )
