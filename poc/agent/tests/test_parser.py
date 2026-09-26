def test_parse_workflow():
    from src.parser import parse_workflow
    workflow = parse_workflow("tests/fixtures/simple_workflow.md")
    assert workflow["title"] == "Test Workflow"
    assert "name" in workflow["inputs"]
    assert len(workflow["steps"]) == 1
    assert workflow["steps"][0]["tool"] == "shell.echo"