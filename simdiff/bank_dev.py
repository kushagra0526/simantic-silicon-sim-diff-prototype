"""Dev bank (used while building)."""

BANK = {
    "crystal_ppm": 50.0,
    "usb_latency": (0.0005, 0.008),
    "split_rate": 0.10,
    "nack_count": (1, 3),
    "firmwares": [
        {"version": "1.2", "duration": 120.0, "hb_period": 1.0, "poll_period": 0.5, "temp_period": 2.0,
         "on_below": 5.0, "off_above": 6.0, "do_range": (3.0, 8.0), "do_step": 0.35, "irq_order": ["temp", "poll", "hb"]},
        {"version": "1.3", "duration": 90.0, "hb_period": 0.5, "poll_period": 1.0, "temp_period": 3.0,
         "on_below": 4.5, "off_above": 6.5, "do_range": (2.5, 8.5), "do_step": 0.5, "irq_order": ["poll", "temp", "hb"]},
    ],
    "mutants": ["inverted", "hb_2x", "hardfault", "wrong_addr"],
    "bugs": [
        {"kind": "prescaler", "timer": "poll", "psc": 8399},
        {"kind": "prescaler", "timer": "hb", "psc": 99},
        {"kind": "pll", "ratio": 84 / 80},
        {"kind": "reset_flags", "flags": "PIN"},
        {"kind": "irq_priority"},
        {"kind": "nack_unmodeled"},
        {"kind": "gpio_slow", "delay": 0.004},
        {"kind": "dropped_irq", "every": 40},
        {"kind": "fp_rounding"},
    ],
}
