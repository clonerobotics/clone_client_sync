import warnings
from contextlib import contextmanager
from time import get_clock_info, perf_counter, perf_counter_ns, sleep
from typing import Generator


def time_nanosleep(p: int) -> None:
    """Wrapper for nanosleep function if C library import fails."""
    sleep(p * 1e-9)


try:
    import ctypes

    libc = ctypes.CDLL("libc.so.6")
    nanosleep = libc.nanosleep
except OSError:
    warnings.warn("Failed to import libc. Using python sleep instead.", ImportWarning)
    nanosleep = time_nanosleep


def precise_interval(
    interval: float, precision: float = 0.2
) -> Generator[None, None, None]:
    """
    Interval ticks for precise timeings.

    Parameters:
    - interval: Duration between each tick in seconds.
    - precision: The precision of the tick, higher precision means more resources used.
                 Smaller intervals require more precision.
    """
    if precision < 0 or precision > 1:
        raise ValueError("Precision must be between 0 and 1")

    if interval <= 0:
        raise ValueError("Interval must be greater than 0")

    interval_ns = int(interval * 1e9)
    resolution = get_clock_info("perf_counter").resolution
    min_tick_ns = int(resolution * 1e9)
    fraction = max(resolution, (1 - precision))

    try:
        while True:
            next_tick = perf_counter_ns() + interval_ns

            yield

            remaining = next_tick - perf_counter_ns()
            time_nanosleep(int(remaining * fraction))
            while perf_counter_ns() < next_tick:
                nanosleep(min_tick_ns)

    except GeneratorExit:
        pass


@contextmanager
def busy_ticker(dur: float, precision: float = 5, min_tick: float = 0.0005):
    """
    A synchronous implementation of async_busy_ticker used in clone_client
    ensuring precise timing of the loop.
    See original implementation in clone_client/utils.py.
    """
    warnings.warn(
        "This method is deprecated and will be removed in future versions. Use 'precise_interval' instead",
        DeprecationWarning,
    )
    next_tick = perf_counter() + dur

    yield

    elapsed = perf_counter() - next_tick
    # Sleep a fraction to save some resources
    sleep(max(0, (dur - elapsed) / precision))

    # Busy sleep until next tick for precise timing
    while perf_counter() < next_tick:
        sleep(min_tick)
