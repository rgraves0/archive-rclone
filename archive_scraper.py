import re
import requests
from urllib.parse import urlparse

ARCHIVE_METADATA_URL = "https://archive.org/metadata/{identifier}"

# Identifier must be URL-safe alphanumeric (no path traversal characters)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._\-]{1,256}$")

# Allowed file source types from archive.org
_ALLOWED_SOURCES = {"original", "derivative", "metadata"}


def _validate_identifier(identifier: str) -> str:
    """Ensure the identifier is safe before embedding it in a URL."""
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Invalid archive.org identifier: {identifier!r}")
    return identifier


def parse_archive_url(url: str) -> str | None:
    """
    Extract and validate the archive.org item identifier from a URL.
    Returns None if the URL is not a valid archive.org details page.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = parsed.netloc.lower().lstrip("www.")
    if not host.endswith("archive.org"):
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "details":
        raw = parts[1]
    elif parts:
        raw = parts[-1]
    else:
        return None

    try:
        return _validate_identifier(raw)
    except ValueError:
        return None


def fetch_metadata(identifier: str) -> dict:
    """Fetch item metadata from archive.org."""
    _validate_identifier(identifier)
    url  = ARCHIVE_METADATA_URL.format(identifier=identifier)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def list_files_from_metadata(meta: dict) -> list[dict]:
    """
    Extract downloadable file entries from archive.org metadata.
    Skips entries with missing names or unsafe source types.
    """
    out: list[dict] = []
    for f in meta.get("files", []):
        name = f.get("name")
        if not name or not isinstance(name, str):
            continue

        # Skip files with obviously unsafe names
        if ".." in name or name.startswith("/"):
            continue

        source = f.get("source", "")
        if source and source not in _ALLOWED_SOURCES:
            continue

        out.append({
            "name":   name,
            "format": f.get("format") or f.get("format-label") or "Unknown",
            "size":   f.get("size") or f.get("original") or "",
            "source": source,
            "md5":    f.get("md5") or "",
        })
    return out
