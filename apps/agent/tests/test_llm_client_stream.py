def test_stream_chat_method_exists():
    from src.llm_client import LLMClient
    client = LLMClient(api_key="test-key", base_url="http://localhost:8080")
    assert hasattr(client, "stream_chat")
    assert callable(client.stream_chat)