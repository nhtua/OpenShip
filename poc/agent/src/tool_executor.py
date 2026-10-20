import shlex
import subprocess

from .tools import TOOL_REGISTRY


def execute_tool(tool_name: str, args: str = "") -> str:
    tool = TOOL_REGISTRY[tool_name]
    if tool["type"] == "shell":
        # Strip outer quotes if present
        if args.startswith('"') and args.endswith('"'):
            args = args[1:-1]
        # Quote the args for shell safety
        cmd = f"{tool['cmd']} {shlex.quote(args)}"
        result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False)
    elif tool["type"] == "exec":
        result = subprocess.run([tool["cmd"], args], capture_output=True, text=True, check=False)
    elif tool["type"] == "interrupt":
        from langgraph.types import interrupt
        return interrupt({"type": "user_ask", "question": args})
    else:
        raise ValueError(f"Unknown tool type: {tool['type']}")
    return result.stdout + result.stderr