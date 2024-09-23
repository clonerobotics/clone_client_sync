import time

from clone_client_sync.client_sync import ClientSync

client = ClientSync(hostname="ubuntu", address="10.0.0.24")
client.connnect()

for _ in range(100):
    pressures = [0] * client.async_client.number_of_muscles
    client.set_pressures(pressures)

    time.sleep(1 / 10)

    pressures = client.get_pressures()
    print(pressures)


print("Done")
client.disconnect()
