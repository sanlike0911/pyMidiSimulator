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
