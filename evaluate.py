"""Benchmark: same firmware on (synthetic) silicon and in a (synthetic) sim with planted simulator bugs.

    python evaluate.py dev        # seeds 1-300, dev bank
    python evaluate.py heldout    # seeds 7001-7300, held-out bank (frozen in git first)

Truth: a planted sim bug counts only if it changes what this firmware does in a noiseless sim
(generator diff, not the checker). Compared:
  exact     text diff of the two transcripts
  payload   per-stream payload sequences, timestamps stripped (UART lines, GPIO levels)
  timing    payload + times, aligned on the first event per capture, no drift fit
  simdiff   this repo
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from simdiff.compare import compare  # noqa: E402
from simdiff.spec import negative_control  # noqa: E402
from simdiff.traces import parse_la, parse_logger, parse_sim  # noqa: E402
from simdiff.world import EXPECT, make_case  # noqa: E402


def baselines(c):
    sim = parse_sim(c["sim"])
    si = parse_logger(c["logger"]) + parse_la(c["la_csv"])
    out = {"exact": c["sim"] != c["logger"]}
    pay = False
    for st in ("uart", "gpio4", "gpio13"):
        if [p for _, s, p in sim if s == st] != [p for _, s, p in si if s == st]:
            pay = True
    out["payload"] = pay
    tim = pay
    if not pay:
        for st, tol in (("uart", 0.015), ("gpio4", 2e-4), ("gpio13", 2e-4)):
            a = [t for t, s, _ in sim if s == st]
            b = [t for t, s, _ in si if s == st]
            if a and b and any(abs((y - b[0]) - (x - a[0])) > tol for x, y in zip(a, b)):
                tim = True
    out["timing"] = tim
    return out


def run(split):
    if split == "heldout":
        from eval.bank_heldout import BANK
        seeds = range(7001, 7301)
    else:
        from simdiff.bank_dev import BANK
        seeds = range(1, 301)
    names = ("exact", "payload", "timing", "simdiff")
    caught = {n: defaultdict(lambda: [0, 0]) for n in names}
    clean_flag = defaultdict(int)
    n_clean = 0
    wrong_cat, misses, false_alarms = 0, [], []
    neg = defaultdict(int)
    neg_buggy = defaultdict(int)
    drift_err = []
    for seed in seeds:
        c = make_case(seed, BANK)
        r = compare(c["sim"], c["logger"], c["la_csv"], c["crystal_ppm"])
        b = baselines(c)
        flagged = {"simdiff": r["verdict"] == "MISMATCH", **b}
        cats = set(r.get("categories", {}))
        bug = c["bug"]["kind"] if c["bug"] else None
        if bug and c["exercised"]:
            exp, may = EXPECT[bug]
            for n in names:
                hit = bool(cats & exp) if n == "simdiff" else flagged[n]
                caught[n][bug][0] += hit
                caught[n][bug][1] += 1
            if not cats & exp:
                misses.append({"seed": seed, "bug": c["bug"], "mutant": c["mutant"], "got": sorted(cats)})
            if cats - exp - may:
                wrong_cat += 1
        else:
            n_clean += 1
            for n in names:
                clean_flag[n] += flagged[n]
            if flagged["simdiff"]:
                false_alarms.append({"seed": seed, "bug": c["bug"], "mutant": c["mutant"], "findings": [f["msg"] for f in r["findings"][:3]]})
        if c["mutant"]:
            si = parse_logger(c["logger"]) + parse_la(c["la_csv"])
            sm = parse_sim(c["sim"])
            v, _, _ = negative_control(sorted(si), sorted(sm), c["fw"])
            (neg_buggy if bug and c["exercised"] else neg)[v] += 1
    def tot(n):
        return [sum(v[0] for v in caught[n].values()), sum(v[1] for v in caught[n].values())]
    return {"split": split, "cases": len(seeds), "clean": n_clean,
            "by_bug": {k: {n: caught[n][k] for n in names} for k in sorted(caught["simdiff"])},
            "caught": {n: tot(n) for n in names}, "clean_flagged": dict(clean_flag),
            "simdiff_wrong_category_cases": wrong_cat,
            "negative_controls_faithful_sim": dict(neg), "negative_controls_buggy_sim": dict(neg_buggy),
            "misses": misses[:40], "false_alarms": false_alarms[:40]}


def show(r):
    print(f"\n== {r['split']}: {r['cases']} runs ({r['clean']} with a faithful sim or an unexercised bug)")
    print(f"{'planted sim bug':<18}" + "".join(f"{n:>10}" for n in ("exact", "payload", "timing", "simdiff")))
    for k, v in r["by_bug"].items():
        print(f"{k:<18}" + "".join(f"{f'{v[n][0]}/{v[n][1]}':>10}" for n in ("exact", "payload", "timing", "simdiff")))
    print(f"{'caught':<18}" + "".join(f"{f'{r['caught'][n][0]}/{r['caught'][n][1]}':>10}" for n in ("exact", "payload", "timing", "simdiff")))
    print(f"{'clean flagged':<18}" + "".join(f"{f'{r['clean_flagged'][n]}/{r['clean']}':>10}" for n in ("exact", "payload", "timing", "simdiff")))
    print(f"simdiff: bug runs with an unexpected extra category: {r['simdiff_wrong_category_cases']}")
    print(f"negative controls, faithful sim: {r['negative_controls_faithful_sim']}")
    print(f"negative controls, buggy sim:    {r['negative_controls_buggy_sim']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    split = sys.argv[1] if len(sys.argv) > 1 else "dev"
    res = run(split)
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / f"out/eval_{split}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    show(res)
