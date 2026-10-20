import uuid

import pytest

from src.openship.artifacts.filesystem import LocalArtifactStore


@pytest.fixture
def store(tmp_path):
    result = LocalArtifactStore(tmp_path / "artifacts", uuid.uuid4())
    result.initialize()
    return result
