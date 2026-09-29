def test_llm_client_initialization():
    from src.llm_client import LLMClient
    client = LLMClient(api_key="test-key", base_url="http://localhost:8080")
    assert client.api_key == "test-key"
    assert client.base_url == "http://localhost:8080"