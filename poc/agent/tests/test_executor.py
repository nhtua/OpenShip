def test_execute_shell_command():
    from src.executor import execute_tool
    result = execute_tool("shell.echo", "hello world")
    assert "hello world" in result


def test_execute_exec_command():
    from src.executor import execute_tool
    result = execute_tool("shell.date", "")
    assert len(result) > 0