"""
Tests for cost_model.py's threshold sweep logic, using small hand-built
fixtures rather than the full synthetic dataset so failures are easy to
reason about.
"""
from cost_model import cost_at_threshold


def make_component(component_id, order_ids, risk_score):
    return {"component_id": component_id, "order_ids": order_ids, "risk_score": risk_score}


def test_higher_threshold_flags_fewer_or_equal_orders():
    components = [
        make_component("CC-1", ["a", "b"], risk_score=0.9),
        make_component("CC-2", ["c", "d"], risk_score=0.4),
    ]
    fraud_ids = {"a", "b"}
    amounts = {"a": 1000, "b": 1000, "c": 500, "d": 500}

    low = cost_at_threshold(components, fraud_ids, amounts, threshold=0.3)
    high = cost_at_threshold(components, fraud_ids, amounts, threshold=0.8)

    assert low["flagged"] >= high["flagged"]


def test_perfect_separation_has_zero_false_positives_and_negatives_at_the_right_threshold():
    components = [
        make_component("CC-1", ["fraud1", "fraud2"], risk_score=0.9),
        make_component("CC-2", ["legit1", "legit2"], risk_score=0.2),
    ]
    fraud_ids = {"fraud1", "fraud2"}
    amounts = {k: 1000 for k in ["fraud1", "fraud2", "legit1", "legit2"]}

    result = cost_at_threshold(components, fraud_ids, amounts, threshold=0.5)

    assert result["false_positives"] == 0
    assert result["false_negatives"] == 0
    assert result["true_positives"] == 2


def test_threshold_of_zero_flags_everything():
    components = [
        make_component("CC-1", ["a"], risk_score=0.1),
        make_component("CC-2", ["b"], risk_score=0.0),
    ]
    fraud_ids = {"a"}
    amounts = {"a": 1000, "b": 1000}

    result = cost_at_threshold(components, fraud_ids, amounts, threshold=0.0)
    assert result["flagged"] == 2


def test_threshold_above_all_scores_flags_nothing():
    components = [
        make_component("CC-1", ["a"], risk_score=0.5),
    ]
    fraud_ids = {"a"}
    amounts = {"a": 1000}

    result = cost_at_threshold(components, fraud_ids, amounts, threshold=1.1)
    assert result["flagged"] == 0
    assert result["false_negatives"] == 1
