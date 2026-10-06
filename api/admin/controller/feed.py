from __future__ import annotations

import flask
from flask import url_for
from flask_babel import gettext

from api.admin.controller.base import AdminPermissionsControllerMixin
from api.controller.circulation_manager import CirculationManagerController
from core.app_server import load_pagination_from_request
from core.classifier import genres
from core.classifier.localized_names import genres as localized_genres
from core.feed.admin import AdminFeed
from core.feed.annotator.admin import AdminAnnotator
from core.util.problem_detail import ProblemDetail


class FeedController(CirculationManagerController, AdminPermissionsControllerMixin):
    def suppressed(self):
        self.require_librarian(flask.request.library)

        this_url = url_for("suppressed", _external=True)
        annotator = AdminAnnotator(self.circulation, flask.request.library)
        pagination = load_pagination_from_request()
        if isinstance(pagination, ProblemDetail):
            return pagination
        opds_feed = AdminFeed.suppressed(
            _db=self._db,
            title="Hidden Books",
            url=this_url,
            annotator=annotator,
            pagination=pagination,
        )
        return opds_feed.as_response(max_age=0)

    def genres(self):
        data = {
            str(gettext("Fiction")): {},
            str(gettext("Nonfiction")): {},
        }
        for name in genres:
            genre = genres[name]
            top = str(gettext("Fiction" if genre.is_fiction else "Nonfiction"))

            # Evaluate all genre labels while the request's Accept-Language
            # locale is active, including the dictionary keys.
            translated_name = str(localized_genres[name])
            data[top][translated_name] = dict(
                {
                    "name": translated_name,
                    "parents": [
                        str(localized_genres[parent.name]) for parent in genre.parents
                    ],
                    "subgenres": [
                        str(localized_genres[subgenre.name])
                        for subgenre in genre.subgenres
                    ],
                }
            )
        return data
