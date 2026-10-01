"""OpenShip CLI interface.

Provides terminal-native commands for interacting with the OpenShip
agent orchestration backend. Uses Typer for CLI framework, Rich for
output formatting, and httpx for API communication.
"""

import json
import os
import sys

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich.pretty import Pretty
from rich.panel import Panel

app = typer.Typer(
    name="openship",
    help="OpenShip - AI-powered agentic workflow automation",
)

console = Console()

# Default API URL
DEFAULT_API_URL = "http://localhost:8000"


def get_api_url() -> str:
    """Get the API URL from environment or default."""
    return os.environ.get("OPENSUP_API_URL", DEFAULT_API_URL)


def api_get(path: str, params: dict | None = None) -> dict | list:
    """Make a GET request to the API."""
    base_url = get_api_url()
    url = f"{base_url}{path}"
    try:
        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error {e.response.status_code}:[/red] {e.response.json().get('detail', str(e))}")
        raise typer.Exit(1)
    except httpx.RequestError as e:
        console.print(f"[red]Connection error:[/red] Could not reach API at {url}")
        console.print(f"  Error: {e}")
        console.print("  Check that the backend is running and OPENSUP_API_URL is correct.")
        raise typer.Exit(1)


def api_post(path: str, body: dict) -> dict:
    """Make a POST request to the API."""
    base_url = get_api_url()
    url = f"{base_url}{path}"
    try:
        response = httpx.post(url, json=body, timeout=300)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        console.print(f"[red]Error {e.response.status_code}:[/red] {detail}")
        raise typer.Exit(1)
    except httpx.RequestError as e:
        console.print(f"[red]Connection error:[/red] Could not reach API at {url}")
        console.print(f"  Error: {e}")
        raise typer.Exit(1)


def format_json(data) -> str:
    """Format data as pretty JSON."""
    return json.dumps(data, indent=2, default=str)


# Health command
@app.command()
def health():
    """Check backend service health."""
    try:
        result = api_get("/api/health")
        console.print("[green]✓[/green] Backend is healthy")
    except typer.Exit:
        raise


# Workflow commands
workflow_app = typer.Typer(help="Manage workflows")
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("list")
def workflow_list(origin: str | None = typer.Option(None, "--origin", help="Filter by origin")):
    """List available workflows."""
    params = {"origin": origin} if origin else None
    workflows = api_get("/api/workflows", params=params)

    if not workflows:
        console.print("[yellow]No workflows found.[/yellow]")
        return

    table = Table(title="Workflows")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Origin")
    table.add_column("Description")
    table.add_column("Tags")

    for wf in workflows:
        table.add_row(
            wf.get("name", "-"),
            str(wf.get("version", "-")),
            wf.get("origin", "-"),
            (wf.get("description") or "")[:50],
            ", ".join(wf.get("tags") or [])[:30],
        )

    console.print(table)


@workflow_app.command("run")
def workflow_run(
    name: str,
    inputs: str = typer.Option(None, "--inputs", "-i", help="JSON string of workflow inputs"),
):
    """Run a workflow by name."""
    input_dict = {}
    if inputs:
        try:
            input_dict = json.loads(inputs)
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON for inputs:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[blue]Running workflow:[/blue] {name}")
    result = api_post("/api/workflows/run", {"name": name, "inputs": input_dict})

    console.print("[green]✓[/green] Workflow completed")
    console.print(Panel(format_json(result)))


@workflow_app.command("status")
def workflow_status(execution_id: str):
    """Check status of a workflow execution."""
    result = api_get(f"/api/workflows/status/{execution_id}")
    console.print(Panel(format_json(result)))


@workflow_app.command("resume")
def workflow_resume(
    execution_id: str,
    response: str = typer.Option(None, "--response", "-r", help="JSON response for human-in-the-loop"),
):
    """Resume a paused workflow execution."""
    response_data = None
    if response:
        try:
            response_data = json.loads(response)
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON for response:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[blue]Resuming execution:[/blue] {execution_id}")
    result = api_post(f"/api/workflows/resume/{execution_id}", {"response": response_data})

    console.print("[green]✓[/green] Execution resumed")
    console.print(Panel(format_json(result)))


@workflow_app.command("search")
def workflow_search(query: str):
    """Search for workflows by query."""
    result = api_get("/api/workflows/search", params={"q": query})
    if not result:
        console.print("[yellow]No workflows found.[/yellow]")
        return

    console.print(f"[blue]Found {len(result)} workflows:[/blue]")
    for wf in result:
        console.print(f"  [bold]{wf.get('name')}[/bold] v{wf.get('version')} ({wf.get('origin')})")
        console.print(f"    {wf.get('description', '')[:100]}")


# Tool commands
tool_app = typer.Typer(help="Manage tools")
app.add_typer(tool_app, name="tool")


@tool_app.command("list")
def tool_list(
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category"),
    origin: str | None = typer.Option(None, "--origin", "-o", help="Filter by origin"),
):
    """List available tools."""
    params = {}
    if category:
        params["category"] = category
    if origin:
        params["origin"] = origin

    tools = api_get("/api/tools", params=params if params else None)

    if not tools:
        console.print("[yellow]No tools found.[/yellow]")
        return

    table = Table(title="Tools")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Category")
    table.add_column("Origin")
    table.add_column("Description")

    for tool in tools:
        table.add_row(
            tool.get("name", "-"),
            str(tool.get("version", "-")),
            tool.get("category") or "-",
            tool.get("origin") or "-",
            (tool.get("description") or "")[:50],
        )

    console.print(table)


@tool_app.command("describe")
def tool_describe(name: str):
    """Get detailed information about a tool."""
    tools = api_get("/api/tools")
    tool = next((t for t in tools if t.get("name") == name), None)

    if not tool:
        console.print(f"[red]Tool not found:[/red] {name}")
        raise typer.Exit(1)

    console.print(Panel(format_json(tool)))


@tool_app.command("search")
def tool_search(query: str):
    """Search for tools by query."""
    result = api_get("/api/tools/search", params={"q": query})
    if not result:
        console.print("[yellow]No tools found.[/yellow]")
        return

    console.print(f"[blue]Found {len(result)} tools:[/blue]")
    for tool in result:
        console.print(f"  [bold]{tool.get('name')}[/bold] v{tool.get('version')} ({tool.get('origin')})")
        console.print(f"    {tool.get('description', '')[:100]}")


# Session commands
session_app = typer.Typer(help="Manage sessions")
app.add_typer(session_app, name="session")


@session_app.command("list")
def session_list():
    """List active sessions."""
    console.print("[yellow]Session listing not yet implemented in backend API.[/yellow]")
    raise typer.Exit(0)


if __name__ == "__main__":
    app()
