from datetime import time

import pytest

from terminus.quickadd import parse_quickadd, parse_time


def test_full_syntax():
    p = parse_quickadd("Write report #Work #deep ~3 @14:30")
    assert p.title == "Write report"
    assert p.tags == ["work", "deep"]
    assert p.estimate == 3
    assert p.fixed_start == time(14, 30)


def test_defaults():
    p = parse_quickadd("Inbox zero")
    assert (p.estimate, p.tags, p.fixed_start) == (1, [], None)


@pytest.mark.parametrize("text,expected", [("9", time(9)), ("930", time(9, 30)),
                                           ("0930", time(9, 30)), ("14:05", time(14, 5))])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_errors():
    with pytest.raises(ValueError):
        parse_quickadd("#only-tags ~2")
    with pytest.raises(ValueError):
        parse_time("25:00")
