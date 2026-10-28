from types import SimpleNamespace

import pytest

from scripts.pytest_shard import pytest_collection_modifyitems


def test_shards_partition_collection_without_changing_order(monkeypatch):
    monkeypatch.setenv("OPENSHIP_TEST_SHARDS", "3")
    original = [SimpleNamespace(nodeid=f"test_{i // 2}.py::test_{i}") for i in range(11)]
    shards = []
    for index in range(3):
        monkeypatch.setenv("OPENSHIP_TEST_SHARD", str(index))
        deselected = []
        config = SimpleNamespace(
            hook=SimpleNamespace(pytest_deselected=lambda items, target=deselected: target.extend(items))
        )
        items = original.copy()
        pytest_collection_modifyitems(config, items)
        assert {item.nodeid for item in items + deselected} == {item.nodeid for item in original}
        assert items == [item for item in original if item in items]
        shards.append(items)
    assert sorted(item.nodeid for shard in shards for item in shard) == sorted(item.nodeid for item in original)
    for index, shard in enumerate(shards):
        paths = {item.nodeid.split("::")[0] for item in shard}
        assert all(not paths.intersection(item.nodeid.split("::")[0] for item in other)
                   for other in shards[index + 1:])


@pytest.mark.parametrize("shard,total", [("3", "3"), ("0", "0"), ("x", "3")])
def test_invalid_shard_configuration_fails(monkeypatch, shard, total):
    monkeypatch.setenv("OPENSHIP_TEST_SHARD", shard)
    monkeypatch.setenv("OPENSHIP_TEST_SHARDS", total)
    with pytest.raises(pytest.UsageError):
        pytest_collection_modifyitems(None, [])
