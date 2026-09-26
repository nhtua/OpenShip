def test_generate_plan_stream_method_exists():
    from src.agent import Agent
    agent = Agent()
    assert hasattr(agent, "generate_plan_stream")
    assert callable(agent.generate_plan_stream)