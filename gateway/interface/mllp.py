"""A local MLLP listener and sender, for trying the gateway as a live interface.

MLLP (the Minimal Lower Layer Protocol) is how HL7 v2 travels over TCP: each message is
wrapped as <VT> message <FS><CR> (0x0B ... 0x1C 0x0D), and the receiver answers each one with
an ACK wrapped the same way. The listener binds to this computer only (127.0.0.1) unless told
otherwise, and the sender only ever connects to the address it is given. Nothing here reaches
the internet. Port 2575 is IANA's registered port for HL7 (service name "hl7").

    python -m gateway listen                   # waits on 127.0.0.1:2575
    python -m gateway send fixtures/golden     # in another terminal: sends, prints each ACK
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

from gateway.interface.session import Session

START, END, CR = b"\x0b", b"\x1c", b"\x0d"
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 2575
MAX_FRAME = 1_000_000          # bytes; a larger frame is rejected rather than buffered forever


def frame(text: str) -> bytes:
    return START + text.encode("utf-8") + END + CR


def split_messages(text: str) -> list[str]:
    """A file's messages, one per MSH, segments joined with \\r as they travel."""
    out, cur = [], []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith("MSH") and cur:
            out.append("\r".join(cur))
            cur = []
        if line.strip():
            cur.append(line.rstrip())
    if cur:
        out.append("\r".join(cur))
    return out


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, session: Session, log) -> None:
    peer = writer.get_extra_info("peername")
    buf = b""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_FRAME:
                log(f"{peer}: frame larger than {MAX_FRAME} bytes; connection closed")
                break
            while END + CR in buf:
                raw, buf = buf.split(END + CR, 1)
                raw = raw[raw.find(START) + 1:] if START in raw else raw
                receipt = session.receive_bytes(raw)
                writer.write(frame(receipt["ack"]))
                await writer.drain()
                log(f"#{receipt['n']:<5} {receipt['type'] or '?':<8} {receipt['control_id'] or '-':<14} "
                    f"{receipt['code']}  {receipt['text']}")
                for r in receipt.get("released", []):
                    log(f"       released #{r['n']} {r['control_id'] or '-'} (held since {r['held_since']}): "
                        f"{r['code']}  {r['text']}")
    finally:
        writer.close()


async def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, session: Session | None = None,
                log=print, ready: asyncio.Event | None = None) -> None:
    session = session or Session()
    server = await asyncio.start_server(lambda r, w: _handle(r, w, session, log), host, port)
    log(f"Listening for HL7 v2 over MLLP on {host}:{port} (synthetic data only; Ctrl+C to stop)")
    if ready:
        ready.set()
    async with server:
        await server.serve_forever()


def send(paths: list[Path], host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 30) -> list[str]:
    """Send every message in the given .hl7 files (or folders), one at a time; return each ACK."""
    files = sorted(p for i in paths for p in ([i] if i.is_file() else i.rglob("*.hl7")))
    acks = []
    with socket.create_connection((host, port), timeout=timeout) as sock:
        for f in files:
            for text in split_messages(f.read_text(encoding="utf-8")):
                sock.sendall(frame(text))
                buf = b""
                while END + CR not in buf:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("listener closed the connection before acknowledging")
                    buf += chunk
                acks.append(buf.split(END + CR, 1)[0].lstrip(START).decode("utf-8"))
    return acks
