"""Download and extract Dublin Bus GTFS to data/current/."""

from __future__ import annotations

import io
import zipfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

import requests

DEFAULT_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_Dublin_Bus.zip"


def data_dir(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parent.parent
    d = root / "data" / "current"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_gtfs(
    url: str | None,
    api_key: str | None,
    project_root: Path | None = None,
) -> tuple[Path, datetime]:
    """
    Download GTFS zip and extract into data/current/.
    Returns (extract_dir, downloaded_at_utc).
    """
    target = url or DEFAULT_URL
    headers = {}
    if api_key:
        headers["Ocp-Apim-Subscription-Key"] = api_key

    resp = requests.get(target, headers=headers, timeout=600)
    resp.raise_for_status()

    extracted = data_dir(project_root)
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True)

    buf = io.BytesIO(resp.content)
    with zipfile.ZipFile(buf) as zf:
        zf.extractall(extracted)

    at = datetime.now(timezone.utc)
    return extracted, at


def feed_info_path(project_root: Path | None = None) -> Path:
    return data_dir(project_root) / "feed_info.txt"
