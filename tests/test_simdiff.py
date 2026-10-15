import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simdiff.bank_dev import BANK  # noqa: E402
from simdiff.compare import compare, theil_sen  # noqa: E402
from simdiff.spec import check  # noqa: E402
from simdiff.traces import parse_la, parse_logger, parse_sim  # noqa: E402
from simdiff.world import EXPECT, make_case  # noqa: E402


def test_parsers_read_the_three_formats():
    assert parse_sim(["[0.500831s] (usart2) DO=7.45..", "[1.000062s] (heartbeat) HIGH", "garbage"]) == \
        [(0.500831, "uart", "DO=7.45"), (1.000062, "gpio13", 1)]
    lines = parse_logger(["12:00:00.500000 RX 'DO=7'", "12:00:00.501000 RX '.45\\r\\n'"])
    assert lines == [(43200.501, "uart", "DO=7.45")]                      # chunks merged, time of completion
    assert parse_la("Time [s],Channel 4,Channel 13\n1.0,0,1\n2.0,1,1\n") == [(1.0, "gpio13", 1), (2.0, "gpio4", 1)]


def test_theil_sen_shrugs_off_outliers():
    xs = list(range(50))
    ys = [3 + 1.0001 * x for x in xs]
    ys[10] += 5
    a, b = theil_sen(xs, ys)
    assert abs(b - 1.0001) < 1e-6 and abs(a - 3) < 1e-3


def test_faithful_sim_matches_despite_drift_jitter_and_split_lines():
    n = 0
    for seed in range(1, 120):
        c = make_case(seed, BANK)
        if c["bug"] is None or not c["exercised"]:
            assert compare(c["sim"], c["logger"], c["la_csv"], c["crystal_ppm"])["verdict"] == "MATCH", seed
            n += 1
    assert n > 30


def test_each_planted_bug_is_named():
    seen = set()
    for seed in range(1, 160):
        c = make_case(seed, BANK)
        if c["bug"] and c["exercised"] and c["mutant"] is None:
            cats = set(compare(c["sim"], c["logger"], c["la_csv"], c["crystal_ppm"])["categories"])
            assert cats & EXPECT[c["bug"]["kind"]][0], (seed, c["bug"], cats)
            seen.add(c["bug"]["kind"])
    assert len(seen) >= 7


def test_negative_control_fails_on_both_for_the_same_reason():
    from demo import build
    from simdiff.spec import negative_control
    fw, logger, la, sim = build("inverted")
    v, a, b = negative_control(sorted(parse_logger(logger) + parse_la(la)), sorted(parse_sim(sim)), fw)
    assert v == "SAME_FAILURE" and a[0][0] == "RELAY"


def test_spec_passes_good_firmware_on_both_sides():
    from demo import build
    fw, logger, la, sim = build()
    assert check(sorted(parse_logger(logger) + parse_la(la)), fw) == []
    assert check(sorted(parse_sim(sim)), fw) == []


def test_demo_names_both_planted_bugs():
    from demo import build
    fw, logger, la, sim = build()
    r = compare(sim, logger, la, crystal_ppm=50)
    cats = set(r["categories"])
    assert {"RATE", "CAUSALITY"} <= cats and "CLOCK" not in cats
    assert any("uart:DO" in f["msg"] for f in r["findings"] if f["cat"] == "RATE")
    assert abs(r["fit"]["device_drift_ppm"] - 37) < 3
