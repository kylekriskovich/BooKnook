import httpx

from app import grimmory_http


def test_already_logged_true_for_http_status_error():
    request = httpx.Request("GET", "https://grimmory.example.com/api/v1/books")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("error", request=request, response=response)

    assert grimmory_http.already_logged(exc) is True


def test_already_logged_false_for_connection_error():
    # A connection-level failure (timeout, DNS, refused) never reaches client()'s response event
    # hook or log_call, since no response was ever received - callers must still log it themselves.
    exc = httpx.ConnectError("boom")

    assert grimmory_http.already_logged(exc) is False
