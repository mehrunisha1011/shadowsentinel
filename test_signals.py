"""
Tests for the low-level signal functions in build_graph.py: fuzzy UPI VPA
matching and geo-cell bucketing. These are the primitives everything else
depends on, so they're tested in isolation first.
"""
from build_graph import vpa_similar, vpa_base, geo_cell


def test_vpa_base_strips_trailing_digits_and_underscore_noise():
    assert vpa_base("rahulk91@ybl") == "rahulk"
    assert vpa_base("rahulk_a@paytm") == "rahulk_a".rstrip("0123456789_")


def test_vpa_similar_matches_rotating_suffix_same_base():
    assert vpa_similar("rahulk91@ybl", "rahulk92@paytm") is True
    assert vpa_similar("rahulk@ybl", "rahulk99@oksbi") is True


def test_vpa_similar_rejects_unrelated_identities():
    assert vpa_similar("rahulkumar@ybl", "priyasharma@ybl") is False


def test_vpa_similar_exact_match_is_always_similar():
    assert vpa_similar("same@ybl", "same@ybl") is True


def test_geo_cell_snaps_nearby_points_to_the_same_cell():
    # two points ~10m apart should land in the same ~60m cell
    lat, lon = 12.9352, 77.6245
    cell_a = geo_cell(lat, lon)
    cell_b = geo_cell(lat + 0.00009, lon + 0.00009)  # ~10m offset
    assert cell_a == cell_b


def test_geo_cell_separates_distant_points():
    cell_a = geo_cell(12.9352, 77.6245)          # Bengaluru
    cell_b = geo_cell(19.1197, 72.8468)          # Mumbai
    assert cell_a != cell_b
