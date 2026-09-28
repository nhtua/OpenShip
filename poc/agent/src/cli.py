import argparse
import json
import re
import sys
import uuid
from .builder import compile_builder_workflow
from .compiler import compile_to_langgraph
from langgraph.types import Command
from langgraph.checkpoint.memory import InMemorySaver

def handle_builder_interrupts(builder, config, initial_input):
    """Run builder workflow, handling approval interrupts."""
    result = builder.invoke(initial_input, config)
    
    while "__interrupt__" in result:
        interrupt_info = result["__interrupt__"][0]
        print("\n[Agent asks for approval]")
        
        # Show the plan again for context
        if interrupt_info.value.get("type") == "approval":
            print(f"Plan: {interrupt_info.value.get('plan', '')[:200]}...")
        
        response = input("Approve? [yes/no]: ").lower().strip()
        
        if response in ["yes", "y", "approve"]:
            result = builder.invoke(Command(resume="yes"), config)
        else:
            feedback = input("What would you like to change? ")
            result = builder.invoke(Command(resume=feedback), config)
    
    return result

def handle_graph_interrupts(graph, config, initial_input):
    """Run compiled graph, handling execution interrupts."""
    result = graph.invoke(initial_input, config)
    
    while "__interrupt__" in result:
        interrupt_info = result["__interrupt__"][0]
        question = interrupt_info.value.get("question", "Question from agent:")
        print(f"\n[Agent asks] {question}")
        user_response = input("Your response: ")
        print(f"  [Resuming with: {user_response}]")
        result = graph.invoke(Command(resume=user_response), config)
    
    return result

def compile_plan_to_graph(plan_str):
    """Compile a plan string to a LangGraph."""
    json_match = re.search(r'```json\s*(.*?)\s*```', plan_str, re.DOTALL)
    json_str = json_match.group(1) if json_match else plan_str
    plan_json = json.loads(json_str)
    
    checkpointer = InMemorySaver()
    graph = compile_to_langgraph(plan_json, checkpointer=checkpointer)
    return graph

def main():
    parser = argparse.ArgumentParser(description="OpenShip Workflow PoC")
    parser.add_argument("workflow", help="Path to workflow markdown file")
    parser.add_argument("--show-thinking", action="store_true",
                        help="Stream LLM reasoning/thinking process to terminal")
    args = parser.parse_args()

    print(f"Loaded workflow from: {args.workflow}")
    
    # Run builder workflow
    builder = compile_builder_workflow()
    builder_config = {"configurable": {"thread_id": f"builder-{uuid.uuid4()}"}}
    
    builder_result = handle_builder_interrupts(
        builder,
        builder_config,
        {"workflow_path": args.workflow, "workflow_content": ""}
    )

    # Compile the approved plan to a graph
    print("\nExecuting workflow...")
    graph = compile_plan_to_graph(builder_result["llm_plan"])
    
    # Execute the graph with interrupt handling
    graph_config = {"configurable": {"thread_id": f"exec-{uuid.uuid4()}"}}
    result = handle_graph_interrupts(graph, graph_config, {"inputs": {}, "outputs": {}})

    print("\nResults:")
    for step_order, output in result.get("outputs", {}).items():
        print(f"  Step {step_order}: {output}")

if __name__ == "__main__":
    main()