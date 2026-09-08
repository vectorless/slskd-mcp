import pytest


@pytest.fixture
def anyio_backend():
    """anyio's pytest plugin ships with mcp; asyncio only, no trio here."""
    return "asyncio"
