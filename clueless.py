"""
Clueless — the multi-turn agent.

One persistent session with the memory store attached read_write. You talk, it
suggests, you react, it revises what it believes. Memory writes are surfaced
inline so you can watch it think about you.

Run it twice. The second run is the point: it reads what it learned the first time.

Usage:
    python clueless.py                  # defaults to the priya persona
    python clueless.py --persona dante
    python clueless.py --no-persona     # you answer the interview yourself
    python clueless.py --no-display     # skip present_picks writes to the live page

    /quit    end the session
    /memory  dump what it currently believes (without spending a turn)

The agent has two custom tools alongside the built-in toolset:
  - query_catalog  -> shells out to scripts/clueless-data (read-only dataset CLI)
  - present_picks  -> guarded call into display.write_picks() (repo root, owned
                       by the parallel feat/display-writer branch); a no-op with
                       --no-display or if display.py isn't present yet.
"""

# System python here is 3.9; defer annotation evaluation so `str | None` and
# `tuple[list[dict], str]` parse. Drop this if the floor moves to 3.10+.
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

# display.py lives at the repo root and is owned by a parallel PR
# (feat/display-writer). Guard the import: if it isn't there yet (or the
# branch isn't merged), present_picks degrades to a no-op instead of crashing.
try:
    from display import write_picks  # type: ignore
except ImportError:
    write_picks = None  # type: ignore


PERSONAS_DIR = Path("personas")
CLOSETS_DIR = Path("closets")  # teammate's, may not exist yet
POLYVORE = Path("samples/polyvore")
WADA = Path("samples/sanzo-wada/colors.json")
SCRIPTS_DIR = Path("scripts")

REQUIRED_IDS = (".agent_id", ".environment_id", ".memory_store_id")

NO_DISPLAY_MESSAGE = (
    "display unavailable (display.py not found — is feat/display-writer merged?)"
)
CATALOG_TRUNCATE_AT = 30000
CATALOG_TRUNCATE_SUFFIX = "\n...[truncated — narrow the query or lower --limit]"

# query_catalog command -> the tool_input field that becomes its positional argv arg.
CATALOG_POSITIONAL_FIELD = {
    "search": "query",
    "item": "item_id",
    "outfit": "set_id",
    "pairs-with": "item_id",
    "sql": "query",
}


# ---------------------------------------------------------------- closet loading

def load_closet(persona: str) -> tuple[list[dict], str]:
    """The closet the agent suggests FROM.

    Prefers closets/<persona>.json (the agreed contract). Falls back to deriving
    one from the committed Polyvore sample so this runs today — with the caveat
    that those 15 items are random womenswear and won't match any persona's taste.
    """
    contract = CLOSETS_DIR / f"{persona}.json"
    if contract.exists():
        items = json.loads(contract.read_text())["items"]
        return items, f"closets/{persona}.json ({len(items)} items)"

    items_path = POLYVORE / "items_sample.json"
    if not items_path.exists():
        raise SystemExit(f"No closet: neither {contract} nor {items_path} exists.")

    raw = json.loads(items_path.read_text())
    items = [
        {
            "id": item_id,
            "name": meta.get("title") or meta.get("url_name") or item_id,
            "category": meta.get("semantic_category", "unknown"),
            "description": meta.get("description", ""),
            "image": f"{POLYVORE}/images/{item_id}.jpg",
        }
        for item_id, meta in raw.items()
    ]
    return items, f"{items_path} ({len(items)} items — FALLBACK, see warning above)"


def load_persona(persona: str | None) -> str | None:
    if persona is None:
        return None
    path = PERSONAS_DIR / f"{persona}.md"
    if not path.exists():
        available = sorted(p.stem for p in PERSONAS_DIR.glob("*.md") if p.stem != "README")
        raise SystemExit(f"No persona {path}. Available: {', '.join(available)}")
    return path.read_text()


# ------------------------------------------------------------------ the kickoff

