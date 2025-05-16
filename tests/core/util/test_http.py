from functools import partial
import json
from typing import Any, Mapping, Sequence
from unittest import mock

import pytest
import requests
from requests import Response

from core.problem_details import INVALID_INPUT
from core.util.http import (
    HTTP,
    INTEGRATION_ERROR,
    BadResponseException,
    RemoteIntegrationException,
    RequestNetworkException,
    RequestTimedOut,
)
from core.util.problem_detail import ProblemDetail, ProblemError
from tests.core.mock import MockRequestsResponse

class FakeRequest:
    def __init__(self, response: Response | None = None) -> None:
        self.agent: str | None = None
        self.args: Sequence[Any] | None = None
        self.kwargs: Mapping[str, Any] | None = None
        self.response = response or MockRequestsResponse(201)

    def fake_request(self, *args: Any, **kwargs: Any) -> Response:
        self.agent = kwargs["headers"]["User-Agent"]
        self.args = args
        self.kwargs = kwargs
        return self.response

class TestHTTP:
    def test_series(self) -> None:
        m = HTTP.series
        assert m(201) == "2xx"
        assert m(399) == "3xx"
        assert m(500) == "5xx"

    @mock.patch("core.util.http.sessions.Session")
    def test_request_with_timeout_defaults(self, mock_session: mock.MagicMock) -> None:
        with (
            mock.patch.object(HTTP, "DEFAULT_REQUEST_TIMEOUT", 10),
            mock.patch.object(HTTP, "DEFAULT_REQUEST_RETRIES", 2),
        ):
            mock_ctx = mock_session().__enter__()
            mock_request = mock_ctx.request
            HTTP.request_with_timeout("GET", "url")
            # The session adapter has a retry attached
            assert mock_ctx.mount.call_args[0][1].max_retries.total == 2
            mock_request.assert_called_once()
            # The request has a timeout
            assert mock_request.call_args[1]["timeout"] == 10

    @mock.patch("core.util.http.core.__version__", "<VERSION>")
    def test_request_with_timeout_success(self):
        request = FakeRequest(MockRequestsResponse(200, content="Success!"))
        response = HTTP._request_with_timeout(
            "GET", "http://url/", request.fake_request, kwarg="value"  # type: ignore[call-arg]
        )
        assert response.status_code == 200
        assert response.content == b"Success!"

        # User agent header should be set
        assert request.agent == "E-Kirjasto/<VERSION>"

        # The HTTP method and URL are passed in the order
        # requests.request would expect.
        assert ("GET", "http://url/") == request.args

        # Keyword arguments to _request_with_timeout are passed in
        # as-is.
        assert request.kwargs is not None
        assert request.kwargs["kwarg"] == "value"

        # A default timeout is added.
        assert request.kwargs["timeout"] == 20

    def test_request_with_timeout_with_ua(self) -> None:
        request = FakeRequest()
        assert (
            HTTP._request_with_timeout(
                "GET",
                "http://url",
                request.fake_request,
                headers={"User-Agent": "Fake Agent"},
            ).status_code
            == 201
        )
        assert request.agent == "Fake Agent"

    @mock.patch("core.util.http.core.__version__", None)
    def test_default_user_agent(self) -> None:
        request = FakeRequest()
        assert (
            HTTP._request_with_timeout("DELETE", "/", request.fake_request).status_code
            == 201
        )
        assert request.agent == "E-Kirjasto/1.x.x"

        # User agent is still set if headers are None
        assert (
            HTTP._request_with_timeout(
                "GET", "/", request.fake_request, headers=None
            ).status_code
            == 201
        )
        assert request.agent == "E-Kirjasto/1.x.x"

        # The headers are not modified if they are passed into the function
        original_headers = {"header": "value"}
        assert (
            HTTP._request_with_timeout(
                "GET", "/", request.fake_request, headers=original_headers
            ).status_code
            == 201
        )
        assert request.agent == "E-Kirjasto/1.x.x"
        assert original_headers == {"header": "value"}

    def test_request_with_timeout_failure(self) -> None:
        def immediately_timeout(*args, **kwargs) -> Response:
            raise requests.exceptions.Timeout("I give up")

        with pytest.raises(
            RequestTimedOut, match="Timeout accessing http://url/: I give up"
        ):
            HTTP._request_with_timeout("PUT", "http://url/", immediately_timeout)

    def test_request_with_network_failure(self) -> None:
        def immediately_fail(*args, **kwargs) -> Response:
            raise requests.exceptions.ConnectionError("a disaster")

        with pytest.raises(
            RequestNetworkException,
            match="Network error contacting http://url/: a disaster",
        ):
            HTTP._request_with_timeout("POST", "http://url/", immediately_fail)

    def test_request_with_response_indicative_of_failure(self) -> None:
        def fake_500_response(*args, **kwargs) -> Response:
            return MockRequestsResponse(500, content="Failure!")

        with pytest.raises(
            BadResponseException,
            match="Bad response from http://url/: Got status code 500 from external server",
        ):
            HTTP._request_with_timeout("GET", "http://url/", fake_500_response)

    def test_allowed_response_codes(self) -> None:
        """Test our ability to raise BadResponseException when
        an HTTP-based integration does not behave as we'd expect.
        """

        def fake_401_response(*args, **kwargs) -> Response:
            return MockRequestsResponse(401, content="Weird")

        def fake_200_response(*args, **kwargs) -> Response:
            return MockRequestsResponse(200, content="Hurray")

        url = "http://url/"
        request = partial(HTTP._request_with_timeout, "GET", url)

        # By default, every code except for 5xx codes is allowed.
        response = request(fake_401_response)
        assert response.status_code == 401

        # You can say that certain codes are specifically allowed, and
        # all others are forbidden.
        with pytest.raises(
            BadResponseException,
            match="Bad response from http://url/: Got status code 401 from external server, but can only continue on: 200, 201.",
        ):
            request(fake_401_response, allowed_response_codes=[201, 200])

        response = request(fake_401_response, allowed_response_codes=[401])
        response = request(fake_401_response, allowed_response_codes=["4xx"])

        # In this way you can even raise an exception on a 200 response code.
        with pytest.raises(
            BadResponseException,
            match="Bad response from http://url/: Got status code 200 from external server, but can only continue on: 401.",
        ):
            request(fake_200_response, allowed_response_codes=[401])

        # You can say that certain codes are explicitly forbidden, and
        # all others are allowed.
        with pytest.raises(
            BadResponseException,
            match="Bad response from http://url/: Got status code 401 from external server, cannot continue.",
        ) as excinfo:
            request(fake_401_response, disallowed_response_codes=[401])

        with pytest.raises(
            BadResponseException,
            match="Bad response from http://url/: Got status code 200 from external server, cannot continue.",
        ):
            request(fake_200_response, disallowed_response_codes=["2xx", 301])

        response = request(fake_401_response, disallowed_response_codes=["2xx"])
        assert response.status_code == 401

        # The exception can be turned into a useful problem detail document.
        with pytest.raises(BadResponseException) as exc_info:
            request(fake_200_response, disallowed_response_codes=["2xx"])

        problem_detail = exc_info.value.problem_detail

        # 502 is the status code to be returned if this integration error
        # interrupts the processing of an incoming HTTP request, not the
        # status code that caused the problem.
        #
        assert problem_detail.status_code == 502
        assert problem_detail.title == "Bad response"
        assert (
            problem_detail.detail
            == "The server made a request to url, and got an unexpected or invalid response."
        )
        print(repr(problem_detail.debug_message))
        assert (
            problem_detail.debug_message
            == "Bad response from http://url/: Got status code 200 from external server, cannot continue.\n\nResponse content: Hurray"
        )

    def test_unicode_converted_to_utf8(self):
        """Any Unicode that sneaks into the URL, headers or body is
        converted to UTF-8.
        """

        class ResponseGenerator:
            def __init__(self):
                self.requests = []

            def response(self, *args, **kwargs):
                self.requests.append((args, kwargs))
                return MockRequestsResponse(200, content="Success!")

        generator = ResponseGenerator()
        url = "http://foo"
        response = HTTP._request_with_timeout(
            url,
            "POST",
            generator.response,
            headers={"unicode header": "unicode value"},
            data="unicode data",
        )
        [(args, kwargs)] = generator.requests
        url, method = args
        headers = kwargs["headers"]
        data = kwargs["data"]

        # All the Unicode data was converted to bytes before being sent
        # "over the wire".
        for k, v in list(headers.items()):
            assert isinstance(k, str)
            assert isinstance(v, str)
        assert isinstance(data, str)

    def test_debuggable_request(self):
        class Mock(HTTP):
            @classmethod
            def _request_with_timeout(cls, *args, **kwargs):
                cls.called_with = (args, kwargs)
                return "response"

        def mock_request(*args, **kwargs):
            response = MockRequestsResponse(200, "Success!")
            return response

        Mock.debuggable_request(
            "method", "url", make_request_with=mock_request, key="value"
        )
        (args, kwargs) = Mock.called_with
        assert args == ("url", mock_request, "method")
        assert kwargs["key"] == "value"
        assert kwargs["process_response_with"] == Mock.process_debuggable_response

    def test_process_debuggable_response(self):
        """Test a method that gives more detailed information when a
        problem happens.
        """
        m = HTTP.process_debuggable_response
        success = MockRequestsResponse(200, content="Success!")
        assert success == m("url", success)

        success = MockRequestsResponse(302, content="Success!")
        assert success == m("url", success)

        # An error is turned into a ProblemError
        error = MockRequestsResponse(500, content="Error!")
        with pytest.raises(ProblemError) as excinfo:
            m("url", error)
        problem = excinfo.value.problem_detail
        assert isinstance(problem, ProblemDetail)
        assert INTEGRATION_ERROR.uri == problem.uri
        assert '500 response from integration server: "Error!"' == problem.detail

        content, status_code, headers = INVALID_INPUT.response
        error = MockRequestsResponse(status_code, headers, content)
        with pytest.raises(ProblemError) as excinfo:
            m("url", error)
        problem = excinfo.value.problem_detail
        assert isinstance(problem, ProblemDetail)
        assert INTEGRATION_ERROR.uri == problem.uri
        assert (
            "Remote service returned a problem detail document: %r" % content
            == problem.detail
        )
        assert content == problem.debug_message
        # You can force a response to be treated as successful by
        # passing in its response code as allowed_response_codes.
        assert error == m("url", error, allowed_response_codes=[400])
        assert error == m("url", error, allowed_response_codes=["400"])
        assert error == m("url", error, allowed_response_codes=["4xx"])


