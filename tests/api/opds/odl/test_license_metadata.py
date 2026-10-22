import json

from api.opds.odl.odl import Publication
from tests.fixtures.files import OPDS2WithODLFilesFixture


def test_license_metadata_validation(
    opds2_with_odl_files_fixture: OPDS2WithODLFilesFixture,
) -> None:
    """Validate the license metadata included in the DeMarque feed."""
    feed = json.loads(
        opds2_with_odl_files_fixture.sample_data("demarque_feed.json")
    )

    publication = Publication.model_validate(feed["publications"][3])
    [license1, license2] = publication.licenses

    metadata = license1.metadata
    assert metadata.identifier == "urn:uuid:eexx11"
    assert metadata.created.isoformat() == "2025-07-03T08:22:26+02:00"
    assert metadata.source == "http://www.cantook.net/"
    assert metadata.formats == ("application/epub+zip", "text/html")
    assert metadata.price is not None
    assert metadata.price.currency == "USD"
    assert metadata.price.value == 11.79
    assert metadata.terms.checkouts == 26
    assert metadata.terms.concurrency == 1
    assert metadata.terms.length == 5097600
    availability = metadata.availability
    assert availability.state is not None
    assert availability.status is not None
    assert availability.since is not None
    assert availability.state.value == "unavailable"
    assert availability.status.value == "unavailable"
    assert availability.since.isoformat() == "2026-01-11T17:12:21+01:00"
    assert (
        metadata.availability.reason
        == "https://registry.opds.io/reason#exhausted"
    )
    assert metadata.protection.formats == (
        "application/vnd.adobe.adept+xml",
        "application/vnd.readium.lcp.license.v1.0+json",
    )
    assert metadata.protection.devices == 6
    assert metadata.protection.allow_copy is False
    assert metadata.protection.allow_print is False
    assert metadata.protection.allow_tts is False
    assert metadata.order is not None
    assert metadata.order.name == "Varatuimmat heinäkuu1"
    assert metadata.order.identifier == "202507-1095-072789"
    assert metadata.order.href == "https://market.cantook.com/carts/72789"

    metadata = license2.metadata
    assert metadata.identifier == "urn:uuid:eexx22"
    assert metadata.created.isoformat() == "2026-02-25T15:17:50+01:00"
    assert metadata.source == "http://www.cantook.net/"
    assert metadata.formats == ("application/epub+zip", "text/html")
    assert metadata.price is not None
    assert metadata.price.currency == "USD"
    assert metadata.price.value == 12.75
    assert metadata.terms.checkouts == 26
    assert metadata.terms.concurrency == 1
    assert metadata.terms.length == 5097600
    availability = metadata.availability
    assert availability.state is not None
    assert availability.status is not None
    assert availability.state.value == "available"
    assert availability.status.value == "available"
    assert availability.since is None
    assert availability.reason is None
    assert metadata.protection.devices == 6
    assert metadata.protection.allow_copy is False
    assert metadata.protection.allow_print is False
    assert metadata.protection.allow_tts is False
    assert metadata.order is not None
    assert metadata.order.name == "Uusittavat ja varaukset helmi 2"
    assert metadata.order.identifier == "202602-1095-081114"
    assert metadata.order.href == "https://market.cantook.com/carts/81114"
