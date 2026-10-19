"""A small synthetic feed for testing the fault injector: 6 registrations and 8 lab reports,
built with the fixture generator's own message builders (so it is clean by construction)."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclasses look their module up while it loads
    spec.loader.exec_module(module)
    return module


def small_feed(dest: Path) -> Path:
    fx = load_script("make_fixtures")
    rng = random.Random(7)
    people = [fx.person(rng) for _ in range(6)]
    (dest / "adt").mkdir(parents=True)
    (dest / "oru").mkdir()
    (dest / "adt" / "adt-001.hl7").write_text(
        "\n".join(fx.adt_a04(p, f"A{n:07d}") for n, p in enumerate(people, 1)) + "\n", newline="")
    (dest / "oru" / "oru-001.hl7").write_text(
        "\n".join(fx.oru_r01(people[n % 6], f"R{n:07d}", f"LR{n:08d}", [fx.HBA1C, fx.GLUCOSE])
                  for n in range(1, 9)) + "\n", newline="")
    return dest
