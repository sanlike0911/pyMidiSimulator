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
