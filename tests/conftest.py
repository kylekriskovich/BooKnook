import pytest

from app import grimmory_auth


@pytest.fixture(autouse=True)
def _clear_grimmory_access_token_cache():
    """grimmory_auth._access_token_cache is a process-lifetime dict (see its docstring in
    app/grimmory_auth.py); tests reuse small user ids across one shared process, so a leftover
    cached token could leak between tests. Cleared before and after every test."""
    grimmory_auth._access_token_cache.clear()
    yield
    grimmory_auth._access_token_cache.clear()
