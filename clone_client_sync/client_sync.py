import asyncio
import logging
import threading
from typing import AsyncGenerator, List, Optional, cast
from clone_client.client import Client
import grpc

LOGGER = logging.getLogger(__name__)


class ClientSync:
    """Synchronous client of the clone communication client."""

    def __init__(self, hostname: Optional[str], address: Optional[str]):
        if not hostname and not address:
            raise ValueError("You need to provide either hostname or address.")

        self._async_client: Client = Client(server=hostname, address=address)
        self._pqueue_in: asyncio.Queue[List[float]] = asyncio.Queue()
        self._pqueue_out: asyncio.Queue[List[float]] = asyncio.Queue()
        self._thread = threading.Thread(target=self._run_in_background)

        self._busy = asyncio.Lock()
        self._stop = threading.Event()
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

        async def task():
            return await asyncio.wait_for(self._pqueue_out.get(), timeout=timeout)

        return asyncio.run(task())

    def set_pressures(self, pressures: List[float]) -> None:
        """
        Allows individual actuation of muscles (setting the pressure).

        Each value is a positive number in the range <0, 1>.
        Actuation values are normalized to the maximum pressure of the system
        and to the calibration values of each sensor to componesate minimal
        drift (1-3%).
        """
        self._pqueue_in.put_nowait(pressures)

    async def run(self) -> None:
        """Run the async client."""
        async with self._async_client as client:
            async def ctrl_generator() -> AsyncGenerator[List[float], None]:
                while not self._stop.is_set():
                    data = await self._pqueue_in.get()
                    if data is None:
                        LOGGER.info("Stopping control stream.")
                        break

                    yield data

            async def telemetry_consumer() -> AsyncGenerator[List[float], None]:
                async for telemetry in client.subscribe_telemetry():
                    if self._stop.is_set():
                        break

                    await self._pqueue_out.put(telemetry.pressures)

                LOGGER.info("Stopping telemetry stream.")

            self.connected.set()

            control_stream_task = asyncio.create_task(client.stream_set_pressures(ctrl_generator()))
            telemetry_stream_task = asyncio.create_task(telemetry_consumer())

            try:
                await asyncio.gather(control_stream_task, telemetry_stream_task)
            except grpc.aio.AioRpcError as err:
                err = cast(grpc.RpcError, err)
                if err.code() == grpc.StatusCode.CANCELLED:
                    LOGGER.info(err.details())
                else:
                    raise

    def _run_in_background(self) -> None:
        """Start the client."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.run())

    def connnect(self) -> None:
        """Initialize the client and wait for the connection to the server."""
        self._stop.clear()
        self._thread.start()
        self.connected.wait()

    def disconnect(self) -> None:
        """Disconnect the client."""
        self._pqueue_in.put_nowait(None)
        self._stop.set()
        self._thread.join()
        self.connected.clear()
        self._stop.clear()
