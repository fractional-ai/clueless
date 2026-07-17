import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from display import display_available, write_picks  # noqa: E402


VALID_PICKS = {
    "headline": "Your outfit",
    "sections": [
        {
            "heading": "The recommendation",
            "blurb": "Sharp and simple.",
            "items": [
                {"item_id": "111", "category": "shirt", "name": "white oxford", "reason": "crisp"},
                {"item_id": "222", "category": "pants", "name": "black trousers", "reason": "anchor"},
            ],
        },
        {
            "heading": "Swaps",
            "items": [
                {"item_id": "333", "category": "shoes", "name": "cap-toe"},
            ],
        },
    ],
}


def make_repo(tmp_path, with_app=True, with_images=None):
    """Build a fake repo root. `with_images` is an iterable of item_ids to
    create <id>.jpg for under data/polyvore/images/ (omit to skip data/ dir)."""
    if with_app:
        (tmp_path / "app" / "src").mkdir(parents=True)
    if with_images is not None:
        images_dir = tmp_path / "data" / "polyvore" / "images"
        images_dir.mkdir(parents=True)
        for item_id in with_images:
            (images_dir / ("%s.jpg" % item_id)).write_bytes(b"fake")
    return tmp_path


def test_display_available_true_when_app_src_exists(tmp_path):
    make_repo(tmp_path)
    assert display_available(tmp_path) is True


def test_display_available_false_when_app_missing(tmp_path):
    assert display_available(tmp_path) is False


def test_write_picks_success_stamps_and_round_trips(tmp_path):
    repo = make_repo(tmp_path, with_images=["111", "222", "333"])

    summary = write_picks(VALID_PICKS, repo_root=repo)

    assert "display updated: 2 sections, 3 items" in summary
    assert "warning" not in summary

    picks_path = repo / "app" / "src" / "picks.json"
    assert picks_path.exists()

    written = json.loads(picks_path.read_text())
    assert written["headline"] == "Your outfit"
    assert len(written["sections"]) == 2
    assert written["sections"][0]["items"][0]["item_id"] == "111"
    # Matches the existing file's "YYYY-MM-DD H:MM AM/PM" format.
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2} (AM|PM)$", written["updated"])


def test_write_picks_does_not_mutate_input(tmp_path):
    repo = make_repo(tmp_path, with_images=["111", "222", "333"])
    picks = json.loads(json.dumps(VALID_PICKS))  # deep copy

    write_picks(picks, repo_root=repo)

    assert "updated" not in picks


def test_write_picks_is_atomic_no_tmp_file_left_behind(tmp_path):
    repo = make_repo(tmp_path, with_images=["111", "222", "333"])

    write_picks(VALID_PICKS, repo_root=repo)

    tmp_leftover = repo / "app" / "src" / "picks.json.tmp"
    assert not tmp_leftover.exists()
    assert (repo / "app" / "src" / "picks.json").exists()


def test_write_picks_missing_app_is_graceful_noop(tmp_path):
    repo = make_repo(tmp_path, with_app=False)

    summary = write_picks(VALID_PICKS, repo_root=repo)

    assert summary == "display not available (app/ missing or --no-display); nothing written"
    assert not (repo / "app").exists()


def test_write_picks_disabled_is_graceful_noop(tmp_path):
    repo = make_repo(tmp_path)

    summary = write_picks(VALID_PICKS, repo_root=repo, enabled=False)

    assert summary == "display not available (app/ missing or --no-display); nothing written"
    assert not (repo / "app" / "src" / "picks.json").exists()


@pytest.mark.parametrize(
    "bad_picks",
    [
        "not a dict",
        {"sections": []},  # missing headline
        {"headline": "x", "sections": "not a list"},
        {"headline": "x", "sections": [{"items": []}]},  # section missing heading
        {"headline": "x", "sections": [{"heading": "h", "items": "not a list"}]},
        {"headline": "x", "sections": [{"heading": "h", "items": [{"item_id": "1"}]}]},  # item missing name
        {"headline": "x", "sections": [{"heading": "h", "items": ["not a dict"]}]},
    ],
)
def test_write_picks_raises_valueerror_on_invalid_shape(tmp_path, bad_picks):
    repo = make_repo(tmp_path)

    with pytest.raises(ValueError):
        write_picks(bad_picks, repo_root=repo)

    assert not (repo / "app" / "src" / "picks.json").exists()


def test_write_picks_warns_on_missing_images(tmp_path):
    repo = make_repo(tmp_path, with_images=["111"])  # 222, 333 missing

    summary = write_picks(VALID_PICKS, repo_root=repo)

    assert "warning: no image for" in summary
    assert "222" in summary
    assert "333" in summary
    assert "111" not in summary.split("warning")[-1]


def test_write_picks_skips_image_check_when_data_dir_absent(tmp_path):
    repo = make_repo(tmp_path)  # no data/ dir at all

    summary = write_picks(VALID_PICKS, repo_root=repo)

    assert "warning" not in summary
