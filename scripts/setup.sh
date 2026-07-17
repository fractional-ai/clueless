#!/usr/bin/env bash
# Clueless — one-shot teammate setup. Idempotent: safe to re-run any time;
# every step checks "is this already done?" before doing it.
#
# Usage: ./scripts/setup.sh
#
# See SETUP.md for the full walkthrough (what each step does, troubleshooting).
set -euo pipefail
cd "$(dirname "$0")/.."

BOLD='\033[1m'
YEL='\033[33m'
RED='\033[31m'
GRN='\033[32m'
RST='\033[0m'

info()  { printf "%b\n" "${BOLD}==>${RST} $1"; }
warn()  { printf "%b\n" "${YEL}warning:${RST} $1"; }
fail()  { printf "%b\n" "${RED}error:${RST} $1" >&2; exit 1; }

# ---------------------------------------------------------------- a. prereqs
info "Checking prerequisites"

missing=()
for bin in python3 node npm curl; do
  command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
if [ "${#missing[@]}" -gt 0 ]; then
  fail "Missing required tools: ${missing[*]}
  Install hints (Homebrew):
    brew install python3
    brew install node          # provides node + npm
    brew install curl"
fi

py_ok=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')
if [ "$py_ok" != "1" ]; then
  fail "python3 >= 3.9 required, found $(python3 --version 2>&1)."
fi
echo "  python3 $(python3 --version 2>&1 | awk '{print $2}'), node $(node --version), npm $(npm --version) — ok"

# ------------------------------------------------------------- b. pip deps
info "Installing Python dependencies (requirements.txt)"
if python3 -m pip install -r requirements.txt >/tmp/clueless-pip.log 2>&1; then
  echo "  pip install ok"
else
  warn "pip install failed (see /tmp/clueless-pip.log). This is often PEP 668
  ('externally-managed-environment') on newer Python/Homebrew installs. Fix with
  a virtualenv, then re-run this script:
    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
  (pipx is for installing standalone CLI tools, not project requirements — a venv
  is the right fix here.)
  Continuing setup — later steps may still work if deps are already present."
fi

# ----------------------------------------------------------- c. dataset
info "Checking Polyvore dataset"
METADATA="data/polyvore/polyvore_item_metadata.json"
IMAGES_DIR="data/polyvore/images"

if [ -f "$METADATA" ]; then
  echo "  metadata present: $METADATA — skip"
else
  warn "Dataset not found. Downloading ~2.75 GB (metadata + outfit splits + images) — this can take a while."
  ./scripts/download_polyvore.sh --with-images
fi

if [ -f "$METADATA" ] && [ ! -d "$IMAGES_DIR" ]; then
  warn "Metadata present but images/ missing (prior metadata-only download). Fetching images (~2.5 GB)."
  ./scripts/download_polyvore.sh --with-images
fi

# ------------------------------------------------------------------- d. db
info "Checking local database"
if [ -f "data/clueless.db" ]; then
  echo "  data/clueless.db present — skip"
else
  python3 scripts/build_db.py
fi

# --------------------------------------------------------------- e. verify
info "Verifying database"
if ! stats=$(scripts/clueless-data stats 2>&1); then
  fail "scripts/clueless-data stats failed:
$stats
  See SETUP.md → Troubleshooting → 'db missing / stats fails'."
fi
items=$(printf '%s' "$stats" | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"])' 2>/dev/null || echo "?")
outfits=$(printf '%s' "$stats" | python3 -c 'import json,sys; print(json.load(sys.stdin)["outfits"])' 2>/dev/null || echo "?")
echo "  items: $items, outfits: $outfits"

# -------------------------------------------------------------------- f. app
info "Checking app (Vite/React)"
if [ -d "app/node_modules" ]; then
  echo "  app/node_modules present — skip"
else
  (cd app && npm install)
fi

if [ -e "app/public/images" ]; then
  echo "  app/public/images symlink resolves — ok"
else
  warn "app/public/images is missing or a broken symlink. This means the Polyvore
  images haven't been downloaded (data/polyvore/images/). Re-run:
    ./scripts/download_polyvore.sh --with-images"
fi

# -------------------------------------------------------------- g. api key
info "Checking ANTHROPIC_API_KEY"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "  ANTHROPIC_API_KEY set in environment — ok"
elif [ -f .env ] && grep -q '^ANTHROPIC_API_KEY=' .env; then
  echo "  ANTHROPIC_API_KEY found in .env — ok"
else
  warn "No ANTHROPIC_API_KEY found. Add it to .env (git-ignored):
    echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
  or export it in your shell. The agent scripts will fail without it."
fi

# ------------------------------------------------------------ h. next steps
printf "\n%b\n" "${GRN}${BOLD}Setup complete.${RST}"
cat <<'EOF'

Next steps:
  1. python3 create_agent.py        # one-time: creates the agent + memory store
                                     # (re-running updates the agent in place;
                                     #  memory is preserved)
  2. cd app && npm run dev          # http://127.0.0.1:3001
  3. python3 clueless.py --persona priya

Full walkthrough, CLI reference, and troubleshooting: SETUP.md
EOF
