import json
from src.state import ExecutorState

def test_executor_compile_node():
    from src.executor import compile_node
    plan_json = {"steps": [{"order": 1, "description": "test", "tool": "shell.echo", "args": "hello"}]}
    state = ExecutorState(plan=json.dumps(plan_json), thread_id="test")
    result = compile_node(state)
    assert result.graph is not None

def test_executor_execute_node():
    from src.executor import compile_node, execute_node
    plan_json = {"steps": [{"order": 1, "description": "test", "tool": "shell.echo", "args": "hello"}]}
    state = ExecutorState(plan=json.dumps(plan_json), thread_id="test-exec")
    state = compile_node(state)
    result = execute_node(state)
    assert result.outputs is not None
    assert 1 in result.outputs