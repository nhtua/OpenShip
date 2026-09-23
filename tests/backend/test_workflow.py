import pytest
from openship.workflow import compile_graph

@pytest.mark.asyncio
async def test_compile_graph():
    graph = await compile_graph()
    assert graph is not None
