"""ControllerState（パラメータ一元管理＋コマンド validate/execute）のユニットテスト。"""
import cc_map
from controller_state import ControllerState


class FakeSender:
    """送信された CC を記録するフェイク send_cc。"""

    def __init__(self):
        self.sent = []

    def __call__(self, cc, value):
        self.sent.append((cc, value))


def make():
    sender = FakeSender()
    resets = []
    logs = []
    cs = ControllerState(
        send_cc=sender, on_reset=lambda: resets.append(True), on_log=logs.append
    )
    return cs, sender, resets, logs


class TestInitialState:
    def test_initial_values(self):
        cs, _s, _r, _l = make()
        assert cs.state == 0
        assert cs.mode == cc_map.MODE_NORMAL
        assert cs.error == 0
        assert cs.preset == 0
        assert cs.valve is None

    def test_notify_initial_sends_all_params_in_cc_order(self):
        cs, s, _r, _l = make()
        cs.notify_initial()
        assert s.sent == [
            (cc_map.STATE_CC, 0),
            (cc_map.MODE_CC, cc_map.MODE_NORMAL),
            (cc_map.ERROR_CC, 0),
            (cc_map.PRESET_CC, 0),
        ]


class TestAdjust:
    def test_adjust_preset_sends_on_change(self):
        cs, s, _r, _l = make()
        assert cs.adjust_preset(+1) == 1
        assert s.sent == [(cc_map.PRESET_CC, 1)]

    def test_adjust_preset_clamps_at_max_without_send(self):
        cs, s, _r, _l = make()
        for _ in range(cc_map.MAX_7BIT):
            cs.adjust_preset(+1)
        s.sent.clear()
        assert cs.adjust_preset(+1) == cc_map.MAX_7BIT  # 127 で頭打ち
        assert s.sent == []  # 変化なし -> 送信なし（仕様 §3 の送信契機）

    def test_adjust_clamps_at_zero_without_send(self):
        cs, s, _r, _l = make()
        assert cs.adjust_error(-1) == 0
        assert s.sent == []

    def test_adjust_error_and_state_use_own_cc(self):
        cs, s, _r, _l = make()
        cs.adjust_error(+1)
        cs.adjust_state(+1)
        assert s.sent == [(cc_map.ERROR_CC, 1), (cc_map.STATE_CC, 1)]


class TestSetScalar:
    def test_set_scalar_sends_and_syncs(self):
        cs, s, _r, _l = make()
        cs.set_scalar(cc_map.PRESET_CC, 64)
        assert cs.preset == 64
        assert s.sent == [(cc_map.PRESET_CC, 64)]

    def test_set_scalar_handles_each_scalar_cc(self):
        cs, s, _r, _l = make()
        cs.set_scalar(cc_map.STATE_CC, 10)
        cs.set_scalar(cc_map.ERROR_CC, 20)
        cs.set_scalar(cc_map.PRESET_CC, 30)
        assert (cs.state, cs.error, cs.preset) == (10, 20, 30)
        assert s.sent == [
            (cc_map.STATE_CC, 10),
            (cc_map.ERROR_CC, 20),
            (cc_map.PRESET_CC, 30),
        ]

    def test_set_scalar_skips_unchanged_value(self):
        cs, s, _r, _l = make()
        cs.set_scalar(cc_map.STATE_CC, 0)  # 初期値と同値
        assert s.sent == []

    def test_set_scalar_ignores_unrelated_cc(self):
        cs, s, _r, _l = make()
        cs.set_scalar(cc_map.MODE_CC, 110)  # Mode は自動巡回対象外（設計書 §7）
        assert cs.mode == cc_map.MODE_NORMAL
        assert s.sent == []


