import logging
import time

from clone_client_sync.client_sync import ClientSync
from clone_client_sync.utils import precise_interval

logging.basicConfig(level=logging.DEBUG)
client = ClientSync("ubuntu", address="/run/clone")
client.connnect()

try:
    # Access underlying client for properties
    # See async client documentation for more details
    muscle_order = client.async_client.muscle_order
    print(muscle_order)

    # To run async functions that are available in the async client
    # you can call it through the run_in_aioloop method
    system_info = client.run_in_aioloop(client.async_client.get_system_info())
    print(system_info.calibration_data)
    client.run_in_aioloop(client.async_client.loose_all())

    # Sleep to allow the system to loose all muscles
    time.sleep(3)

    # Initialize a precise interval ticker
    tick = precise_interval(1 / 50, precision=1)
    for _ in range(1000):
        next(tick)  # Tick

        pressures = [0.0] * client.async_client.number_of_muscles
        client.set_pressures(pressures)

        # Get pressures waits for available telemetry readout
        pressures = client.get_pressures()
        print(pressures)

        # You can also get magnetic data
        # [BETA] V1
        mags = client.get_mags()
        print(mags)

        # [BETA] V2
        gr = client.get_gauss_rider()
        print(gr)

        # Or IMU based qpos [BETA]
        qpos = client.get_qpos()
        print(qpos)

        # Or get the whole telemetry data at once
        telemetry = client.get_telemetry()
        print(telemetry)

        # All methods above behave in the same way, i.e. they wait for the next available
        # telemetry readout

        # In the current scenario all calls above would return data from different
        # telemetry ticks.
except Exception as exc:
    # Handle any exceptions here
    logging.error(f"An error occurred: {exc}")
    raise exc
finally:
    # Make sure to cleanup client by disconnecting at the end at all times.
    # ClientSync uses threading and any exception that stops the main thread would
    # not stop the background thread from running if not explicitly stopped
    # leaving program in a zombie state.
    client.disconnect()
