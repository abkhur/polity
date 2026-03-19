"""Tests for neutral label aliasing."""

from src.state import NEUTRAL_LABEL_MAP, REVERSE_LABEL_MAP


class TestLabelMaps:
    def test_neutral_map_has_all_society_ids(self):
        assert "democracy_1" in NEUTRAL_LABEL_MAP
        assert "oligarchy_1" in NEUTRAL_LABEL_MAP
        assert "blank_slate_1" in NEUTRAL_LABEL_MAP

    def test_neutral_map_has_all_roles(self):
        assert "oligarch" in NEUTRAL_LABEL_MAP
        assert "citizen" in NEUTRAL_LABEL_MAP
        assert "leader" in NEUTRAL_LABEL_MAP

    def test_neutral_map_has_governance_types(self):
        assert "democracy" in NEUTRAL_LABEL_MAP
        assert "oligarchy" in NEUTRAL_LABEL_MAP
        assert "blank_slate" in NEUTRAL_LABEL_MAP

    def test_neutral_labels_contain_no_loaded_terms(self):
        loaded_terms = {"democracy", "oligarchy", "oligarch", "citizen", "leader", "blank_slate"}
        for neutral in NEUTRAL_LABEL_MAP.values():
            for term in loaded_terms:
                assert term not in neutral.lower(), f"Neutral label '{neutral}' contains loaded term '{term}'"

    def test_reverse_map_inverts_neutral_map(self):
        for internal, neutral in NEUTRAL_LABEL_MAP.items():
            assert REVERSE_LABEL_MAP[neutral] == internal

    def test_maps_are_same_length(self):
        assert len(NEUTRAL_LABEL_MAP) == len(REVERSE_LABEL_MAP)
