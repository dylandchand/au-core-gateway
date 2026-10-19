"""Finding the HL7 source of a validator issue, for any resource type.

Every builder returns a trace: FHIR paths ("Observation.valueQuantity") -> the SourceRef they
came from ("OBX(2)-5"). The validator reports issues by expression, in several spellings:
  Patient.identifier[0].value              direct path
  Observation.value.ofType(Quantity)       choice element, typed
  Patient  + "Patient.birthDate: minimum required = 1 ..."   missing element, named in the message
  Observation + invariant au-core-obs-01   rule on the root that concerns one element
locate() normalises all of these and picks the longest trace path that prefixes the expression.
"""

from __future__ import annotations

import re

from gateway.model.canonical import SourceRef

Trace = dict[str, SourceRef]

# Invariants that fail on the resource root but concern one element (read from the profiles).
ROOT_INVARIANT_PATHS = {
    "au-core-pat-01": "Patient.identifier",
    "au-core-pat-02": "Patient.name",
    "au-core-pat-03": "Patient.name",
    "au-core-obs-01": "Observation.value",       # value or dataAbsentReason required
    "au-core-obs-02": "Observation.effective",   # effective at least to the day
}

MISSING = re.compile(r"^([A-Z][A-Za-z]+(?:\.[A-Za-z\[\]]+)+)\s*:")
OF_TYPE = re.compile(r"\.(\w+)\.ofType\((\w)(\w*)\)")


def _candidates(expression: str) -> list[str]:
    """The expression as given, with typed choices collapsed (value.ofType(Quantity) ->
    valueQuantity), and with the type dropped (-> value)."""
    collapsed = OF_TYPE.sub(lambda m: f".{m.group(1)}{m.group(2).upper()}{m.group(3)}", expression)
    dropped = OF_TYPE.sub(lambda m: f".{m.group(1)}", expression)
    return list(dict.fromkeys([expression, collapsed, dropped]))


def locate(trace: Trace, expression: str, message: str = "", message_id: str = "") -> SourceRef | None:
    root = expression.split(".", 1)[0].split("[", 1)[0]
    if expression == root:
        named = MISSING.match(message or "")
        invariant = message_id.rsplit("#", 1)[-1]
        if named and named.group(1).startswith(root + "."):
            expression = named.group(1)
        elif invariant in ROOT_INVARIANT_PATHS and ROOT_INVARIANT_PATHS[invariant].startswith(root + "."):
            expression = ROOT_INVARIANT_PATHS[invariant]
    best: tuple[str, SourceRef] | None = None
    for candidate in _candidates(expression):
        for path, ref in trace.items():
            if (candidate == path or candidate.startswith(path + ".") or candidate.startswith(path + "[")) \
                    and (best is None or len(path) > len(best[0])):
                best = (path, ref)
    return best[1] if best else None
