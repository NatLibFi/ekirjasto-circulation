import json
from typing import Any

from requests import Request, Response


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
