"""HL7 v2.4 acknowledgements (ACK) for messages the gateway receives over an interface.

Every value here is from HL7's own tables (HL7 Terminology, CC0), checked against the local
package hl7.terminology.r4#7.0.1:
  MSA-1 acknowledgment code, table 0008: AA accept, AE error, AR reject
  MSH-9 message type, table 0076: ACK (general acknowledgment); structure, table 0354: ACK
The ACK echoes the original's trigger event (A04, R01), processing id and version, swaps the
sending and receiving application and facility, and quotes the original control id in MSA-2.

What decides the code (the gateway's own checks; the official validator is a separate,
slower step and never runs per message on an interface):
  AR  the message could not be read, or its type is not one this gateway handles
  AE  it was read, but the gateway found an error in its content (format, translation rules,
      patient matching)
  AA  it was read and translated with no errors (warnings are mentioned in MSA-3)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gateway.ingest.hl7v2 import Encoding, Message

ACCEPT, ERROR, REJECT = "AA", "AE", "AR"
CODE_NAMES = {ACCEPT: "Accepted", ERROR: "Error", REJECT: "Rejected"}
# This gateway's own application and facility names, as the built-in examples address it.
GATEWAY_APP, GATEWAY_FACILITY = "GATEWAY", "AUCORE"
MSA3_MAX = 80        # MSA-3 Text Message is an 80-character ST in HL7 v2.4


@dataclass
class Ack:
    code: str            # AA / AE / AR
    text: str            # MSA-3, plain words
    message: str         # the full ACK, segments joined with \r


def _ts(when: datetime) -> str:
    return when.strftime("%Y%m%d%H%M%S") + (when.strftime("%z") or "")


def build(code: str, text: str, ack_id: str, when: datetime, original: Message | None) -> Ack:
    """original is None when the message could not be parsed at all."""
    enc = original.msh.enc if original is not None else Encoding()
    if original is not None:
        msh = original.msh
        send_app, send_fac = msh.field(5) or GATEWAY_APP, msh.field(6) or GATEWAY_FACILITY
        recv_app, recv_fac = msh.field(3), msh.field(4)
        trigger = msh.value(9, 1, 2)
        processing, version, control = msh.field(11), msh.field(12), original.control_id
    else:
        send_app, send_fac, recv_app, recv_fac = GATEWAY_APP, GATEWAY_FACILITY, "", ""
        trigger, processing, version, control = "", "", "2.4", ""
    c = enc.component
    msg_type = f"ACK{c}{trigger}{c}ACK" if trigger else "ACK"
    encoding_chars = enc.component + enc.repetition + enc.escape_char + enc.subcomponent
    words = text[:MSA3_MAX]
    f = enc.field
    msh_seg = f.join(["MSH", encoding_chars, send_app, send_fac, recv_app, recv_fac, _ts(when), "",
                      msg_type, ack_id, processing, version])
    msa_seg = f.join(["MSA", code, enc.escape(control), enc.escape(words)])
    return Ack(code=code, text=words, message="\r".join([msh_seg, msa_seg]))
