import json
import re
from langgraph.checkpoint.memory import InMemorySaver
from .state import ExecutorState
from .compiler import compile_to_langgraph

def compile_node(state: ExecutorState) -> ExecutorState:
    json_match = re.search(r'```json\s*(.*?)\s*```', state.plan, re.DOTALL)
    json_str = json_match.group(1) if json_match else state.plan
    plan_json = json.loads(json_str)
    
    checkpointer = InMemorySaver()
    graph = compile_to_langgraph(plan_json, checkpointer=checkpointer)
    
    return state.model_copy(update={"graph": graph})