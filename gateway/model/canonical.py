"""The canonical model: the one internal shape every ingest adapter produces.

Ingest adapters know file formats. The FHIR builder knows FHIR. Neither knows about the
other; they meet here. Every value carries a SourceRef so any validation issue on the
FHIR side can be traced back to the exact file, line and field it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRef:
    """Where a value came from, e.g. file='lab-a.hl7', line=2, locator='PID-3[2]'."""
    file: str
    line: int
    locator: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} {self.locator}"


@dataclass
class Traced:
    """A raw value plus where it came from. value may be '' when the source field is empty;
    the SourceRef is kept anyway so a 'missing' error still points at the right field."""
    value: str
    source: SourceRef


@dataclass
class Identifier:
    kind: str                      # "ihi" | "medicare" | "mrn" | "other"
    value: Traced
    type_code: str                 # HL7 v2 table 0203 code as received, e.g. NI, MC, MR
    authority: str = ""            # assigning authority as received, e.g. AUSHIC


@dataclass
class Name:
    family: Traced
    given: list[Traced]
    use: str = ""                  # FHIR name use, already translated by the ingest rules


@dataclass
class Address:
    lines: list[Traced]
    city: Traced
    state: Traced
    postcode: Traced
    country: Traced
    use: str = ""


@dataclass
class PatientRecord:
    """One patient, as received from one source message."""
    record_id: str                 # stable id: source file + message control id
    source_file: str
    message_control_id: str
    identifiers: list[Identifier] = field(default_factory=list)
    identifiers_source: SourceRef | None = None
    names: list[Name] = field(default_factory=list)
    names_source: SourceRef | None = None
    birth_date: Traced | None = None   # ISO 8601, possibly partial: "1980", "1980-02", "1980-02-15"
    sex: Traced | None = None
    addresses: list[Address] = field(default_factory=list)
    addresses_source: SourceRef | None = None


@dataclass
class IngestIssue:
    """A problem found before any FHIR exists: unparseable input, impossible dates, etc."""
    severity: str                  # "error" | "warning"
    code: str                      # stable machine code, e.g. "ingest.bad-date"
    message: str
    source: SourceRef
