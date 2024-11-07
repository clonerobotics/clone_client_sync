from contextlib import contextmanager
from time import perf_counter, perf_counter_ns, sleep
from typing import Generator
import warnings

import ctypes
try:
    libc = ctypes.CDLL('libc.so.6')
    nanosleep = libc.nanosleep
except OSError:
    warnings.warn("Failed to import libc. Using python sleep instead.", ImportWarning)

    def nanosleep(p: int) -> None:
        """Wrapper for nanosleep function if C library import fails."""
        sleep(p / 1e9)


def precise_interval(interval: float, tolerance: float = 1e-4) -> Generator[None, None, None]:
    """
    Interval ticks for precise timeings.

    Parameters:
    - interval: Duration between each tick in seconds.
    - tolerance: Acceptable drift in seconds for each tick.
    """
    interval_ns = int(interval * 1e9)
    tolerance_ns = int(tolerance * 1e9)
    next_tick = perf_counter_ns() + interval_ns

    try:
        while True:
            remaining = next_tick - perf_counter_ns()

            if remaining > tolerance_ns:
                nanosleep(remaining - tolerance_ns)
            elif remaining > 0:
                nanosleep(remaining)

            yield

            next_tick += interval_ns
    except GeneratorExit:
        pass


@contextmanager
def busy_ticker(dur: float, precision: float = 5, min_tick: float = 0.0005):
    """
    A synchronous implementation of async_busy_ticker used in clone_client
    ensuring precise timing of the loop.
    See original implementation in clone_client/utils.py.
    """
    warnings.warn("This method is deprecated and will be removed in future versions. Use 'precise_interval' instead", DeprecationWarning)
    next_tick = perf_counter() + dur

    yield

    elapsed = perf_counter() - next_tick
    # Sleep a fraction to save some resources
    sleep(max(0, (dur - elapsed) / precision))

    # Busy sleep until next tick for precise timing
    while perf_counter() < next_tick:
        sleep(min_tick)
