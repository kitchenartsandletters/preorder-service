from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import requests

from ..config import Defaults
from ..models import EnrichedRecord, ImageAsset


def _bump_query_param(url: str, key: str, value: int) -> str:
    """
    Replace ?width=400 style params.
    If key doesn't exist, append it.
    """
    if not url:
        return url
    if re.search(rf"[?&]{key}=\d+", url):
        return re.sub(rf"([?&]{key}=)\d+", rf"\g<1>{value}", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{key}={value}"


def _download(url: str, dest: Path, timeout: int = 60) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return True
    except Exception:
        return False


def download_images_for_record(
    rec: EnrichedRecord,
    assets_root: Path,
    defaults: Defaults,
) -> None:
    """
    Mutates rec.images[*].local_path after successful download.
    """
    isbn_dir = assets_root / "edelweiss" / rec.isbn13
    isbn_dir.mkdir(parents=True, exist_ok=True)

    # Cover
    cover_assets = [a for a in rec.images if a.kind == "cover"]
    if cover_assets:
        a = cover_assets[0]
        hi = _bump_query_param(a.src_url, "width", defaults.cover_width)
        dest = isbn_dir / "cover.jpg"
        if _download(hi, dest):
            a.local_path = str(dest)

    # Interior images (if present)
    interior_assets = [a for a in rec.images if a.kind == "interior"]
    for idx, a in enumerate(interior_assets, start=1):
        hi = _bump_query_param(a.src_url, "height", defaults.interior_height)
        dest = isbn_dir / f"interior_{idx:02d}.jpg"
        if _download(hi, dest):
            a.local_path = str(dest)