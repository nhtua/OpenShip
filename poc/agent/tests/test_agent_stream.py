import os
from unittest.mock import patch

def test_generate_plan_stream_method_exists():
    from src.agent import Agent
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
        agent = Agent()
    assert hasattr(agent, "generate_plan_stream")
    assert callable(agent.generate_plan_stream)