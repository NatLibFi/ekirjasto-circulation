from palace.manager.util.pydantic import HttpUrl
from pydantic import TypeAdapter


class TestStrUrlTypes:
    def test_http_url(self) -> None:
        ta = TypeAdapter(HttpUrl)
        validate = ta.validate_python

        assert validate("http://localhost:6379") == "http://localhost:6379"
        assert validate("http://localhost:6379/") == "http://localhost:6379"
        assert validate("http://10.0.0.1/foo") == "http://10.0.0.1/foo"
        assert validate("http://10.0.0.1/foo/") == "http://10.0.0.1/foo"
