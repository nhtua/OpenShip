"""Partition a pytest collection into deterministic, mutually exclusive CI shards."""

import os
from collections import Counter

import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    try:
        shard = int(os.environ["OPENSHIP_TEST_SHARD"])
        total = int(os.environ["OPENSHIP_TEST_SHARDS"])
    except (KeyError, ValueError) as exc:
        raise pytest.UsageError("set integer OPENSHIP_TEST_SHARD and OPENSHIP_TEST_SHARDS") from exc
    if total < 1 or not 0 <= shard < total:
        raise pytest.UsageError("shard must be between 0 and OPENSHIP_TEST_SHARDS - 1")

    # Keep each module together: some existing module-scoped fixtures share
    # state across tests. Greedy balancing keeps shard sizes similar.
    sizes = Counter(item.nodeid.split("::", 1)[0] for item in items)
    loads = [0] * total
    assignments = {}
    for path, count in sorted(sizes.items(), key=lambda entry: (-entry[1], entry[0])):
        target = min(range(total), key=loads.__getitem__)
        assignments[path] = target
        loads[target] += count

    selected = [item for item in items if assignments[item.nodeid.split("::", 1)[0]] == shard]
    deselected = [item for item in items if assignments[item.nodeid.split("::", 1)[0]] != shard]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
