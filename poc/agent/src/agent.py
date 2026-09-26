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
        """Use LLM to generate or regenerate the plan with tool selection and variable passing."""
        workflow_desc = self.workflow["raw"]

        tools = """Available tools (choose the best one for each step):
- shell.echo: Print text to stdout. args: the text to print (no "echo" prefix)
- shell.date: Get current date/time. args: date format string quoted (e.g. "+%Y-%m-%d", no "date" prefix)
- shell.xargs: Execute command with piped input. args: the command to execute
- exec.curl: HTTP requests. args: the URL and options (no "curl" prefix)
- user.ask: Ask the user a question and wait for their response. args: the question text
  The user's response is stored as the step output and can be referenced as {step_N} in later steps.

IMPORTANT: For each step, you MUST select one of these tools. Do not output "auto" or "None".
The "args" field should only contain the arguments, not the tool name itself.
When an argument contains spaces, enclose it in quotes. Example: "+%Y-%m-%d %H:%M:%S" not +%Y-%m-%d %H:%M:%S"""

        plan_instructions = """
Read the workflow description carefully. Break it down into individual steps and select the appropriate tool for each.

When steps need to share data, use {step_N} placeholders where N is the step number. For example:
- Step 1: Ask user their name → output stored as step 1
- Step 2: Use that name → args: "Hello {step_1}!"

Each step MUST include these fields:
- order: step number (1, 2, 3...)
- description: human-readable description of what this step does
- tool: the tool to use
- args: the arguments for the tool

Output ONLY the JSON plan, nothing else. Example:
{
  "steps": [
    {
      "order": 1,
      "description": "Ask user for their name",
      "tool": "user.ask",
      "args": "What is your name?"
    },
    {
      "order": 2,
      "description": "Get today's date",
      "tool": "shell.date",
      "args": "+\"%Y-%m-%d\""
    },
    {
      "order": 3,
      "description": "Greet user with their name and date",
      "tool": "shell.echo",
      "args": "Hello {step_1}, today is {step_2}"
    }
  ]
}
"""

        if feedback:
            prompt = f"""You are an AI agent that converts workflow descriptions into executable plans.

Workflow description:
{workflow_desc}

User feedback on previous plan: {feedback}

{tools}
{plan_instructions}

Generate a revised execution plan that incorporates the user's feedback.
"""
        else:
            prompt = f"""You are an AI agent that converts workflow descriptions into executable plans.

Workflow description:
{workflow_desc}

{tools}
{plan_instructions}

Generate an execution plan.
"""

        response = self.llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024
        )
        self.plan = response
        return self.plan

    def generate_plan_stream(self, feedback=None):
        """Stream the plan generation, yielding chunks as they arrive."""
        workflow_desc = self.workflow["raw"]

        tools = """Available tools (choose the best one for each step):
- shell.echo: Print text to stdout. args: the text to print (no "echo" prefix)
- shell.date: Get current date/time. args: date format string quoted (e.g. "+%Y-%m-%d", no "date" prefix)
- shell.xargs: Execute command with piped input. args: the command to execute
- exec.curl: HTTP requests. args: the URL and options (no "curl" prefix)
- user.ask: Ask the user a question and wait for their response. args: the question text
  The user's response is stored as the step output and can be referenced as {step_N} in later steps.

IMPORTANT: For each step, you MUST select one of these tools. Do not output "auto" or "None".
The "args" field should only contain the arguments, not the tool name itself.
When an argument contains spaces, enclose it in quotes. Example: "+%Y-%m-%d %H:%M:%S" not +%Y-%m-%d %H:%M:%S"""

        plan_instructions = """
Read the workflow description carefully. Break it down into individual steps and select the appropriate tool for each.

When steps need to share data, use {step_N} placeholders where N is the step number. For example:
- Step 1: Ask user their name → output stored as step 1
- Step 2: Use that name → args: "Hello {step_1}!"

Each step MUST include these fields:
- order: step number (1, 2, 3...)
- description: human-readable description of what this step does
- tool: the tool to use
- args: the arguments for the tool

Output ONLY the JSON plan, nothing else. Example:
{
  "steps": [
    {
      "order": 1,
      "description": "Ask user for their name",
      "tool": "user.ask",
      "args": "What is your name?"
    },
    {
      "order": 2,
      "description": "Get today's date",
      "tool": "shell.date",
      "args": "+\"%Y-%m-%d\""
    },
    {
      "order": 3,
      "description": "Greet user with their name and date",
      "tool": "shell.echo",
      "args": "Hello {step_1}, today is {step_2}"
    }
  ]
}
"""

        if feedback:
            prompt = f"""You are an AI agent that converts workflow descriptions into executable plans.

Workflow description:
{workflow_desc}

User feedback on previous plan: {feedback}

{tools}
{plan_instructions}

Generate a revised execution plan that incorporates the user's feedback.
"""
        else:
            prompt = f"""You are an AI agent that converts workflow descriptions into executable plans.

Workflow description:
{workflow_desc}

{tools}
{plan_instructions}

Generate an execution plan.
"""

        for chunk in self.llm.stream_chat(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024
        ):
            yield chunk

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