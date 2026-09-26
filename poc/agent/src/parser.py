import re
from pathlib import Path


def parse_workflow(path: str):
    content = Path(path).read_text()

    # Extract title
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    title = title_match.group(1) if title_match else ""

    # Extract inputs
    inputs = {}
    input_section = re.search(r"## Inputs\n((?:- .+\n)*)", content)
    if input_section:
        for line in input_section.group(1).strip().split("\n"):
            match = re.search(r"- (\w+):\s+\[(\w+)\]", line)
            if match:
                inputs[match.group(1)] = {"required": match.group(2) == "required"}

    # Extract steps
    steps = []
    step_section = re.search(r"## Steps\n(.*)", content, re.DOTALL)
    if step_section:
        for step_match in re.finditer(r"(\d+)\.\s+(.+?)\n((?:\s+- .+\n)*)", step_section.group(1)):
            step = {
                "order": int(step_match.group(1)),
                "description": step_match.group(2).strip(),
                "tool": None,
                "args": None,
            }
            for line in step_match.group(3).strip().split("\n"):
                if line.strip().startswith("- tool:"):
                    step["tool"] = line.strip().replace("- tool:", "").strip()
                elif line.strip().startswith("- args:"):
                    step["args"] = line.strip().replace("- args:", "").strip()
            steps.append(step)

    return {"title": title, "inputs": inputs, "steps": steps, "raw": content}