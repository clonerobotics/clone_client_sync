import logging

from clone_client_sync.angle_estimator import Interpol
from clone_client_sync.client_sync import ClientSync
from clone_client_sync.utils import precise_interval

logging.basicConfig(level=logging.DEBUG)
interpol = Interpol("/path/to/interpol_mapping.json")
client = ClientSync("ubuntu", address="/run/clone")
client.connnect()

try:
    # Initialize a precise interval ticker
    tick = precise_interval(1 / 150, precision=1)
    while True:
        next(tick)  # Tick

        # mags = client.get_mags()

        # # Due to hardware limitation we always get 9 readouts,
        # # but we only need 3 first of them for the v1 finger
        # sensors = [
        #     mags[0].sensors[0],
        #     mags[0].sensors[1],
        #     mags[0].sensors[2],
        # ]

        # angles = interpol.get_angles(sensors)
        # print(angles)

        # The same but for V2 API
        # Assuming same order for the finger
        # Usually this should be verified against Node ID and documentation
        gr = client.get_gauss_rider()
        sensors = [
            gr[1].sensor,
            gr[2].sensor,
            gr[0].sensor,
        ]

        angles = interpol.get_angles(sensors)
        print()
        print(angles)

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
