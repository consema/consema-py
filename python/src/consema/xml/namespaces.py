"""Namespace-aware expanded names and immutable binding scope (RFC 0012 §5).

Authority:

- RFC 0012 §5 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:193-225):
  prefix spelling is source representation; expanded-name equality compares
  the namespace URI and the local name, never the prefix; the default
  namespace applies to element names, not unprefixed attributes; prefixed
  names require an in-scope binding; ``xml`` is permanently bound to its
  standard URI; ``xmlns`` is reserved and cannot be rebound; namespace-name
  comparison is exact string comparison with no URI fetch or normalization;
  namespace scope is immutable ancestry-derived data.
- The resolution rules transcribe crates/consema-xml/src/namespace.rs:9-219
  (XML_NAMESPACE_URI:10, XMLNS_NAMESPACE_URI:12, QName:16-39,
  ExpandedName:41-57, Binding:59-66, NamespaceError:68-89,
  NamespaceScope:91-218) — byte/registry arbitration only; this module is a
  Python-idiomatic reimplementation.
- The four namespace error codes are frozen by the parser
  (crates/consema-xml/src/parser.rs:130-137): unbound-prefix@1,
  reserved-prefix@1, xml-rebinding@1, default-xmlns@1.

go/xml is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# Standard URI permanently bound to the `xml` prefix (namespace.rs:10).
XML_NAMESPACE_URI = "http://www.w3.org/XML/1998/namespace"
# URI of the reserved `xmlns` prefix (namespace.rs:12).
XMLNS_NAMESPACE_URI = "http://www.w3.org/2000/xmlns/"


@dataclass(frozen=True, slots=True)
class QName:
    """One lexical QName with its source-derived parts (namespace.rs:16-39)."""

    prefix: str | None
    local: str

    @classmethod
    def new(cls, prefix: str | None, local: str) -> QName:
        return cls(prefix=prefix, local=local)

    @property
    def as_str(self) -> str:
        """Full lexical spelling ``prefix:local`` or ``local``
        (namespace.rs:31-38)."""
        if self.prefix is not None:
            return f"{self.prefix}:{self.local}"
        return self.local


@dataclass(frozen=True, slots=True)
class ExpandedName:
    """Resolved expanded name = ``{ namespace URI or none, local name }``
    (namespace.rs:41-57)."""

    namespace: str | None
    local: str

    @classmethod
    def new(cls, namespace: str | None, local: str) -> ExpandedName:
        return cls(namespace=namespace, local=local)


@dataclass(frozen=True, slots=True)
class Binding:
    """One in-scope namespace binding (namespace.rs:59-66); a ``None``
    prefix is the default namespace."""

    prefix: str | None
    uri: str


class NamespaceErrorKind(enum.Enum):
    """Namespace resolution failure category (namespace.rs:68-89,
    transcribed verbatim)."""

    UNBOUND_PREFIX = "UnboundPrefix"
    RESERVED_PREFIX = "ReservedPrefix"
    ILLEGAL_XML_REBINDING = "IllegalXmlRebinding"
    ILLEGAL_DEFAULT_XMLNS = "IllegalDefaultXmlns"


class NamespaceError(Exception):
    """One namespace resolution failure carrying the failing prefix/URI
    spellings (namespace.rs:68-89)."""

    def __init__(self, kind: NamespaceErrorKind, *, prefix: str | None = None, uri: str | None = None):
        super().__init__(kind.value)
        self.kind = kind
        self.prefix = prefix
        self.uri = uri

    @property
    def code(self) -> str:
        """The frozen diagnostic code of one namespace failure
        (parser.rs:130-137)."""
        return {
            NamespaceErrorKind.UNBOUND_PREFIX: "xml.namespace.unbound-prefix@1",
            NamespaceErrorKind.RESERVED_PREFIX: "xml.namespace.reserved-prefix@1",
            NamespaceErrorKind.ILLEGAL_XML_REBINDING: "xml.namespace.xml-rebinding@1",
            NamespaceErrorKind.ILLEGAL_DEFAULT_XMLNS: "xml.namespace.default-xmlns@1",
        }[self.kind]


class NamespaceScope:
    """Immutable, ancestry-derived namespace scope (namespace.rs:91-218).

    A scope is never mutated in place. Declaring a binding appends to a new
    child scope, so the immutable ancestry chain of a tree is preserved.
    """

    __slots__ = ("_bindings",)

    def __init__(self, bindings: tuple[Binding, ...] = ()) -> None:
        # Most-recent binding first; a None prefix is the default namespace.
        self._bindings = bindings

    @classmethod
    def new(cls) -> NamespaceScope:
        """Creates an empty scope holding only the permanent ``xml`` rule."""
        return cls()

    @property
    def bindings(self) -> tuple[Binding, ...]:
        """All in-scope bindings in declaration order (namespace.rs:110-115)."""
        return self._bindings

    def declare(self, prefix: str | None, uri: str) -> NamespaceScope:
        """Appends one namespace declaration and returns the child scope
        (namespace.rs:122-144). Raises NamespaceError for the reserved
        ``xmlns`` prefix, the ``xml`` rebinding rule, and the ``xmlns`` URI
        as default namespace."""
        if uri == XMLNS_NAMESPACE_URI and prefix is None:
            raise NamespaceError(NamespaceErrorKind.ILLEGAL_DEFAULT_XMLNS)
        if prefix is not None:
            if prefix == "xmlns":
                raise NamespaceError(NamespaceErrorKind.RESERVED_PREFIX, prefix=prefix)
            if prefix == "xml" and uri != XML_NAMESPACE_URI:
                raise NamespaceError(
                    NamespaceErrorKind.ILLEGAL_XML_REBINDING, prefix=prefix, uri=uri
                )
        return NamespaceScope(bindings=self._bindings + (Binding(prefix=prefix, uri=uri),))

    def resolve_element(self, qname: QName) -> ExpandedName:
        """Resolves an element name: the default namespace applies
        (namespace.rs:147-155)."""
        if qname.prefix is None:
            return ExpandedName(namespace=self._lookup_default(), local=qname.local)
        return self._resolve_prefixed(qname, qname.prefix)

    def resolve_attribute(self, qname: QName) -> ExpandedName:
        """Resolves an attribute name: the default namespace never applies
        (namespace.rs:158-166)."""
        if qname.prefix is None:
            return ExpandedName(namespace=None, local=qname.local)
        return self._resolve_prefixed(qname, qname.prefix)

    @staticmethod
    def declaration_expanded_name(prefix: str | None) -> ExpandedName:
        """Expanded name of a namespace declaration attribute itself:
        ``xmlns`` is ``{ xmlns-URI, "xmlns" }`` and ``xmlns:p`` is
        ``{ xmlns-URI, "p" }``, used for attribute-uniqueness checks
        (namespace.rs:172-179)."""
        return ExpandedName(namespace=XMLNS_NAMESPACE_URI, local=prefix or "xmlns")

    def _lookup_default(self) -> str | None:
        for binding in reversed(self._bindings):
            if binding.prefix is None:
                return binding.uri
        return None

    def _resolve_prefixed(self, qname: QName, prefix: str) -> ExpandedName:
        if prefix == "xml":
            return ExpandedName(namespace=XML_NAMESPACE_URI, local=qname.local)
        if prefix == "xmlns":
            raise NamespaceError(NamespaceErrorKind.RESERVED_PREFIX, prefix=prefix)
        for binding in reversed(self._bindings):
            if binding.prefix == prefix:
                return ExpandedName(namespace=binding.uri, local=qname.local)
        raise NamespaceError(NamespaceErrorKind.UNBOUND_PREFIX, prefix=prefix)
