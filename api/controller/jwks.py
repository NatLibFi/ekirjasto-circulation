import os
import time

from flask import Response

from api.problem_details import JWKS_FILE_ERROR
from core.util.log import LoggerMixin
from core.util.problem_detail import ProblemDetail


class JwksController(LoggerMixin):
    def __init__(self):
        self._jwks_cache = {"data": None, "timestamp": 0}
        self._JWKS_CACHE_TTL = 3600  # 1 hour
        self.jwks_file_path = os.environ.get("PALACE_DEMARQUE_WEBREADER_JWK_FILE", "")

    def get_jwks(self) -> Response | ProblemDetail:
        """
        Return the JWKS data, either from cache or by reading from the file.
        """
        current_time = time.time()

        # Return cached version if still valid
        if (
            self._jwks_cache["data"]
            and (current_time - self._jwks_cache["timestamp"]) < self._JWKS_CACHE_TTL
        ):
            response = Response(self._jwks_cache["data"], mimetype="application/json")
        else:
            # Read from disk and cache it.
            try:
                with open(self.jwks_file_path, "rb") as f:
                    data = f.read()
                self._jwks_cache["data"] = data
                self._jwks_cache["timestamp"] = time.time()
                response = Response(data, mimetype="application/json")
            except Exception as e:
                self.log.error(f"Error reading JWKS file: {e}")
                return JWKS_FILE_ERROR

        response.headers["Cache-Control"] = "public, max-age=86400"  # 24 hours
        return response
