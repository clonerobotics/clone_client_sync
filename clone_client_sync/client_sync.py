import asyncio
import enum
from queue import Queue
import threading
from typing import List, Optional
from clone_client.client import Client


class ReqType(enum.IntEnum):
    """Request type enumeration."""

    SET_PRESSURES = enum.auto()
    GET_PRESSURES = enum.auto()


class ClientSync:
    """Synchronous client of the clone communication client."""

    def __init__(self, hostname: Optional[str], address: Optional[str]):
        if not hostname and not address:
            raise ValueError("You need to provide either hostname or address.")

        self._async_client: Client = Client(server=hostname, address=address)
        self._req_queue: Queue[ReqType] = Queue()
        self._pqueue_in: Queue[List[float]] = Queue()
        self._pqueue_out: Queue[List[float]] = Queue()
        self._busy = threading.Lock()
        self._thread = threading.Thread(target=self._run_in_background)
        self.connected = threading.Event()

    @property
    def async_client(self) -> Client:
        """Return the underlying async client for all properties access."""
        return self._async_client

    def get_pressures(self, timeout: int = None) -> List[float]:
        """
        Returns current contraction reading for each muscle.

        Each reading is a positive number in the range <0, 1>.
        Values are normalized to the maximum pressure of the system
        and to calibration values of each sensor to compensate minimal
        drift (1-3%).
        """

        self._req_queue.put(ReqType.GET_PRESSURES)
        return self._pqueue_out.get(timeout=timeout)

    def set_pressures(self, pressures: List[float]) -> None:
        """
        Allows individual actuation of muscles (setting the pressure).

        Each value is a positive number in the range <0, 1>.
        Actuation values are normalized to the maximum pressure of the system
        and to the calibration values of each sensor to componesate minimal
        drift (1-3%).
        """
        self._req_queue.put(ReqType.SET_PRESSURES)
        self._pqueue_in.put(pressures)
        with self._busy:
            pass

    async def run(self) -> None:
        """Run the async client."""
        async with self._async_client as client:
            self.connected.set()
            while True:
                req = self._req_queue.get()
                with self._busy:
                    if req == ReqType.GET_PRESSURES:
                        tele = await client.get_telemetry()
                        pressures = tele.pressures
                        self._pqueue_out.put(pressures)
                    elif req == ReqType.SET_PRESSURES:
                        pressures = self._pqueue_in.get()
                        await client.set_pressures(pressures)
                    elif req is None:
                        break

    def _run_in_background(self) -> None:
        """Start the client."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.run())

    def connnect(self) -> None:
        """Initialize the client and wait for the connection to the server."""
        self._thread.start()
        self.connected.wait()

    def disconnect(self) -> None:
        """Disconnect the client."""
        self._req_queue.put(None)
        self._thread.join()
        self.connected.clear()
