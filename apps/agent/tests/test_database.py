
import pytest

from src.database.operations import (
    create_execution,
    get_execution,
    get_latest_checkpoint,
    get_tool,
    get_workflow,
    list_tools,
    list_workflows,
    register_tool,
    register_workflow,
    save_checkpoint,
    update_execution_status,
)
from src.database.schema import close_db, init_db


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database file for tests."""
    return str(tmp_path / "test.db")


@pytest.fixture
def db(db_path):
    """Initialize and teardown database for each test."""
    connection = init_db(db_path)
    yield connection
    close_db(connection)


class TestDatabaseInitialization:
    def test_init_db_creates_tables(self, db):
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "workflows" in tables
        assert "tools" in tables
        assert "workflow_executions" in tables
        assert "checkpoints" in tables

    def test_init_db_creates_indexes(self, db):
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        # Primary key indexes are auto-created by SQLite
        assert "sqlite_autoindex_workflows_1" in indexes or "workflows_id_key" in indexes


class TestWorkflowOperations:
    def test_register_and_get_workflow(self, db):
        workflow = {
            "name": "test_workflow",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test workflow",
            "definition": "# Test\nThis is a test workflow."
        }
        register_workflow(db, workflow)
        retrieved = get_workflow(db, "test_workflow")
        assert retrieved is not None
        assert retrieved["name"] == "test_workflow"
        assert retrieved["version"] == "1.0.0"
        assert retrieved["origin"] == "builtin"

    def test_register_workflow_with_json_fields(self, db):
        workflow = {
            "name": "json_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Workflow with JSON fields",
            "definition": "# JSON Test",
            "inputs": {"type": "object", "properties": {"name": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"result": {"type": "string"}}},
            "tags": ["test", "json"],
            "required_tools": ["shell.exec"],
            "required_connectors": ["aws"]
        }
        register_workflow(db, workflow)
        retrieved = get_workflow(db, "json_workflow")
        assert retrieved is not None
        # JSON fields should be serialized/deserialized correctly
        assert retrieved["inputs"]["type"] == "object"
        assert retrieved["tags"] == ["test", "json"]
        assert retrieved["required_tools"] == ["shell.exec"]

    def test_list_workflows(self, db):
        for i in range(3):
            workflow = {
                "name": f"workflow_{i}",
                "version": "1.0.0",
                "origin": "builtin",
                "description": f"Workflow {i}",
                "definition": "# Workflow"
            }
            register_workflow(db, workflow)

        workflows = list_workflows(db)
        assert len(workflows) == 3

        # Test origin filter
        custom_wf = {
            "name": "custom_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom workflow",
            "definition": "# Custom"
        }
        register_workflow(db, custom_wf)

        builtin_workflows = list_workflows(db, origin="builtin")
        assert len(builtin_workflows) == 3

        custom_workflows = list_workflows(db, origin="custom")
        assert len(custom_workflows) == 1

    def test_get_nonexistent_workflow(self, db):
        retrieved = get_workflow(db, "nonexistent")
        assert retrieved is None


class TestToolOperations:
    def test_register_and_get_tool(self, db):
        tool = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file contents",
            "category": "file.read",
            "inputs": {"type": "object", "properties": {"path": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"content": {"type": "string"}}}
        }
        register_tool(db, tool)
        retrieved = get_tool(db, "file.read")
        assert retrieved is not None
        assert retrieved["name"] == "file.read"
        assert retrieved["version"] == "1.0.0"
        assert retrieved["origin"] == "builtin"

    def test_register_tool_with_json_fields(self, db):
        tool = {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute shell command",
            "category": "shell.exec",
            "inputs": {"type": "object", "properties": {"command": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"exit_code": {"type": "integer"}}},
            "tags": ["shell", "command"],
            "permissions": ["shell:execute"]
        }
        register_tool(db, tool)
        retrieved = get_tool(db, "shell.exec")
        assert retrieved is not None
        assert retrieved["tags"] == ["shell", "command"]
        assert retrieved["permissions"] == ["shell:execute"]

    def test_list_tools(self, db):
        for name in ["file.read", "file.write", "shell.exec"]:
            tool = {
                "name": name,
                "version": "1.0.0",
                "origin": "builtin",
                "description": f"Tool {name}",
                "category": name,
                "inputs": {},
                "outputs": {}
            }
            register_tool(db, tool)

        tools = list_tools(db)
        assert len(tools) == 3

        # Test origin filter
        custom_tool = {
            "name": "custom.tool",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom tool",
            "category": "custom",
            "inputs": {},
            "outputs": {}
        }
        register_tool(db, custom_tool)

        builtin_tools = list_tools(db, origin="builtin")
        assert len(builtin_tools) == 3

        custom_tools = list_tools(db, origin="custom")
        assert len(custom_tools) == 1

    def test_get_nonexistent_tool(self, db):
        retrieved = get_tool(db, "nonexistent")
        assert retrieved is None


class TestExecutionOperations:
    def test_create_and_get_execution(self, db):
        # Register a workflow first
        workflow = {
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test",
            "definition": "# Test"
        }
        register_workflow(db, workflow)
        wf = get_workflow(db, "test_wf")

        execution = create_execution(db, wf["id"], {"input1": "value1"})
        assert execution is not None
        assert execution["workflow_id"] == wf["id"]
        assert execution["status"] == "running"

        retrieved = get_execution(db, execution["id"])
        assert retrieved is not None
        assert retrieved["id"] == execution["id"]

    def test_update_execution_status(self, db):
        workflow = {
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test",
            "definition": "# Test"
        }
        register_workflow(db, workflow)
        wf = get_workflow(db, "test_wf")

        execution = create_execution(db, wf["id"], {})
        assert execution["status"] == "running"

        update_execution_status(db, execution["id"], "paused")
        retrieved = get_execution(db, execution["id"])
        assert retrieved["status"] == "paused"

        update_execution_status(db, execution["id"], "completed", {"output": "done"})
        retrieved = get_execution(db, execution["id"])
        assert retrieved["status"] == "completed"
        assert retrieved["outputs"]["output"] == "done"

    def test_update_execution_sandbox_id(self, db):
        workflow = {
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test",
            "definition": "# Test"
        }
        register_workflow(db, workflow)
        wf = get_workflow(db, "test_wf")

        execution = create_execution(db, wf["id"], {})
        retrieved = get_execution(db, execution["id"])
        assert retrieved["sandbox_id"] is None

        # Update with sandbox_id
        db.execute(
            "UPDATE workflow_executions SET sandbox_id = ? WHERE id = ?",
            ("sandbox-123", execution["id"])
        )
        db.commit()

        retrieved = get_execution(db, execution["id"])
        assert retrieved["sandbox_id"] == "sandbox-123"


class TestCheckpointOperations:
    def test_save_and_get_checkpoint(self, db):
        workflow = {
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test",
            "definition": "# Test"
        }
        register_workflow(db, workflow)
        wf = get_workflow(db, "test_wf")

        execution = create_execution(db, wf["id"], {})

        # Save a checkpoint
        checkpoint_data = {"step": "step1", "state": {"counter": 42}}
        save_checkpoint(db, execution["id"], "step1", checkpoint_data)

        # Retrieve it
        retrieved = get_latest_checkpoint(db, execution["id"], "step1")
        assert retrieved is not None
        assert retrieved["step_name"] == "step1"
        assert retrieved["state_data"]["step"] == "step1"
        assert retrieved["state_data"]["state"]["counter"] == 42

    def test_multiple_checkpoints(self, db):
        workflow = {
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test",
            "definition": "# Test"
        }
        register_workflow(db, workflow)
        wf = get_workflow(db, "test_wf")

        execution = create_execution(db, wf["id"], {})

        # Save checkpoints for different steps
        save_checkpoint(db, execution["id"], "step1", {"counter": 1})
        save_checkpoint(db, execution["id"], "step2", {"counter": 2})
        save_checkpoint(db, execution["id"], "step1", {"counter": 3})

        # Get latest for each step
        step1_checkpoint = get_latest_checkpoint(db, execution["id"], "step1")
        assert step1_checkpoint["state_data"]["counter"] == 3

        step2_checkpoint = get_latest_checkpoint(db, execution["id"], "step2")
        assert step2_checkpoint["state_data"]["counter"] == 2

    def test_get_nonexistent_checkpoint(self, db):
        workflow = {
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test",
            "definition": "# Test"
        }
        register_workflow(db, workflow)
        wf = get_workflow(db, "test_wf")

        execution = create_execution(db, wf["id"], {})

        retrieved = get_latest_checkpoint(db, execution["id"], "nonexistent")
        assert retrieved is None


class TestInMemoryDatabase:
    def test_init_db_in_memory(self):
        db = init_db(":memory:")
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "workflows" in tables
        assert "tools" in tables
        assert "workflow_executions" in tables
        assert "checkpoints" in tables
        close_db(db)
