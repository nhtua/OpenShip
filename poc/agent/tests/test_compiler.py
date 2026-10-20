def test_compile_simple_workflow():
    from src.compiler import compile_to_langgraph
    workflow = {
        "title": "Test",
        "inputs": {},
        "steps": [
            {"order": 1, "description": "Say hello", "tool": "shell.echo", "args": "hello"}
        ]
    }
    graph = compile_to_langgraph(workflow)
    assert graph is not None