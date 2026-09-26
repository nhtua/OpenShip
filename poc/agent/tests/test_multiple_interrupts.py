import uuid
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field
from typing import Dict, Any


class State(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[int, Any] = Field(default_factory=dict)


def step1(state: State) -> State:
    print("  [step1: executing]")
    name = interrupt({"question": "What is your name?"})
    print(f"  [step1: got name: {name}]")
    state.outputs[1] = name
    return state


def step2(state: State) -> State:
    print("  [step2: executing]")
    city = interrupt({"question": "What city do you live in?"})
    print(f"  [step2: got city: {city}]")
    state.outputs[2] = city
    return state


def step3(state: State) -> State:
    print("  [step3: executing]")
    result = f"Hello {state.outputs[1]} from {state.outputs[2]}!"
    print(f"  [step3: {result}]")
    state.outputs[3] = result
    return state


graph = StateGraph(State)
graph.add_node("step1", step1)
graph.add_node("step2", step2)
graph.add_node("step3", step3)
graph.add_edge(START, "step1")
graph.add_edge("step1", "step2")
graph.add_edge("step2", "step3")
graph.add_edge("step3", END)

checkpointer = InMemorySaver()
app = graph.compile(checkpointer=checkpointer)
config = {"configurable": {"thread_id": str(uuid.uuid4())}}

# First invoke
print("First invoke...")
result = app.invoke({"inputs": {}, "outputs": {}}, config)
print(f"Has interrupt: {'__interrupt__' in result}")

if "__interrupt__" in result:
    print(f"Question: {result['__interrupt__'][0].value['question']}")

# Resume with name
print("\nResuming with 'Alice'...")
result = app.invoke(Command(resume="Alice"), config)
print(f"Has interrupt: {'__interrupt__' in result}")

if "__interrupt__" in result:
    print(f"Question: {result['__interrupt__'][0].value['question']}")

# Resume with city
print("\nResuming with 'NYC'...")
result = app.invoke(Command(resume="NYC"), config)
print(f"Has interrupt: {'__interrupt__' in result}")
print(f"Final result: {result}")