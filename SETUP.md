# Setup

The comprehensive teammate doc for Clueless: what each piece is, how to run it,
and what to do when something breaks. If you just want to get running, use the
quick start. If you're trying to understand a specific layer (data, agent, app),
jump to that section.

## Quick start

```bash
git clone <repo-url> clueless
cd clueless
./scripts/setup.sh
```

The script is idempotent — every step checks whether it's already done before
doing it, so re-running is always safe and fast on a second pass. It prints
next steps (creating the agent, starting the app, talking to it) when it's
done. Full step-by-step is in `scripts/setup.sh` itself; this doc explains
*why* each step exists and how to debug it when it doesn't.

## Prereqs

| Tool | Why | Check |
| --- | --- | --- |
| `python3` >= 3.9 | scripts, agent, CLI | `python3 --version` |
| `node` + `npm` | the Vite/React app | `node --version` |
| `curl` | dataset download | `curl --version` |
| `ANTHROPIC_API_KEY` | talking to the agent (`create_agent.py`, `clueless.py`) | in `.env` or exported |

On macOS: `brew install python3 node curl`. `setup.sh` checks all of these and
fails fast with the install hint above if any are missing.

## The data layer

Three pieces, in order:

**1. `scripts/download_polyvore.sh [--with-images]`** — pulls the Polyvore
Outfits dataset (a Hugging Face mirror, `Stylique/Polyvore`) into
`data/polyvore/` (git-ignored — see below). Without the flag it fetches
metadata + outfit splits + hard negatives (~250 MB): item metadata, category
mappings, and the disjoint/nondisjoint/hard-negative outfit splits used for
the compatibility and fill-in-blank tasks. `--with-images` additionally
downloads and unzips `images.zip` (~2.5 GB) into `data/polyvore/images/`, one
`<item_id>.jpg` per catalog item. Skips files that already exist (`[[ -s "$out" ]]`),
so it's safe to re-run to resume an interrupted download or to add images
later to a metadata-only checkout.

**2. `scripts/build_db.py`** — builds `data/clueless.db`, a local SQLite
database, from the downloaded JSON/CSV. Run after step 1. Tables:

| Table | Rows (full dataset) | Notes |
| --- | --- | --- |
| `items` | ~251k | one row per catalog item. Enrichment columns `colors`, `formality`, `pattern`, `silhouette`, `genre` exist in the schema but are **currently NULL for every row** — they're reserved for a future vision-model tagging pass, not filled yet. |
| `outfits` | ~68k | one row per human-curated outfit (the `nondisjoint` split: train+valid+test) |
| `outfit_items` | ~365k | which items make up each outfit — the "goes together" ground truth |
| `items_fts` | — | FTS5 full-text index over item name/title/description, backing `clueless-data search` |

**3. Why `data/` is git-ignored.** The full dataset is multi-GB (2.75 GB with
images, 14 GB more if you count the legacy HGLMM text features the download
script deliberately skips). It doesn't belong in git. `samples/` (committed,
~tens of KB) exists precisely so you can see real shapes without downloading
anything — see "Samples" below.

## The CLI: `scripts/clueless-data`

Read-only queries against `data/clueless.db`, JSON out, built for both humans
and agents. Requires the DB to exist (`scripts/build_db.py`). Every
subcommand below with a real invocation and real (truncated) output from this
dataset.

### `search <query> [--category CAT] [--limit N]`

Full-text search over name/title/description.

```
$ scripts/clueless-data search "denim jacket" --category outerwear --limit 2
[
  {
    "item_id": "100059862",
    "name": "denim jacket",
    "title": "",
    "category_id": 25,
    "fine_category": "parka",
    "semantic_category": "outerwear",
    "image_path": "data/polyvore/images/100059862.jpg",
    "colors": null,
    "formality": null,
    ...
  },
  { "item_id": "100062481", "name": "maurices medium sandblast denim jacket", ... }
]
```

### `item <item_id>`

One item plus every outfit it appears in.

```
$ scripts/clueless-data item 201813350
{
  "item_id": "201813350",
  "name": "prada printed leather clutch",
  "semantic_category": "bags",
  "image_path": "data/polyvore/images/201813350.jpg",
  "colors": null, "formality": null, "pattern": null, "silhouette": null, "genre": null,
  "outfit_ids": ["217602530"]
}
```

### `outfit <set_id>`

One curated outfit, items in position order.

