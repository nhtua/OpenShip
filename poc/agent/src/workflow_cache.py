import hashlib
import json
import re
import sys
from pathlib import Path


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