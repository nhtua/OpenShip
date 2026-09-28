import argparse
import sys
from .agent import Agent
from .workflow_cache import save_cached_workflow, load_cached_workflow


def main():
    parser = argparse.ArgumentParser(description="OpenShip Workflow PoC")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Build command
    build_parser = subparsers.add_parser("build", help="Build and cache standardized workflow")
    build_parser.add_argument("workflow", help="Path to workflow markdown file")
    build_parser.add_argument("--show-thinking", action="store_true",
                              help="Stream LLM reasoning/thinking process to terminal")
    
    # Execute command
    exec_parser = subparsers.add_parser("execute", help="Execute cached standardized workflow")
    exec_parser.add_argument("workflow", help="Path to workflow markdown file")
    
    args = parser.parse_args()

    if args.command == "build":
        build_workflow(args.workflow, args.show_thinking)
    elif args.command == "execute":
        execute_workflow(args.workflow)
    else:
        parser.print_help()


def build_workflow(workflow_path: str, show_thinking: bool = False):
    """Build and cache standardized workflow using Agent."""
    agent = Agent()
    agent.load_workflow(workflow_path)
    
    # Generate plan
    print("Generating plan with LLM...")
    agent.generate_plan()
    
    # Approval loop
    while True:
        agent.show_plan()
        
        response = input("\nApprove plan? [yes/no]: ").lower().strip()
        if response in ["yes", "y"]:
            break
        elif response in ["no", "n"]:
            feedback = input("What would you like to change? ")
            print("Regenerating plan...")
            agent.generate_plan(feedback)
        else:
            print("Invalid response. Please enter 'yes' or 'no'.")
    
    # Save compiled workflow to cache with integrity signature
    plan_json = agent.parse_plan()
    print("Compiling workflow to LangGraph...")
    agent.compile_graph(plan_json)
    print("Compiled successfully.")
    
    # Cache the compiled workflow
    save_cached_workflow(workflow_path, plan_json)


def execute_workflow(workflow_path: str):
    """Execute cached standardized workflow using Agent."""
    # Load cached workflow by input file hash
    cached = load_cached_workflow(workflow_path)
    if not cached:
        print("No cached compiled workflow found. Run 'openship build' first.")
        sys.exit(1)
    
    cache_file, plan_json = cached
    print(f"Using cached compiled workflow: {cache_file}")
    
    # Create agent and compile/execute
    agent = Agent()
    agent.compile_graph(plan_json)
    
    # Execute with human-in-the-loop
    result = agent.execute()
    
    print("\nResults:")
    for step_order, output in result.get("outputs", {}).items():
        print(f"  Step {step_order}: {output}")


if __name__ == "__main__":
    main()