"""Interleaving of the test set (pure function, synthetic items)."""

import random
from collections import Counter

from jobfacts.testset import _interleave, _spread


def test_first_thirty_contain_all_languages():
    items = (
        [{"language": "en", "i": i} for i in range(50)]
        + [{"language": "de", "i": i} for i in range(27)]
        + [{"language": "fr", "i": i} for i in range(23)]
    )
    out = _interleave(items, random.Random(1))
    assert len(out) == 100
    assert set(Counter(i["language"] for i in out[:30])) == {"en", "de", "fr"}


def test_spread_caps_per_key():
    items = [{"employer": e, "n": n} for e in "abc" for n in range(5)]
    out = _spread(items, "employer", 2, 10, random.Random(1))
    assert len(out) == 6 and max(Counter(i["employer"] for i in out).values()) == 2
