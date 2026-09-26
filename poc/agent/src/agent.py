import os
from dotenv import load_dotenv
from .parser import parse_workflow
from .compiler import compile_to_langgraph
from .cache import GraphCache
from .llm_client import LLMClient


class Agent:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY", "sk-test")
        base_url = os.getenv("LOCAL_LLM_URL")
        self.llm = LLMClient(api_key=api_key, base_url=base_url)
        self.cache = GraphCache(".openship-poc-cache")
        self.workflow = None
        self.graph = None

    def load_workflow(self, path: str):
        self.workflow = parse_workflow(path)
        return self.workflow

    def compile_graph(self):
        if not self.workflow:
            raise ValueError("No workflow loaded")
        checksum = self.cache.compute_checksum(self.workflow["raw"])
        cached = self.cache.load(checksum)
        if cached:
            print("Using cached graph")
        else:
            self.graph = compile_to_langgraph(self.workflow)
            self.cache.save(checksum, {"compiled": True})
        return self.graph

    def execute(self, inputs: dict = None):
        if not self.graph:
            self.compile_graph()
        if inputs is None:
            inputs = {}
        return self.graph.invoke({"inputs": inputs, "outputs": {}})