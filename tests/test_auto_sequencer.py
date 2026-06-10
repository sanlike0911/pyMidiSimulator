"""AutoSequencer 純粋ロジックのユニットテスト。"""
import cc_map
from auto_sequencer import ActionKind, AutoSequencer, Phase, SendAction


def _make() -> AutoSequencer:
    """テスト用にステップを大きめにして少 Tick で各フェーズを通過させる。"""
    return AutoSequencer(stick_step=4096, button_hold_ticks=2, cc_step=64)


class TestInit:
    def test_send_action_defaults_log_to_none(self):
        action = SendAction(ActionKind.AXIS, 0, cc_map.CENTER_14BIT)
        assert action.kind is ActionKind.AXIS
        assert action.target == 0
        assert action.value == cc_map.CENTER_14BIT
        assert action.log is None

    def test_initial_phase_is_stick(self):
        seq = _make()
        assert seq._phase is Phase.STICK

    def test_initial_axis_starts_at_center(self):
        seq = _make()
        assert seq._axis_index == 0
        assert seq._axis_value == cc_map.CENTER_14BIT


class TestStickPhase:
    def test_axis0_sweeps_center_max_min_center(self):
        # stick_step=4096: 8192→(12288→16383)→(12287→…→0)→(4096→8192) と往復
        seq = AutoSequencer(stick_step=4096, button_hold_ticks=2, cc_step=64)

        values = []
        # 軸0 が中心へ復帰する瞬間まで AXIS アクションを集める
        for _ in range(20):
            actions = seq.tick(event_pending=False)
            axis_actions = [a for a in actions if a.kind is ActionKind.AXIS and a.target == 0]
            values.extend(a.value for a in axis_actions)
            if seq._axis_index != 0:  # 軸0完了 → 次軸へ進んだ
                break

        assert values[0] == 12288            # 8192 + 4096（上昇開始）
        assert max(values) == cc_map.MAX_14BIT   # 16383 に到達
        assert min(values) == 0                  # 0 に到達
        assert values[-1] == cc_map.CENTER_14BIT # 8192 へ復帰して軸完了

    def test_processes_four_axes_then_enters_button_phase(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=2, cc_step=64)
        # stick_step を最大にすると 1 Tick で各 leg が端へ到達 → 軸あたり 3 Tick
        seen_axes = set()
        for _ in range(50):
            if seq._phase is Phase.BUTTON:
                break
            for a in seq.tick(event_pending=False):
                if a.kind is ActionKind.AXIS:
                    seen_axes.add(a.target)
        assert seen_axes == {0, 1, 2, 3}
        assert seq._phase is Phase.BUTTON

    def test_endpoints_carry_log_and_midpoints_do_not(self):
        seq = AutoSequencer(stick_step=4096, button_hold_ticks=2, cc_step=64)
        first = seq.tick(event_pending=False)[0]   # 12288（中間点）
        assert first.log is None
        second = seq.tick(event_pending=False)[0]   # 16383（上端）
        assert second.value == cc_map.MAX_14BIT
        assert second.log is not None


def _advance_to_phase(seq: AutoSequencer, phase: Phase, limit: int = 2000):
    """目的フェーズに到達するまで tick を回す（応答待ちは即解決扱い）。"""
    for _ in range(limit):
        if seq._phase is phase:
            return
        seq.tick(event_pending=False)
    raise AssertionError(f"{phase} に到達しませんでした")


class TestButtonPhase:
    def test_each_button_turns_on_then_off_in_order(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=2, cc_step=64)
        _advance_to_phase(seq, Phase.BUTTON)

        on_order, off_order = [], []
        for _ in range(200):
            if seq._phase is not Phase.BUTTON:
                break
            for a in seq.tick(event_pending=False):
                if a.kind is ActionKind.BUTTON and a.value == cc_map.MAX_7BIT:
                    on_order.append(a.target)
                elif a.kind is ActionKind.BUTTON and a.value == 0:
                    off_order.append(a.target)

        assert on_order == list(range(len(cc_map.BUTTON_CCS)))   # 0..9 を順に ON
        assert off_order == list(range(len(cc_map.BUTTON_CCS)))  # 0..9 を順に OFF

    def test_button_held_for_configured_ticks(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=3, cc_step=64)
        _advance_to_phase(seq, Phase.BUTTON)

        # ON の Tick
        on = seq.tick(event_pending=False)
        assert on[0].kind is ActionKind.BUTTON and on[0].value == cc_map.MAX_7BIT
        # 保持中（button_hold_ticks=3 未満）は何も出ない
        assert seq.tick(event_pending=False) == []
        assert seq.tick(event_pending=False) == []
        # 3 Tick 目で OFF
        off = seq.tick(event_pending=False)
        assert off[0].kind is ActionKind.BUTTON and off[0].value == 0

    def test_enters_scalar_phase_after_last_button(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=2, cc_step=64)
        _advance_to_phase(seq, Phase.SCALAR)
        assert seq._phase is Phase.SCALAR


