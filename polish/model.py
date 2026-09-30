from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Diagnostic:
    code: str
    message: str
    line: int = 1
    column: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Field:
    type: str
    primary_key: bool = False


@dataclass
class Node:
    name: str
    kind: str
    parent: str | None
    line: int
    properties: dict[str, Any] = field(default_factory=dict)
    requirements: list[str] = field(default_factory=list)
    fields: dict[str, Field] = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    kind: str
    target: str
    line: int
    pattern: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    request: dict[str, Any]
    expected: dict[str, Any]
    assertions: list[tuple[str, str]]
    line: int


@dataclass
class Architecture:
    name: str
    errors: dict[str, Any] = field(default_factory=dict)
    aws: dict[str, Any] = field(default_factory=dict)
    cloud: dict[str, Any] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)

    def outgoing(self, source: str, kind: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.source == source and (kind is None or e.kind == kind)]

    def resolve(self, reference: str, scope: str | None = None) -> str | None:
        while scope:
            candidate = f"{scope}.{reference}"
            if candidate in self.nodes:
                return candidate
            scope = self.nodes[scope].parent
        return reference if reference in self.nodes else None


@dataclass
class Compilation:
    architecture: Architecture | None
    diagnostics: list[Diagnostic]

    @property
    def ok(self) -> bool:
        return not self.diagnostics
