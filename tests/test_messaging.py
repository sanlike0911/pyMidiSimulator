"""messaging ステートマシン（プロトコル層）のユニットテスト。

opcode のビジネスロジックは controller_state 側でテストする。ここではフェイクの
validate/execute を注入し、フレーミング・ACK・seq・保留管理・タイムアウトのみ検証する。
"""
import cc_map
from messaging import Messaging, RESPONSE_TIMEOUT_TICKS


class FakeSender:
    """送信された CC を記録するフェイク send_cc。"""

    def __init__(self):
        self.sent = []

    def __call__(self, cc, value):
        self.sent.append((cc, value))


class FakeHandler:
    """validate/execute の呼び出しを記録するフェイクコマンドハンドラ。"""

    def __init__(self, status=cc_map.STATUS_OK):
        self.status = status
        self.validated = []
        self.executed = []

    def validate(self, opcode, arg1, arg2):
        self.validated.append((opcode, arg1, arg2))
        return self.status

    def execute(self, opcode, arg1, arg2):
        self.executed.append((opcode, arg1, arg2))


def make(status=cc_map.STATUS_OK):
    sender = FakeSender()
    handler = FakeHandler(status)
    return Messaging(sender, handler.validate, handler.execute), sender, handler


class TestCommandReceive:
    def test_commit_validates_acks_and_executes(self):
        m, s, h = make()
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 100)
        m.handle_incoming_cc(cc_map.CMD_ARG2_CC, 3)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_PRESET, 1))

        assert h.validated == [(cc_map.OP_SET_PRESET, 100, 3)]
        assert h.executed == [(cc_map.OP_SET_PRESET, 100, 3)]
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))

    def test_ack_echoes_seq_zero(self):
        m, s, _h = make()
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))

    def test_ack_sent_before_execute(self):
        """仕様: Reset/SetMode は「ACK 送信後に実行」。ACK 送信が execute より先であること。"""
        order = []
        m = Messaging(
            send_cc=lambda cc, value: order.append("ack"),
            validate_command=lambda opcode, a1, a2: cc_map.STATUS_OK,
            execute_command=lambda opcode, a1, a2: order.append("execute"),
        )
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_RESET, 0))
        assert order == ["ack", "execute"]

    def test_execute_skipped_when_not_ok(self):
        m, s, h = make(status=cc_map.STATUS_INVALID_ARG)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_MODE, 0))
        assert h.executed == []  # 非 OK -> 実行されない
        _cc, value = s.sent[-1]
        assert cc_map.payload_of(value) == cc_map.STATUS_INVALID_ARG

    def test_args_consumed_after_commit(self):
        m, _s, h = make()
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 50)
        m.handle_incoming_cc(cc_map.CMD_ARG2_CC, 9)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_PRESET, 0))
        assert h.validated[-1] == (cc_map.OP_SET_PRESET, 50, 9)

        # 2 回目は引数を送らず commit -> arg1/arg2 は 0 に戻っている
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        assert h.validated[-1] == (cc_map.OP_PING, 0, 0)

    def test_last_command_records_status_and_seq(self):
        m, _s, _h = make(status=cc_map.STATUS_UNKNOWN_OP)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(9, 1))
        cmd = m.snapshot().last_command
        assert (cmd.opcode, cmd.status, cmd.seq) == (9, cc_map.STATUS_UNKNOWN_OP, 1)


class TestEventSend:
    def test_send_without_arg_omits_evt_arg(self):
        # 確定イベント Ping は ARG 未使用 -> EVT_ARG を省略し EVT_OP のみ（仕様の正規例）
        m, s, _h = make()
        assert m.send_event(cc_map.OP_PING) is True
        assert s.sent == [(cc_map.EVT_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))]

    def test_send_with_arg_sends_arg_then_op(self):
        # ARG 付きイベント（将来の C→G opcode 用）は EVT_ARG -> EVT_OP の順
        m, s, _h = make()
        assert m.send_event(cc_map.OP_PING, 42) is True
        assert s.sent[0] == (cc_map.EVT_ARG_CC, 42)
        assert s.sent[1] == (cc_map.EVT_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))

    def test_seq_toggles_each_send(self):
        m, s, _h = make()
        m.send_event(cc_map.OP_PING)
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        m.send_event(cc_map.OP_PING)

        op_seqs = [cc_map.seq_of(v) for (cc, v) in s.sent if cc == cc_map.EVT_OP_CC]
        assert op_seqs == [0, 1]

    def test_blocked_while_pending(self):
        m, _s, _h = make()
        assert m.send_event(cc_map.OP_PING) is True
        assert m.send_event(cc_map.OP_PING) is False
        assert m.snapshot().event_pending is True

    def test_response_resolves_pending(self):
        m, _s, _h = make()
        m.send_event(cc_map.OP_PING)
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))

        snap = m.snapshot()
        assert snap.event_pending is False
        assert snap.last_event_response.status == cc_map.STATUS_OK
        assert snap.last_event_response.timed_out is False

    def test_response_seq_mismatch_discarded(self):
        m, _s, _h = make()
        m.send_event(cc_map.OP_PING)  # seq=0
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))
        assert m.snapshot().event_pending is True  # 不一致 -> 保留のまま

    def test_response_without_pending_discarded(self):
        m, _s, _h = make()
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        assert m.snapshot().last_event_response is None


class TestClearPending:
    def test_clear_pending_discards_silently_and_allows_resend(self):
        # Reset 実行時のキャンセル: 応答記録（タイムアウト扱い）を残さず破棄する
        m, _s, _h = make()
        m.send_event(cc_map.OP_PING)
        m.clear_pending()
        snap = m.snapshot()
        assert snap.event_pending is False
        assert snap.last_event_response is None
        assert m.send_event(cc_map.OP_PING) is True  # 再送可

    def test_clear_pending_without_pending_is_noop(self):
        m, _s, _h = make()
        m.clear_pending()
        assert m.snapshot().event_pending is False


class TestTimeout:
    def test_timeout_clears_pending_and_allows_resend(self):
        m, _s, _h = make()
        m.send_event(cc_map.OP_PING)
        for _ in range(RESPONSE_TIMEOUT_TICKS + 1):
            m.tick()

        snap = m.snapshot()
        assert snap.event_pending is False
        assert snap.last_event_response.timed_out is True
        assert m.send_event(cc_map.OP_PING) is True  # 再送可

    def test_no_timeout_at_threshold(self):
        m, _s, _h = make()
        m.send_event(cc_map.OP_PING)
        for _ in range(RESPONSE_TIMEOUT_TICKS):
            m.tick()
        assert m.snapshot().event_pending is True

    def test_tick_without_pending_is_noop(self):
        m, _s, _h = make()
        m.tick()  # 保留なしで呼んでも何も起きない
        assert m.snapshot().event_pending is False
        assert m.snapshot().last_event_response is None


class TestCross:
    def test_command_received_while_event_pending(self):
        m, s, _h = make()
        m.send_event(cc_map.OP_PING)
        assert m.snapshot().event_pending is True

        # 応答待ち中にコマンド受信 -> 即 ACK、イベント保留は維持
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))
        assert m.snapshot().event_pending is True
