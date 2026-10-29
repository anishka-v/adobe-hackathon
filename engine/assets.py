"""Read-only asset measurements.

The brand kit's logo and product art are mostly transparent padding (the
logo lockup's visible pixels cover ~40 % of its width), so a box that meets a
size floor can still *read* as tiny. This module measures where the visible
pixels are so floors can be applied to what the viewer actually sees.

It only ever reads the files. Nothing here writes, crops, or alters pixels —
the renderer is still handed the untouched asset.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Optional

ASSETS_DIR = Path("brand_kit/assets")  # same CWD-relative convention as engine/main.py
_ALPHA_THRESHOLD = 8  # out of 255: ignore anti-aliasing haze around the artwork


class AssetInfo(NamedTuple):
    width: int
    height: int
    vis_w: float  # fraction of the asset's width spanned by visible pixels
    vis_h: float
    vis_cx: float  # centre of the visible region, as a fraction of the asset size
    vis_cy: float


@lru_cache(maxsize=256)
def asset_info(asset_id: Optional[str]) -> Optional[AssetInfo]:
    """Size and visible-pixel bounds of an asset, or None if it can't be read.
    Opaque images report full visibility."""
    if not asset_id:
        return None
    path = ASSETS_DIR / asset_id
    try:
        if not path.is_file():
            return None
        from PIL import Image  # local import: keeps the engine importable without Pillow

        with Image.open(path) as im:
            w, h = im.size
            has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            if not has_alpha:
                return AssetInfo(w, h, 1.0, 1.0, 0.5, 0.5)
            alpha = im.convert("RGBA").getchannel("A")
        bb = alpha.point(lambda a: 255 if a > _ALPHA_THRESHOLD else 0).getbbox()
        if not bb:
            return AssetInfo(w, h, 1.0, 1.0, 0.5, 0.5)
        x0, y0, x1, y1 = bb
        return AssetInfo(w, h, (x1 - x0) / w, (y1 - y0) / h, (x0 + x1) / (2 * w), (y0 + y1) / (2 * h))
    except Exception:
        return None


def visible_size(info: Optional[AssetInfo], box_w: float, box_h: float, fit: str) -> tuple[float, float]:
    """Width/height of the visible artwork when the asset is drawn into a
    box_w × box_h box with the renderer's `fit` rule."""
    if info is None or box_w <= 0 or box_h <= 0:
        return box_w, box_h
    if fit == "contain":
        s = min(box_w / info.width, box_h / info.height)
        return info.vis_w * info.width * s, info.vis_h * info.height * s
    if fit == "cover":
        s = max(box_w / info.width, box_h / info.height)
        return min(box_w, info.vis_w * info.width * s), min(box_h, info.vis_h * info.height * s)
    return info.vis_w * box_w, info.vis_h * box_h  # fill: stretched to the box
