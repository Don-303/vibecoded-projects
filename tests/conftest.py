from datetime import datetime, time, timedelta

import pytest

from terminus.config import Config
from terminus.db import Database

DAY = datetime(2026, 9, 25).date()


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def config(tmp_path):
    # brief_time=None: no automatic brief (it would hit the real news feeds)
    return Config(db_path=tmp_path / "t.db", day_start=time(8), day_end=time(20),
                  pomodoro=timedelta(minutes=25), brief_time=None)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return FakeClock(datetime.combine(DAY, time(10, 0)))


def gmail_id(when: datetime, n: int = 0) -> str:
    """A realistic Gmail message id for ``when`` (ms timestamp in the high bits)."""
    return format((int(when.timestamp() * 1000) << 20) | n, "x")