def build_kickoff(persona_text: str | None, items: list[dict], using_fallback: bool) -> str:
    parts = [
        "Your memory store is at /mnt/memory/. Read it FIRST, before anything else.",
        "If it's empty you've never met this person — interview them (and save what",
        "you learn). If it isn't, tell me what you already believe about them and",
        "how confident you are, then pick up where you left off.",
        "",
        "Sanzo Wada's colour-combination reference is mounted at /workspace/colors.json",
        "— 157 colours, each with `combinations` (ids of groups it belongs to). Two",
        "colours sharing a combination id 'agree' GENERICALLY. It says nothing about",
        "what this person likes. Use it to explain colour harmony, never as taste.",
        "",
        "=== THE CLOSET (suggest only from these items) ===",
        json.dumps(items, indent=2),
    ]

    if using_fallback:
        parts += [
            "",
            "NOTE: this closet is a random dataset sample, not this person's real",
            "wardrobe — it may clash badly with their stated taste. If nothing here",
            "can dress them, SAY SO plainly rather than forcing a bad suggestion.",
            "That mismatch is real information; log it.",
        ]

    if persona_text:
        parts += [
            "",
            "=== INTERVIEW TRANSCRIPT (this is the person) ===",
            persona_text,
            "",
            "Treat the transcript as what they told you — knowledge to save, not",
            "ground truth to parrot. Note their 'taste shift' section is a LATER",
            "development: do not assume it yet. Wait for them to raise it.",
        ]
    else:
        parts += [
            "",
            "No transcript — you haven't met this person. Interview them. Ask about",
            "what they wear, what they'd never wear, and what they own. One question",
            "at a time; don't interrogate.",
        ]

    parts += [
        "",
        "Then suggest one outfit and explain it across the five dimensions.",
        "Before you finish this turn, write what's worth keeping to /mnt/memory/.",
    ]
    return "\n".join(parts)


# ----------------------------------------------------------------- custom tools

def build_catalog_argv(tool_input: dict) -> list:
    """Build the argv for scripts/clueless-data from a query_catalog call.

    Pure and side-effect-free (no subprocess) so it's testable in isolation.
    Raises ValueError with a tool_result-ready message on a missing required
    field.
    """
    command = tool_input.get("command")
    if not command:
        raise ValueError("missing required field command")

    argv = [sys.executable, str(SCRIPTS_DIR / "clueless-data"), command]

    positional_field = CATALOG_POSITIONAL_FIELD.get(command)
    if positional_field:
        value = tool_input.get(positional_field)
        if not value:
            raise ValueError(f"missing required field {positional_field} for {command}")
        argv.append(str(value))

    if tool_input.get("category"):
        argv += ["--category", str(tool_input["category"])]
    if tool_input.get("limit") is not None:
        argv += ["--limit", str(tool_input["limit"])]

    return argv


def run_custom_tool(name: str, tool_input: dict, display_enabled: bool) -> tuple:
    """Execute one custom tool call. Returns (result_text, is_error)."""
    if name == "query_catalog":
        try:
            argv = build_catalog_argv(tool_input)
        except ValueError as e:
            return str(e), True

        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return "query timed out after 60s", True

        if proc.returncode != 0:
            return (proc.stderr or "").strip() or "query failed", True

        stdout = proc.stdout or ""
        if len(stdout) > CATALOG_TRUNCATE_AT:
            stdout = stdout[:CATALOG_TRUNCATE_AT] + CATALOG_TRUNCATE_SUFFIX
        return stdout, False

    if name == "present_picks":
        if write_picks is None:
            return NO_DISPLAY_MESSAGE, False
        try:
            return write_picks(tool_input, enabled=display_enabled), False
        except ValueError as e:
            return str(e), True

    return f"unknown tool {name}", True


# --------------------------------------------------------------------- the loop

def drain(
    client: Anthropic,
    session_id: str,
    message: str,
    display_enabled: bool = True,
) -> None:
    """Send one message and stream until the agent genuinely stops.

    Stream-first, then send: the stream only delivers events emitted after it
    opens, so opening it second means missing the start of the turn.

    Custom tool calls (agent.custom_tool_use) are collected as they arrive.
    When the session goes idle with stop_reason.type == "requires_action",
    every pending call named in stop_reason.event_ids (falling back to all
    pending calls if that list is absent) is executed via run_custom_tool(),
    the results are batched into ONE events.send, and we keep draining the
    SAME stream — a custom tool call does not end the turn.
    """
    with client.beta.sessions.events.stream(session_id) as stream:
        client.beta.sessions.events.send(
            session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
        )
        pending = {}  # event.id -> (name, input)

        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        print(block.text, end="", flush=True)

            elif event.type == "agent.custom_tool_use":
                name = getattr(event, "name", "?")
                inp = getattr(event, "input", {}) or {}
                pending[event.id] = (name, inp)
                print(f"\n  \033[2m[tool: {name}]\033[0m", flush=True)

            elif event.type == "agent.tool_use":
                name = getattr(event, "name", "?")
                inp = getattr(event, "input", {}) or {}
                target = str(
                    inp.get("path") or inp.get("file_path") or inp.get("command") or ""
                )
                # Memory writes are the demo — make them impossible to miss.
                if "/mnt/memory" in target:
                    print(f"\n  \033[36m[memory: {name} {target}]\033[0m", flush=True)
                else:
                    print(f"\n  \033[90m[{name}]\033[0m", flush=True)

            elif event.type == "session.error":
                msg = getattr(getattr(event, "error", None), "message", event)
                print(f"\n\033[31m[session error] {msg}\033[0m", flush=True)

            elif event.type == "session.status_terminated":
                print("\n\033[31m[session terminated]\033[0m")
                raise SystemExit(1)

            elif event.type == "session.status_idle":
                # Idle is transient — it also fires while the agent waits on US.
                # Only a non-requires_action stop_reason means the turn is over.
                stop = getattr(event, "stop_reason", None)
                if getattr(stop, "type", None) == "requires_action":
                    event_ids = getattr(stop, "event_ids", None) or list(pending.keys())
                    results = []
                    for event_id in event_ids:
                        if event_id not in pending:
                            continue  # unknown id — skip it
                        name, tool_input = pending.pop(event_id)
                        result_text, is_error = run_custom_tool(
                            name, tool_input, display_enabled
                        )
                        results.append(
                            {
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": event_id,
                                "content": [{"type": "text", "text": result_text}],
                                "is_error": is_error,
                            }
                        )
                    if results:
                        client.beta.sessions.events.send(session_id, events=results)
                    continue
                print()
                return


