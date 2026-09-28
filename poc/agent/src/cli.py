import argparse
import json
import sys
from .agent import Agent


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
    
    # Save standardized workflow back to input file
    import json
    import re
    json_match = re.search(r'```json\s*(.*?)\s*```', agent.plan, re.DOTALL)
    json_str = json_match.group(1) if json_match else agent.plan
    plan_json = json.loads(json_str)
    
    standardized = "# Standardized Workflow\n\n"
    for step in plan_json.get("steps", []):
        standardized += f"## Step {step.get('order', '?')}: {step.get('description', '')}\n"
        standardized += f"- tool: {step.get('tool', '')}\n"
        standardized += f"- args: {step.get('args', '')}\n\n"
    
    from pathlib import Path
    Path(workflow_path).write_text(standardized)
    print(f"Standardized workflow saved to: {workflow_path}")
    
    # Cache the standardized workflow
    from .workflow_cache import save_standardized_workflow, compute_checksum
    cache_path = save_standardized_workflow(workflow_path, agent.plan)
    print(f"Workflow cached to: {cache_path}")


def execute_workflow(workflow_path: str):
    """Execute cached standardized workflow using Agent."""
    from .workflow_cache import load_standardized_workflow, compute_checksum, parse_standardized_workflow
    
    # Load cached standardized workflow
    cached = load_standardized_workflow(workflow_path)
    if not cached:
        content_hash = compute_checksum(workflow_path)
        print(f"No cached standardized workflow found for hash: {content_hash[:16]}")
        print("Please run 'openship build' first to standardize and cache this workflow.")
        sys.exit(1)
    
    cache_file, content = cached
    print(f"Using cached standardized workflow: {cache_file}")
    
    # Verify hashes
    input_hash = compute_checksum(workflow_path)
    cached_hash = compute_checksum(cache_file)
    if input_hash != cached_hash:
        print("Verification failed: input hash does not match cached hash.")
        sys.exit(1)
    print("Verification passed")
    
    # Parse the standardized workflow back to plan JSON
    plan_json = parse_standardized_workflow(content)
    print(f"Parsed {len(plan_json['steps'])} steps from cached workflow")
    
    # Create agent and compile/execute
    agent = Agent()
    agent.compile_graph(json.dumps(plan_json))
    
    # Execute with human-in-the-loop
    result = agent.execute()
    
    print("\nResults:")
    for step_order, output in result.get("outputs", {}).items():
        print(f"  Step {step_order}: {output}")


if __name__ == "__main__":
    main()