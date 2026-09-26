import subprocess
from .tools import TOOL_REGISTRY


def execute_tool(tool_name: str, args: str = "") -> str:
    tool = TOOL_REGISTRY[tool_name]
    if tool["type"] == "shell":
        cmd = f"{tool['cmd']} {args}"
        result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    elif tool["type"] == "interrupt":
        from langgraph.types import interrupt
        return interrupt({"question": args})
    else:
        result = subprocess.run([tool["cmd"], args], capture_output=True, text=True)
    return result.stdout + result.stderr