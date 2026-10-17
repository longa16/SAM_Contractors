import pytest

from scripts.eval_screening import metrics


def test_metrics_use_human_ground_truth_and_handle_missing_positives():
    report = metrics(["RECOMMENDED", "RECOMMENDED", "PASS"], ["RECOMMENDED", "PASS", "PASS"])
    assert report["accuracy"] == pytest.approx(2 / 3)
    assert report["recommended_recall"] == 0.5
    assert metrics(["PASS"], ["PASS"])["recommended_recall"] is None
    with pytest.raises(ValueError):
        metrics([], [])
