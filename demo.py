"""One validation run end to end -> out/report.html

    python demo.py

The aerator controller from Simantic's docs, captured on a board (USB-serial logger + logic
analyzer, crystal +37 ppm) and run in a sim that has two planted bugs: TIM3 prescaler off by one
(PSC 8399 -> +119 ppm on the Modbus poll) and the relay GPIO landing 4 ms late. Plus a negative
control: the same firmware with the relay threshold inverted.
"""
import hashlib
import html
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from simdiff.bank_dev import BANK  # noqa: E402
from simdiff.compare import compare  # noqa: E402
from simdiff.spec import negative_control  # noqa: E402
from simdiff.traces import parse_la, parse_logger, parse_sim  # noqa: E402
from simdiff.world import apply_bug, run_firmware, silicon_capture, sim_output  # noqa: E402

e = html.escape


def build(mutant=None):
    rng = random.Random(5)
    fw = dict(BANK["firmwares"][0])
    seq, do = [], 6.5
    for _ in range(260):
        do = min(8.0, max(3.0, do + rng.gauss(0, 0.35)))
        seq.append(round(do, 2))
    fw.update(do_seq=seq, temp_seq=[round(21 + rng.gauss(0, 0.6), 1) for _ in range(70)], nack_reads={7, 31}, hardfault_after=60)
    si = run_firmware(fw, {"clock_scale": 1 + 37e-6, "reset_flags": "POR"}, mutant)
    plat = apply_bug({"clock_scale": 1.0, "reset_flags": "POR"}, {"kind": "prescaler", "timer": "poll", "psc": 8399}, fw, rng)
    plat = apply_bug(plat, {"kind": "gpio_slow", "delay": 0.004}, fw, rng)
    sm = run_firmware(fw, plat, mutant)
    logger, la = silicon_capture(si, rng, {"host_start": 43200.25, "la_offset": 0.734, "usb_latency": (0.0005, 0.008), "split_rate": 0.1})
    return fw, logger, la, sim_output(sm)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fw, logger, la, sim = build()
    meta = {"firmware_sha256": hashlib.sha256(json.dumps(fw, sort_keys=True, default=list).encode()).hexdigest()[:16],
            "target": "STM32F401RE (board rev B)", "sim_version": "sim 0.6.x (model under test)", "crystal_ppm": 50}
    r = compare(sim, logger, la, crystal_ppm=50, meta=meta)
    print(f"verdict: {r['verdict']}   device drift fitted: {r['fit']['device_drift_ppm']} ppm")
    for k, v in r["fit"]["streams"].items():
        print(f"  {k:<14} {v['source']:<7} {v['ppm_vs_sim']:+8.1f} ppm  ({v['n']} events)")
    for f in r["findings"]:
        if f["cat"] != "ORDER":
            print(f"  {f['cat']:<10} {f['msg']}")
    n_order = sum(f["cat"] == "ORDER" for f in r["findings"])
    print(f"  ORDER      {n_order} lines (TIM3 drifting past TIM4 changes which ISR prints first)")
    fw2, logger2, la2, sim2 = build("inverted")
    nc, a, b = negative_control(sorted(parse_logger(logger2) + parse_la(la2)), sorted(parse_sim(sim2)), fw2)
    print(f"negative control (relay threshold inverted): {nc}\n  silicon: {a[0] if a else None}\n  sim:     {b[0] if b else None}")
    ev = {}
    for k, f in (("dev", "out/eval_dev.json"), ("heldout_first_run", "out/heldout_first_run.json")):
        if (HERE / f).exists():
            ev[k] = json.loads((HERE / f).read_text(encoding="utf-8"))
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out/report.html").write_text(render(r, logger, la, sim, nc, a, b, ev, n_order), encoding="utf-8")
    print("wrote out/report.html")


