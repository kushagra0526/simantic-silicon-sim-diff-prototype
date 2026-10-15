"""The aerator controller from Simantic's docs, as a behaviour model, run on two platforms.

Firmware: Modbus dissolved-oxygen poll on TIM3, relay on GPIO4 with hysteresis ("AERATOR ON/OFF"
printed after the pin moves), heartbeat on GPIO13 from TIM2, I2C temperature on TIM4 with NACK/retry,
boot line with the reset flags.

Silicon: crystal drift, ISR jitter, UART transmit time, a USB-serial logger (host timestamps, ms of
latency, lines split into chunks) and a logic analyzer for the GPIOs (its own start offset).
Simulator: ideal timing, plus at most one planted simulator bug. Mutant firmware = negative controls.
"""
import random

BAUD = 115200


def run_firmware(fw, plat, mutant=None):
    """-> list of (t_device_seconds, stream, payload). Deterministic given fw/plat."""
    clk = plat.get("clock_scale", 1.0)
    ev = []
    rst = plat.get("reset_flags", "POR")
    ev.append((0.012 * clk, "uart", f"boot v{fw['version']} reset={rst}"))
    timers = {"hb": fw["hb_period"] * (2 if mutant == "hb_2x" else 1), "poll": fw["poll_period"], "temp": fw["temp_period"]}
    ticks = []
    for name, per in timers.items():
        p = per * clk * plat.get("timer_scale", {}).get(name, 1.0)
        n = 1
        while n * p <= fw["duration"]:
            ticks.append((n * p, name, n))
            n += 1
    prio = plat.get("irq_order", fw["irq_order"])   # who runs first when two timers fire together
    ticks.sort(key=lambda x: (round(x[0], 6), prio.index(x[1])))   # same microsecond = simultaneous
    hb, relay, i_poll, i_temp, dead = 0, 0, 0, 0, False
    last_iso = -1.0
    for t, name, n in ticks:
        if dead:
            break
        # back-to-back ISRs are serialised: the second one starts after the first finishes
        t = max(t, last_iso + 60e-6)
        last_iso = t
        if name == "hb":
            if plat.get("drop_every") and n % plat["drop_every"] == 0:
                continue
            hb ^= 1
            ev.append((t + 2e-6, "gpio13", hb))
        elif name == "poll":
            seq = fw["do_seq"]
            do = seq[min(i_poll, len(seq) - 1)]
            i_poll += 1
            shown = do - 0.006 if plat.get("fp_bug") and int(round(do * 100)) % 2 else do
            ev.append((t + 50e-6, "uart", f"DO={shown:.2f}"))
            on = do > fw["on_below"] if mutant == "inverted" else do < fw["on_below"]
            off = do < fw["off_above"] if mutant == "inverted" else do > fw["off_above"]
            if on and not relay:
                relay = 1
                ev.append((t + 300e-6 + plat.get("gpio_delay", 0.0), "gpio4", 1))
                ev.append((t + 900e-6, "uart", "AERATOR ON"))
            elif off and relay:
                relay = 0
                ev.append((t + 300e-6 + plat.get("gpio_delay", 0.0), "gpio4", 0))
                ev.append((t + 900e-6, "uart", "AERATOR OFF"))
            if mutant == "hardfault" and i_poll >= fw["hardfault_after"]:
                dead = True
        elif name == "temp":
            temps = fw["temp_seq"]
            tc = temps[min(i_temp, len(temps) - 1)]
            nack = (i_temp in fw["nack_reads"]) and plat.get("nack_modeled", True)
            i_temp += 1
            if mutant == "wrong_addr":
                ev.append((t + 50e-6, "uart", "I2C NACK"))
                ev.append((t + 1.2e-3, "uart", "T=ERR"))
                continue
            if nack:
                ev.append((t + 50e-6, "uart", "I2C NACK"))
                ev.append((t + 1.2e-3, "uart", f"T={tc:.1f}"))
            else:
                ev.append((t + 50e-6, "uart", f"T={tc:.1f}"))
    ev.sort(key=lambda e: e[0])
    return ev


def tx_time(line):
    return (len(line) + 2) * 10 / BAUD


