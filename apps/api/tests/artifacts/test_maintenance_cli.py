import json
import uuid

from src.openship.artifacts.maintenance import main
from src.openship.config import settings


def test_artifact_init_cli_explicitly_creates_configured_storage(tmp_path, monkeypatch, capsys):
    root = tmp_path / "artifact-volume"
    storage_id = uuid.uuid4()
    monkeypatch.setattr(settings, "artifact_root", root)
    monkeypatch.setattr(settings, "artifact_storage_id", storage_id)

    assert not root.exists()
    assert main(["init"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output == {"initialized": True, "storage_id": str(storage_id)}
    assert (root / ".storage-id").read_text().strip() == str(storage_id)
