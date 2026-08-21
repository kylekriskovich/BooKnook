import pytest

from app import grimmory_auth


@pytest.fixture(autouse=True)
def _clear_grimmory_access_token_cache():
    """grimmory_auth._access_token_cache is a module-level, process-lifetime dict (deliberately —
    see its own docstring in app/grimmory_auth.py) so it survives across requests within a real
    running app. In the test suite, though, many tests reuse the same small user ids (1, 2, ...)
    within one shared Python process, so a cached token left over from one test could otherwise
    leak into an unrelated later test and mask what that test is actually meant to exercise.
    Cleared before and after every test."""
    grimmory_auth._access_token_cache.clear()
    yield
    grimmory_auth._access_token_cache.clear()
