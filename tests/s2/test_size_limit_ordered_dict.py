from flexmeasures_client.s2.utils import SizeLimitOrderedDict


def test_a_full_history_keeps_the_newest_entries():
    """popitem() takes the newest by default, so a full history kept a session's first entries instead."""
    history = SizeLimitOrderedDict(max_size=3)
    for n in range(5):
        history[n] = n
    assert list(history) == [2, 3, 4]


def test_setting_a_key_already_present_evicts_nothing_and_makes_it_the_latest():
    history = SizeLimitOrderedDict(max_size=2)
    history["a"] = 1
    history["b"] = 2
    history["a"] = 3
    assert list(history.items()) == [("b", 2), ("a", 3)]


def test_made_from_more_than_fits_it_keeps_the_last():
    history = SizeLimitOrderedDict([(n, n) for n in range(5)], max_size=2)
    assert list(history) == [3, 4]
