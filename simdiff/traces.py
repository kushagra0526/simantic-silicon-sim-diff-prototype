"""Parse the three trace formats into events (t, stream, payload).

sim:     `[0.001234s] (usart2) DO=7.45..`        Simantic `sim --ascii` records; scripted observers
         `[0.532688s] (relay) HIGH`             map to pins through LABELS
logger:  `10:02:03.123456 RX 'DO=7.45\\r\\n'`     USB-serial logger, host clock, chunks merged into lines
la csv:  `Time [s],Channel 4,Channel 13`        logic-analyzer export, one row per edge
"""
import ast
import re

LABELS = {"relay": "gpio4", "heartbeat": "gpio13"}
SIM_RX = re.compile(r"^\[(\d+\.\d+)s\] \(([\w.-]+)\) (.*)$")
LOG_RX = re.compile(r"^(\d\d):(\d\d):(\d\d\.\d+) RX (.+)$")


def parse_sim(lines, uart="usart2"):
    out = []
    for ln in lines:
        m = SIM_RX.match(ln.strip())
        if not m:
            continue
        t, label, payload = float(m.group(1)), m.group(2), m.group(3)
        if label == uart:
            out.append((t, "uart", payload.rstrip(".").rstrip()))
        elif label in LABELS:
            out.append((t, LABELS[label], 1 if payload.strip().upper() in ("HIGH", "1", "ON") else 0))
    return out


def parse_logger(lines):
    """Merge chunks into lines; a line's time is the host time of the chunk that completed it."""
    out, buf = [], ""
    for ln in lines:
        m = LOG_RX.match(ln.strip())
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        buf += ast.literal_eval(m.group(4))
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            out.append((t, "uart", line.rstrip("\r")))
    return out


def parse_la(text, channels=None):
    channels = channels or {"Channel 4": "gpio4", "Channel 13": "gpio13"}
    rows = [r for r in text.strip().splitlines() if r.strip()]
    head = [h.strip() for h in rows[0].split(",")]
    idx = {channels[h]: i for i, h in enumerate(head) if h in channels}
    out, prev = [], {s: 0 for s in idx}
    for r in rows[1:]:
        cells = r.split(",")
        t = float(cells[0])
        for s, i in idx.items():
            v = int(cells[i])
            if v != prev[s]:
                out.append((t, s, v))
                prev[s] = v
    return out


def kind_of(stream, payload):
    """Timing stream key: UART lines by message type ('DO', 'T', 'AERATOR', 'boot'), GPIOs by pin."""
    if stream != "uart":
        return stream
    m = re.match(r"([A-Za-z0-9 ]+?)(=|$| v)", payload)
    k = m.group(1).strip() if m else payload[:6]
    return "uart:" + ("AERATOR" if k.startswith("AERATOR") else k)
