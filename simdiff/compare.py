"""Silicon vs simulation: align, remove what is expected to differ, name what is left.

Expected to differ (not a finding):
  * capture start offsets (logger host clock, analyzer start), one per capture source;
  * the board's crystal drift, within the stated tolerance (the sim clock is ideal);
  * USB-serial latency (ms) on logged lines, analyzer edge jitter (us);
  * how the logger chunked a line.
Findings:
  CLOCK      whole-device rate off beyond the crystal tolerance (PLL / clock tree)
  RATE       one timer-driven stream runs at a different rate than the rest (prescaler, reload)
  VALUE      same slot, different payload
  MISSING    silicon shows it, the sim never does      EXTRA   the reverse
  ORDER      the same events in a different order
  TIMING     matched events further apart than the capture can explain
  CAUSALITY  a declared cause -> effect pair inverted or too slow in the sim
"""
import difflib
import statistics

from .traces import kind_of, parse_la, parse_logger, parse_sim

TOL = {"logger": 0.015, "la": 0.0002}          # residual tolerance per capture source (s)
RATE_TOL = {"logger": 50e-6, "la": 10e-6}       # slope tolerance per stream kind
JITTER = {"logger": 0.008, "la": 5e-6}          # timestamp spread of each capture source (s)
CAUSAL = [("gpio4", 1, "AERATOR ON"), ("gpio4", 0, "AERATOR OFF")]
CAUSAL_MAX = 0.005


