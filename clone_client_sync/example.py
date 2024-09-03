import time

from clone_client_sync.client_sync import ClientSync

client = ClientSync(hostname="ubuntu", address="127.0.0.1")
client.connnect()

for _ in range(1000):
    pressures = [0] * client.async_client.number_of_muscles
    client.set_pressures(pressures)
    time.sleep(0.001)
    pressures = client.get_pressures()

    print(pressures)

client.disconnect()
