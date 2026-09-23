import json
import time
from dataclasses import dataclass, asdict
from typing import Optional, Any


@dataclass
class WorkflowEvent:
    type: str
    stage: str
    message: str = ""
    data: Optional[dict] = None
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_sse(self) -> str:
        payload = {
            "type": self.type,
            "stage": self.stage,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        return f"event: {self.type}\ndata: {json.dumps(payload)}\n\n"

    @property
    def payload(self) -> dict:
        return asdict(self)
