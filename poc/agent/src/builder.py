from pathlib import Path
from .state import BuilderState

def load_workflow_node(state: BuilderState) -> BuilderState:
    content = Path(state.workflow_path).read_text()
    return state.model_copy(update={"workflow_content": content})