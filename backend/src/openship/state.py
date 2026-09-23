from typing import TypedDict, Optional


class WorkflowState(TypedDict):
    requirements: str
    diagram: str
    terraform: str
    apply_output: str
    approved_diagram: bool
    approved_terraform: bool
    step: str
    done: bool
    error: Optional[str]


def create_state() -> WorkflowState:
    return {
        "requirements": "",
        "diagram": "",
        "terraform": "",
        "apply_output": "",
        "approved_diagram": False,
        "approved_terraform": False,
        "step": "idle",
        "done": False,
        "error": None,
    }
