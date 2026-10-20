import hashlib
import json
from pathlib import Path


class GraphCache:
    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_checksum(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def get_cache_path(self, checksum: str) -> Path:
        return self.cache_dir / f"{checksum}.json"

    def save(self, checksum: str, graph_data: dict) -> None:
        path = self.get_cache_path(checksum)
        path.write_text(json.dumps(graph_data))

    def load(self, checksum: str) -> dict | None:
        path = self.get_cache_path(checksum)
        if path.exists():
            return json.loads(path.read_text())
        return None