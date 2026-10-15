"""Held-out bank: committed to git before the first run on it, not used for tuning.
Other firmware timings, a noisier USB logger, more split lines, a tighter crystal, and subtler sim
bugs, two of them deliberately at or below what one silicon capture can resolve:
  prescaler PSC=47999 -> 21 ppm (below a 30 ppm crystal tolerance), PLL 168/167 -> ~6000 ppm,
  GPIO 1.5 ms late (inside the 5 ms causality budget, but far outside analyzer jitter)."""

BANK = {
    "crystal_ppm": 30.0,
    "usb_latency": (0.001, 0.016),
    "split_rate": 0.30,
    "nack_count": (2, 5),
    "firmwares": [
        {"version": "2.0", "duration": 180.0, "hb_period": 0.25, "poll_period": 0.75, "temp_period": 1.5,
         "on_below": 4.0, "off_above": 5.5, "do_range": (2.0, 7.5), "do_step": 0.4, "irq_order": ["hb", "temp", "poll"]},
        {"version": "2.1", "duration": 60.0, "hb_period": 2.0, "poll_period": 0.2, "temp_period": 1.0,
         "on_below": 5.5, "off_above": 6.0, "do_range": (4.0, 7.0), "do_step": 0.25, "irq_order": ["poll", "hb", "temp"]},
        {"version": "2.2", "duration": 150.0, "hb_period": 1.0, "poll_period": 0.5, "temp_period": 5.0,
         "on_below": 5.0, "off_above": 6.0, "do_range": (3.0, 8.0), "do_step": 0.3, "irq_order": ["temp", "hb", "poll"]},
    ],
    "mutants": ["inverted", "hb_2x", "hardfault", "wrong_addr"],
    "bugs": [
        {"kind": "prescaler", "timer": "temp", "psc": 1999},
        {"kind": "prescaler", "timer": "poll", "psc": 47999},
        {"kind": "prescaler", "timer": "hb", "psc": 4199},
        {"kind": "pll", "ratio": 168 / 167},
        {"kind": "reset_flags", "flags": "WDG"},
        {"kind": "irq_priority"},
        {"kind": "nack_unmodeled"},
        {"kind": "gpio_slow", "delay": 0.0015},
        {"kind": "dropped_irq", "every": 25},
        {"kind": "fp_rounding"},
    ],
}
