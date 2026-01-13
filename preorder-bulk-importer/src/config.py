from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class Paths:
    project_root: Path
    inputs_dir: Path
    outputs_dir: Path
    assets_dir: Path
    logs_dir: Path
    playwright_dir: Path


@dataclass(frozen=True)
class EdelweissConfig:
    user: str
    password: str
    base_url: str = "https://www.edelweiss.plus/"


@dataclass(frozen=True)
class PlaywrightConfig:
    headful: bool = False
    slowmo_ms: int = 0
    timeout_ms: int = 30000
    trace: bool = False


@dataclass(frozen=True)
class Defaults:
    language_tag: str = "Ln_En"
    product_type: str = "BOOK"
    weight_lbs: float = 5.0
    country_of_origin: str = "China"
    hs_code: str = "4901.99"
    interior_images_limit: int = 5
    cover_width: int = 800
    interior_height: int = 600


def get_paths() -> Paths:
    project_root = Path(__file__).resolve().parents[1]
    inputs_dir = project_root / "inputs"
    outputs_dir = project_root / "outputs"
    assets_dir = project_root / "assets"
    logs_dir = project_root / "logs"
    playwright_dir = logs_dir / "playwright"

    # Ensure folders exist
    for p in [inputs_dir, outputs_dir, assets_dir, logs_dir, playwright_dir]:
        p.mkdir(parents=True, exist_ok=True)

    return Paths(
        project_root=project_root,
        inputs_dir=inputs_dir,
        outputs_dir=outputs_dir,
        assets_dir=assets_dir,
        logs_dir=logs_dir,
        playwright_dir=playwright_dir,
    )


def load_env(project_root: Path) -> None:
    # Looks for a local .env alongside README/requirements
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # fallback to system env only
        load_dotenv()


def get_edelweiss_config(paths: Paths) -> EdelweissConfig:
    load_env(paths.project_root)
    user = os.environ.get("EDELWEISS_USER", "").strip()
    pw = os.environ.get("EDELWEISS_PASS", "").strip()
    if not user or not pw:
        raise RuntimeError("Missing EDELWEISS_USER and/or EDELWEISS_PASS in environment.")
    return EdelweissConfig(user=user, password=pw)