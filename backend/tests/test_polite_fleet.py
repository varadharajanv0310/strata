"""Offline tests for the polite-fleet harness — no network, no real waiting.

A FakeClock advances virtual time on sleep(), so token-bucket pacing, circuit-breaker
reset windows, and backoff are exercised deterministically and instantly.
"""
from __future__ import annotations

from backend.ingest.polite_fleet import (
    BackoffPolicy,
    CircuitBreaker,
    FleetRetry,
    ParquetCheckpoint,
    PoliteFleet,
    TokenBucket,
    Watchdog,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_backoff_is_exponential_and_capped():
    b = BackoffPolicy(base=0.5, factor=2.0, cap=10.0)
    assert b.delay(0) == 0.5
    assert b.delay(1) == 1.0
    assert b.delay(2) == 2.0
    assert b.delay(10) == 10.0  # capped


def test_token_bucket_paces():
    fc = FakeClock()
    tb = TokenBucket(rate=10.0, burst=1.0, clock=fc.now, sleep=fc.sleep)
    assert tb.acquire() == 0.0           # first token free (burst)
    waited = tb.acquire()                # must wait ~1/rate
    assert abs(waited - 0.1) < 1e-9
    assert fc.t >= 0.1                   # virtual time advanced


def test_circuit_breaker_opens_and_half_opens():
    fc = FakeClock()
    cb = CircuitBreaker(fail_threshold=3, reset_timeout=5.0, clock=fc.now)
    assert cb.allow()
    for _ in range(3):
        cb.on_failure()
    assert cb.state == "open"
    assert not cb.allow()                # blocked while open
    fc.t += 6.0                          # past reset window
    assert cb.allow()                    # half-open trial allowed
    cb.on_success()
    assert cb.state == "closed"


def test_watchdog_trips_on_blocks():
    wd = Watchdog(trip_after=3)
    wd.record(200); wd.record(500)       # non-block statuses ignored
    assert not wd.tripped
    wd.record(403); wd.record(429); wd.record(403)
    assert wd.tripped
    assert wd.count == 3


def test_checkpoint_roundtrip_resumes(tmp_path):
    cp = ParquetCheckpoint(tmp_path / "ck.parquet")
    cp.mark_done("unitA", [{"x": 1}, {"x": 2}])
    cp.mark_done("unitB", [{"x": 3}])
    cp.flush()
    # reload — a fresh instance must see the done units + rows
    cp2 = ParquetCheckpoint(tmp_path / "ck.parquet")
    assert cp2.is_done("unitA") and cp2.is_done("unitB")
    assert not cp2.is_done("unitC")
    assert len(cp2.landed()) == 3
    assert all("_unit" not in r for r in cp2.landed())


def test_fleet_paces_circuit_and_checkpoints(tmp_path):
    fc = FakeClock()
    # host "bad" always rate-limits (403); host "good" lands a row
    def fetch(unit):
        host, _id = unit
        if host == "bad":
            raise FleetRetry(403, "blocked")
        return [{"unit": _id}]

    units = [("good", i) for i in range(5)] + [("bad", i) for i in range(3)]
    fleet = PoliteFleet(
        host_of=lambda u: u[0],
        max_workers=2,
        per_host_rate=100.0,
        max_retries=2,
        backoff=BackoffPolicy(base=0.01),
        breaker_threshold=2,
        watchdog=Watchdog(trip_after=999),     # don't trip; we want drops not a halt
        checkpoint=ParquetCheckpoint(tmp_path / "fleet.parquet"),
        clock=fc.now, sleep=fc.sleep,
    )
    landed = fleet.run(units, fetch)
    assert len(landed) == 5                      # all good units landed
    assert fleet.stats["ok"] == 5
    assert fleet.stats["dropped"] == 3           # bad units exhausted retries / circuit
    assert fleet.stats["retries"] >= 1           # backoff actually fired

    # resume: a fresh fleet over the same units skips the 5 done ones
    fleet2 = PoliteFleet(
        host_of=lambda u: u[0], clock=fc.now, sleep=fc.sleep,
        checkpoint=ParquetCheckpoint(tmp_path / "fleet.parquet"),
    )
    fleet2.run([("good", i) for i in range(5)], fetch)
    assert fleet2.stats["skipped"] == 5
