import os
from pathlib import Path
from typing import TextIO

import pytest


class ResourcesFilesFixture:
    """A fixture providing access to resource files."""

    """These are files that are served by the CM in production, and aren't test suite specific."""

    def __init__(self):
        self._base_path = Path(__file__).parent.parent.parent
        self._resource_path = os.path.join(self._base_path, "resources", "images")

    def sample_data(self, filename) -> bytes:
        with open(self.sample_path(filename), "rb") as fh:
            return fh.read()

    def sample_text(self, filename) -> str:
        with open(self.sample_path(filename)) as fh:
            return fh.read()

    def sample_path(self, filename) -> str:
        return os.path.join(self._resource_path, filename)


@pytest.fixture(scope="session")
def resources_files_fixture() -> ResourcesFilesFixture:
    return ResourcesFilesFixture()


class FilesFixture:
    """A fixture providing access to test files."""

    def __init__(self, directory: str):
        self._base_path = Path(__file__).parent.parent
        self._resource_path = os.path.join(self._base_path, "core", "files", directory)

    def sample_data(self, filename) -> bytes:
        with open(self.sample_path(filename), "rb") as fh:
            return fh.read()

    def sample_text(self, filename) -> str:
        with open(self.sample_path(filename)) as fh:
            return fh.read()

    def sample_path(self, filename) -> str:
        return os.path.join(self._resource_path, filename)

    def sample_fd(self, filename) -> TextIO:
        return open(self.sample_path(filename))


class APIFilesFixture:
    """A fixture providing access to API test files."""

    def __init__(self, directory: str):
        self._base_path = Path(__file__).parent.parent
        self._resource_path = os.path.join(self._base_path, "api", "files", directory)

    @property
    def directory(self) -> str:
        return self._resource_path

    def sample_data(self, filename) -> bytes:
        with open(self.sample_path(filename), "rb") as fh:
            return fh.read()

    def sample_text(self, filename) -> str:
        with open(self.sample_path(filename)) as fh:
            return fh.read()

    def sample_path(self, filename) -> str:
        return os.path.join(self._resource_path, filename)


# TODO: Once we move all our test files to tests/files, we can remove the below specifc OPDSODLFilesFixture.
class OPDSODLFilesFixture:
    """A fixture providing access to API test files."""

    def __init__(self, directory: str):
        self._base_path = Path(__file__).parent.parent
        self._resource_path = os.path.join(self._base_path, "files", directory)

    @property
    def directory(self) -> str:
        return self._resource_path

    def sample_data(self, filename) -> bytes:
        with open(self.sample_path(filename), "rb") as fh:
            return fh.read()

    def sample_text(self, filename) -> str:
        with open(self.sample_path(filename)) as fh:
            return fh.read()

    def sample_path(self, filename) -> str:
        return os.path.join(self._resource_path, filename)


class OPDS2WithODLFilesFixture(OPDSODLFilesFixture):
    """A fixture providing access to OPDS2 + ODL files."""

    def __init__(self):
        super().__init__("odl")


@pytest.fixture()
def opds2_with_odl_files_fixture() -> OPDS2WithODLFilesFixture:
    """A fixture providing access to OPDS2 + ODL files."""
    return OPDS2WithODLFilesFixture()


class OPDS2FilesFixture(OPDSODLFilesFixture):
    """A fixture providing access to OPDS2 files."""

    def __init__(self):
        super().__init__("opds2")


@pytest.fixture()
def opds2_files_fixture() -> OPDS2FilesFixture:
    """A fixture providing access to OPDS2 files."""
    return OPDS2FilesFixture()


class OPDSFilesFixture(OPDSODLFilesFixture):
    """A fixture providing access to OPDS files."""

    def __init__(self):
        super().__init__("opds")


@pytest.fixture()
def opds_files_fixture() -> OPDSFilesFixture:
    """A fixture providing access to OPDS files."""
    return OPDSFilesFixture()
