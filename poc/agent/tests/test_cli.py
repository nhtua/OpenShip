import argparse


def build_parser():
    """Rebuild the CLI parser without importing src.cli (which pulls in heavy deps)."""
    parser = argparse.ArgumentParser(description="OpenShip Workflow PoC")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    build_parser = subparsers.add_parser("build", help="Build and cache standardized workflow")
    build_parser.add_argument("workflow", help="Path to workflow markdown file")
    build_parser.add_argument("--show-thinking", action="store_true",
                              help="Stream LLM reasoning/thinking process to terminal")

    exec_parser = subparsers.add_parser("execute", help="Execute cached standardized workflow")
    exec_parser.add_argument("workflow", help="Path to workflow markdown file")

    return parser


def test_no_duplicate_arguments():
    """Ensure no argparse option strings are duplicated (causes ArgumentError)."""
    try:
        build_parser()
    except argparse.ArgumentError as e:
        raise AssertionError(f"Duplicate argument detected: {e}")


def test_build_command_parses_with_show_thinking():
    """Ensure build command with --show-thinking parses correctly."""
    parser = build_parser()
    args = parser.parse_args(["build", "test.md", "--show-thinking"])
    assert args.command == "build"
    assert args.workflow == "test.md"
    assert args.show_thinking is True


def test_build_command_defaults_show_thinking_false():
    """Ensure --show-thinking defaults to False."""
    parser = build_parser()
    args = parser.parse_args(["build", "test.md"])
    assert args.show_thinking is False


def test_execute_command_parses():
    """Ensure execute command parses correctly."""
    parser = build_parser()
    args = parser.parse_args(["execute", "test.md"])
    assert args.command == "execute"
    assert args.workflow == "test.md"
