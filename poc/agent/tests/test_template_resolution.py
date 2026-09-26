from src.compiler import resolve_template, WorkflowState


def test_resolve_input_variable():
    state = WorkflowState(inputs={"name": "Alice"})
    result = resolve_template("Hello {name}", state)
    assert result == "Hello Alice"


def test_resolve_step_output():
    state = WorkflowState(outputs={1: "Alice"})
    result = resolve_template("Hello {step_1}", state)
    assert result == "Hello Alice"


def test_resolve_step_output_with_field():
    state = WorkflowState(outputs={1: {"name": "Alice", "city": "NYC"}})
    result = resolve_template("Hello {step_1.name} from {step_1.city}", state)
    assert result == "Hello Alice from NYC"


def test_resolve_multiple_steps():
    state = WorkflowState(outputs={1: "Alice", 2: "NYC"})
    result = resolve_template("Hello {step_1} from {step_2}", state)
    assert result == "Hello Alice from NYC"


def test_unresolved_variable_unchanged():
    state = WorkflowState()
    result = resolve_template("Hello {unknown}", state)
    assert result == "Hello {unknown}"


def test_mixed_inputs_and_outputs():
    state = WorkflowState(
        inputs={"greeting": "Hi"},
        outputs={1: "Alice"}
    )
    result = resolve_template("{greeting} {step_1}", state)
    assert result == "Hi Alice"


def test_step_no_underscore():
    """Test {step1} format without underscore (LLM-generated)."""
    state = WorkflowState(outputs={1: "Alice"})
    result = resolve_template("Hello {step1}", state)
    assert result == "Hello Alice"


def test_step_no_underscore_multiple():
    """Test multiple {stepN} format without underscore."""
    state = WorkflowState(outputs={1: "Alice", 2: "2026-09-25"})
    result = resolve_template("Hello {step1}, today is {step2}", state)
    assert result == "Hello Alice, today is 2026-09-25"