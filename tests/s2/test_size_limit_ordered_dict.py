from flexmeasures_client.s2.utils import SizeLimitOrderedDict


def test_a_full_history_keeps_the_newest_entries():
    """popitem() takes the newest by default, so a full history kept a session's first entries instead."""
    history = SizeLimitOrderedDict(max_size=3)
    for n in range(5):
        history[n] = n
    assert list(history) == [2, 3, 4]


def test_setting_a_key_already_present_evicts_nothing():
    history = SizeLimitOrderedDict(max_size=2)
    history["a"] = 1
    history["b"] = 2
    history["b"] = 3
    assert dict(history) == {"a": 1, "b": 3}


def test_made_from_more_than_fits_it_keeps_the_last():
    history = SizeLimitOrderedDict([(n, n) for n in range(5)], max_size=2)
    assert list(history) == [3, 4]
