def test_agent_initialization():
    from src.agent import Agent
    agent = Agent()
    assert agent is not None


def test_agent_load_workflow():
    from src.agent import Agent
    agent = Agent()
    workflow = agent.load_workflow("tests/fixtures/simple_workflow.md")
    assert workflow["title"] == "Test Workflow"