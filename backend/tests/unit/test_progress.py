"""Time-remaining estimates (ADR-0053): ranges from observed samples, confidence labels, and no
estimate without evidence."""

from app.services.progress import RETRY_FACTOR, estimate_remaining


def test_no_samples_means_no_estimate() -> None:
    estimate = estimate_remaining(10, [], parallel=2)
    assert (estimate.low_seconds, estimate.high_seconds, estimate.confidence) == (
        None,
        None,
        "none",
    )
    assert "nothing to estimate from" in estimate.explanation


def test_nothing_left_is_zero() -> None:
    estimate = estimate_remaining(0, [30.0], parallel=1)
    assert (estimate.low_seconds, estimate.high_seconds) == (0, 0)


def test_few_samples_give_a_wide_low_confidence_range() -> None:
    estimate = estimate_remaining(6, [10.0, 30.0], parallel=2)
    assert estimate.confidence == "low" and estimate.samples == 2
    assert estimate.low_seconds == 6 * 10 // 2
    assert estimate.high_seconds == round(6 * 30 * RETRY_FACTOR / 2)


def test_confidence_grows_with_samples_and_parallelism_shortens_the_range() -> None:
    samples = [20.0] * 4 + [40.0] * 6
    serial = estimate_remaining(10, samples, parallel=1)
    parallel = estimate_remaining(10, samples, parallel=4)
    assert (
        serial.confidence == "high"
        and estimate_remaining(10, samples[:5], 1).confidence == "medium"
    )
    assert serial.low_seconds is not None and parallel.low_seconds is not None
    assert parallel.low_seconds < serial.low_seconds
    assert serial.high_seconds is not None and serial.high_seconds >= serial.low_seconds
