import pytest
from mcp import Client
from srv_official import mcp

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend(): return "asyncio"

async def test_count_by_structured():
    async with Client(mcp) as c:
        r = await c.call_tool("count_by", {"dimension": "component", "limit": 1})
    assert not r.is_error
    assert r.structured_content == {"evidence_id": "ev_abc123", "rows": [{"key": "KERNEL", "count": 42}], "truncated": False}

async def test_bad_dimension_is_tool_error():
    async with Client(mcp) as c:
        r = await c.call_tool("count_by", {"dimension": "host"})
    assert r.is_error and "Input should be 'component' or 'level'" in r.content[0].text