class TestRemoteIntegrationException:
    def test_with_service_name(self):
        """You don't have to provide a URL when creating a
        RemoteIntegrationException; you can just provide the service
        name.
        """
        exc = RemoteIntegrationException(
            "Unreliable Service", "I just can't handle your request right now."
        )

        # Since only the service name is provided, there are no details to
        # elide in the non-debug version of a problem detail document.
        debug_detail = exc.document_detail(debug=True)
        other_detail = exc.document_detail(debug=False)
        assert debug_detail == other_detail

        assert (
            "The server tried to access Unreliable Service but the third-party service experienced an error."
            == debug_detail
        )


class TestBadResponseException:
    def test_helper_constructor(self):
        response = MockRequestsResponse(102, content="nonsense")
        exc = BadResponseException(
            "http://url/", "Terrible response, just terrible", response
        )

        # Turn the exception into a problem detail document, and it's full
        # of useful information.
        doc, status_code, headers = exc.as_problem_detail_document(debug=True).response
        doc = json.loads(doc)

        assert "Bad response" == doc["title"]
        assert (
            "The server made a request to http://url/, and got an unexpected or invalid response."
            == doc["detail"]
        )
        print(repr(doc["debug_message"]))
        assert (
            'Bad response from http://url/: Terrible response, just terrible\n\nStatus code: 102\nContent: nonsense\n\nStatus code: 102\nContent: nonsense'
            == doc["debug_message"]
        )

        # Unless debug is turned off, in which case none of that
        # information is present.
        doc, status_code, headers = exc.as_problem_detail_document(debug=False).response
        assert "debug_message" not in json.loads(doc)

    def test_bad_status_code_helper(object):
        response = MockRequestsResponse(500, content="Internal Server Error!")
        exc = BadResponseException.bad_status_code("http://url/", response)
        doc, status_code, headers = exc.as_problem_detail_document(debug=True).response
        doc = json.loads(doc)

        assert doc["debug_message"].startswith(
            "Bad response from http://url/: Got status code 500 from external server, cannot continue."
        )

    def test_as_problem_detail_document(self):
        exception = BadResponseException(
            "http://url/", "What even is this", debug_message="some debug info"
        )
        document = exception.as_problem_detail_document(debug=True)
        assert 502 == document.status_code
        assert "Bad response" == document.title
        assert (
            "The server made a request to http://url/, and got an unexpected or invalid response."
            == document.detail
        )
        assert (
            "Bad response from http://url/: What even is this\n\nsome debug info\n\nsome debug info"
            == document.debug_message
        )


