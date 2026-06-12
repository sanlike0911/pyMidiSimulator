"""messaging ステートマシン（プロトコル層）のユニットテスト。

opcode のビジネスロジックは controller_state 側でテストする。ここではフェイクの
validate/execute を注入し、フレーミング・ACK・seq・保留管理・タイムアウト・
ACK 注入（遅延・無応答・強制 status）を検証する。
"""
import pytest

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


class TestAckDelay:
    """ACK 注入: 遅延（コマンド処理全体＝検証→ACK→実行を N Tick 遅らせる）。"""

    def test_delay_defers_validate_ack_and_execute_until_fire(self):
        m, s, h = make()
        m.set_ack_delay(5)
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 100)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_PRESET, 1))

        # commit 後 4 Tick までは検証・ACK・実行・記録のいずれも発生しない
        for _ in range(4):
            m.tick()
        assert s.sent == []
        assert h.validated == []
        assert h.executed == []
        assert m.snapshot().last_command is None

        m.tick()  # 5 Tick 目で発火
        assert s.sent == [(cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))]
        assert h.validated == [(cc_map.OP_SET_PRESET, 100, 0)]
        assert h.executed == [(cc_map.OP_SET_PRESET, 100, 0)]
        cmd = m.snapshot().last_command
        assert (cmd.opcode, cmd.status, cmd.seq) == (
            cc_map.OP_SET_PRESET, cc_map.STATUS_OK, 1
        )

    def test_ack_sent_before_execute_when_delayed(self):
        # 遅延発火時も「ACK 送信後に実行」の仕様順序を保つ
        order = []
        m = Messaging(
            send_cc=lambda cc, value: order.append("ack"),
            validate_command=lambda opcode, a1, a2: cc_map.STATUS_OK,
            execute_command=lambda opcode, a1, a2: order.append("execute"),
        )
        m.set_ack_delay(2)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_RESET, 0))
        m.tick()
        m.tick()
        assert order == ["ack", "execute"]

    def test_validate_runs_at_fire_time_against_current_state(self):
        # 検証は発火時＝ACK 時点の状態に対して行う（保留中の状態変化と ACK が整合する）
        m, s, h = make()
        m.set_ack_delay(3)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_MODE, 0))
        h.status = cc_map.STATUS_REJECTED  # 保留中に検証結果が変わる状況を模擬
        for _ in range(3):
            m.tick()
        _cc, value = s.sent[-1]
        assert cc_map.payload_of(value) == cc_map.STATUS_REJECTED
        assert h.executed == []

    def test_args_bound_and_consumed_at_commit(self):
        m, _s, h = make()
        m.set_ack_delay(2)
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 50)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_PRESET, 0))
        # 保留中に次コマンドの引数が届いても、保留分の引数には混ざらない
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 99)
        m.tick()
        m.tick()
        assert h.validated == [(cc_map.OP_SET_PRESET, 50, 0)]

    def test_two_commands_fire_fifo_with_arrival_spacing(self):
        m, s, h = make()
        m.set_ack_delay(10)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        for _ in range(3):
            m.tick()
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        for _ in range(7):
            m.tick()  # 1 件目が commit から 10 Tick 到達
        acks = [cc_map.seq_of(v) for cc, v in s.sent if cc == cc_map.CMDRSP_STATUS_CC]
        assert acks == [0]
        for _ in range(3):
            m.tick()  # 2 件目も 10 Tick 到達（到着間隔 3 Tick を保って発火）
        acks = [cc_map.seq_of(v) for cc, v in s.sent if cc == cc_map.CMDRSP_STATUS_CC]
        assert acks == [0, 1]
        assert len(h.executed) == 2

    def test_shortening_delay_does_not_reorder_queue(self):
        # 遅延を途中で短縮しても FIFO 順は保存（後続が先頭を追い越さない）
        m, s, _h = make()
        m.set_ack_delay(10)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))  # A
        m.tick()
        m.set_ack_delay(2)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))  # B
        for _ in range(2):
            m.tick()  # B は期限到来だが先頭 A が未到来のため待つ
        acks = [cc_map.seq_of(v) for cc, v in s.sent if cc == cc_map.CMDRSP_STATUS_CC]
        assert acks == []
        for _ in range(7):
            m.tick()  # A が 10 Tick 到達 → A, B の順で発火
        acks = [cc_map.seq_of(v) for cc, v in s.sent if cc == cc_map.CMDRSP_STATUS_CC]
        assert acks == [0, 1]

    def test_delay_zero_after_delay_restores_immediate(self):
        m, s, h = make()
        m.set_ack_delay(5)
        m.set_ack_delay(0)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        assert len(h.executed) == 1

    def test_set_ack_delay_rejects_negative(self):
        m, _s, _h = make()
        with pytest.raises(ValueError):
            m.set_ack_delay(-1)


class TestAckDrop:
    """ACK 注入: 無応答（ACK も実行もしない＝受信しなかった相当）。"""

    def test_drop_sends_nothing_and_skips_validate_and_execute(self):
        m, s, h = make()
        m.set_ack_delay(None)
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 7)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_PRESET, 1))
        for _ in range(RESPONSE_TIMEOUT_TICKS + 10):
            m.tick()
        assert s.sent == []
        assert h.validated == []
        assert h.executed == []

    def test_drop_records_command_as_dropped(self):
        m, _s, _h = make()
        m.set_ack_delay(None)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        cmd = m.snapshot().last_command
        assert cmd.dropped is True
        assert cmd.status is None
        assert (cmd.opcode, cmd.seq) == (cc_map.OP_PING, 1)

    def test_drop_consumes_args(self):
        m, _s, h = make()
        m.set_ack_delay(None)
        m.handle_incoming_cc(cc_map.CMD_ARG1_CC, 50)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_PRESET, 0))
        m.set_ack_delay(0)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        assert h.validated == [(cc_map.OP_PING, 0, 0)]  # 黙殺コマンドの ARG が残らない

    def test_drop_then_restore_resumes_normal_ack(self):
        m, s, h = make()
        m.set_ack_delay(None)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        m.set_ack_delay(0)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        assert s.sent == [(cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))]
        assert len(h.executed) == 1


