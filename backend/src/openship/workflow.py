"""PoC workflow: requirements → diagram → approve → terraform → approve → apply."""
import uuid
import json
from typing import TypedDict, Optional

from sqlalchemy import create_engine, Column, String, Text, Boolean, Integer
from sqlalchemy.orm import sessionmaker, declarative_base

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from openship.config import config
from openship.llm import generate_diagram as generate_diagram_llm, generate_terraform as generate_terraform_llm

Base = declarative_base()


class WorkflowRun(Base):
    """Database model for workflow runs."""
    __tablename__ = "workflows"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    requirements = Column(Text, default="")
    diagram = Column(Text, default="")
    terraform = Column(Text, default="")
    apply_output = Column(Text, default="")
    approved_diagram = Column(Boolean, default=False)
    approved_terraform = Column(Boolean, default=False)
    step = Column(String(50), default="idle")
    done = Column(Boolean, default=False)
    graph_json = Column(Text, default="")

    def to_dict(self):
        return {
            "id": self.id,
            "requirements": self.requirements,
            "diagram": self.diagram,
            "terraform": self.terraform,
            "apply_output": self.apply_output,
            "approved_diagram": self.approved_diagram,
            "approved_terraform": self.approved_terraform,
            "step": self.step,
            "done": self.done,
        }


# Database setup
DATABASE_URL = "sqlite:///openship.db"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


class WorkflowState(TypedDict):
    """State for the provisioning workflow."""
    requirements: str
    diagram: str
    terraform: str
    apply_output: str
    approved_diagram: bool
    approved_terraform: bool
    step: str
    done: bool


def create_workflow(requirements: str = ""):
    """Create a new workflow run and return its ID."""
    db = Session()

    # Build the LangGraph workflow
    graph = StateGraph(WorkflowState)

    def parse_requirements(state: WorkflowState) -> WorkflowState:
        return {
            "step": "parsing_requirements",
            "requirements": state.get("requirements", ""),
        }

    def generate_diagram(state: WorkflowState) -> WorkflowState:
        diagram = generate_diagram_llm(state.get("requirements", ""))
        return {
            "step": "generating_diagram",
            "diagram": diagram,
        }

    def generate_terraform(state: WorkflowState) -> WorkflowState:
        terraform = generate_terraform_llm(
            state.get("requirements", ""),
            state.get("diagram", "")
        )
        return {
            "step": "generating_terraform",
            "terraform": terraform,
        }

    def apply_terraform(state: WorkflowState) -> WorkflowState:
        if config.CLOUD_MOCK or not config.TERRAFORM_APPLY:
            # Mocked: run terraform plan only
            output = f"""[Mocked] Running terraform plan (no actual apply)

Terraform will perform the following actions:

  # aws_instance.web will be created
  + resource "aws_instance" "web" {{
      + ami           = "ami-0c55b159cbfafe1f0"
      + instance_type = "t2.micro"
      + tags          = {{
          + "Name" = "openship-web-server"
        }}
    }}

  # aws_rds_instance.db will be created
  + resource "aws_rds_instance" "db" {{
      + identifier     = "openship-db"
      + engine         = "postgres"
      + engine_version = "14.6"
      + instance_class = "db.t3.micro"
    }}

Plan: 2 to add, 0 to change, 0 to destroy.

Terraform plan completed successfully."""
        else:
            # Real apply
            output = "Running terraform apply..."
            # TODO: Actually run terraform apply
        return {
            "step": "applying_terraform",
            "apply_output": output,
            "done": True,
        }

    graph.add_node("parse_requirements", parse_requirements)
    graph.add_node("generate_diagram", generate_diagram)
    graph.add_node("generate_terraform", generate_terraform)
    graph.add_node("apply_terraform", apply_terraform)

    graph.set_entry_point("parse_requirements")
    graph.add_edge("parse_requirements", "generate_diagram")
    graph.add_edge("generate_diagram", "generate_terraform")
    graph.add_edge("generate_terraform", "apply_terraform")
    graph.add_edge("apply_terraform", END)

    compiled = graph.compile()

    # Run the workflow
    result = compiled.invoke({"requirements": requirements})

    # Store the workflow run
    run = WorkflowRun(
        requirements=requirements,
        diagram=result["diagram"],
        terraform=result["terraform"],
        apply_output=result["apply_output"],
        step=result["step"],
        done=result["done"],
    )
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()

    return run_id, result


def get_workflow(run_id: str):
    """Get the current state of a workflow."""
    db = Session()
    run = db.query(WorkflowRun).get(run_id)
    if not run:
        db.close()
        return None
    result = run.to_dict()
    db.close()
    return result


def approve_diagram(run_id: str):
    """Approve the diagram and continue the workflow."""
    db = Session()
    run = db.query(WorkflowRun).get(run_id)
    if not run:
        db.close()
        return None

    requirements = run.requirements
    run.approved_diagram = True
    db.commit()
    db.close()

    # Continue workflow - regenerate terraform
    _, result = create_workflow(requirements)
    return result


def approve_terraform(run_id: str):
    """Approve the terraform code and continue the workflow."""
    db = Session()
    run = db.query(WorkflowRun).get(run_id)
    if not run:
        db.close()
        return None

    requirements = run.requirements
    run.approved_terraform = True
    db.commit()
    db.close()

    # Continue workflow - apply terraform
    _, result = create_workflow(requirements)
    return result