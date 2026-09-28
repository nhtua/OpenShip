import os
from pathlib import Path
from dotenv import load_dotenv
from .state import BuilderState
from .llm_client import LLMClient

def get_llm_client():
    load_dotenv()
    local_url = os.getenv("LOCAL_LLM_URL")
    if local_url:
        model_name = os.getenv("MODEL_NAME", "local-model")
        return LLMClient(api_key="local", base_url=local_url, model=model_name)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("Set either OPENAI_API_KEY or LOCAL_LLM_URL")
    return LLMClient(api_key=api_key)

def load_workflow_node(state: BuilderState) -> BuilderState:
    content = Path(state.workflow_path).read_text()
    return state.model_copy(update={"workflow_content": content})

def generate_plan_node(state: BuilderState) -> BuilderState:
    llm = get_llm_client()
    
    tools = """Available tools:
- shell.echo: Print text. args: text to print
- shell.date: Get date/time. args: format string quoted
- shell.xargs: Execute with piped input. args: command
- exec.curl: HTTP requests. args: URL and options
- user.ask: Ask user a question. args: question text"""

    plan_instructions = """Break the workflow into steps with tool selection.
Output JSON with steps array. Each step has: order, description, tool, args.
Use {step_N} for data flow between steps."""

    if state.user_feedback:
        prompt = f"""Generate a revised execution plan incorporating user feedback: {state.user_feedback}

Workflow:
{state.workflow_content}

{tools}
{plan_instructions}
"""
    else:
        prompt = f"""Generate an execution plan for this workflow:

{state.workflow_content}

{tools}
{plan_instructions}
"""

    plan = llm.chat([{"role": "user", "content": prompt}], temperature=0.7, max_tokens=1024)
    return state.model_copy(update={"llm_plan": plan})