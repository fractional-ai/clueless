#!/usr/bin/env python3
"""Build data/clueless.db (SQLite) from the downloaded Polyvore data.

Prereq: scripts/download_polyvore.sh has run (metadata; --with-images optional).
Run:    python3 scripts/build_db.py

Tables:
  items        one row per catalog item; enrichment columns (colors, formality,
               pattern, silhouette, genre) are NULL until a vision pass fills them
  outfits      one row per human-curated outfit (nondisjoint split)
  outfit_items which items make up each outfit — the "goes together" ground truth
  items_fts    FTS5 full-text index over item names/descriptions
"""
import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "polyvore"
DB = ROOT / "data" / "clueless.db"

SCHEMA = """
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS outfits;
DROP TABLE IF EXISTS outfit_items;
DROP TABLE IF EXISTS items_fts;

CREATE TABLE items (
    item_id           TEXT PRIMARY KEY,
    name              TEXT,             -- url_name: the reliable text field
    title             TEXT,
    description       TEXT,
    category_id       INTEGER,
    fine_category     TEXT,             -- from categories.csv, e.g. "dress"
    semantic_category TEXT,             -- tops/bottoms/shoes/bags/...
    image_path        TEXT,             -- repo-relative, NULL if not downloaded
    -- enrichment columns: filled by a vision-model pass, NULL until then
    colors            TEXT,             -- JSON array of dominant colors
    formality         INTEGER,          -- 1 (beach) .. 5 (black tie)
    pattern           TEXT,             -- solid/stripe/floral/...
    silhouette        TEXT,             -- fitted/loose/...
    genre             TEXT              -- preppy/streetwear/...
);

CREATE TABLE outfits (
    set_id   TEXT PRIMARY KEY,
    split    TEXT NOT NULL,             -- train/valid/test (nondisjoint)
    name     TEXT,                      -- url_name of the outfit
    title    TEXT
);

CREATE TABLE outfit_items (
    set_id   TEXT NOT NULL REFERENCES outfits(set_id),
    position INTEGER NOT NULL,          -- 1-based index within the outfit
    item_id  TEXT NOT NULL REFERENCES items(item_id),
    PRIMARY KEY (set_id, position)
);
CREATE INDEX idx_outfit_items_item ON outfit_items(item_id);

CREATE VIRTUAL TABLE items_fts USING fts5(
    item_id UNINDEXED, name, title, description
);
"""


def main():
    metadata = json.loads((SRC / "polyvore_item_metadata.json").read_text())
    titles = json.loads((SRC / "polyvore_outfit_titles.json").read_text())
    categories = {}
    with open(SRC / "categories.csv") as f:
        for cat_id, fine, semantic in csv.reader(f):
            categories[cat_id] = (fine, semantic)

    images_dir = SRC / "images"
    have_images = images_dir.is_dir()

    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    item_rows = []
    for item_id, m in metadata.items():
        cat_id = m.get("category_id", "")
        fine, _ = categories.get(cat_id, (None, None))
        img = f"data/polyvore/images/{item_id}.jpg"
        if have_images and not (images_dir / f"{item_id}.jpg").exists():
            img = None
        item_rows.append((
            item_id, m.get("url_name"), m.get("title"), m.get("description"),
            int(cat_id) if cat_id else None, fine, m.get("semantic_category"), img,
        ))
    con.executemany(
        "INSERT INTO items (item_id, name, title, description, category_id,"
        " fine_category, semantic_category, image_path) VALUES (?,?,?,?,?,?,?,?)",
        item_rows,
    )
    con.executemany(
        "INSERT INTO items_fts (item_id, name, title, description)"
        " SELECT item_id, name, title, description FROM items WHERE item_id=?",
        [(r[0],) for r in item_rows],
    )

    n_outfits = n_links = 0
    for split in ("train", "valid", "test"):
        outfits = json.loads((SRC / "nondisjoint" / f"{split}.json").read_text())
        for o in outfits:
            t = titles.get(o["set_id"], {})
            con.execute(
                "INSERT OR IGNORE INTO outfits (set_id, split, name, title) VALUES (?,?,?,?)",
                (o["set_id"], split, t.get("url_name"), t.get("title")),
            )
            for it in o["items"]:
                con.execute(
                    "INSERT OR REPLACE INTO outfit_items (set_id, position, item_id) VALUES (?,?,?)",
                    (o["set_id"], it["index"], it["item_id"]),
                )
                n_links += 1
            n_outfits += 1

    con.commit()
    con.execute("VACUUM")
    con.close()
    size = DB.stat().st_size / 1e6
    print(f"{DB}: {len(item_rows)} items, {n_outfits} outfits, {n_links} links, {size:.0f} MB")


if __name__ == "__main__":
    main()