def dump_memory(client: Anthropic, store_id: str) -> None:
    page = client.beta.memory_stores.memories.list(store_id, path_prefix="/")
    items = sorted(page.data, key=lambda i: i.path)
    if not items:
        print("  (memory is empty)")
        return
    for item in items:
        if item.type != "memory":
            continue
        got = client.beta.memory_stores.memories.retrieve(item.id, memory_store_id=store_id)
        print(f"\n\033[36m--- {item.path} ---\033[0m")
        print(got.content or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="priya", help="persona file stem, e.g. priya")
    ap.add_argument("--no-persona", action="store_true", help="interview interactively")
    ap.add_argument(
        "--no-display",
        action="store_true",
        help="disable present_picks writes to the live page",
    )
    args = ap.parse_args()
    display_enabled = not args.no_display

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("No ANTHROPIC_API_KEY found. Put it in .env or export it.")
    missing = [f for f in REQUIRED_IDS if not Path(f).exists()]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)}. Run: python create_agent.py")

    agent_id = Path(".agent_id").read_text().strip()
    environment_id = Path(".environment_id").read_text().strip()
    memory_store_id = Path(".memory_store_id").read_text().strip()

    persona = None if args.no_persona else args.persona
    persona_text = load_persona(persona)
    items, closet_src = load_closet(persona or "none")
    using_fallback = "FALLBACK" in closet_src

    client = Anthropic()

    print(f"closet:  {closet_src}")
    if using_fallback:
        print(
            "\033[33m  warning: no closets/ contract yet — using the Polyvore sample.\n"
            "  Those 15 items are random womenswear and won't match the persona.\033[0m"
        )
    print(f"persona: {persona or '(none — interactive interview)'}")
    print(f"memory:  {memory_store_id}")

    # Mount Wada's colours so the agent can read them with its own tools rather
    # than us pasting 85KB into every kickoff.
    with open(WADA, "rb") as f:
        colors_file = client.beta.files.upload(file=("colors.json", f, "application/json"))

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title=f"Clueless — {persona or 'interactive'}",
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": memory_store_id,
                "access": "read_write",
                "instructions": (
                    "This person's taste, learned across sessions. Read it before "
                    "anything else. observations.jsonl is append-only — never edit "
                    "a line. beliefs.md is derived and rewritable. Taste "
                    "accumulates; newer feedback does not supersede an established "
                    "belief. On a conflict, mark the belief contested and ask which "
                    "dimension actually drove the reaction."
                ),
            },
            {"type": "file", "file_id": colors_file.id, "mount_path": "/workspace/colors.json"},
        ],
    )
    print(f"session: {session.id}")
    print(f"trace:   https://platform.claude.com/sessions/{session.id}\n")

    drain(
        client,
        session.id,
        build_kickoff(persona_text, items, using_fallback),
        display_enabled=display_enabled,
    )

    while True:
        try:
            said = input("\n\033[1myou >\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            said = "/quit"

        if not said:
            continue
        if said in ("/quit", "/q", "/exit"):
            print("\nWhat it believes about you now:")
            dump_memory(client, memory_store_id)
            print(f"\nSession {session.id} left open — run again to pick it back up.")
            return
        if said == "/memory":
            dump_memory(client, memory_store_id)
            continue

        print()
        drain(client, session.id, said, display_enabled=display_enabled)


if __name__ == "__main__":
    main()
