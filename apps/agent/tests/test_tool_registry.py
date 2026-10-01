import pytest

from src.registry.tools import (
    close_registry,
    find_tools,
    get_tool,
    init_registry,
    list_tools,
    register_tool,
)


@pytest.fixture
def registry(tmp_path):
    """Initialize and teardown tool registry for each test."""
    db_path = str(tmp_path / "registry.db")
    init_registry(db_path)
    yield
    close_registry()


class TestToolRegistry:
    def test_register_and_get_tool(self, registry):
        tool = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file contents",
            "category": "file.read",
            "inputs": {"type": "object", "properties": {"path": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"content": {"type": "string"}}}
        }
        register_tool(tool)
        retrieved = get_tool("file.read")
        assert retrieved["name"] == "file.read"
        assert retrieved["version"] == "1.0.0"
        assert retrieved["origin"] == "builtin"

    def test_register_tool_with_json_fields(self, registry):
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
        register_tool(tool)
        retrieved = get_tool("shell.exec")
        assert retrieved["tags"] == ["shell", "command"]
        assert retrieved["permissions"] == ["shell:execute"]

    def test_get_tool_latest_version(self, registry):
        tool_v1 = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file contents v1",
            "category": "file.read",
            "inputs": {},
            "outputs": {}
        }
        tool_v2 = {
            "name": "file.read",
            "version": "2.0.0",
            "origin": "builtin",
            "description": "Read file contents v2",
            "category": "file.read",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool_v1)
        register_tool(tool_v2)

        # Latest version is returned by default
        latest = get_tool("file.read")
        assert latest["version"] == "2.0.0"

    def test_get_nonexistent_tool(self, registry):
        retrieved = get_tool("nonexistent")
        assert retrieved is None

    def test_list_tools(self, registry):
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
            register_tool(tool)

        tools = list_tools()
        assert len(tools) == 3

    def test_list_tools_by_origin(self, registry):
        for name in ["file.read", "shell.exec"]:
            tool = {
                "name": name,
                "version": "1.0.0",
                "origin": "builtin",
                "description": f"Tool {name}",
                "category": name,
                "inputs": {},
                "outputs": {}
            }
            register_tool(tool)

        custom_tool = {
            "name": "custom.tool",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom tool",
            "category": "custom",
            "inputs": {},
            "outputs": {}
        }
        register_tool(custom_tool)

        builtin_tools = list_tools(origin="builtin")
        assert len(builtin_tools) == 2

        custom_tools = list_tools(origin="custom")
        assert len(custom_tools) == 1

    def test_list_tools_by_category(self, registry):
        tool1 = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file contents",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        tool2 = {
            "name": "file.write",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Write file contents",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        tool3 = {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute shell command",
            "category": "shell",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool1)
        register_tool(tool2)
        register_tool(tool3)

        file_tools = list_tools(category="file")
        assert len(file_tools) == 2
        assert all(t["category"] == "file" for t in file_tools)

        shell_tools = list_tools(category="shell")
        assert len(shell_tools) == 1
        assert shell_tools[0]["name"] == "shell.exec"

    def test_list_tools_by_category_and_origin(self, registry):
        tool1 = {
            "name": "builtin.file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Builtin read file",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        tool2 = {
            "name": "custom.file.read",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom read file",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool1)
        register_tool(tool2)

        builtin_file = list_tools(category="file", origin="builtin")
        assert len(builtin_file) == 1
        assert builtin_file[0]["name"] == "builtin.file.read"

    def test_find_tools_by_name(self, registry):
        tool1 = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file contents",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        tool2 = {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute shell command",
            "category": "shell",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool1)
        register_tool(tool2)

        results = find_tools("file")
        assert len(results) == 1
        assert results[0]["name"] == "file.read"

    def test_find_tools_by_description(self, registry):
        tool = {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute shell command in sandbox",
            "category": "shell",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool)

        results = find_tools("sandbox")
        assert len(results) == 1
        assert results[0]["name"] == "shell.exec"

    def test_find_tools_by_category(self, registry):
        tool1 = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        tool2 = {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute shell",
            "category": "shell",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool1)
        register_tool(tool2)

        results = find_tools("file")
        assert len(results) == 1
        assert results[0]["name"] == "file.read"

    def test_find_tools_no_results(self, registry):
        tool = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool)

        results = find_tools("nonexistent")
        assert len(results) == 0
