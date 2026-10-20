def test_tool_registry_has_shell_commands():
    from src.tools import TOOL_REGISTRY
    assert "shell.echo" in TOOL_REGISTRY
    assert "shell.date" in TOOL_REGISTRY
    assert TOOL_REGISTRY["shell.echo"]["type"] == "shell"


def test_tool_registry_has_exec_commands():
    from src.tools import TOOL_REGISTRY
    assert "exec.curl" in TOOL_REGISTRY
    assert TOOL_REGISTRY["exec.curl"]["type"] == "exec"