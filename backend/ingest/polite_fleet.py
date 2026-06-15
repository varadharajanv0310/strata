"""Polite fleet — reusable, resumable, well-mannered concurrent-fetch infra.

Extracted from the Common Crawl recovery (we got IP-blocked hammering CC and had to
slow down: concurrency 16→5, 0.12s pacing, 403-backoff + a re-block watchdog,
per-unit checkpointing). That discipline is exactly what separates "I scraped some
JSON" from "I run a fleet" — and the brainstorm's ATS-fleet / CC-index ingestion
will need it at 65k+ boards. So it lives here as composable infra, not buried in one
connector:

  * ``TokenBucket``    — per-host pacing (the 0.12s rate limit, generalized),
  * ``CircuitBreaker`` — per-host open/half-open/closed; stop hammering a host that
                         keeps failing,
  * ``BackoffPolicy``  — exponential backoff + cap (+ optional jitter),
  * ``Watchdog``       — global re-block detector (≥N rate-limit statuses → trip and
                         stop the whole sweep gracefully),
  * ``ParquetCheckpoint`` — landed rows + done-unit set persisted to Parquet, so a
                         multi-hour sweep resumes mid-flight,
  * ``PoliteFleet``    — bounded-concurrency orchestrator wiring them together.

This module performs **no network I/O itself** — it takes a user ``fetch_fn(unit)``
and runs it politely. Clocks/sleeps are injectable so the behaviour is unit-tested
offline (no real waiting, no real requests). Connectors migrate onto it incrementally
(common_crawl.py keeps its proven inline logic until then).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from backend.core.logging import get_logger

log = get_logger("ingest.polite_fleet")

# rate-limit / transient HTTP statuses worth retrying with backoff
RETRY_STATUS = frozenset({403, 429, 500, 502, 503, 504})
BLOCK_STATUS = frozenset({403, 429})  # these signal an IP rate-limit block


class FleetRetry(Exception):
    """Raise from ``fetch_fn`` to request a backoff+retry, carrying the HTTP status."""

    def __init__(self, status: int, msg: str = ""):
        super().__init__(msg or f"retryable status {status}")
        self.status = status


@dataclass
class BackoffPolicy:
    base: float = 0.5
    factor: float = 2.0
    cap: float = 60.0

    def delay(self, attempt: int) -> float:
        return min(self.cap, self.base * (self.factor ** attempt))


class TokenBucket:
    """Per-host pacing. ``rate`` tokens/sec, ``burst`` capacity. Thread-safe."""

    def __init__(self, rate: float, burst: float = 1.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rate = rate
        self.burst = max(burst, 1.0)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.burst
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a token is available; returns the seconds waited."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            wait = (1.0 - self._tokens) / self.rate
        self._sleep(wait)
        with self._lock:
            self._last = self._clock()
            self._tokens = 0.0
        return wait


class CircuitBreaker:
    """Per-host breaker: closed → (fail_threshold consecutive fails) → open →
    (reset_timeout elapsed) → half-open → success closes / fail re-opens."""

    def __init__(self, fail_threshold: int = 5, reset_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.fail_threshold = fail_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._fails = 0
        self._opened_at = 0.0
        self.state = "closed"
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == "open":
                if self._clock() - self._opened_at >= self.reset_timeout:
                    self.state = "half_open"
                    return True
                return False
            return True

    def on_success(self):
        with self._lock:
            self._fails = 0
            self.state = "closed"

    def on_failure(self):
        with self._lock:
            self._fails += 1
            if self.state == "half_open" or self._fails >= self.fail_threshold:
                self.state = "open"
                self._opened_at = self._clock()


class Watchdog:
    """Global re-block detector — trips after ``trip_after`` block statuses so the
    whole sweep stops gracefully instead of digging the IP-block deeper."""

    def __init__(self, trip_after: int = 50, block_status=BLOCK_STATUS):
        self.trip_after = trip_after
        self.block_status = block_status
        self._count = 0
        self._lock = threading.Lock()

    def record(self, status: int) -> None:
        if status in self.block_status:
            with self._lock:
                self._count += 1

    @property
    def count(self) -> int:
        return self._count

    @property
    def tripped(self) -> bool:
        return self._count >= self.trip_after


class ParquetCheckpoint:
    """Resumable landed-rows + done-unit store, persisted to Parquet.

    ``mark_done(unit, rows)`` appends and flushes; on restart ``is_done`` skips
    completed units so a killed sweep continues where it stopped.
    """

    def __init__(self, path: str | Path, flush_every: int = 1):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.flush_every = flush_every
        self._rows: list[dict] = []
        self._done: set[str] = set()
        self._lock = threading.Lock()
        self._since_flush = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            import pandas as pd
            df = pd.read_parquet(self.path)
            self._rows = df.to_dict("records")
            self._done = {str(u) for u in df.get("_unit", pd.Series(dtype=str)).dropna().unique()}
            log.info("checkpoint resumed: %d rows, %d done units (%s)",
                     len(self._rows), len(self._done), self.path.name)
        except Exception as e:  # noqa: BLE001 — corrupt/partial checkpoint: start clean
            log.warning("checkpoint load failed (%s) — starting fresh", e)

    def is_done(self, unit: str) -> bool:
        return str(unit) in self._done

    def mark_done(self, unit: str, rows: list[dict]) -> None:
        with self._lock:
            for r in rows:
                self._rows.append({**r, "_unit": str(unit)})
            self._done.add(str(unit))
            self._since_flush += 1
            if self._since_flush >= self.flush_every:
                self._flush_locked()

    def _flush_locked(self) -> None:
        try:
            import pandas as pd
            pd.DataFrame(self._rows).to_parquet(self.path, index=False)
            self._since_flush = 0
        except Exception as e:  # noqa: BLE001
            log.warning("checkpoint flush failed: %s", e)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def landed(self) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "_unit"} for r in self._rows]


@dataclass
class PoliteFleet:
    """Bounded-concurrency, per-host-paced, circuit-broken, checkpointed fetcher."""

    host_of: Callable[[object], str]
    max_workers: int = 5
    per_host_rate: float = 8.0          # tokens/sec/host (0.12s pacing ≈ 8/s)
    per_host_burst: float = 1.0
    max_retries: int = 4
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    breaker_threshold: int = 5
    breaker_reset: float = 30.0
    watchdog: Watchdog = field(default_factory=Watchdog)
    checkpoint: ParquetCheckpoint | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self):
        self._buckets: dict[str, TokenBucket] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self.stats = {"ok": 0, "skipped": 0, "dropped": 0, "retries": 0, "circuit_open": 0}

    def _bucket(self, host: str) -> TokenBucket:
        with self._lock:
            if host not in self._buckets:
                self._buckets[host] = TokenBucket(self.per_host_rate, self.per_host_burst,
                                                  self.clock, self.sleep)
            return self._buckets[host]

    def _breaker(self, host: str) -> CircuitBreaker:
        with self._lock:
            if host not in self._breakers:
                self._breakers[host] = CircuitBreaker(self.breaker_threshold,
                                                      self.breaker_reset, self.clock)
            return self._breakers[host]

    def _run_unit(self, unit, fetch_fn) -> list[dict]:
        host = self.host_of(unit)
        breaker = self._breaker(host)
        for attempt in range(self.max_retries + 1):
            if self.watchdog.tripped:
                raise RuntimeError("watchdog tripped — global rate-limit block")
            if not breaker.allow():
                self.stats["circuit_open"] += 1
                raise FleetRetry(0, f"circuit open for {host}")
            self._bucket(host).acquire()
            try:
                rows = fetch_fn(unit)
                breaker.on_success()
                return rows or []
            except FleetRetry as fr:
                self.watchdog.record(fr.status)
                breaker.on_failure()
                if attempt >= self.max_retries or self.watchdog.tripped:
                    raise
                self.stats["retries"] += 1
                self.sleep(self.backoff.delay(attempt))
            except Exception:  # noqa: BLE001 — non-retryable: count as a failure, drop unit
                breaker.on_failure()
                raise
        return []

    def run(self, units, fetch_fn) -> list[dict]:
        """Fetch every unit politely. Returns landed rows (incl. checkpoint resume)."""
        pending = []
        for u in units:
            if self.checkpoint and self.checkpoint.is_done(str(u)):
                self.stats["skipped"] += 1
                continue
            pending.append(u)
        landed: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self._run_unit, u, fetch_fn): u for u in pending}
            for fut in as_completed(futs):
                unit = futs[fut]
                try:
                    rows = fut.result()
                    self.stats["ok"] += 1
                    landed.extend(rows)
                    if self.checkpoint:
                        self.checkpoint.mark_done(str(unit), rows)
                except RuntimeError:  # watchdog tripped — stop gracefully
                    log.warning("fleet stopping early: watchdog tripped (%d blocks)",
                                self.watchdog.count)
                    break
                except Exception as e:  # noqa: BLE001
                    self.stats["dropped"] += 1
                    log.debug("unit %s dropped: %s", unit, e)
        if self.checkpoint:
            self.checkpoint.flush()
            landed = self.checkpoint.landed()
        log.info("polite fleet done: %s", self.stats)
        return landed
