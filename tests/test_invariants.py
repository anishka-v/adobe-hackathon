"""Hard invariants every output must satisfy (see DESIGN.md §2).

Run with:  python -m pytest -q
"""

import json
from pathlib import Path

import pytest

from engine.quality import design_issues, tiny_issues
from engine.resize import _resize, resize
from engine.units import build_units
from engine.validate import check_design
from tools.fuzz import make_design, random_target
from tools.matrix import CATALOGUE

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = sorted((ROOT / "input").glob("*.json"))
PASSTHROUGH = ("id", "type", "content", "font_family", "font_weight", "font_style", "color",
               "fill_color", "stroke_color", "asset_id", "fit", "shape_type", "opacity",
               "z_index", "group_id", "text_align", "line_height", "max_lines", "style_runs", "rotation")


def _load(path: Path) -> dict:
    return json.loads(path.read_text())["source"]


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
@pytest.mark.parametrize("fmt", CATALOGUE, ids=[f"{n}-{w}x{h}" for n, w, h in CATALOGUE])
def test_sample_on_catalogue(path, fmt):
    source = _load(path)
    _, w, h = fmt
    out = _resize(source, w, h)
    assert out["canvas"] == {"width": w, "height": h}
    assert check_design(source, out) == []
    assert [e["id"] for e in out["elements"]] == [e["id"] for e in source["elements"]]
    for s, o in zip(source["elements"], out["elements"]):
        for k in PASSTHROUGH:
            if k in s:
                assert o.get(k) == s[k], f"{s['id']}: {k} changed"
        if o["type"] == "text":
            assert isinstance(o["font_size"], int) and o["font_size"] >= 1


# Below this canvas area (~245 px square) seventeen elements cannot all meet the
# readability floors; the contract there is the weaker `tiny_issues` one.
FULL_FLOOR_AREA = 60_000


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
@pytest.mark.parametrize("fmt", CATALOGUE, ids=[f"{n}-{w}x{h}" for n, w, h in CATALOGUE])
def test_sample_meets_brand_floors(path, fmt):
    """Structurally valid is not enough: the logo must stay visible, the hero
    must stay a hero, and no text may drop below a readable size. On canvases
    too small for that, nothing may drop below legibility and the headline
    must remain the largest text."""
    source = _load(path)
    _, w, h = fmt
    out = _resize(source, w, h)
    units = build_units(source)
    assert tiny_issues(units, out) == []
    if w * h >= FULL_FLOOR_AREA:
        assert design_issues(units, out) == []


@pytest.mark.parametrize("path", SAMPLES[:2], ids=[p.stem for p in SAMPLES[:2]])
def test_identity_resize_is_near_noop(path):
    source = _load(path)
    out = _resize(source, source["canvas"]["width"], source["canvas"]["height"])
    for s, o in zip(source["elements"], out["elements"]):
        if s["type"] == "shape" and s["x"] < 0:  # decorations bleeding off-canvas get pulled inside
            continue
        assert abs(s["x"] - o["x"]) <= 4 and abs(s["y"] - o["y"]) <= 4, s["id"]
        assert abs(s["width"] - o["width"]) <= 4, s["id"]


def test_deterministic():
    source = _load(SAMPLES[1])
    a = _resize(source, 1600, 400)
    b = _resize(source, 1600, 400)
    assert a == b


@pytest.mark.parametrize("seed", range(120))
def test_fuzz(seed):
    design = make_design(seed)
    t = random_target(seed)
    out = _resize(design, t["width"], t["height"])
    assert check_design(design, out) == []


def test_public_entry_never_raises():
    broken = {"canvas": {"width": 100, "height": 100}, "elements": [
        {"id": "x", "type": "text", "x": 0, "y": 0, "width": 10, "height": 10,
         "content": "hi", "font_family": "Nope", "font_size": 12, "color": "rgba(0,0,0,1)"}]}
    out = resize(broken, {"width": 50, "height": 500})
    assert [e["id"] for e in out["elements"]] == ["x"]
