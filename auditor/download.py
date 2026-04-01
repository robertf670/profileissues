"""Download and extract Dublin Bus GTFS to data/current/."""

from __future__ import annotations

import io
import json
import zipfile
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

DEFAULT_URL = "https://www.transportforireland.ie/transitData/Data/GTFS_Dublin_Bus.zip"

_LAST_DOWNLOAD_FILENAME = ".gtfs_downloaded_at"
_META_FILENAME = ".gtfs_download_meta.json"


def data_dir(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parent.parent
    d = root / "data" / "current"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _auth_headers(api_key: str | None) -> dict[str, str]:
    h: dict[str, str] = {}
    if api_key:
        h["Ocp-Apim-Subscription-Key"] = api_key
    return h


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
    headers = _auth_headers(api_key)

    resp = requests.get(target, headers=headers, timeout=600)
    resp.raise_for_status()

    etag = resp.headers.get("ETag")
    last_modified_header = resp.headers.get("Last-Modified")

    extracted = data_dir(project_root)
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True)

    buf = io.BytesIO(resp.content)
    with zipfile.ZipFile(buf) as zf:
        zf.extractall(extracted)

    at = datetime.now(timezone.utc)
    _write_last_download_utc(project_root, at)
    _write_download_meta(project_root, at, etag=etag, last_modified_header=last_modified_header)
    return extracted, at


def _write_last_download_utc(project_root: Path | None, at: datetime) -> None:
    p = data_dir(project_root) / _LAST_DOWNLOAD_FILENAME
    try:
        p.write_text(at.isoformat(), encoding="utf-8")
    except OSError:
        pass


def _write_download_meta(
    project_root: Path | None,
    at: datetime,
    etag: str | None,
    last_modified_header: str | None,
) -> None:
    payload = {
        "downloaded_at": at.isoformat(),
        "etag": etag,
        "last_modified_header": last_modified_header,
    }
    p = data_dir(project_root) / _META_FILENAME
    try:
        p.write_text(json.dumps(payload, indent=0), encoding="utf-8")
    except OSError:
        pass


def read_download_meta(project_root: Path | None = None) -> dict | None:
    """Optional JSON written on download: etag, last_modified_header, downloaded_at."""
    p = data_dir(project_root) / _META_FILENAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_last_download_utc(project_root: Path | None = None) -> datetime | None:
    """When this extract was downloaded (UTC), if recorded."""
    meta = read_download_meta(project_root)
    if meta and meta.get("downloaded_at"):
        try:
            return datetime.fromisoformat(str(meta["downloaded_at"]).replace("Z", "+00:00"))
        except ValueError:
            pass
    p = data_dir(project_root) / _LAST_DOWNLOAD_FILENAME
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def _parse_http_last_modified(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    try:
        dt = parsedate_to_datetime(str(value).strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_etag(value: str) -> str:
    e = value.strip()
    if e.upper().startswith("W/"):
        e = e[2:].strip()
    if len(e) >= 2 and e[0] == e[-1] == '"':
        e = e[1:-1]
    return e


def _fetch_remote_headers_only(url: str, headers: dict[str, str]) -> dict[str, str]:
    """HEAD first; if not allowed, GET and discard body (headers only)."""
    r = requests.head(url, headers=headers, timeout=90, allow_redirects=True)
    if r.status_code in (405, 501):
        r.close()
        r = requests.get(url, headers=headers, stream=True, timeout=90, allow_redirects=True)
        try:
            r.raise_for_status()
            return dict(r.headers)
        finally:
            r.close()
    r.raise_for_status()
    return dict(r.headers)


@dataclass(frozen=True)
class FeedCheckResult:
    kind: str  # "newer" | "current" | "unknown" | "error"
    message: str


def check_feed_update_available(
    url: str | None,
    api_key: str | None,
    project_root: Path | None = None,
) -> FeedCheckResult:
    """
    Compare remote Last-Modified / ETag to local meta without downloading the zip body
    (uses HEAD, or GET+close if HEAD is not supported).
    """
    target = url or DEFAULT_URL
    headers = _auth_headers(api_key)
    local_dt = read_last_download_utc(project_root)
    meta = read_download_meta(project_root)
    stored_etag = (meta or {}).get("etag")
    if isinstance(stored_etag, str):
        stored_etag = stored_etag.strip() or None
    else:
        stored_etag = None

    try:
        remote_h = _fetch_remote_headers_only(target, headers)
    except requests.RequestException as exc:
        return FeedCheckResult("error", f"Could not reach server: {exc}")

    remote_etag = remote_h.get("ETag")
    if isinstance(remote_etag, str):
        remote_etag = remote_etag.strip() or None
    remote_lm_raw = remote_h.get("Last-Modified")
    parsed_remote = _parse_http_last_modified(remote_lm_raw)

    # Strongest signal when we have both ETags from last download and HEAD/GET.
    if stored_etag and remote_etag:
        if _normalize_etag(stored_etag) != _normalize_etag(remote_etag):
            return FeedCheckResult(
                "newer",
                "Remote file identity changed (ETag differs). Use “Download / refresh GTFS” to update.",
            )
        return FeedCheckResult(
            "current",
            "Remote ETag matches your last download — no change detected.",
        )

    if parsed_remote is not None and local_dt is not None:
        skew = timedelta(seconds=3)
        if parsed_remote > local_dt + skew:
            return FeedCheckResult(
                "newer",
                "Remote Last-Modified is newer than your local download time — a refresh may be available.",
            )
        if parsed_remote < local_dt - timedelta(minutes=5):
            return FeedCheckResult(
                "current",
                "Remote Last-Modified is older than your download time (clock skew or CDN). Treat as unchanged unless you know otherwise.",
            )
        return FeedCheckResult(
            "current",
            "Remote Last-Modified is not newer than your last download — looks up to date.",
        )

    if parsed_remote is not None and local_dt is None:
        return FeedCheckResult(
            "unknown",
            "Remote sent a Last-Modified date, but this install has no recorded download time — run “Download / refresh” once to enable comparison.",
        )

    return FeedCheckResult(
        "unknown",
        "The server did not provide ETag and Last-Modified headers (or they could not be compared). "
        "Use “Download / refresh” when you want the latest file.",
    )


def feed_info_path(project_root: Path | None = None) -> Path:
    return data_dir(project_root) / "feed_info.txt"
