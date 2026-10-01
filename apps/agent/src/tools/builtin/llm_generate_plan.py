"""llm.generate-plan built-in tool.

Uses the LLM to generate a workflow plan from a natural language description.
"""

import os
from typing import ClassVar

from src.tools.base import Tool


class LLMGeneratePlanTool(Tool):
    """Generate a workflow plan using LLM.

    Inputs:
        description (str): Natural language description of the workflow.
        feedback (str): Optional feedback from user for plan revision.

    Outputs:
        success (bool): Whether the plan was generated successfully.
        plan (str): The generated plan JSON string.
        error (str): Error message if generation failed.
    """

    name = "llm.generate-plan"
    version = "1.0.0"
    description = "Generate a workflow plan using LLM"
    inputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Workflow description"},
            "feedback": {"type": "string", "description": "User feedback for revision"}
        },
        "required": ["description"]
    }
    outputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "plan": {"type": "string"},
            "error": {"type": "string"}
        }
    }

    def execute(self, inputs: dict, context: dict | None = None) -> dict:
        try:
            from src.llm_client import LLMClient

            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return {
                    "success": False,
                    "error": "OPENAI_API_KEY environment variable not set"
                }

            model = os.environ.get("OPENAI_MODEL", "gpt-4o")
            client = LLMClient(api_key=api_key, model=model)

            description = inputs.get("description", "")
            feedback = inputs.get("feedback", "")

            # Build the prompt
            prompt = f"""Generate a workflow plan for the following description:

{description}

{f'User feedback: {feedback}' if feedback else ''}

Respond with a JSON object containing the workflow plan with the following structure:
{{
    "name": "workflow-name",
    "version": "1.0.0",
    "description": "Workflow description",
    "steps": [
        {{
            "order": 1,
            "tool": "tool-name",
            "args": "arguments",
            "description": "Step description"
        }}
    ]
}}

Available tools: shell.exec, file.read, file.write
"""

            # Get the plan from LLM
            messages = [
                {"role": "system", "content": "You are a workflow planner. Generate workflow plans in JSON format."},
                {"role": "user", "content": prompt}
            ]

            response = client.chat(messages, temperature=0)

            return {
                "success": True,
                "plan": response,
                "error": ""
            }

        except Exception as e:
            return {
                "success": False,
                "plan": "",
                "error": str(e)
            }