def theil_sen(xs, ys, max_pairs=4000):
    n = len(xs)
    if n < 2:
        return 0.0, 1.0
    step = max(1, n * n // (2 * max_pairs))
    slopes = []
    k = 0
    for i in range(n):
        for j in range(i + 1, n):
            k += 1
            if k % step:
                continue
            dx = xs[j] - xs[i]
            if abs(dx) > 1e-3:
                slopes.append((ys[j] - ys[i]) / dx)
    b = statistics.median(slopes) if slopes else 1.0
    a = statistics.median(y - b * x for x, y in zip(xs, ys))
    return a, b


def align_uart(sim_u, si_u):
    a = [p for _, _, p in sim_u]
    b = [p for _, _, p in si_u]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    match, missing, extra, value = [], [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            match += [(sim_u[i1 + k], si_u[j1 + k]) for k in range(i2 - i1)]
        elif op == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                s, g = sim_u[i1 + k], si_u[j1 + k]
                if kind_of("uart", s[2]) == kind_of("uart", g[2]):
                    value.append((s, g))
                    match.append((s, g))
                else:
                    extra.append(s)
                    missing.append(g)
        else:
            extra += sim_u[i1:i2]
            missing += si_u[j1:j2]
    return match, missing, extra, value


def align_gpio(sim_g, si_g, win=0.02):
    """Edges matched by time after finding the analyzer offset (vote over candidate offsets)."""
    if not sim_g or not si_g:
        return [], list(si_g), list(sim_g)
    cands = [g[0] - s[0] for s in sim_g[:6] for g in si_g[:6] if s[1] == g[1] and s[2] == g[2]]
    best, best_n = 0.0, -1
    for d in cands:
        n = sum(1 for s in sim_g if any(g[1] == s[1] and g[2] == s[2] and abs(g[0] - s[0] - d) < win for g in si_g))
        if n > best_n:
            best, best_n = d, n
    used, match, extra = set(), [], []
    for s in sim_g:
        # match on time only: after one lost toggle every later edge has the opposite level,
        # which is one finding, not eighty
        opts = [(abs(g[0] - s[0] - best), j) for j, g in enumerate(si_g)
                if j not in used and g[1] == s[1] and abs(g[0] - s[0] - best) < win]
        if opts:
            j = min(opts)[1]
            used.add(j)
            match.append((s, si_g[j]))
        else:
            extra.append(s)
    missing = [g for j, g in enumerate(si_g) if j not in used]
    return match, missing, extra


def compare(sim_lines, logger_lines, la_csv, crystal_ppm=50.0, meta=None):
    sim = parse_sim(sim_lines)
    si_u = parse_logger(logger_lines)
    si_g = parse_la(la_csv)
    sim_u = [e for e in sim if e[1] == "uart"]
    sim_g = [e for e in sim if e[1] != "uart"]
    findings = []

    def add(cat, msg, evidence=None):
        findings.append({"cat": cat, "msg": msg, "evidence": evidence or []})

    mu, miss_u, extra_u, value = align_uart(sim_u, si_u)
    # ORDER: an event the sim "lost" here and "added" a few slots away is a reordering
    order = []
    for g in list(miss_u):
        hit = next((s for s in extra_u if s[2] == g[2]), None)
        if hit is not None:
            order.append((hit, g))
            miss_u.remove(g)
            extra_u.remove(hit)
    mu = sorted(mu + order, key=lambda x: x[0][0])   # reordered events are still the same events for timing
    # GPIO edges are matched by time, so first get each pin's rate: from the logged lines' rate, or,
    # for a periodic pin, straight from its own edge intervals (a 1% timer error breaks time matching)
    pre = []
    for k in {kind_of(x[1], x[2]) for x, _ in mu}:
        xy = [(x[0], g[0]) for x, g in mu if kind_of(x[1], x[2]) == k]
        if len(xy) >= 6 and xy[-1][0] - xy[0][0] > 5:
            pre.append(theil_sen([x for x, _ in xy], [y for _, y in xy])[1])
    dev_u = statistics.median(pre) if pre else 1.0
    mg, miss_g, extra_g, flipped = [], [], [], {}
    for pin in sorted({e[1] for e in sim_g} | {e[1] for e in si_g}):
        a_ = [e for e in sim_g if e[1] == pin]
        b_ = [e for e in si_g if e[1] == pin]
        scale = dev_u
        ia = [y[0] - x[0] for x, y in zip(a_, a_[1:])]
        ib = [y[0] - x[0] for x, y in zip(b_, b_[1:])]
        if len(ia) >= 5 and len(ib) >= 5:
            ma, mb = statistics.median(ia), statistics.median(ib)
            if ma > 0 and sum(abs(x - ma) < 0.02 * ma for x in ia) >= 0.8 * len(ia) and 0.5 < mb / ma < 2:
                scale = mb / ma
        scaled = [(e[0] * scale, e[1], e[2], e) for e in a_]
        m, mi, ex = align_gpio([x[:3] for x in scaled], b_, win=0.03)
        back = {x[:3]: x[3] for x in scaled}
        flips = [(back[s_], g_) for s_, g_ in m if s_[2] != g_[2]]
        if flips:
            flipped[pin] = flips
        mg += [(back[s_], g_) for s_, g_ in m]
        miss_g += mi
        extra_g += [back[x] for x in ex]

    kind_slopes, periodic = {}, set()
    for src, pairs in (("logger", mu), ("la", mg)):
        by = {}
        for s, g in pairs:
            by.setdefault(kind_of(s[1], s[2]), []).append((s[0], g[0]))
        for k, xy in by.items():
            if len(xy) >= 6 and xy[-1][0] - xy[0][0] > 5:
                kind_slopes[k] = (src, theil_sen([x for x, _ in xy], [y for _, y in xy])[1], len(xy), xy[-1][0] - xy[0][0])
                iv = [b[0] - a[0] for a, b in zip(xy, xy[1:])]
                m = statistics.median(iv)
                # periodic: intervals are whole multiples of the median (a few events may be missing)
                if m > 0 and sum(round(x / m) >= 1 and abs(x / m - round(x / m)) < 0.01 * round(x / m) for x in iv) >= 0.8 * len(iv):
                    periodic.add(k)      # a timer-driven stream; event-driven lines don't vote on the clock
    if not kind_slopes:
        return {"verdict": "INCONCLUSIVE", "findings": [{"cat": "COVERAGE", "msg": "too few matched events to fit time", "evidence": []}],
                "fit": {}, "meta": meta or {}}
    def tol(k):
        src, b, n, span = kind_slopes[k]
        return max(RATE_TOL[src], 3 * JITTER[src] / (span * max(1.0, (n / 4) ** 0.5)))

    voters = [k for k in kind_slopes if k in periodic] or list(kind_slopes)
    la_k = [k for k in voters if kind_slopes[k][0] == "la"]
    lg_k = [k for k in voters if kind_slopes[k][0] != "la"]
    precise = False
    if la_k:
        cand = statistics.median(kind_slopes[k][1] for k in la_k)
        disagree = [k for k in lg_k if abs(kind_slopes[k][1] - cand) > tol(k)]
        if len(disagree) >= 2 and len(disagree) > len(lg_k) - len(disagree):
            dev = statistics.median(kind_slopes[k][1] for k in lg_k)   # the analyzer's own timer is the odd one
        else:
            dev, precise = cand, True                                  # the analyzer is far more precise
    else:
        dev = statistics.median(kind_slopes[k][1] for k in voters)
    drift_ppm = (dev - 1) * 1e6
    if abs(drift_ppm) > crystal_ppm + (5 if precise else 25):
        add("CLOCK", f"device clock runs {drift_ppm:+.0f} ppm vs silicon; crystal tolerance is ±{crystal_ppm:.0f} ppm")
    rate_bad = set()
    for k, (src, b, n, span) in sorted(kind_slopes.items()):
        # a short or sparse stream can't resolve small rate differences: tolerance grows with jitter
        if k in periodic and abs(b - dev) > tol(k):
            rate_bad.add(k)
            add("RATE", f"{k} runs {(b - dev) * 1e6:+.0f} ppm vs the rest of the device ({n} events)")
    # residuals: one line per capture source fitted on the streams that keep the device rate
    for src, pairs in (("logger", mu), ("la", mg)):
        good = [(s, g) for s, g in pairs if kind_of(s[1], s[2]) not in rate_bad]
        if len(good) < 3:
            continue
        a, b = theil_sen([s[0] for s, _ in good], [g[0] for _, g in good])
        if not rate_bad and abs(b - dev) > RATE_TOL[src] * 3:
            b = dev
        # identical repeated lines ("DO=3.00" x 20) can pair with a neighbour; re-pair to the nearest in time
        pool = si_u if src == "logger" else si_g
        used, fixed = set(), []
        for s_, g_ in sorted(good, key=lambda x: x[0][0]):     # rebuild pairs in time order
            pred = a + b * s_[0]
            if abs(g_[0] - pred) > TOL[src] or id(g_) in used:
                alt = [g2 for g2 in pool if g2[1] == s_[1] and g2[2] == s_[2] and id(g2) not in used and abs(g2[0] - pred) <= TOL[src]]
                if alt:
                    g_ = min(alt, key=lambda g2: abs(g2[0] - pred))
            used.add(id(g_))
            fixed.append((s_, g_))
        good = fixed
        bad = [(s, g, g[0] - (a + b * s[0])) for s, g in good if abs(g[0] - (a + b * s[0])) > TOL[src]]
        if bad:
            worst = max(bad, key=lambda x: abs(x[2]))
            add("TIMING", f"{len(bad)} {src} events off by more than {TOL[src] * 1e3:g} ms (worst {worst[2] * 1e3:+.2f} ms: {worst[0][2]})",
                [f"sim {worst[0][0]:.6f}s {worst[0][1]} {worst[0][2]}"])
    for s, g in value:
        add("VALUE", f"sim printed '{s[2]}' where silicon printed '{g[2]}'", [f"sim {s[0]:.6f}s", f"silicon line '{g[2]}'"])
    for s, g in order:
        add("ORDER", f"'{g[2]}' comes in a different order in the sim", [f"sim {s[0]:.6f}s"])
    # captures start and stop at their own times: unmatched events at the very edges are not findings
    allm = mu + mg
    if allm:
        lo = min(s[0] for s, _ in allm)
        hi = max(s[0] for s, _ in allm)
        edge = 0.05 + (2e-4 + abs(dev - 1)) * hi   # a wrong clock also moves where the run ends

        def inside_sim(e):
            return lo - edge <= e[0] <= hi + edge and not (e[0] > hi - edge and e[0] > max(x[0] for x in sim) - edge)
        extra_u = [e for e in extra_u if inside_sim(e)]
        extra_g = [e for e in extra_g if inside_sim(e)]
        si_end = {"uart": max((g[0] for g in si_u), default=0), "gpio": max((g[0] for g in si_g), default=0)}
        miss_u = [g for g in miss_u if g[0] < si_end["uart"] - edge * 2]
        miss_g = [g for g in miss_g if g[0] < si_end["gpio"] - edge]
    for g in miss_u + miss_g:
        note = ""
        if g[1] in flipped:
            note = f"; {len(flipped.pop(g[1]))} later {g[1]} edges have the opposite level (one toggle lost)"
        add("MISSING", f"silicon shows {g[1]} '{g[2]}', the sim never does{note}", [f"silicon {g[0]:.6f} (capture clock)"])
    for pin, fl in flipped.items():
        add("VALUE", f"{len(fl)} {pin} edges at the right time with the opposite level", [f"sim {fl[0][0][0]:.6f}s"])
    for s in extra_u + extra_g:
        add("EXTRA", f"sim shows {s[1]} '{s[2]}' at {s[0]:.6f}s, silicon doesn't", [])
    # causality inside the sim trace (one clock, exact)
    for pin, level, line in CAUSAL:
        edges = [e for e in sim_g if e[1] == pin and e[2] == level]
        lines = [e for e in sim_u if e[2] == line]
        for ln in lines:
            prior = [e for e in edges if e[0] <= ln[0]]
            if not prior or ln[0] - prior[-1][0] > CAUSAL_MAX:
                after = [e for e in edges if e[0] > ln[0]]
                if after and after[0][0] - ln[0] < 1.0:
                    add("CAUSALITY", f"sim prints '{line}' {(after[0][0] - ln[0]) * 1e3:.2f} ms before {pin} goes {level}",
                        [f"sim {ln[0]:.6f}s"])
    # collapse repeats per category for the report
    cats = {}
    for f in findings:
        cats.setdefault(f["cat"], []).append(f)
    seen_kinds_si = {kind_of(s, p) for _, s, p in si_u + si_g}
    seen_kinds_sim = {kind_of(s, p) for _, s, p in sim}
    coverage = {"in_silicon_only": sorted(seen_kinds_si - seen_kinds_sim), "in_sim_only": sorted(seen_kinds_sim - seen_kinds_si),
                "never_exercised": sorted({"uart:I2C NACK", "gpio4"} - seen_kinds_si - seen_kinds_sim)}
    return {"verdict": "MISMATCH" if findings else "MATCH", "findings": findings, "categories": {k: len(v) for k, v in cats.items()},
            "fit": {"device_drift_ppm": round(drift_ppm, 1), "streams": {k: {"source": s, "ppm_vs_sim": round((b - 1) * 1e6, 1), "n": n}
                                                                        for k, (s, b, n, _) in kind_slopes.items()}},
            "coverage": coverage, "meta": meta or {}}
