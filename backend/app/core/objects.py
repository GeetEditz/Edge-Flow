"""Object identifier validation shared by the origin, edges and the control plane.

An object id is a relative POSIX path of 1-5 segments. Each segment starts with an
alphanumeric, underscore or hyphen and contains only [A-Za-z0-9._-]. Because no segment
may begin with '.', the ids '..', '.', and hidden files are impossible, and since '%' is
not allowed, percent-encoded traversal ('%2e%2e') is rejected before any decoding.
"""

import re
from pathlib import Path

_OBJECT_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}){0,4}$")


def valid_object_id(object_id: str) -> bool:
    return bool(_OBJECT_ID.match(object_id))


def safe_resolve(root: Path, object_id: str) -> Path | None:
    """Return the file for object_id inside root, or None if invalid / outside root / missing."""
    if not valid_object_id(object_id):
        return None
    root = root.resolve()
    candidate = (root / object_id).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate
