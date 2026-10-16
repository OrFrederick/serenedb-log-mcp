import pytest
from fastmcp import Client
from srv_fast import mcp
@pytest.mark.anyio
async def test_count_by():
    async with Client(mcp) as c:
        r = await c.call_tool("count_by", {"dimension": "component", "limit": 1})
    assert r.structured_content["rows"] == [{"key": "KERNEL", "count": 42}]
@pytest.mark.anyio
async def test_x(): pass
@pytest.fixture
def anyio_backend(): return "asyncio"
