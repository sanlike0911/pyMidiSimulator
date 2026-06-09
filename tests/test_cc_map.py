"""cc_map 純粋関数のユニットテスト。"""
import pytest

import cc_map


class TestSplitCombine14bit:
    @pytest.mark.parametrize("value", [0, 1, 8192, 12345, 16383])
    def test_split_combine_roundtrip(self, value):
        msb, lsb = cc_map.split_14bit(value)
        assert cc_map.combine_14bit(msb, lsb) == value

    def test_split_center(self):
        assert cc_map.split_14bit(8192) == (64, 0)

    def test_split_max(self):
        assert cc_map.split_14bit(16383) == (127, 127)

    def test_split_zero(self):
        assert cc_map.split_14bit(0) == (0, 0)

    def test_split_clamps_out_of_range(self):
        assert cc_map.split_14bit(99999) == (127, 127)
        assert cc_map.split_14bit(-5) == (0, 0)


class TestNormalization:
    def test_bipolar_zero(self):
        assert cc_map.norm14_bipolar(0) == -1.0

    def test_bipolar_center(self):
        assert cc_map.norm14_bipolar(8192) == 0.0

    def test_bipolar_max_approaches_one(self):
        assert cc_map.norm14_bipolar(16383) == pytest.approx(0.9999, abs=1e-3)

    def test_bipolar_clamped_above(self):
        assert cc_map.norm14_bipolar(99999) == 1.0


class TestSeqCodec:
    @pytest.mark.parametrize("payload", [0, 1, 4, 63])
    @pytest.mark.parametrize("seq", [0, 1])
    def test_pack_unpack_roundtrip(self, payload, seq):
        value = cc_map.pack_seq(payload, seq)
        assert cc_map.payload_of(value) == payload
        assert cc_map.seq_of(value) == seq

    def test_pack_seq_known_values(self):
        assert cc_map.pack_seq(cc_map.CMD_SET_PRESET, 1) == 4 + 64
        assert cc_map.pack_seq(0, 0) == 0
        assert cc_map.pack_seq(63, 1) == 127

    def test_payload_and_seq_isolation(self):
        assert cc_map.payload_of(127) == 63
        assert cc_map.seq_of(127) == 1

    def test_seq_even_value_has_zero_seq(self):
        assert cc_map.seq_of(63) == 0
        assert cc_map.payload_of(63) == 63
