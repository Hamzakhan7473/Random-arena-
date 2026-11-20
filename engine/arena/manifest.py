from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from arena.errors import ManifestError
from arena.paths import manifest_path
from arena.timeutil import format_utc, parse_utc

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> list[tuple[str, str, str]]:
    """Return (issue_time, rel, sha256) rows from hashes.log."""
    if not path.exists():
        return []
    rows: list[tuple[str, str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ManifestError(f"{path.name}:{lineno} is blank; the manifest is hash lines only")
        parsed = parse_manifest_line(line)
        if parsed is None:
            raise ManifestError(f"{path.name}:{lineno} is not '<issue_time> <path> <sha256>'")
        rows.append(parsed)
    return rows


def parse_manifest_line(line: str) -> tuple[str, str, str] | None:
    parts = line.split(" ")
    if len(parts) != 3 or not _HEX64.fullmatch(parts[2]):
        return None
    issue_time, rel, digest = parts
    try:
        parse_utc(issue_time)
    except ValueError:
        return None
    if not rel or " " in rel:
        return None
    return issue_time, rel, digest


def lookup_digest(root: Path, rel: str) -> str | None:
    found: str | None = None
    for _issue_time, recorded, digest in read_manifest(manifest_path(root)):
        if recorded != rel:
            continue
        if found is not None:
            raise ManifestError(f"manifest lists {rel} more than once")
        found = digest
    return found


def append_hash(root: Path, rel: str, digest: str, *, issue_time: datetime) -> None:
    if not _HEX64.fullmatch(digest):
        raise ManifestError("digest is not 64 hex characters")
    if " " in rel:
        raise ManifestError("forecast paths cannot contain spaces")
    stamp = format_utc(issue_time)
    existing = lookup_digest(root, rel)
    if existing is not None:
        if existing != digest:
            raise ManifestError(f"{rel} is already sealed with a different hash")
        return
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {rel} {digest}\n")
