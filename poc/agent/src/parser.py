from pathlib import Path


def parse_workflow(path: str):
    """Read workflow.md and return its content. LLM will parse and generate the plan."""
    content = Path(path).read_text()
    return {"raw": content}