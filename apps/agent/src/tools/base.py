"""Tool base class definition.

All tools must inherit from the Tool base class and implement the execute method.
"""

from abc import ABC, abstractmethod
from typing import ClassVar


class Tool(ABC):
    """Abstract base class for all tools.

    Tools encapsulate discrete operations that can be invoked by the agent
    orchestration engine. Each tool has a name, version, description, and
    input/output schemas.
    """

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    inputs_schema: ClassVar[dict] = {}
    outputs_schema: ClassVar[dict] = {}

    @abstractmethod
    def execute(self, inputs: dict, context: dict) -> dict:
        """Execute the tool with the given inputs and context.

        Args:
            inputs: Input parameters as validated by inputs_schema.
            context: Execution context including sandbox_id, working_dir, etc.

        Returns:
            Execution result dictionary with 'success' boolean and tool-specific
            output fields. On error, 'success' should be False and an 'error'
            field should contain the error message.
        """