def render(r, logger, la, sim, nc, a, b, ev, n_order):
    pre = lambda xs: e("\n".join(xs))
    streams = "".join(f"<tr><td>{e(k)}</td><td>{v['source']}</td><td>{v['ppm_vs_sim']:+.1f}</td><td>{v['n']}</td></tr>" for k, v in r["fit"]["streams"].items())
    fl = "".join(f"<tr><td><b>{e(f['cat'])}</b></td><td>{e(f['msg'])}</td><td>{e('; '.join(f['evidence']))}</td></tr>"
                 for f in r["findings"] if f["cat"] != "ORDER")
    fl += f"<tr><td><b>ORDER</b></td><td>{n_order} lines print in a different order (TIM3 drifts past TIM4)</td><td></td></tr>"
    bench = ""
    for k, lab in (("dev", "dev (300 runs)"), ("heldout_first_run", "held-out, first run (300)")):
        if k in ev:
            x = ev[k]
            c, cf = x["caught"], x["clean_flagged"]
            bench += (f"<tr><td>{lab}</td>" + "".join(f"<td>{cf[n]}/{x['clean']}</td>" for n in ("exact", "payload", "timing")) +
                      f"<td><b>{cf['simdiff']}/{x['clean']}</b></td><td><b>{c['simdiff'][0]}/{c['simdiff'][1]}</b></td>"
                      f"<td>{x['negative_controls_faithful_sim'].get('SAME_FAILURE', 0)}/{sum(x['negative_controls_faithful_sim'].values())}</td></tr>")
    m = r["meta"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Silicon vs sim diff</title><style>
:root{{--bg:#0f1217;--fg:#e8ecf2;--mut:#93a0b4;--card:#171c25;--line:#2a3140}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:26px 16px 60px}}h1{{font-size:25px;margin:0 0 4px}}h2{{font-size:17px;margin:24px 0 8px}}.sub{{color:var(--mut)}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;overflow-x:auto}}pre{{margin:6px 0 0;font:12px Consolas,monospace;color:#b9f0cd;white-space:pre}}
table{{width:100%;border-collapse:collapse;background:var(--card);font-size:13.5px}}td,th{{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
.v{{display:inline-block;background:#b3261e;color:#fff;border-radius:6px;padding:1px 10px;font-weight:700}}
</style></head><body><main>
<h1>Same firmware on silicon and in simulation: what actually differs</h1>
<div class="sub">Synthetic captures in real formats (sim --ascii, USB-serial logger, logic-analyzer CSV). Two planted sim bugs: TIM3 prescaler off by one (PSC 8399), relay GPIO 4 ms late.
Unofficial prototype by Kushagra Pratap Singh, not affiliated with Simantic.</div>
<div class="grid" style="margin-top:14px">
<div class="card"><b>sim output</b><pre>{pre(sim[:9])}</pre></div>
<div class="card"><b>silicon: USB-serial logger</b><pre>{pre(logger[:9])}</pre></div>
<div class="card"><b>silicon: logic analyzer</b><pre>{pre(la.splitlines()[:9])}</pre></div>
</div>
<h2>Time fit (expected differences removed) → <span class="v">{r['verdict']}</span></h2>
<div class="sub">Board crystal fitted at {r['fit']['device_drift_ppm']:+.1f} ppm (within ±50: not a finding). Each capture gets its own offset. Logger latency and analyzer jitter set the tolerances.</div>
<div style="overflow-x:auto;margin-top:8px"><table><tr><th>stream</th><th>capture</th><th>rate vs sim (ppm)</th><th>matched events</th></tr>{streams}</table></div>
<h2>Findings</h2><div style="overflow-x:auto"><table><tr><th>category</th><th>finding</th><th>evidence</th></tr>{fl}</table></div>
<h2>Negative control: relay threshold inverted in the firmware → {e(nc)}</h2>
<div class="card" style="font-size:14px">silicon first failure: <code>{e(str(a[0]) if a else '-')}</code><br>sim first failure: <code>{e(str(b[0]) if b else '-')}</code></div>
<h2>Traceability</h2><div class="card" style="font-size:14px">firmware {e(m['firmware_sha256'])} · target {e(m['target'])} · {e(m['sim_version'])} · crystal tolerance ±{m['crystal_ppm']} ppm ·
not exercised in this run: {e(', '.join(r['coverage']['never_exercised']) or 'none')}</div>
<h2>Benchmark</h2><div style="overflow-x:auto"><table><tr><th>set</th><th>clean runs flagged: exact diff</th><th>payload-only</th><th>timing, no drift fit</th><th>simdiff</th><th>sim bugs named (simdiff)</th><th>negative controls reproduced</th></tr>{bench}</table></div>
</main></body></html>"""


if __name__ == "__main__":
    main()
