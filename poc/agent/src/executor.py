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

def execute_node(state: ExecutorState) -> ExecutorState:
    config = {"configurable": {"thread_id": state.thread_id}}
    result = state.graph.invoke({"inputs": {}, "outputs": {}}, config)
    return state.model_copy(update={"outputs": result.get("outputs", {})})

from langgraph.graph import StateGraph, END

def compile_executor_workflow():
    workflow = StateGraph(ExecutorState)
    
    workflow.add_node("compile", compile_node)
    workflow.add_node("execute", execute_node)
    
    workflow.set_entry_point("compile")
    workflow.add_edge("compile", "execute")
    workflow.add_edge("execute", END)
    
    return workflow.compile()