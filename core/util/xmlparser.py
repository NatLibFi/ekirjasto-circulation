from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from io import BytesIO
import re
from typing import TYPE_CHECKING, Generic, TypeVar

from lxml import etree

if TYPE_CHECKING:
    from lxml.etree import _Element, _ElementTree

T = TypeVar("T")


class XMLParser:

    """Helper functions to process XML data."""

    NAMESPACES: dict[str, str] = {}
    # Matches decimal (&#123;) and hex (&#x7F; / &#X7F;) numeric character references.
    INVALID_XML_CHARACTER_REFERENCES = re.compile(
        rb"&#(?:(?P<hex>[xX][0-9a-fA-F]+)|(?P<dec>[0-9]+));"
    )

    @classmethod
    def _xpath(
        cls, tag: _Element, expression: str, namespaces: dict[str, str] | None = None
    ) -> list[_Element]:
        if not namespaces:
            namespaces = cls.NAMESPACES
        return tag.xpath(expression, namespaces=namespaces)  # type: ignore[no-any-return]

    @classmethod
    def _xpath1(
        cls, tag: _Element, expression: str, namespaces: dict[str, str] | None = None
    ) -> _Element | None:
        """Wrapper to do a namespaced XPath expression."""
        values = cls._xpath(tag, expression, namespaces=namespaces)
        if not values:
            return None
        return values[0]

    def _cls(self, tag_name: str, class_name: str) -> str:
        """Return an XPath expression that will find a tag with the given CSS class."""
        return (
            'descendant-or-self::node()/%s[contains(concat(" ", normalize-space(@class), " "), " %s ")]'
            % (tag_name, class_name)
        )

    def text_of_optional_subtag(
        self, tag: _Element, name: str, namespaces: dict[str, str] | None = None
    ) -> str | None:
        tag = self._xpath1(tag, name, namespaces=namespaces)
        if tag is None or tag.text is None:
            return None
        else:
            return str(tag.text)

    def text_of_subtag(
        self, tag: _Element, name: str, namespaces: dict[str, str] | None = None
    ) -> str:
        return str(tag.xpath(name, namespaces=namespaces)[0].text)

    def int_of_subtag(
        self, tag: _Element, name: str, namespaces: dict[str, str] | None = None
    ) -> int:
        return int(self.text_of_subtag(tag, name, namespaces=namespaces))

    def int_of_optional_subtag(
        self, tag: _Element, name: str, namespaces: dict[str, str] | None = None
    ) -> int | None:
        v = self.text_of_optional_subtag(tag, name, namespaces=namespaces)
        if not v:
            return None
        return int(v)

    @staticmethod
    def _load_xml(
        xml: str | bytes | _ElementTree,
    ) -> _ElementTree:
        """
        Load an XML document from string or bytes and handle the case where
        the document has already been parsed.
        """
        if isinstance(xml, str):
            xml = xml.encode("utf8")

        if isinstance(xml, bytes):
            # XMLParser can handle most characters and entities that are
            # invalid in XML but it will stop processing a document if it
            # encounters the null character. Remove that character
            # immediately and XMLParser will handle the rest.
            xml = xml.replace(b"\x00", b"")
            xml = XMLParser._strip_invalid_xml_character_references(xml)
            parser = etree.XMLParser(recover=True)
            return etree.parse(BytesIO(xml), parser)

        else:
            return xml

    @staticmethod
    def _strip_invalid_xml_character_references(xml: bytes) -> bytes:
        """Remove numeric character references that are invalid in XML 1.0.

        lxml 6.x rejects character references whose codepoints fall outside the
        XML 1.0 Char production (§2.2 https://www.w3.org/TR/xml/#charsets).
        Vendor feeds occasionally contain illegal references (e.g. &#x8; for
        backspace); this strips them before parsing so lxml can proceed.
        """

        def replace(match: re.Match[bytes]) -> bytes:
            hex_group = match.group("hex")
            codepoint = int(hex_group[1:], 16) if hex_group else int(match.group("dec"))
            # XML 1.0 §2.2 Char: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
            valid = (
                codepoint in (0x09, 0x0A, 0x0D)
                or 0x20 <= codepoint <= 0xD7FF
                or 0xE000 <= codepoint <= 0xFFFD
                or 0x10000 <= codepoint <= 0x10FFFF
            )
            return match.group(0) if valid else b""

        return XMLParser.INVALID_XML_CHARACTER_REFERENCES.sub(replace, xml)

    @staticmethod
    def _process_all(
        xml: _ElementTree,
        xpath_expression: str,
        namespaces: dict[str, str],
        handler: Callable[[_Element, dict[str, str]], T | None],
    ) -> Generator[T, None, None]:
        """
        Process all elements matching the given XPath expression. Calling
        the given handler function on each element and yielding the result
        if it is not None.
        """
        for i in xml.xpath(xpath_expression, namespaces=namespaces):
            data = handler(i, namespaces)
            if data is not None:
                yield data


class XMLProcessor(XMLParser, Generic[T], ABC):
    """
    A class that simplifies making a class that processes XML documents.
    It loads the XML document, runs an XPath expression to find all matching
    elements, and calls the process_one function on each element.
    """

    def process_all(
        self,
        xml: str | bytes | _ElementTree,
    ) -> Generator[T, None, None]:
        """
        Process all elements matching the given XPath expression. Calling
        process_one on each element and yielding the result if it is not None.
        """
        root = self._load_xml(xml)
        return self._process_all(
            root, self.xpath_expression, self.NAMESPACES, self.process_one
        )

    def process_first(
        self,
        xml: str | bytes | _ElementTree,
    ) -> T | None:
        """
        Process the first element matching the given XPath expression. Calling
        process_one on the element and returning None if no elements match or
        if process_one returns None.
        """
        for i in self.process_all(xml):
            return i
        return None

    @property
    @abstractmethod
    def xpath_expression(self) -> str:
        """
        The xpath expression to use to find elements to process.
        """
        ...

    @abstractmethod
    def process_one(self, tag: _Element, namespaces: dict[str, str] | None) -> T | None:
        """
        Process one element and return the result. Return None if the element
        should be ignored.
        """
        ...
