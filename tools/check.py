"""Full check without pytest, so it also runs inside the scoring image.

Text measures ~2% wider on Linux (FreeType rounds glyph advances) than on
macOS, and stage-one scoring runs on Linux, so run this in Docker too:

    python -m tools.check                 # local
    docker run --rm --network none --entrypoint python \\
      -v "$(pwd)/engine:/app/engine" -v "$(pwd)/tools:/app/tools" \\
      -v "$(pwd)/input:/app/input" my-chameleon-engine -m tools.check

Exits non-zero if any sample × catalogue cell breaks a structural rule or a
brand/readability floor, or any fuzz seed breaks a structural rule.
"""

import json
import platform
import sys
from pathlib import Path

from engine.quality import design_issues, tiny_issues
from engine.resize import _resize
from engine.units import build_units
from engine.validate import check_design
from tools.fuzz import make_design, random_target
from tools.matrix import CATALOGUE


def main() -> int:
    n_fuzz = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print(f"platform: {platform.system()} {platform.machine()}")
    bad = 0
    cells = 0
    for path in sorted(Path("input").glob("*.json")):
        source = json.loads(path.read_text())["source"]
        units = build_units(source)
        for name, w, h in CATALOGUE:
            cells += 1
            out = _resize(source, w, h)
            problems = check_design(source, out) + tiny_issues(units, out)
            if w * h >= 60_000:  # same tier rule as tests/test_invariants.py
                problems += design_issues(units, out)
            if problems:
                bad += 1
                print(f"FAIL {path.stem} {name} {w}x{h}: {problems}")
    print(f"catalogue: {cells - bad}/{cells} cells clean")

    fuzz_bad = 0
    for seed in range(n_fuzz):
        design = make_design(seed)
        t = random_target(seed)
        try:
            problems = check_design(design, _resize(design, t["width"], t["height"]))
        except Exception as e:  # noqa: BLE001
            problems = [f"EXCEPTION {type(e).__name__}: {e}"]
        if problems:
            fuzz_bad += 1
            print(f"FAIL fuzz seed {seed}: {problems[:3]}")
    print(f"fuzz: {n_fuzz - fuzz_bad}/{n_fuzz} seeds clean")
    return 1 if (bad or fuzz_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
