"""SDK clients of one home are constructed one at a time.

Build 35, clean install: right after the model was activated, the write
drain's client and a UI read client started in the same second. Each opened
Chroma on the same empty vector directory, Chroma's shared System cache has
no lock around that first open, and neither client reached `VectorStore
ready`. The first write job was lost as unknown.
"""

import threading
import time

from one_memory import hy_sdk_runtime
from one_memory.hy_sdk_runtime import acquire_client, builtin_retrieval_snapshot


def test_two_clients_on_a_fresh_home_are_constructed_one_at_a_time(tmp_path, monkeypatch):
    real = hy_sdk_runtime.HyMemoryClient
    state = {"inside": 0, "most": 0}
    count = threading.Lock()

    def recording_client(**options):
        with count:
            state["inside"] += 1
            state["most"] = max(state["most"], state["inside"])
        try:
            # Widen the window two unserialized constructors would share.
            time.sleep(0.2)
            return real(**options)
        finally:
            with count:
                state["inside"] -= 1

    monkeypatch.setattr(hy_sdk_runtime, "HyMemoryClient", recording_client)
    start = threading.Barrier(2)
    leases: list[hy_sdk_runtime.ClientLease] = []
    failures: list[BaseException] = []

    def open_client():
        start.wait()
        try:
            leases.append(
                acquire_client(builtin_retrieval_snapshot(), api_key=None, data_dir=tmp_path)
            )
        except BaseException as error:  # noqa: BLE001 - reported below
            failures.append(error)

    threads = [threading.Thread(target=open_client) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    try:
        assert failures == []
        assert len(leases) == 2
        assert state["most"] == 1
        for lease in leases:
            assert lease.client.memory_stats(user_id="local") is not None
    finally:
        for lease in leases:
            lease.close()
