from contextlib import contextmanager
from time import perf_counter, sleep


@contextmanager
def busy_ticker(dur: float, precision: float = 5, min_tick: float = 0.0005):
    """
    A synchronous implementation of async_busy_ticker used in clone_client
    ensuring precise timing of the loop.
    See original implementation in clone_client/utils.py.
    """

    next_tick = perf_counter() + dur

    yield

    elapsed = perf_counter() - next_tick
    # Sleep a fraction to save some resources
    sleep(max(0, (dur - elapsed) / precision))

    # Busy sleep until next tick for precise timing
    while perf_counter() < next_tick:
        sleep(min_tick)


