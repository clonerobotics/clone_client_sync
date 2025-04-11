import asyncio
import logging
import threading
from typing import Any, AsyncGenerator, Coroutine, List, Optional, Sequence, TypeVar, cast

import grpc

from clone_client.client import Client
from clone_client.state_store.proto.state_store_pb2 import TelemetryData, MagneticHubRaw, GaussRiderRaw


LOGGER = logging.getLogger(__name__)
RT = TypeVar("RT")


class ClientSync:
    """Synchronous client of the clone communication client."""

    def __init__(self, hostname: Optional[str], address: Optional[str]):
        if not hostname and not address:
            raise ValueError("You need to provide either hostname or address.")

        self._async_client: Client = Client(server=hostname, address=address)
        self._latest_telemetry: TelemetryData
        self._latest_pressures: Sequence[float]
        self._latest_mag: Sequence[MagneticHubRaw]
        self._latest_gr: Sequence[GaussRiderRaw]

        self._thread = threading.Thread(target=self._run_in_background)
        self._trcv = threading.Event()
        self._pack = threading.Event()
        self.connected = threading.Event()
        self._stop: threading.Event = threading.Event()

        self.ready: asyncio.Event
        self._pqueue_in: asyncio.Queue[List[float]]
        self.aioloop: asyncio.BaseEventLoop

        self.tasks = []

    @property
    def async_client(self) -> Client:
        """Return the underlying async client for all properties access."""
        return self._async_client

    def run_in_aioloop(self, coro: Coroutine[Any, Any, RT], timeout: Optional[float] = 1) -> RT:
        """Run a coroutine in the current asyncio loop and wait for the result."""
        future = asyncio.run_coroutine_threadsafe(coro, self.aioloop)
        return future.result(timeout)

    def get_telemetry(self, timeout: Optional[int] = None) -> TelemetryData:
        """Return the latest telemetry data including IMUs and pressure readouts"""
        self._trcv.wait(timeout)
        telemetry = self._latest_telemetry
        self._trcv.clear()

        return telemetry

    def get_mags(self, timeout: Optional[int] = None) -> Sequence[MagneticHubRaw]:
        """Return the latest IMU data."""
        self._trcv.wait(timeout)
        mags = self._latest_mag
        self._trcv.clear()

        return mags
    
    def get_gauss_rider(self, timeout: Optional[int] = None) -> Sequence[GaussRiderRaw]:
        """Return the latest IMU data."""
        self._trcv.wait(timeout)
        gr = self._latest_gr
        self._trcv.clear()

        return gr

    def get_pressures(self, timeout: Optional[int] = None) -> Sequence[float]:
        """
        Returns current contraction reading for each muscle.

        Each reading is a positive number in the range <0, 1>.
        Values are normalized to the maximum pressure of the system
        and to calibration values of each sensor to compensate minimal
        drift (1-3%).
        """
        self._trcv.wait(timeout)
        pressures = self._latest_pressures
        self._trcv.clear()

        return pressures

    def set_pressures(self, pressures: List[float], timeout: Optional[int] = None) -> None:
        """
        Allows individual actuation of muscles (setting the pressure).

        Each value is a positive number in the range <0, 1>.
        Actuation values are normalized to the maximum pressure of the system
        and to the calibration values of each sensor to componesate minimal
        drift (1-3%).
        """
        self._pack.clear()
        self._pqueue_in.put_nowait(pressures)
        print(pressures)
        self._pack.wait(timeout)

    async def run(self) -> None:
        """Run the async client."""
        async with self._async_client as client:
            async def ctrl_generator() -> AsyncGenerator[List[float], None]:
                await self.ready.wait()
                while not self._stop.is_set():
                    try:
                        data = await self._pqueue_in.get()
                        if data is None:
                            LOGGER.info("Stopping control stream.")
                            return

                        yield data
                        self._pack.set()
                    except Exception as err:
                        LOGGER.exception(err)
                        raise err

            async def telemetry_consumer() -> AsyncGenerator[List[float], None]:
                await self.ready.wait()
                async for telemetry in client.subscribe_telemetry():

                    try:
                        if self._stop.is_set():
                            LOGGER.info("Stopping telemetry stream.")
                            return

                        self._latest_telemetry = telemetry
                        self._latest_pressures = telemetry.pressures
                        self._latest_mag = telemetry.magnetic_data
                        self._latest_gr = telemetry.gauss_rider_data
                        self._trcv.set()
                    except Exception as err:
                        LOGGER.exception(err)
                        raise err
                    


            control_stream_task = self.aioloop.create_task(client.stream_set_pressures(ctrl_generator()))
            telemetry_stream_task = self.aioloop.create_task(telemetry_consumer())
            self.tasks = [control_stream_task, telemetry_stream_task]

            self.connected.set()
            LOGGER.info("Connected to the robot.")

            self.ready.set()
            try:
                await control_stream_task
                await telemetry_stream_task
            except grpc.aio.AioRpcError as err:
                err = cast(grpc.RpcError, err)
                if err.code() == grpc.StatusCode.CANCELLED:
                    LOGGER.info(err.details())
                else:
                    LOGGER.exception(err)
            except asyncio.CancelledError:
                LOGGER.info("Task was cancelled.")
            finally:
                LOGGER.info("Closing the connection.")

    def _run_in_background(self) -> None:
        """Start the client."""
        self.ready = asyncio.Event()
        self._pqueue_in = asyncio.Queue()
        self.aioloop = asyncio.new_event_loop()

        self.aioloop.run_until_complete(self.run())

    def connnect(self) -> None:
        """Initialize the client and wait for the connection to the server."""
        self._stop.clear()
        self._thread.start()
        self.connected.wait()

    def disconnect(self) -> None:
        """Disconnect the client."""
        self._pqueue_in.put_nowait(None)
        self._stop.set()

        for task in self.tasks:
            task.cancel()

        self._thread.join()
        self.connected.clear()
