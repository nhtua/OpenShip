import argparse
import sys
import uuid
from .builder import compile_builder_workflow
from .executor import compile_executor_workflow
from langgraph.types import Command

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

def handle_executor_interrupts(executor, config, initial_input):
    """Run executor workflow, handling execution interrupts."""
    result = executor.invoke(initial_input, config)
    
    while "__interrupt__" in result:
        interrupt_info = result["__interrupt__"][0]
        question = interrupt_info.value.get("question", "Question from agent:")
        print(f"\n[Agent asks] {question}")
        user_response = input("Your response: ")
        print(f"  [Resuming with: {user_response}]")
        result = executor.invoke(Command(resume=user_response), config)
    
    return result

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

    # Run executor workflow
    print("\nExecuting workflow...")
    executor = compile_executor_workflow()
    executor_config = {"configurable": {"thread_id": f"executor-{uuid.uuid4()}"}}
    
    executor_result = handle_executor_interrupts(
        executor,
        executor_config,
        {"plan": builder_result["llm_plan"]}
    )

    print("\nResults:")
    for step_order, output in executor_result.get("outputs", {}).items():
        print(f"  Step {step_order}: {output}")

if __name__ == "__main__":
    main()