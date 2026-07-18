"""Search result index cache for short-index navigation.

Stores the last search/recommend result set so that users can quickly
access a job by its 1-based index number (e.g., `boss show 3`).

Cache file: ~/.config/boss-cli/index_cache.json
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .platform import PATHS, ensure_private_directory, ensure_private_file

logger = logging.getLogger(__name__)

CONFIG_DIR = PATHS.config_dir  # Backward-compatible public constant.
INDEX_CACHE_FILE = PATHS.index_cache_file


def save_index(jobs: list[dict[str, Any]], source: str = "search") -> None:
    """Persist an ordered list of jobs for short-index navigation.

    Each entry stores the minimum metadata needed for `show` and `detail`:
    securityId, jobName, brandName, salaryDesc, lid.
    """
    if not jobs:
        return

    ensure_private_directory(INDEX_CACHE_FILE.parent)

    entries = []
    for job in jobs:
        entry = {
            "securityId": job.get("securityId", ""),
            "jobName": job.get("jobName", ""),
            "brandName": job.get("brandName", ""),
            "salaryDesc": job.get("salaryDesc", ""),
            "cityName": job.get("cityName", ""),
            "areaDistrict": job.get("areaDistrict", ""),
            "jobExperience": job.get("jobExperience", ""),
            "jobDegree": job.get("jobDegree", ""),
            "skills": job.get("skills", []),
            "lid": job.get("lid", ""),
        }
        if entry["securityId"]:
            entries.append(entry)

    payload = {
        "source": source,
        "saved_at": time.time(),
        "count": len(entries),
        "items": entries,
    }

    INDEX_CACHE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    ensure_private_file(INDEX_CACHE_FILE)
    logger.debug("Saved index cache with %d entries from %s", len(entries), source)


def get_job_by_index(index: int) -> dict[str, Any] | None:
    """Resolve a 1-based short index to a cached job reference.

    Returns the job entry dict or None if index is out of range.
    """
    if index <= 0:
        return None

    if not INDEX_CACHE_FILE.exists():
        return None

    try:
        data = json.loads(INDEX_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    items = data.get("items", [])
    if index > len(items):
        return None

    return items[index - 1]


def get_index_info() -> dict[str, Any]:
    """Get metadata about the current index cache."""
    if not INDEX_CACHE_FILE.exists():
        return {"exists": False, "count": 0}

    try:
        data = json.loads(INDEX_CACHE_FILE.read_text(encoding="utf-8"))
        return {
            "exists": True,
            "source": data.get("source", "unknown"),
            "count": data.get("count", 0),
            "saved_at": data.get("saved_at", 0),
        }
    except (OSError, json.JSONDecodeError):
        return {"exists": False, "count": 0}
