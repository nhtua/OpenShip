from openship.workflow import compile_graph

def test_compile_graph():
    graph = compile_graph()
    assert graph is not None