class TestValidate:
    def test_validate_ping_reset_zero_preset_ok(self):
        cs, _s, _r, _l = make()
        for opcode in (
            cc_map.OP_PING, cc_map.OP_RESET, cc_map.OP_SET_ZERO, cc_map.OP_SET_PRESET
        ):
            assert cs.validate_command(opcode, 0, 0) == cc_map.STATUS_OK

    def test_validate_set_mode_accepts_defined_values(self):
        for value in cc_map.MODE_VALUES:
            cs, _s, _r, _l = make()
            assert cs.validate_command(cc_map.OP_SET_MODE, value, 0) == cc_map.STATUS_OK

    def test_validate_set_mode_rejects_invalid_value(self):
        cs, _s, _r, _l = make()
        assert cs.validate_command(cc_map.OP_SET_MODE, 50, 0) == cc_map.STATUS_INVALID_ARG
        assert cs.mode == cc_map.MODE_NORMAL  # 仕様: 不正引数では状態を変更しない

    def test_validate_set_mode_rejected_after_transition(self):
        cs, _s, _r, _l = make()
        cs.execute_command(cc_map.OP_SET_MODE, cc_map.MODE_VERSION_UP, 0)
        # 一方向遷移: 非通常モードからは SetMode(0) による復帰も不可
        assert (
            cs.validate_command(cc_map.OP_SET_MODE, cc_map.MODE_NORMAL, 0)
            == cc_map.STATUS_REJECTED
        )

    def test_validate_set_valve_accepts_open_close(self):
        cs, _s, _r, _l = make()
        assert cs.validate_command(cc_map.OP_SET_VALVE, cc_map.VALVE_OPEN, 0) == cc_map.STATUS_OK
        assert cs.validate_command(cc_map.OP_SET_VALVE, cc_map.VALVE_CLOSE, 0) == cc_map.STATUS_OK

    def test_validate_set_valve_rejects_invalid_value(self):
        cs, _s, _r, _l = make()
        assert cs.validate_command(cc_map.OP_SET_VALVE, 2, 0) == cc_map.STATUS_INVALID_ARG

    def test_validate_unknown_opcode(self):
        cs, _s, _r, _l = make()
        assert cs.validate_command(6, 0, 0) == cc_map.STATUS_UNKNOWN_OP  # 6 = 予約 = 未実装

    def test_validate_ignores_unused_args(self):
        # 仕様の共通規約: 未使用 ARG は検証しない（0 以外が届いても INVALID_ARG にしない）
        cs, _s, _r, _l = make()
        assert cs.validate_command(cc_map.OP_SET_PRESET, 10, 99) == cc_map.STATUS_OK
        assert cs.validate_command(cc_map.OP_PING, 99, 99) == cc_map.STATUS_OK


class TestExecute:
    def test_execute_set_preset_updates_and_notifies(self):
        cs, s, _r, _l = make()
        cs.execute_command(cc_map.OP_SET_PRESET, 100, 0)
        assert cs.preset == 100
        assert s.sent == [(cc_map.PRESET_CC, 100)]  # 変化時のみ CC105 で新値通知

    def test_execute_set_preset_same_value_no_notify(self):
        cs, s, _r, _l = make()
        cs.execute_command(cc_map.OP_SET_PRESET, 0, 0)  # 初期値と同値
        assert s.sent == []  # 仕様: 変化が無いため通知されない

    def test_execute_set_mode_updates_and_notifies(self):
        cs, s, _r, _l = make()
        cs.execute_command(cc_map.OP_SET_MODE, cc_map.MODE_VERSION_UP, 0)
        assert cs.mode == cc_map.MODE_VERSION_UP
        assert s.sent == [(cc_map.MODE_CC, cc_map.MODE_VERSION_UP)]

    def test_execute_set_mode_same_value_no_notify(self):
        cs, s, _r, _l = make()
        cs.execute_command(cc_map.OP_SET_MODE, cc_map.MODE_NORMAL, 0)  # 通常→通常
        assert s.sent == []

    def test_execute_reset_restores_initial_and_renotifies(self):
        cs, s, resets, _l = make()
        cs.adjust_preset(+5)
        cs.adjust_error(+2)
        cs.execute_command(cc_map.OP_SET_VALVE, cc_map.VALVE_CLOSE, 0)
        s.sent.clear()

        cs.execute_command(cc_map.OP_RESET, 0, 0)

        assert resets == [True]  # 呼び出し側の初期化フック（軸中心化等）が 1 回だけ呼ばれる
        assert (cs.state, cs.mode, cs.error, cs.preset, cs.valve) == (
            0, cc_map.MODE_NORMAL, 0, 0, None
        )
        # 接続直後相当の初期通知が再送される
        assert s.sent == [
            (cc_map.STATE_CC, 0),
            (cc_map.MODE_CC, cc_map.MODE_NORMAL),
            (cc_map.ERROR_CC, 0),
            (cc_map.PRESET_CC, 0),
        ]

    def test_execute_set_valve_updates_state(self):
        cs, s, _r, logs = make()
        cs.execute_command(cc_map.OP_SET_VALVE, cc_map.VALVE_CLOSE, 0)
        assert cs.valve == cc_map.VALVE_CLOSE
        assert s.sent == []  # バルブ状態を通知する CC は仕様にない
        assert any("close" in log for log in logs)

    def test_execute_set_zero_logs_only(self):
        cs, s, _r, logs = make()
        cs.execute_command(cc_map.OP_SET_ZERO, 0, 0)
        assert s.sent == []
        assert any("SetZero" in log for log in logs)

    def test_execute_ping_no_side_effect(self):
        cs, s, resets, _l = make()
        cs.execute_command(cc_map.OP_PING, 0, 0)
        assert s.sent == []
        assert resets == []
        assert (cs.state, cs.mode, cs.error, cs.preset, cs.valve) == (
            0, cc_map.MODE_NORMAL, 0, 0, None
        )
