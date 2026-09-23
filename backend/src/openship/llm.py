"""LLM integration for OpenShip."""
import json
import requests
from openship.config import config


def generate_diagram(requirements: str) -> str:
    """Generate a Mermaid diagram from requirements using LLM."""
    prompt = f"""Generate a Mermaid flowchart diagram representing the architecture described in these requirements.
Use the 'graph TD' syntax. Keep it simple and clear.

Requirements:
{requirements}

Return ONLY the Mermaid diagram code, no explanation."""

    try:
        response = call_llm(prompt)
        # Clean up the response to get just the diagram
        lines = response.strip().split('\n')
        diagram_lines = []
        in_diagram = False
        for line in lines:
            if line.startswith('graph ') or line.startswith('flowchart '):
                in_diagram = True
            if in_diagram:
                diagram_lines.append(line)
            if in_diagram and line == '' and len(diagram_lines) > 1:
                break
        return '\n'.join(diagram_lines).strip()
    except Exception as e:
        print(f"LLM error generating diagram: {e}")
        # Fallback to hardcoded diagram
        return """graph TD
    A[User] --> B[Load Balancer]
    B --> C[Web Server 1]
    B --> D[Web Server 2]
    C --> E[Database]
    D --> E
    C --> F[Cache]
    D --> F"""


def generate_terraform(requirements: str, diagram: str) -> str:
    """Generate Terraform code from requirements using LLM."""
    prompt = f"""Generate Terraform code for AWS based on these requirements and architecture diagram.
Use AWS provider ~> 5.0. Include all necessary resources.

Requirements:
{requirements}

Architecture Diagram:
{diagram}

Return ONLY the Terraform code, no explanation."""

    try:
        response = call_llm(prompt)
        return response.strip()
    except Exception as e:
        print(f"LLM error generating terraform: {e}")
        # Fallback to hardcoded terraform
        return f"""terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = "{config.CLOUD_REGION}"
}}

resource "aws_instance" "web" {{
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t2.micro"
  tags = {{
    Name = "openship-web-server"
  }}
}}"""


def call_llm(prompt: str) -> str:
    """Call the LLM API (OpenAI-compatible)."""
    headers = {
        "Content-Type": "application/json",
    }
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert DevOps engineer. Generate clean, production-ready infrastructure code and diagrams."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    print(f"Calling LLM at {config.LLM_BASE_URL}/chat/completions with model {config.LLM_MODEL}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        print(f"LLM response status: {response.status_code}")
        if response.status_code != 200:
            print(f"LLM response body: {response.text}")
        response.raise_for_status()
        data = response.json()
        print(f"LLM response: {json.dumps(data, indent=2)}")
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.ConnectionError as e:
        print(f"LLM connection error: {e}")
        raise
    except Exception as e:
        print(f"LLM error: {e}")
        raise