import os
from unittest.mock import patch

def test_agent_initialization():
    from src.agent import Agent
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
        agent = Agent()
    assert agent is not None


def test_agent_load_workflow():
    from src.agent import Agent
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
        agent = Agent()
        workflow = agent.load_workflow("tests/fixtures/simple_workflow.md")
    assert "raw" in workflow
    assert "Test Workflow" in workflow["raw"]