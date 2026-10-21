from hy_memory.config import MemoryConfig


def test_chain_block_defaults_disabled(monkeypatch):
    monkeypatch.delenv("MEMORY_CODING_CHAIN_BLOCK_ENABLED", raising=False)
    assert MemoryConfig().coding.chain_block_enabled is False


def test_chain_block_env_can_enable(monkeypatch):
    monkeypatch.setenv("MEMORY_CODING_CHAIN_BLOCK_ENABLED", "true")
    assert MemoryConfig().coding.chain_block_enabled is True


def test_chain_block_from_dict_can_enable(monkeypatch):
    monkeypatch.delenv("MEMORY_CODING_CHAIN_BLOCK_ENABLED", raising=False)
    config = MemoryConfig.from_dict({
        "coding": {"chain_block_enabled": True},
    })
    assert config.coding.chain_block_enabled is True
