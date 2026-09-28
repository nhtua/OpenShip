import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
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

def get_workflows_dir():
    """Get the workflows cache directory."""
    workflows_dir = Path.home() / ".openship" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    return workflows_dir

def compute_checksum(file_path: str) -> str:
    """Compute SHA-256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256.update(chunk)
    return sha256.hexdigest()

def save_standardized_workflow(input_path: str, plan_str: str) -> str:
    """Save standardized workflow to cache. Returns cache file path."""
    checksum = compute_checksum(input_path)
    workflows_dir = get_workflows_dir()
    cache_file = workflows_dir / f"{checksum}.md"
    
    # Convert plan JSON to standardized workflow.md
    json_match = re.search(r'```json\s*(.*?)\s*```', plan_str, re.DOTALL)
    json_str = json_match.group(1) if json_match else plan_str
    plan_json = json.loads(json_str)
    
    # Generate standardized workflow.md
    standardized = "# Standardized Workflow\n\n"
    for step in plan_json.get("steps", []):
        standardized += f"## Step {step.get('order', '?')}: {step.get('description', '')}\n"
        standardized += f"- tool: {step.get('tool', '')}\n"
        standardized += f"- args: {step.get('args', '')}\n\n"
    
    cache_file.write_text(standardized)
    print(f"Standardized workflow cached to: {cache_file}")
    return str(cache_file)

def load_standardized_workflow(input_path: str):
    """Load standardized workflow from cache. Returns (cache_file, content) or None."""
    checksum = compute_checksum(input_path)
    workflows_dir = get_workflows_dir()
    cache_file = workflows_dir / f"{checksum}.md"
    
    if not cache_file.exists():
        return None
    
    content = cache_file.read_text()
    return (str(cache_file), content)

def parse_standardized_workflow(content: str) -> dict:
    """Parse standardized workflow.md back to plan JSON."""
    steps = []
    current_step = None
    
    for line in content.split("\n"):
        # Match step header
        step_match = re.match(r"## Step (\d+): (.+)", line)
        if step_match:
            if current_step:
                steps.append(current_step)
            current_step = {
                "order": int(step_match.group(1)),
                "description": step_match.group(2)
            }
            continue
        
        # Match tool line
        tool_match = re.match(r"- tool: (.+)", line)
        if tool_match and current_step:
            current_step["tool"] = tool_match.group(1).strip()
            continue
        
        # Match args line
        args_match = re.match(r"- args: (.+)", line)
        if args_match and current_step:
            current_step["args"] = args_match.group(1).strip()
            continue
    
    if current_step:
        steps.append(current_step)
    
    return {"steps": steps}

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
    """Build and cache standardized workflow."""
    print(f"Building workflow from: {workflow_path}")
    
    # Run builder workflow
    builder = compile_builder_workflow()
    builder_config = {"configurable": {"thread_id": f"builder-{uuid.uuid4()}"}}
    
    builder_result = handle_builder_interrupts(
        builder,
        builder_config,
        {"workflow_path": workflow_path, "workflow_content": ""}
    )

    # Cache the standardized workflow
    cache_path = save_standardized_workflow(workflow_path, builder_result["llm_plan"])
    print(f"Workflow built and cached to: {cache_path}")

def execute_workflow(workflow_path: str):
    """Execute cached standardized workflow."""
    print(f"Executing workflow from: {workflow_path}")
    
    # Load cached standardized workflow
    cached = load_standardized_workflow(workflow_path)
    if not cached:
        checksum = compute_checksum(workflow_path)
        print(f"No cached standardized workflow found for checksum: {checksum[:16]}")
        print("Please run 'openship build' first to standardize and cache this workflow.")
        sys.exit(1)
    
    cache_file, content = cached
    print(f"Using cached standardized workflow: {cache_file}")
    
    # Parse the standardized workflow back to plan JSON
    plan_json = parse_standardized_workflow(content)
    print(f"Parsed {len(plan_json['steps'])} steps from cached workflow")
    
    # Compile the plan to a graph
    print("\nExecuting workflow...")
    checkpointer = InMemorySaver()
    graph = compile_to_langgraph(plan_json, checkpointer=checkpointer)
    
    # Execute the graph with interrupt handling
    graph_config = {"configurable": {"thread_id": f"exec-{uuid.uuid4()}"}}
    result = handle_graph_interrupts(graph, graph_config, {"inputs": {}, "outputs": {}})

    print("\nResults:")
    for step_order, output in result.get("outputs", {}).items():
        print(f"  Step {step_order}: {output}")

if __name__ == "__main__":
    main()