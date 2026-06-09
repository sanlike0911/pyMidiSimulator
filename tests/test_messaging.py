"""messaging ステートマシンのユニットテスト。"""
import cc_map
from messaging import Messaging, RESPONSE_TIMEOUT_TICKS


class FakeSender:
    """送信された CC を記録するフェイク send_cc。"""

    def __init__(self):
        self.sent = []

    def __call__(self, cc, value):
        self.sent.append((cc, value))


def make():
    sender = FakeSender()
    return Messaging(sender), sender


class TestCommandReceive:
    def test_set_preset_updates_and_acks(self):
        m, s = make()
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 100)
        m.handle_incoming_cc(cc_map.CMD_ARG2_CC, 3)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.CMD_SET_PRESET, 1))

        snap = m.snapshot()
        assert snap.received_preset == 100
        assert snap.preset_option == 3
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))

    def test_ack_echoes_seq_zero(self):
        m, s = make()
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.CMD_PING, 0))
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))

    def test_unknown_opcode_returns_unknown_op(self):
        m, s = make()
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(3, 0))  # 3 = 予約 = 未知
        cc, value = s.sent[-1]
        assert cc == cc_map.CMDRSP_STATUS_CC
        assert cc_map.payload_of(value) == cc_map.STATUS_UNKNOWN_OP

    def test_led_and_haptic_acked_ok(self):
        m, s = make()
        for opcode in (cc_map.CMD_LED, cc_map.CMD_HAPTIC):
            m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(opcode, 0))
            _cc, value = s.sent[-1]
            assert cc_map.payload_of(value) == cc_map.STATUS_OK

    def test_args_consumed_after_commit(self):
        m, _s = make()
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 50)
        m.handle_incoming_cc(cc_map.CMD_ARG2_CC, 9)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.CMD_SET_PRESET, 0))
        first = m.snapshot().last_command
        assert (first.arg1, first.arg2) == (50, 9)

        # 2 回目は引数を送らず commit -> arg1/arg2 は 0 に戻っている
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.CMD_PING, 1))
        second = m.snapshot().last_command
        assert (second.arg1, second.arg2) == (0, 0)

    def test_set_preset_invalid_arg_rejected(self):
        m, s = make()
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 200)  # 0-127 を超える不正値
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.CMD_SET_PRESET, 0))
        _cc, value = s.sent[-1]
        assert cc_map.payload_of(value) == cc_map.STATUS_INVALID_ARG
        assert m.snapshot().received_preset is None  # 不正なので更新されない


class TestEventSend:
    def test_send_order_arg_then_op(self):
        m, s = make()
        assert m.send_event(cc_map.EVT_HEARTBEAT, 42) is True
        assert s.sent[0] == (cc_map.EVT_ARG_CC, 42)
        assert s.sent[1] == (cc_map.EVT_OP_CC, cc_map.pack_seq(cc_map.EVT_HEARTBEAT, 0))

    def test_seq_toggles_each_send(self):
        m, s = make()
        m.send_event(cc_map.EVT_HEARTBEAT, 0)
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        m.send_event(cc_map.EVT_HEARTBEAT, 1)

        op_seqs = [cc_map.seq_of(v) for (cc, v) in s.sent if cc == cc_map.EVT_OP_CC]
        assert op_seqs == [0, 1]

    def test_blocked_while_pending(self):
        m, _s = make()
        assert m.send_event(cc_map.EVT_HEARTBEAT, 0) is True
        assert m.send_event(cc_map.EVT_BUTTON_COMBO, 1) is False
        assert m.snapshot().event_pending is True

    def test_response_resolves_pending(self):
        m, _s = make()
        m.send_event(cc_map.EVT_HEARTBEAT, 7)
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))

        snap = m.snapshot()
        assert snap.event_pending is False
        assert snap.last_event_response.status == cc_map.STATUS_OK
        assert snap.last_event_response.timed_out is False

    def test_response_seq_mismatch_discarded(self):
        m, _s = make()
        m.send_event(cc_map.EVT_HEARTBEAT, 0)  # seq=0
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))
        assert m.snapshot().event_pending is True  # 不一致 -> 保留のまま

    def test_response_without_pending_discarded(self):
        m, _s = make()
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        assert m.snapshot().last_event_response is None


class TestTimeout:
    def test_timeout_clears_pending_and_allows_resend(self):
        m, _s = make()
        m.send_event(cc_map.EVT_HEARTBEAT, 0)
        for _ in range(RESPONSE_TIMEOUT_TICKS + 1):
            m.tick()

        snap = m.snapshot()
        assert snap.event_pending is False
        assert snap.last_event_response.timed_out is True
        assert m.send_event(cc_map.EVT_HEARTBEAT, 1) is True  # 再送可

    def test_no_timeout_at_threshold(self):
        m, _s = make()
        m.send_event(cc_map.EVT_HEARTBEAT, 0)
        for _ in range(RESPONSE_TIMEOUT_TICKS):
            m.tick()
        assert m.snapshot().event_pending is True

    def test_tick_without_pending_is_noop(self):
        m, _s = make()
        m.tick()  # 保留なしで呼んでも何も起きない
        assert m.snapshot().event_pending is False
        assert m.snapshot().last_event_response is None


class TestCross:
    def test_command_received_while_event_pending(self):
        m, s = make()
        m.send_event(cc_map.EVT_HEARTBEAT, 0)
        assert m.snapshot().event_pending is True

        # 応答待ち中にコマンド受信 -> 即 ACK、イベント保留は維持
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.CMD_PING, 1))
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))
        assert m.snapshot().event_pending is True
