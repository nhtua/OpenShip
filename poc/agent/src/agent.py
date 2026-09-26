import os
import uuid
from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
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
        self.checkpointer = InMemorySaver()
        self.config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    def load_workflow(self, path: str):
        self.workflow = parse_workflow(path)
        return self.workflow

    def generate_plan(self, feedback=None):
        """Use LLM to generate or regenerate the plan with tool selection."""
        workflow_desc = self.workflow["raw"]

        tools = """Available tools (choose the best one for each step):
- shell.echo: Print text to stdout. Usage: echo "message"
- shell.date: Get current date/time. Usage: date "+%Y-%m-%d"
- shell.xargs: Execute command with piped input. Usage: echo "text" | xargs -I{} command {}
- exec.curl: HTTP requests. Usage: curl -s -L https://example.com
- user.ask: Ask the user a question and wait for their response. Usage: "What is your name?"
  The user's response is stored as the step output and can be referenced as {step_N} in later steps.

IMPORTANT: For each step, you MUST select one of these tools. Do not output "auto" or "None"."""

        if feedback:
            prompt = f"""You are an AI agent that converts workflow descriptions into executable plans.

Workflow description:
{workflow_desc}

User feedback on previous plan: {feedback}

{tools}

Generate a revised execution plan that incorporates the user's feedback. For each step, select the most appropriate tool from the available tools list.

Output format (JSON):
{{
  "steps": [
    {{
      "order": 1,
      "description": "Step description",
      "tool": "shell.echo",
      "args": "command arguments"
    }}
  ]
}}"""
        else:
            prompt = f"""You are an AI agent that converts workflow descriptions into executable plans.

Workflow description:
{workflow_desc}

{tools}

Generate an execution plan. For each step in the workflow, select the most appropriate tool from the available tools list.

Output format (JSON):
{{
  "steps": [
    {{
      "order": 1,
      "description": "Step description",
      "tool": "shell.echo",
      "args": "command arguments"
    }}
  ]
}}"""

        response = self.llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024
        )
        self.plan = response
        return self.plan

    def compile_graph(self, plan=None):
        """Compile a plan to LangGraph. Uses LLM-generated plan if provided, otherwise original workflow."""
        if plan:
            # Parse the LLM-generated JSON plan
            import json
            import re
            # Extract JSON from markdown code block
            json_match = re.search(r'```json\s*(.*?)\s*```', plan, re.DOTALL)
            if json_match:
                plan_json = json.loads(json_match.group(1))
            else:
                plan_json = json.loads(plan)
            self.graph = compile_to_langgraph(plan_json, checkpointer=self.checkpointer)
        elif self.workflow:
            self.graph = compile_to_langgraph(self.workflow, checkpointer=self.checkpointer)
        else:
            raise ValueError("No workflow or plan available")
        return self.graph

    def execute(self, inputs=None):
        """Execute the compiled graph."""
        if not self.graph:
            self.compile_graph()
        if inputs is None:
            inputs = {}
        return self.graph.invoke({"inputs": inputs, "outputs": {}}, self.config)