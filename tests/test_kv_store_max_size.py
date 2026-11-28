"""KVStoreConfig.max_size is documented as the key ceiling of the memory backend."""

import unittest

from hy_memory.data.kv_store import KVStore, KVStoreConfig


def _store(max_size):
    return KVStore(KVStoreConfig(backend="memory", max_size=max_size))


def _keys(store):
    return sorted(store.keys())


class KVStoreMaxSizeTests(unittest.TestCase):
    def test_memory_backend_stays_within_max_size(self):
        store = _store(3)
        for index in range(10):
            store.set(f"k{index}", index)

        self.assertEqual(store.get_stats()["total_keys"], 3)
        # the oldest writes are the ones dropped; the newest value is always kept
        self.assertEqual(_keys(store), ["k7", "k8", "k9"])
        self.assertEqual(store.get("k9"), 9)
        self.assertIsNone(store.get("k6"))

    def test_overwriting_an_existing_key_does_not_evict_another_one(self):
        store = _store(2)
        store.set("a", 1)
        store.set("b", 2)
        store.set("a", 3)

        self.assertEqual(_keys(store), ["a", "b"])
        self.assertEqual(store.get("a"), 3)
        self.assertEqual(store.get("b"), 2)


if __name__ == "__main__":
    unittest.main()