class TestAckForcedStatus:
    """ACK 注入: 強制 status（検証結果を上書きして NG を返し、実行しない）。"""

    def test_forced_overrides_ok_and_skips_execute(self):
        m, s, h = make()  # validate は OK を返す
        m.set_ack_forced_status(cc_map.STATUS_REJECTED)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        assert s.sent[-1] == (
            cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_REJECTED, 1)
        )
        assert h.executed == []
        cmd = m.snapshot().last_command
        assert cmd.status == cc_map.STATUS_REJECTED
        assert cmd.forced is True

    def test_forced_overrides_validation_ng(self):
        m, s, _h = make(status=cc_map.STATUS_INVALID_ARG)
        m.set_ack_forced_status(cc_map.STATUS_UNKNOWN_OP)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_SET_MODE, 0))
        _cc, value = s.sent[-1]
        assert cc_map.payload_of(value) == cc_map.STATUS_UNKNOWN_OP

    def test_forced_reserved_value_keeps_seq_echo(self):
        # 仕様 STATUS 予約帯（4–63）の代表値。受信側の「未知の値は NG 扱い」規約の試験用
        m, s, _h = make()
        m.set_ack_forced_status(63)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        _cc, value = s.sent[-1]
        assert cc_map.payload_of(value) == 63
        assert cc_map.seq_of(value) == 1

    def test_forced_cleared_restores_normal(self):
        m, s, h = make()
        m.set_ack_forced_status(cc_map.STATUS_REJECTED)
        m.set_ack_forced_status(None)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        assert s.sent[-1] == (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        assert len(h.executed) == 1
        assert m.snapshot().last_command.forced is False

    def test_forced_with_delay_fires_late_with_forced_status(self):
        m, s, h = make()
        m.set_ack_delay(2)
        m.set_ack_forced_status(cc_map.STATUS_REJECTED)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        m.tick()
        assert s.sent == []
        m.tick()
        assert s.sent == [
            (cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_REJECTED, 0))
        ]
        assert h.executed == []

    def test_set_ack_forced_status_rejects_ok_and_out_of_range(self):
        m, _s, _h = make()
        with pytest.raises(ValueError):
            m.set_ack_forced_status(cc_map.STATUS_OK)  # 強制 OK は禁止（検証 NG の実行を防ぐ）
        with pytest.raises(ValueError):
            m.set_ack_forced_status(64)  # payload(bit0–5) 範囲外
        with pytest.raises(ValueError):
            m.set_ack_forced_status(-1)


class TestAckInjectionIsolation:
    """ACK 注入がイベント経路・clear_pending と独立であること。"""

    def test_event_channel_unaffected_by_injection(self):
        m, s, _h = make()
        m.set_ack_delay(None)
        m.set_ack_forced_status(cc_map.STATUS_REJECTED)
        assert m.send_event(cc_map.OP_PING) is True
        assert s.sent == [(cc_map.EVT_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))]
        m.handle_incoming_cc(cc_map.EVTRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 0))
        snap = m.snapshot()
        assert snap.event_pending is False
        assert snap.last_event_response.status == cc_map.STATUS_OK

    def test_event_timeout_still_counts_while_acks_queued(self):
        m, _s, _h = make()
        m.set_ack_delay(100)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        m.send_event(cc_map.OP_PING)
        for _ in range(RESPONSE_TIMEOUT_TICKS + 1):
            m.tick()
        assert m.snapshot().last_event_response.timed_out is True

    def test_clear_pending_keeps_ack_queue(self):
        # clear_pending はイベント保留のみ破棄する（保留中の遅延 ACK は発火する）
        m, s, h = make()
        m.set_ack_delay(3)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_RESET, 1))
        m.clear_pending()
        for _ in range(3):
            m.tick()
        assert s.sent == [(cc_map.CMDRSP_STATUS_CC, cc_map.pack_seq(cc_map.STATUS_OK, 1))]
        assert len(h.executed) == 1


class TestAckInjectionLog:
    """ACK 注入の on_log 通知（HUD を持つ層が print を注入する）。"""

    def test_on_log_reports_drop_and_delay(self):
        logs = []
        sender = FakeSender()
        handler = FakeHandler()
        m = Messaging(sender, handler.validate, handler.execute, on_log=logs.append)
        m.set_ack_delay(None)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        m.set_ack_delay(5)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 1))
        assert any("無応答" in line for line in logs)
        assert any("保留" in line for line in logs)

    def test_on_log_reports_forced_status(self):
        logs = []
        sender = FakeSender()
        handler = FakeHandler()
        m = Messaging(sender, handler.validate, handler.execute, on_log=logs.append)
        m.set_ack_forced_status(cc_map.STATUS_REJECTED)
        m.handle_incoming_cc(cc_map.CMD_OP_CC, cc_map.pack_seq(cc_map.OP_PING, 0))
        assert any("強制 status" in line for line in logs)
