from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from ..config import Defaults
from ..models import EnrichedRecord, ImageAsset

import requests

def extract_interior_images(page, max_images: int = 5) -> list[str]:
    """
    Extract interior illustration image URLs from the active Images tabpanel.
    Assumes Images tab has already been activated.
    """

    # Wait for any visible tabpanel to exist
    page.wait_for_function(
        """
        () => {
            const panels = [...document.querySelectorAll('[id^="title-references-tabpanel-"]')];
            return panels.some(p => p.offsetParent !== null);
        }
        """,
        timeout=5000,
    )

    urls = page.evaluate(
        """
        () => {
            const panels = [...document.querySelectorAll('[id^="title-references-tabpanel-"]')];
            const panel = panels.find(p => p.offsetParent !== null);
            if (!panel) return [];

            const imgs = [...panel.querySelectorAll('img')]
                .map(img => img.src)
                .filter(src =>
                    src.includes('/images/illustrations/original/') &&
                    !src.includes('_cover') &&
                    !src.includes('_jacket')
                )
                .map(src => src.split('?')[0]); // strip ?height=200

            // Deduplicate while preserving order
            return [...new Set(imgs)];
        }
        """
    )

    return urls[:max_images]

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
        dest = isbn_dir / f"{rec.isbn13}.jpg"
        if _download(hi, dest):
            a.local_path = str(dest)

    # Interior images (if present) — limit to first 5
    interior_assets = [a for a in rec.images if a.kind == "interior"][:5]
    for idx, a in enumerate(interior_assets, start=1):
        hi = _bump_query_param(a.src_url, "height", defaults.interior_height)
        dest = isbn_dir / f"{rec.isbn13}-{idx}.jpg"
        if _download(hi, dest):
            a.local_path = str(dest)