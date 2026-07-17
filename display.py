"""
Clueless — the display writer.

The Vite React app in app/ renders app/src/picks.json as its ENTIRE state
(hot-reloads on change — see app/src/App.jsx). This module owns the one path
that's allowed to touch that file: given a `picks` dict shaped like the
contract below, it validates, stamps, and atomically writes it.

    {updated, headline, sections: [{heading, blurb, items: [{item_id, category, name, reason}]}]}

A parallel `present_picks` custom tool (in clueless.py) calls write_picks()
as its handler and turns a ValueError into a tool error for the model.

Usage:
    from display import write_picks
    write_picks({"headline": "...", "sections": [...]})
"""

import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def display_available(repo_root=REPO_ROOT):
    """True iff <repo_root>/app/src exists (i.e. the Vite app is checked out)."""
    return (repo_root / "app" / "src").exists()


def _validate(picks):
    """Shallow structural validation. Raises ValueError with a precise message.

    Only checks the shape the app actually reads (App.jsx) — not vocabulary
    or business rules. Anything else is the model's problem, not ours.
    """
    if not isinstance(picks, dict):
        raise ValueError("picks must be a dict")

    headline = picks.get("headline")
    if not isinstance(headline, str):
        raise ValueError("picks['headline'] must be a str")

    sections = picks.get("sections")
    if not isinstance(sections, list):
        raise ValueError("picks['sections'] must be a list")

    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError("picks['sections'][%d] must be a dict" % i)
        if not isinstance(section.get("heading"), str):
            raise ValueError("picks['sections'][%d]['heading'] must be a str" % i)
        items = section.get("items")
        if not isinstance(items, list):
            raise ValueError("picks['sections'][%d]['items'] must be a list" % i)
        for j, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(
                    "picks['sections'][%d]['items'][%d] must be a dict" % (i, j)
                )
            if "item_id" not in item:
                raise ValueError(
                    "picks['sections'][%d]['items'][%d] missing 'item_id'" % (i, j)
                )
            if not isinstance(item.get("name"), str):
                raise ValueError(
                    "picks['sections'][%d]['items'][%d]['name'] must be a str" % (i, j)
                )


def _missing_image_ids(picks, repo_root):
    """Item ids with no corresponding jpg under data/polyvore/images/.

    Missing the data/ dir entirely just means we can't check — skip silently
    rather than warning about every single item.
    """
    images_dir = repo_root / "data" / "polyvore" / "images"
    if not images_dir.exists():
        return []

    missing = []
    for section in picks.get("sections", []):
        for item in section.get("items", []):
            item_id = item.get("item_id")
            if not (images_dir / ("%s.jpg" % item_id)).exists():
                missing.append(str(item_id))
    return missing


def write_picks(picks, repo_root=REPO_ROOT, enabled=True):
    """Validate, stamp, and atomically write `picks` to app/src/picks.json.

    Returns a human-readable summary string. Raises ValueError only for
    structurally invalid `picks` (caller — the present_picks tool handler —
    converts that into a tool error). A disabled/unavailable display is a
    graceful no-op, not an error.
    """
    if not enabled or not display_available(repo_root):
        return "display not available (app/ missing or --no-display); nothing written"

    _validate(picks)

    picks = dict(picks)
    picks["updated"] = time.strftime("%Y-%m-%d %-I:%M %p")

    missing_ids = _missing_image_ids(picks, repo_root)

    picks_path = repo_root / "app" / "src" / "picks.json"
    tmp_path = picks_path.with_suffix(picks_path.suffix + ".tmp")

    # Write to a temp file then os.replace (atomic rename) onto picks.json.
    # Vite's JSON import hot-reloads on every write to picks.json; if it ever
    # observes a partially-written file, that JSON.parse crashes the whole
    # app's HMR. Writing elsewhere first and renaming into place means the
    # app only ever sees a complete file.
    with open(tmp_path, "w") as f:
        json.dump(picks, f, indent=2)
    os.replace(tmp_path, picks_path)

    n_sections = len(picks["sections"])
    n_items = sum(len(s.get("items", [])) for s in picks["sections"])
    summary = "display updated: %d sections, %d items" % (n_sections, n_items)
    if missing_ids:
        summary += "; warning: no image for %s" % ", ".join(missing_ids)
    return summary
