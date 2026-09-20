"""Evidence vocabulary and serializable evidence dataclasses."""

from dataclasses import dataclass, field
from typing import List, Optional


class EvidenceKind:
    """String constants for the strength and origin of a graph fact."""

    OBSERVED = "OBSERVED"
    RESOLVED = "RESOLVED"
    CONTRACT_DERIVED = "CONTRACT_DERIVED"
    HEURISTIC = "HEURISTIC"
    UNKNOWN = "UNKNOWN"


class ResolutionStatus:
    """String constants for how completely a fact was resolved."""

    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


EVIDENCE_KINDS = frozenset(
    {
        EvidenceKind.OBSERVED,
        EvidenceKind.RESOLVED,
        EvidenceKind.CONTRACT_DERIVED,
        EvidenceKind.HEURISTIC,
        EvidenceKind.UNKNOWN,
    }
)

RESOLUTION_STATUSES = frozenset(
    {
        ResolutionStatus.EXACT,
        ResolutionStatus.PARTIAL,
        ResolutionStatus.UNRESOLVED,
        ResolutionStatus.NOT_ATTEMPTED,
    }
)


@dataclass(frozen=True)
class SourceRef:
    """One source location copied from an emitted edge's ``source`` field."""

    file: str
    line_start: int
    line_end: int
    statement_order: int
    original_text: str
    rule: str


@dataclass(frozen=True)
class Evidence:
    """The serializable evidence vocabulary for one graph fact."""

    kind: str
    extractor: str
    extractor_version: str = "1"
    source_refs: List[SourceRef] = field(default_factory=list)
    derivation_refs: List[str] = field(default_factory=list)
    resolution: str = ResolutionStatus.NOT_ATTEMPTED
    confidence: Optional[float] = None
    limitations: List[str] = field(default_factory=list)