def _collect_scalar_actions(seq: AutoSequencer) -> tuple[dict, list]:
    """SCALAR フェーズの全アクションを (CC別値列, CC出現順) で収集する。"""
    by_cc: dict = {}
    order: list = []
    for _ in range(300):
        if seq._phase is not Phase.SCALAR:
            break
        for a in seq.tick(event_pending=False):
            if a.kind is ActionKind.SCALAR:
                by_cc.setdefault(a.target, []).append(a.value)
                if a.target not in order:
                    order.append(a.target)
    return by_cc, order


class TestScalarPhase:
    def test_sweeps_scalars_in_cc_order(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=64)
        _advance_to_phase(seq, Phase.SCALAR)
        by_cc, order = _collect_scalar_actions(seq)

        # State(102) → Mode(103) → Error(104) → Preset(105) の CC 昇順で処理される
        assert order == [
            cc_map.STATE_CC, cc_map.MODE_CC, cc_map.ERROR_CC, cc_map.PRESET_CC
        ]
        # Mode 以外の各スカラーは 0 から始まり 127 で終わる
        for cc in (cc_map.STATE_CC, cc_map.ERROR_CC, cc_map.PRESET_CC):
            assert by_cc[cc][0] == 0
            assert by_cc[cc][-1] == cc_map.MAX_7BIT
            assert max(by_cc[cc]) == cc_map.MAX_7BIT  # 127 を超えない

    def test_mode_sends_only_valid_values_and_returns_to_normal(self):
        # Mode(CC103) はスイープせず有効 3 値のみ送信し、最後に必ず通常(0)へ復帰する
        # （無効値の大量送信と、非通常モードのまま終わる誤認を防ぐ）
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=64)
        _advance_to_phase(seq, Phase.SCALAR)
        by_cc, _order = _collect_scalar_actions(seq)
        assert by_cc[cc_map.MODE_CC] == [
            cc_map.MODE_VERSION_UP,
            cc_map.MODE_FACTORY_INSPECTION,
            cc_map.MODE_NORMAL,
        ]

    def test_mode_actions_always_carry_log(self):
        # Mode は値の意味が重要なため全送信をログ（モード名付き）で可視化する
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=64)
        _advance_to_phase(seq, Phase.SCALAR)
        for _ in range(300):
            if seq._phase is not Phase.SCALAR:
                break
            for a in seq.tick(event_pending=False):
                if a.kind is ActionKind.SCALAR and a.target == cc_map.MODE_CC:
                    assert a.log is not None

    def test_enters_event_phase_after_preset(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=64)
        _advance_to_phase(seq, Phase.EVENT)
        assert seq._phase is Phase.EVENT


class TestEventPhase:
    def test_sends_single_ping_waiting_for_response(self):
        # 確定イベントは Ping のみ（仕様: 方向 C→G / G⇄C の opcode は Ping だけ）
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)

        sent = []
        pending = False
        for _ in range(50):
            actions = seq.tick(event_pending=pending)
            events = [a for a in actions if a.kind is ActionKind.EVENT]
            if events:
                sent.append(events[0].target)
                pending = True
            else:
                pending = False
            if seq._phase is Phase.STICK:
                break

        assert sent == [cc_map.OP_PING]

    def test_does_not_advance_while_event_pending(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)
        first = seq.tick(event_pending=False)
        assert first[0].kind is ActionKind.EVENT
        assert seq.tick(event_pending=True) == []
        assert seq.tick(event_pending=True) == []

    def test_completing_event_phase_loops_back_to_stick(self):
        seq = AutoSequencer(stick_step=cc_map.MAX_14BIT, button_hold_ticks=1, cc_step=127)
        _advance_to_phase(seq, Phase.EVENT)
        for _ in range(20):
            seq.tick(event_pending=False)
            if seq._phase is Phase.STICK:
                break
        assert seq._phase is Phase.STICK
        assert seq._axis_index == 0
        assert seq._axis_value == cc_map.CENTER_14BIT
