# Clone Client Sync

Synchronous wrapper for async clone_client.
Currently only pressure controller is implemented but more functionalities will be added as they are needed.

## Installation

Please refer to <https://github.com/clonerobotics/clone_client> for detailed API and installation instructions.

## Usage

For details see the example file: [example.py](./clone_client_sync/example.py)

```python
import time

from clone_client_sync.client_sync import ClientSync

client = ClientSync(hostname="ubuntu", address=None)
client.connnect()

for _ in range(1000):
    pressures = [0] * client.async_client.number_of_muscles
    client.set_pressures(pressures)
    time.sleep(0.001)
    pressures = client.get_pressures()

    print(pressures)

client.disconnect()
```
