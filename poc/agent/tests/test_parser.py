def test_parse_workflow():
    from src.parser import parse_workflow
    workflow = parse_workflow("tests/fixtures/simple_workflow.md")
    assert "raw" in workflow
    assert "Test Workflow" in workflow["raw"]
    assert "shell.echo" in workflow["raw"]