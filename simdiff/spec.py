"""The firmware's own spec, checked on one trace (silicon or sim). Used for negative controls:
deliberately broken firmware must fail on silicon AND in the sim, at the same first failing check
("fails for the right reason"). Checks only use time differences inside one stream, so they work on
any capture clock."""
import re
import statistics


def check(events, fw):
    """events: (t, stream, payload) -> list of failures [(check, index, detail)] in check order."""
    fails = []
    uart = [e for e in events if e[1] == "uart"]
    hb = [e for e in events if e[1] == "gpio13"]
    if not uart or not uart[0][2].startswith("boot"):
        fails.append(("BOOT", 0, "first line is not the boot banner"))
    iv = [b[0] - a[0] for a, b in zip(hb, hb[1:])]
    if len(iv) >= 3:
        m = statistics.median(iv)
        if abs(m - fw["hb_period"]) > 0.02 * fw["hb_period"]:
            fails.append(("HEARTBEAT", 0, f"heartbeat half-period {m:.3f}s, spec {fw['hb_period']}s"))
    else:
        fails.append(("HEARTBEAT", 0, "no heartbeat"))
    relay, k = 0, 0
    for i, (t, s, p) in enumerate(uart):
        m = re.match(r"DO=([\d.]+)", p)
        if m:
            do = float(m.group(1))
            want = "AERATOR ON" if do < fw["on_below"] and not relay else "AERATOR OFF" if do > fw["off_above"] and relay else None
            nxt = uart[i + 1][2] if i + 1 < len(uart) else None
            got = nxt if nxt in ("AERATOR ON", "AERATOR OFF") else None
            if want != got:
                fails.append(("RELAY", k, f"after DO={do:.2f} expected {want or 'no change'}, got {got or 'no change'}"))
                break
            if got:
                relay = 1 if got == "AERATOR ON" else 0
            k += 1
    temps = [e for e in uart if e[2].startswith("T=")]
    if any(e[2] == "T=ERR" for e in temps):
        fails.append(("TEMP", next(i for i, e in enumerate(temps) if e[2] == "T=ERR"), "temperature read failed"))
    if uart:
        span = uart[-1][0] - uart[0][0]
        if span < fw["duration"] - 3 * max(fw["hb_period"], fw["poll_period"], fw["temp_period"]):
            fails.append(("LIVENESS", 0, f"output stops after {span:.1f}s of {fw['duration']}s"))
    return fails


def negative_control(si_events, sim_events, fw):
    a, b = check(si_events, fw), check(sim_events, fw)
    if not a:
        return "NOT_DETECTED_ON_SILICON", a, b
    if not b:
        return "SIM_PASSES_BROKEN_FIRMWARE", a, b
    same = a[0][0] == b[0][0] and abs(a[0][1] - b[0][1]) <= 1
    return ("SAME_FAILURE" if same else "DIFFERENT_FAILURE"), a, b
