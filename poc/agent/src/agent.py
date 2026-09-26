import os
from dotenv import load_dotenv
from .parser import parse_workflow
from .compiler import compile_to_langgraph
from .llm_client import LLMClient


class Agent:
    def __init__(self):
        load_dotenv()
        local_url = os.getenv("LOCAL_LLM_URL")
        if local_url:
            # Use local model (Llama.cpp, vLLM, etc.)
            model_name = os.getenv("MODEL_NAME", "local-model")
            self.llm = LLMClient(api_key="local", base_url=local_url, model=model_name)
            print(f"Using local LLM at {local_url} (model: {model_name})")
        else:
            # Use OpenAI API
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise EnvironmentError("Set either OPENAI_API_KEY or LOCAL_LLM_URL")
            self.llm = LLMClient(api_key=api_key)
            print("Using OpenAI API")
        self.workflow = None
        self.graph = None
        self.plan = None

    def load_workflow(self, path: str):
        self.workflow = parse_workflow(path)
        return self.workflow

    def generate_plan(self, feedback=None):
        """Use LLM to generate or regenerate the plan."""
        workflow_desc = self.workflow["raw"]

        if feedback:
            prompt = f"""Based on this workflow description and user feedback, generate a revised execution plan.

Workflow:
{workflow_desc}

User feedback: {feedback}

Available tools:
- shell.echo: Print text to stdout
- shell.date: Get current date/time
- shell.xargs: Execute command with arguments
- exec.curl: HTTP requests via curl

Output the plan as JSON with steps array, each step having: order, description, tool, args"""
        else:
            prompt = f"""Analyze this workflow description and generate an execution plan.

Workflow:
{workflow_desc}

Available tools:
- shell.echo: Print text to stdout
- shell.date: Get current date/time
- shell.xargs: Execute command with arguments
- exec.curl: HTTP requests via curl

Output the plan as JSON with steps array, each step having: order, description, tool, args"""

        response = self.llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024
        )
        self.plan = response
        return self.plan

    def compile_graph(self):
        """Compile the workflow to LangGraph."""
        if not self.workflow:
            raise ValueError("No workflow loaded")
        self.graph = compile_to_langgraph(self.workflow)
        return self.graph

    def execute(self, inputs=None):
        """Execute the compiled graph."""
        if not self.graph:
            self.compile_graph()
        if inputs is None:
            inputs = {}
        return self.graph.invoke({"inputs": inputs, "outputs": {}})