def silicon_capture(events, rng, cap):
    """-> (logger_lines, la_csv_text). Host timestamps for UART, analyzer CSV for GPIO."""
    host0 = cap["host_start"]            # seconds since midnight when the board came out of reset
    log = []
    last_ht = 0.0
    tx_free = 0.0
    for t, s, p in events:
        if s != "uart":
            continue
        start = max(t + abs(rng.gauss(0, 2e-6)), tx_free)   # one UART: a line waits for the previous one
        t_end = start + tx_time(p)
        tx_free = t_end
        line = p + "\r\n"
        chunks = [line]
        if rng.random() < cap["split_rate"] and len(line) > 4:
            k = rng.randint(2, len(line) - 2)
            chunks = [line[:k], line[k:]]
        for j, c in enumerate(chunks):
            lat = rng.uniform(*cap["usb_latency"]) + (0.0 if j == len(chunks) - 1 else -tx_time(line[k:]) if len(chunks) > 1 else 0)
            ht = max(host0 + t_end + lat, last_ht + 1e-6)   # a serial port delivers bytes in order
            last_ht = ht
            hh, mm, ss = int(ht // 3600), int(ht % 3600 // 60), ht % 60
            log.append((ht, f"{hh:02d}:{mm:02d}:{ss:09.6f} RX {c!r}"))
    log.sort()
    rows = ["Time [s],Channel 4,Channel 13"]
    lv = {"gpio4": 0, "gpio13": 0}
    la0 = cap["la_offset"]
    for t, s, p in events:
        if s.startswith("gpio"):
            lv[s] = p
            tt = t + la0 + rng.gauss(0, 1.5e-6)
            rows.append(f"{tt:.9f},{lv['gpio4']},{lv['gpio13']}")
    return [x for _, x in log], "\n".join(rows) + "\n"


def sim_output(events):
    """Simantic `sim --ascii` style: UART records and scripted GPIO observers."""
    out, tx_free = [], 0.0
    for t, s, p in events:
        if s == "uart":
            end = max(t, tx_free) + tx_time(p)
            tx_free = end
            out.append((end, f"[{end:.6f}s] (usart2) {p}.."))
        else:
            name = {"gpio4": "relay", "gpio13": "heartbeat"}[s]
            out.append((t, f"[{t:.6f}s] ({name}) {'HIGH' if p else 'LOW'}"))
    out.sort()
    return [x for _, x in out]


# ---------- simulator bugs (what the sim gets wrong) ----------
def apply_bug(plat, bug, fw, rng):
    plat = dict(plat)
    kind = bug["kind"]
    if kind == "prescaler":
        psc = bug["psc"]
        plat["timer_scale"] = {bug["timer"]: (psc + 2) / (psc + 1)}
    elif kind == "pll":
        plat["clock_scale"] = bug["ratio"]
    elif kind == "reset_flags":
        plat["reset_flags"] = bug["flags"]
    elif kind == "irq_priority":
        plat["irq_order"] = list(reversed(fw["irq_order"]))
    elif kind == "nack_unmodeled":
        plat["nack_modeled"] = False
    elif kind == "gpio_slow":
        plat["gpio_delay"] = bug["delay"]
    elif kind == "dropped_irq":
        plat["drop_every"] = bug["every"]
    elif kind == "fp_rounding":
        plat["fp_bug"] = True
    return plat


EXPECT = {  # bug -> mismatch categories that name it (any counts), categories that may also show
    "prescaler": ({"RATE"}, {"TIMING", "ORDER", "MISSING", "EXTRA"}),
    "pll": ({"CLOCK"}, {"RATE", "TIMING", "ORDER", "VALUE"}),
    "reset_flags": ({"VALUE"}, set()),
    "irq_priority": ({"ORDER"}, set()),
    "nack_unmodeled": ({"MISSING"}, {"TIMING", "ORDER", "EXTRA"}),
    "gpio_slow": ({"CAUSALITY", "TIMING"}, set()),
    "dropped_irq": ({"MISSING"}, {"TIMING", "VALUE"}),
    "fp_rounding": ({"VALUE"}, {"MISSING", "EXTRA", "TIMING"}),  # different values also shift the relay decisions
}


def make_case(seed, bank):
    rng = random.Random(seed)
    fw = dict(rng.choice(bank["firmwares"]))
    n_do = int(fw["duration"] / fw["poll_period"]) + 2
    lo, hi = fw["do_range"]
    do, seq = rng.uniform(lo, hi), []
    for _ in range(n_do):
        do = min(hi, max(lo, do + rng.gauss(0, fw["do_step"])))
        seq.append(round(do, 2))
    fw["do_seq"] = seq
    fw["temp_seq"] = [round(21 + rng.gauss(0, 0.6), 1) for _ in range(int(fw["duration"] / fw["temp_period"]) + 2)]
    n_t = int(fw["duration"] / fw["temp_period"])
    fw["nack_reads"] = set(rng.sample(range(max(1, n_t)), min(n_t, rng.randint(*bank["nack_count"]))))
    fw["hardfault_after"] = rng.randint(20, max(21, n_do // 2))
    drift = rng.uniform(-bank["crystal_ppm"], bank["crystal_ppm"]) * 1e-6
    si_plat = {"clock_scale": 1 + drift, "reset_flags": "POR"}
    u = rng.random()
    mutant = rng.choice(bank["mutants"]) if u < 0.25 else None
    bug = None if rng.random() < 0.35 else dict(rng.choice(bank["bugs"]))
    sim_plat = {"clock_scale": 1.0, "reset_flags": "POR"}
    if bug:
        sim_plat = apply_bug(sim_plat, bug, fw, rng)
    si_ev = run_firmware(fw, si_plat, mutant)
    sim_ev = run_firmware(fw, sim_plat, mutant)
    # truth: does the bug change anything this firmware actually does? (noiseless sim with vs without)
    exercised = False
    if bug:
        ref = run_firmware(fw, {"clock_scale": 1.0, "reset_flags": "POR"}, mutant)
        exercised = [(round(t, 9), s, p) for t, s, p in ref] != [(round(t, 9), s, p) for t, s, p in sim_ev]
    cap = {"host_start": rng.uniform(30000, 60000), "la_offset": rng.uniform(-0.5, 2.0), "usb_latency": tuple(bank["usb_latency"]),
           "split_rate": bank["split_rate"]}
    logger, la = silicon_capture(si_ev, rng, cap)
    return {"seed": seed, "fw": fw, "mutant": mutant, "bug": bug, "exercised": exercised,
            "logger": logger, "la_csv": la, "sim": sim_output(sim_ev), "crystal_ppm": bank["crystal_ppm"]}
