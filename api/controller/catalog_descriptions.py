from __future__ import annotations

from flask import Response
from sqlalchemy.orm.exc import NoResultFound

from api.circulation_exceptions import *
from api.controller.circulation_manager import CirculationManagerController
from api.problem_details import *
from core.app_server import url_for
from core.model import Library, json_serializer
from core.util.datetime_helpers import utc_now


# Finland
class CatalogDescriptionsController(CirculationManagerController):
    def get_catalogs(self, library_uuid=None):
        catalogs = []
        libraries = []

        if library_uuid != None:
            try:
                libraries = [
                    self._db.query(Library).filter(Library.uuid == library_uuid).one()
                ]
            except NoResultFound:
                return LIBRARY_NOT_FOUND
        else:
            libraries = self._db.query(Library).order_by(Library.name).all()

        for library in libraries:
            settings = library.settings_dict
            images = []
            if library.logo:
                images += [
                    {
                        "rel": "http://opds-spec.org/image/thumbnail",
                        "href": library.logo.data_url,
                        "type": "image/png",
                    }
                ]

            authentication_document_url = url_for(
                "authentication_document",
                library_short_name=library.short_name,
                _external=True,
            )

            catalog_url = url_for(
                "acquisition_groups",
                library_short_name=library.short_name,
                _external=True,
            )

            timenow = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")

            metadata = {
                "id": "urn:uuid:" + library.uuid,
                "title": library.name,
                "short_name": library.short_name,
                "modified": timenow,
                "updated": timenow,
                "isAutomatic": False,
            }

            if "library_description" in settings:
                metadata["description"] = settings["library_description"]

            links = [
                {
                    "rel": "http://opds-spec.org/catalog",
                    "href": catalog_url,
                    "type": "application/atom+xml;profile=opds-catalog;kind=acquisition",
                },
                {
                    "href": authentication_document_url,
                    "type": "application/vnd.opds.authentication.v1.0+json",
                },
            ]

            if "help_web" in settings:
                links += [{"href": settings["help_web"], "rel": "help"}]
            elif "help_email" in settings:
                links += [{"href": "mailto:" + settings["help_email"], "rel": "help"}]

            catalogs += [{"metadata": metadata, "links": links, "images": images}]

        response_json = {
            "metadata": {"title": "Libraries"},
            "catalogs": catalogs,
            "links": [
                {
                    "rel": "self",
                    "href": url_for("client_libraries", _external=True),
                    "type": "application/opds+json",
                }
            ],
        }

        return Response(
            json_serializer(response_json),
            status=200,
            mimetype="application/json",
        )