```
$ scripts/clueless-data outfit 210750761
{
  "set_id": "210750761",
  "split": "train",
  "name": "parka time is now",
  "title": "Parkas",
  "items": [
    { "position": 1, "item_id": "154249722", "name": "bean scotch plaid shirt relaxed", "semantic_category": "tops", ... },
    { "position": 2, "item_id": "188425631", "name": "pre-owned watch in gold", "semantic_category": "jewellery", ... },
    ...
  ]
}
```

### `pairs-with <item_id> [--category CAT] [--limit N]`

Items that co-occur with the given item in curated outfits, ranked by shared
outfit count — the compatibility signal.

```
$ scripts/clueless-data pairs-with 201813350 --category shoes --limit 2
[
  {
    "item_id": "195213892",
    "name": "vetements black reflector-heel boots",
    "semantic_category": "shoes",
    "n_shared_outfits": 1
  }
]
```

### `random [--category CAT] [--limit N]`

Random sample of items, optionally filtered by category.

```
$ scripts/clueless-data random --category bottoms --limit 2
[
  { "item_id": "100859561", "name": "stella mccartney kravitz satin-twill wrap-effect", "semantic_category": "bottoms", ... },
  { "item_id": "161475115", "name": "abercrombie fitch chiffon sheer wide", "semantic_category": "bottoms", ... }
]
```

### `stats`

Row counts and a category breakdown — also what `setup.sh` uses to verify the
DB built correctly.

```
$ scripts/clueless-data stats
{
  "items": 251008,
  "outfits": 68306,
  "items_by_semantic_category": {
    "shoes": 44850, "jewellery": 41414, "bags": 40717, "tops": 32998,
    "bottoms": 27670, "all-body": 18478, "outerwear": 17065,
    "sunglasses": 9874, "accessories": 6973, "hats": 6071, "scarves": 4898
  },
  "enriched_items": 0
}
```

### `schema`

Full `CREATE TABLE` / `CREATE VIRTUAL TABLE` statements (including FTS5's
internal shadow tables). Use this to see exact column types before writing
`sql`.

```
$ scripts/clueless-data schema
CREATE TABLE items ( item_id TEXT PRIMARY KEY, name TEXT, title TEXT, ... )
CREATE TABLE outfits ( set_id TEXT PRIMARY KEY, split TEXT NOT NULL, ... )
CREATE TABLE outfit_items ( set_id TEXT NOT NULL REFERENCES outfits(set_id), ... )
CREATE INDEX idx_outfit_items_item ON outfit_items(item_id)
CREATE VIRTUAL TABLE items_fts USING fts5( item_id UNINDEXED, name, title, description )
```

### `sql <query>` (read-only escape hatch)

The DB connection opens `mode=ro`, so this can't write. Anything else goes.

```
$ scripts/clueless-data sql "SELECT semantic_category, COUNT(*) FROM items GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
[
  { "semantic_category": "shoes", "COUNT(*)": 44850 },
  { "semantic_category": "jewellery", "COUNT(*)": 41414 },
  { "semantic_category": "bags", "COUNT(*)": 40717 }
]
```

## The agent

**`create_agent.py`** — one-time provisioning: creates the Managed Agent
(model + system prompt + toolset), a cloud Environment (the container its
tools run in), and a Memory Store (where taste actually lives — see the main
[README](README.md) for the doctrine). Writes the three resulting IDs to
`.agent_id`, `.environment_id`, `.memory_store_id` — all git-ignored, because
they're workspace-specific. **Re-running it updates the agent in place**
(same IDs get overwritten with a new version); it does not create a second
agent, and the memory store's contents are untouched by re-running this
script — memory only changes via the agent's own reads/writes during a
session.

**`clueless.py`** — the interactive REPL. One persistent session per run,
with the memory store mounted `read_write` so you can watch it read/revise
what it believes about you in real time.

```
python3 clueless.py                  # persona defaults to priya
python3 clueless.py --persona dante  # any file in personas/*.md
python3 clueless.py --no-persona     # you answer the interview yourself
```

In-session commands: `/quit` (end the session — dumps current beliefs first)
and `/memory` (dump what it believes right now, without spending a turn).
Run it twice: the first run interviews and saves; the second run is the
demo — it picks up where it left off instead of re-interviewing.

A `--no-display` flag is being added in a parallel PR (branch
`feat/display-writer`) — it will suppress the tool-use/memory-write trace
printed during a turn for a cleaner terminal output. Not present in this
branch; check `clueless.py --help` once that PR lands.

