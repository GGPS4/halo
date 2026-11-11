"""Interactive helpers:  python -m halo.calibrate scale   — set the load-cell factor
                        python -m halo.calibrate prime   — fill every tube from its bottle
                        python -m halo.calibrate clean   — run all pumps (put the inlets in warm water)"""
from __future__ import annotations

import sys
import time

from .config import ROOT, load_settings
from .hardware import Pumps, Scale


def scale():
    s = load_settings()["hardware"]
    sc = Scale(s["hx711_dt"], s["hx711_sck"], 1.0)
    input("Empty the glass stage and press Enter… ")
    sc.tare()
    grams = float(input("Put a known weight on the stage (e.g. a full 330 ml can ≈ 350 g). Its weight in grams: "))
    factor = sc.raw(15) - sc.offset
    factor /= grams
    print(f"\nscale_factor = {factor:.2f}")
    cfg = ROOT / "config.toml"
    text = cfg.read_text() if cfg.exists() else "[hardware]\n"
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("scale_factor")]
    idx = lines.index("[hardware]") + 1 if "[hardware]" in lines else 0
    if "[hardware]" not in lines:
        lines.insert(0, "[hardware]")
        idx = 1
    lines.insert(idx, f"scale_factor = {factor:.2f}")
    cfg.write_text("\n".join(lines) + "\n")
    print(f"Saved to {cfg}")


def run_all(seconds: float, label: str):
    s = load_settings()["hardware"]
    pumps = Pumps(s["pump_pins"])
    try:
        for slot in range(1, 9):
            print(f"{label} slot {slot}…")
            pumps.on(slot)
            time.sleep(seconds)
            pumps.off(slot)
    finally:
        pumps.all_off()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "scale":
        scale()
    elif cmd == "prime":
        input("Put a glass under the nozzle and press Enter… ")
        run_all(6, "Priming")
    elif cmd == "clean":
        input("Put all 8 inlets in a jug of warm water, a big glass under the nozzle, then Enter… ")
        run_all(20, "Flushing")
    else:
        print(__doc__)