class TestRequestTimedOut:
    def test_as_problem_detail_document(self):
        exception = RequestTimedOut("http://url/", "I give up")

        debug_detail = exception.as_problem_detail_document(debug=True)
        assert "Timeout" == debug_detail.title
        assert (
            "The server made a request to http://url/, and that request timed out."
            == debug_detail.detail
        )

        # If we're not in debug mode, we hide the URL we accessed and just
        # show the hostname.
        standard_detail = exception.as_problem_detail_document(debug=False)
        assert (
            "The server made a request to url, and that request timed out."
            == standard_detail.detail
        )

        # The status code corresponding to an upstream timeout is 502.
        document, status_code, headers = standard_detail.response
        assert 502 == status_code


class TestRequestNetworkException:
    def test_as_problem_detail_document(self):
        exception = RequestNetworkException("http://url/", "Colossal failure")

        debug_detail = exception.as_problem_detail_document(debug=True)
        assert "Network failure contacting third-party service" == debug_detail.title
        assert (
            "The server experienced a network error while contacting http://url/."
            == debug_detail.detail
        )

        # If we're not in debug mode, we hide the URL we accessed and just
        # show the hostname.
        standard_detail = exception.as_problem_detail_document(debug=False)
        assert (
            "The server experienced a network error while contacting url."
            == standard_detail.detail
        )

        # The status code corresponding to an upstream timeout is 502.
        document, status_code, headers = standard_detail.response
        assert 502 == status_code
