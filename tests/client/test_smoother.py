"""The smoother: spikes die in the median, jitter in the mean, steps arrive with half a window of lag."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from flexmeasures_client.reaction import Smoother

T0 = datetime(2026, 12, 10, 18, 0, tzinfo=timezone.utc)
SAMPLE = timedelta(seconds=1)


def _run(smoother: Smoother, values: list[float]) -> list[float]:
    return [smoother.update(T0 + i * SAMPLE, v) for i, v in enumerate(values)]


def test_a_one_sample_spike_never_reaches_the_output():
    out = _run(
        Smoother(window=timedelta(seconds=5), median_samples=3),
        [0.0] * 5 + [4.0] + [0.0] * 5,
    )
    assert max(out) == 0.0


def test_jitter_is_averaged_out():
    out = _run(Smoother(window=timedelta(seconds=4), median_samples=1), [1.0, 3.0] * 10)
    assert out[-1] == pytest.approx(2.0)


def test_a_step_arrives_with_half_a_window_of_lag():
    out = _run(
        Smoother(window=timedelta(seconds=4), median_samples=1), [0.0] * 4 + [4.0] * 8
    )
    assert out[3] == 0.0
    assert out[4 + 1] == pytest.approx(2.0)  # halfway through the window
    assert out[-1] == pytest.approx(4.0)


def test_without_a_median_and_a_one_second_window_it_is_the_identity():
    values = [0.0, 2.0, -1.0, 5.0]
    assert (
        _run(Smoother(window=timedelta(seconds=1), median_samples=1), values) == values
    )


def test_reset_forgets_the_past():
    smoother = Smoother(window=timedelta(seconds=5), median_samples=1)
    _run(smoother, [4.0] * 5)
    smoother.reset()
    assert smoother.update(T0 + 10 * SAMPLE, 0.0) == 0.0


def test_samples_out_of_order_are_refused():
    smoother = Smoother()
    smoother.update(T0 + SAMPLE, 0.0)
    with pytest.raises(ValueError):
        smoother.update(T0, 0.0)