**`inspect_memory.py`** — lists everything in the agent's memory store with
content previews (`--full` for complete content instead of a 400-char
preview). The demo helper: run it between `clueless.py` sessions to see what
got saved.

**`stretch_memory_curator.py`** — stretch goal: spins up a *second*, cheaper
agent whose only job is memory hygiene on the first agent's store (merge
duplicates, flag unresolved contradictions, prune stale/ephemeral entries).
Does not add new taste knowledge, only cleans.

## The app

A Vite + React app (`app/`) serving at **http://127.0.0.1:3001** (fixed via
`app/vite.config.js`, `strictPort: true` — chosen because 3000 and 5173 are
already in use by other local apps on the dev machine).

**`app/src/picks.json` is the entire app state.** There is no backend, no
API call, no build step to see a change — Claude (or you, by hand) rewrites
this file, Vite's dev server hot-reloads, and the page updates. The contract,
exactly (see `app/src/App.jsx` for the renderer):

```jsonc
{
  "updated": "<human-readable timestamp string>",
  "headline": "<one-line summary shown under the title>",
  "sections": [
    {
      "heading": "<section title, e.g. 'The recommendation'>",
      "blurb": "<optional prose under the heading>",
      "items": [
        {
          "item_id": "<string — MUST be a real catalog item_id>",
          "category": "<free-text label chip, e.g. 'shirt'>",
          "name": "<item name>",
          "reason": "<optional — why this item, shown under the name>"
        }
      ]
    }
  ]
}
```

`item_id` must be a real id from `data/clueless.db` (or `samples/polyvore/`)
because the page renders `<img src="/images/${item_id}.jpg">` directly — a
made-up id just renders a broken image. `app/public/images` is a symlink to
`../../data/polyvore/images`, which is why the dataset must be downloaded
with `--with-images` for the app to show actual photos (without it, the
symlink target doesn't exist and every image 404s — see Troubleshooting).

## Samples

`samples/` (committed, not git-ignored — a few tens of KB total) holds small
slices of all three datasets (Polyvore, Fashionpedia, Sanzo Wada) so you can
explore real shapes and start building **before downloading anything**.
Regenerate with `python3 scripts/make_samples.py`. Full field-by-field
documentation of what's in each subdirectory: [`samples/README.md`](samples/README.md).

## Troubleshooting

**`data/clueless.db` missing / `clueless-data stats` fails with "not found"**
Run `python3 scripts/build_db.py` — it needs `data/polyvore/polyvore_item_metadata.json`
to exist first (from `scripts/download_polyvore.sh`). `setup.sh` does both
steps in order and will tell you which one is missing.

**Broken `app/public/images` symlink / images show as broken in the browser**
The symlink target `data/polyvore/images/` doesn't exist yet — you downloaded
metadata only. Run `./scripts/download_polyvore.sh --with-images` (or just
re-run `./scripts/setup.sh`, which detects this exact case and re-downloads
with images).

**Port 3001 busy (`npm run dev` fails to bind)**
`app/vite.config.js` hard-codes `127.0.0.1:3001` with `strictPort: true` (no
auto-fallback to another port). Find and stop whatever's using it
(`lsof -i :3001`), or temporarily edit `vite.config.js`'s `port` if you need
a different one on your machine.

**`pip install -r requirements.txt` fails ("externally-managed-environment")**
This is PEP 668 — recent Python/Homebrew builds block global `pip install` by
default. Use a virtualenv:
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
`setup.sh` warns on this failure but does not stop — later steps (DB build,
CLI, app) don't need the Python deps, only `create_agent.py`/`clueless.py`
(the `anthropic` SDK) and `inspect_memory.py` do.

**Stale `.agent_id` (agent behaves unexpectedly / references an old version)**
`.agent_id`, `.environment_id`, `.memory_store_id` are git-ignored and
workspace-specific. Re-running `python3 create_agent.py` updates the agent
in place under the same ID — it does not create a new one and does not touch
the memory store's contents. If you genuinely want a fresh agent + fresh
memory, delete all three files and re-run `create_agent.py` (this abandons
the old memory store; there's no undo).

**`ANTHROPIC_API_KEY` not found**
Checked in this order by the scripts (via `python-dotenv`'s `load_dotenv()`,
which does not override an already-exported shell variable): an exported
shell environment variable first, then a `.env` file in the repo root
(git-ignored). Add one:
```
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```
`setup.sh` warns (does not fail) if neither is found, since the dataset/DB/app
steps don't need it — only `create_agent.py`, `clueless.py`, and
`inspect_memory.py` do.
