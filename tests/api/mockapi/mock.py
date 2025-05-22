import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from requests import Request, Response
from typing_extensions import Unpack

from core.util.http import HTTP, GetRequestKwargs, RequestKwargs


class MockHTTPClient:
    def __init__(self) -> None:
        self.responses: list[Response] = []
        self.requests: list[str] = []
        self.requests_args: list[RequestKwargs] = []
        self.requests_methods: list[str] = []

    def reset_mock(self) -> None:
        self.responses = []
        self.requests = []
        self.requests_args = []
        self.requests_methods = []

    def queue_response(
        self,
        response_code: int,
        media_type: str | None = None,
        other_headers: dict[str, str] | None = None,
        content: str | bytes | dict[str, Any] = "",
    ):
        """Queue a response of the type produced by HTTP.get_with_timeout."""
        headers = dict(other_headers or {})
        if media_type:
            headers["Content-Type"] = media_type

        self.responses.append(MockRequestsResponse(response_code, headers, content))

    def _request(self, *args: Any, **kwargs: Any) -> Response:
        return self.responses.pop(0)

    def do_request(
        self, http_method: str, url: str, **kwargs: Unpack[RequestKwargs]
    ) -> Response:
        self.requests.append(url)
        self.requests_methods.append(http_method)
        self.requests_args.append(kwargs)
        return HTTP._request_with_timeout(http_method, url, self._request, **kwargs)

    def do_get(self, url: str, **kwargs: Unpack[GetRequestKwargs]) -> Response:
        return self.do_request("GET", url, **kwargs)

    @contextmanager
    def patch(self) -> Generator[None, None, None]:
        with patch.object(HTTP, "request_with_timeout", self.do_request):
            yield


class MockRequestsRequest:
    """A mock object that simulates an HTTP request from the
    `requests` library.
    """

    def __init__(self, url, method="GET", headers=None):
        self.url = url
        self.method = method
        self.headers = headers or dict()


class MockRequestsResponse(Response):
    """A mock object that simulates an HTTP response from the
    `requests` library.
    """

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str] | None = None,
        content: Any = None,
        url: str | None = None,
        request: Request | None = None,
    ):
        super().__init__()

        self.status_code = status_code
        if headers is not None:
            for k, v in headers.items():
                self.headers[k] = v

        # We want to enforce that the mocked content is a bytestring
        # just like a real response.
        if content is not None:
            if isinstance(content, str):
                content_bytes = content.encode("utf-8")
            elif isinstance(content, bytes):
                content_bytes = content
            else:
                content_bytes = json.dumps(content).encode("utf-8")
            self._content = content_bytes

        if request and not url:
            url = request.url
        self.url = url or "http://url/"
        self.encoding = "utf-8"
        if request:
            self.request = request.prepare()
