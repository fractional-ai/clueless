"""
Setup for Clueless. Provisions the three things it needs:
  1. A Managed Agent (model + system prompt + toolset)
  2. A cloud Environment (the container tools run in)
  3. A Memory Store — where taste lives, and the only reason this works

The memory store mounts at /mnt/memory/ inside the session container. The agent
reads and writes it with normal file tools. It persists across sessions.

IDs are saved to .agent_id, .environment_id, .memory_store_id (all git-ignored)
so clueless.py can pick them up.

Re-running this script is how you change the agent's behavior: if .agent_id
already exists, it re-provisions the agent IN PLACE via agents.update (a new
immutable version — sessions pinned to the old version keep working) instead
of creating a second agent. The environment and memory store are created once
and never re-created; their ids never change once .environment_id /
.memory_store_id exist.

Usage:
    # put ANTHROPIC_API_KEY in .env
    python create_agent.py
"""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv


MODEL = "claude-opus-4-8"

# Reference library of GENERIC fashion priors — 251k items, 68k human-curated
# outfits (Polyvore). Co-occurrence evidence, swap candidates, id verification.
# It knows nothing about this person's taste; that lives only in memory.
QUERY_CATALOG_TOOL = {
    "type": "custom",
    "name": "query_catalog",
    "description": (
        "Query the local fashion catalog: 251k items and 68k human-curated "
        "outfits. This is GENERIC fashion knowledge — co-occurrence evidence "
        "(what commonly gets worn together), swap candidates for an item, and "
        "item-id verification. It contains ZERO information about what THIS "
        "person likes. Say out loud when you're leaning on it, so it's never "
        "confused with what you've learned about them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": [
                    "search",
                    "item",
                    "outfit",
                    "pairs-with",
                    "random",
                    "stats",
                    "schema",
                    "sql",
                ],
                "description": "Which catalog query to run.",
            },
            "query": {
                "type": "string",
                "description": (
                    "Free-text search terms for `search`, or a read-only SELECT "
                    "statement for `sql`."
                ),
            },
            "item_id": {
                "type": "string",
                "description": "Item id — required for `item` and `pairs-with`.",
            },
            "set_id": {
                "type": "string",
                "description": "Outfit (set) id — required for `outfit`.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional semantic_category filter, e.g. tops, bottoms, "
                    "shoes, bags. Supported by search/pairs-with/random."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Optional max rows to return.",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}

# Updates the live page the user is looking at, in the room, right now.
PRESENT_PICKS_TOOL = {
    "type": "custom",
    "name": "present_picks",
    "description": (
        "Update the live page the user is looking at with your current outfit "
        "pick(s). This is a WHOLESALE REPLACEMENT of what's on the page, not "
        "an addition. Call it at least once per suggestion turn — keep your "
        "reasoning, questions, and back-and-forth in chat; put the actual "
        "outfit on the page. Every item_id MUST be a real id from the catalog "
        "or the person's closet: the page renders each item as "
        "/images/<item_id>.jpg, so a made-up id shows a broken image. Do not "
        "include an `updated` field — the host stamps that itself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "One-line summary of the pick(s), shown at the top of the page.",
            },
            "sections": {
                "type": "array",
                "description": "Wholesale replacement of the page's sections.",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "blurb": {
                            "type": "string",
                            "description": "Optional short explanation for this section.",
                        },
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item_id": {
                                        "type": "string",
                                        "description": (
                                            "Real catalog/closet id. Rendered as "
                                            "/images/<item_id>.jpg."
                                        ),
                                    },
                                    "category": {"type": "string"},
                                    "name": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["item_id", "name"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["heading", "items"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["headline", "sections"],
        "additionalProperties": False,
    },
}

TOOLS = [
    {"type": "agent_toolset_20260401"},
    QUERY_CATALOG_TOOL,
    PRESENT_PICKS_TOOL,
]

# The doctrine. This is the product.
#
# Note how hard this pushes AGAINST the usual memory-agent instruction ("when new
# info contradicts old memory, update it and trust the newer version"). That rule
# is right for a policy wiki: a May 15 access policy really does void the April
# one. It is wrong for taste. Taste accumulates; it does not supersede.
SYSTEM_PROMPT = """\
You are Clueless. You learn one person's taste in clothes and you dress them.

Your loop: interview -> knowledge -> suggest -> discuss -> revise. The loop is the
product. Every turn should move it forward.

# The five dimensions

These are your vocabulary. Use them for BOTH jobs:
  1. Explaining WHY an outfit works (or doesn't).
  2. Attributing a reaction to a CAUSE when the user tells you how it landed.

  - color harmony    — do the colors agree? (you have Sanzo Wada's combination
                       groups as a reference for what "agrees" means generically)
  - formality match  — does the outfit's formality match the occasion?
  - silhouette balance — do the shapes work together and on this person?
  - pattern load     — is there too much going on? too little?
  - genre coherence  — do the pieces belong to the same world?

Always name the dimensions you reasoned over. "This works" is useless; "the camel
and cream agree, and the tailored trouser balances the boxy top" is the product.

# Generic priors vs personal taste — do not confuse them

You have reference data (color-combination groups, attribute vocabularies, a
catalog of curated outfits). That data encodes what goes together IN GENERAL. It
contains ZERO information about what THIS person likes. Not one label.

Personal taste lives in exactly one place: your memory store. When a generic prior
and your memory disagree, memory wins and the disagreement is worth saying out
loud — that tension is the most interesting thing you can show the user.

# Memory protocol (mandatory)

Your memory store is mounted at /mnt/memory/ and survives across sessions. Layout:

    /mnt/memory/
      taste/observations.jsonl  APPEND-ONLY. Never edit or delete a line.
      taste/beliefs.md          Derived beliefs. Freely rewritable.
      taste/open-questions.md   Contested beliefs awaiting the user's answer.
      outfits/log.md            What you suggested, why, and how it landed.

1. At the START of every session, list and read /mnt/memory/ before anything else.
   If it's empty, you've never met this person — interview them.

2. observations.jsonl is APPEND-ONLY and this is not negotiable. Each line:
     {"turn": <int>, "outfit": [<item ids>], "reaction": "<their words, verbatim>",
      "attributed_to": [<dimension(s)>] or null, "note": "<optional>"}
   Their reactions are the ONLY real labels you will ever have. Never rewrite one,
   never "clean up" their wording, never delete one because it turned out to be
   misleading. A misleading observation is still data.

3. beliefs.md is DERIVED from the log — a reading of it. Because it's
   reconstructible, rewriting it costs nothing. Each belief carries:
     - the claim, stated in the five-dimension vocabulary where possible
     - confidence: low | medium | high
     - evidence: how many observations support it, and which
     - contested: (only if applicable) what contradicts it and what you asked
   Keep it short. A belief you can't tie to an observation is a guess — label it.

4. Do NOT memorize: the catalog (it's on disk), one-off logistics, or your own
   prose. Memorize what you learned about the PERSON.

# The contradiction rule (the most important thing you do)

When feedback conflicts with a belief you hold at confidence >= medium backed by
>= 3 observations:

  DO NOT revise the belief. Do not flip it. Do not quietly soften it.

  Instead:
    a. Append the observation. Always. It's data.
    b. Mark the belief `contested:` in beliefs.md, keeping the original claim.
    c. Ask ONE disambiguating question, and write it to open-questions.md.

  Your question's job is ATTRIBUTION: finding which of the five dimensions
  actually caused the reaction. A reaction almost never means what it literally
  says. "I hated the beige one" is not evidence against beige. It could be the
  color against their skin, the fabric, the silhouette, or the occasion. Find out
  which. That conversation IS the feature — it is not a fallback, and it is not a
  failure to have to ask.

One data point does not overturn a pattern. Ten might. Say which you think it is.

# Your tools: closet, catalog, display

Three sources, three jobs — do not mix them up:

  - closet (pasted into your kickoff message) — the actual items you may dress
    this person in. This is the only inventory you may suggest FROM.
  - query_catalog — a reference library of generic fashion priors (251k items,
    68k human-curated outfits). Co-occurrence evidence, swap candidates, id
    verification. It knows nothing about this person's taste — say out loud
    when you're leaning on it, so it never gets confused with what you've
    learned about them.
  - present_picks — updates the page the user is LOOKING AT, right now, in the
    room. Call it at least once per suggestion turn, and only with real
    catalog/closet item_ids (fake ids render as broken images). It's a
    wholesale replacement, not an addition. Keep your reasoning, questions,
    and back-and-forth in chat; put the actual outfit on the page.

# How to talk

- Lead with the outcome. If you revised a belief or you're contesting one, say
  that first and say why.
- When you rely on memory, cite it: "you told me X, and I've seen it three times."
- When you're contesting, be honest that you're not convinced yet.
- Be concise. Name dimensions, not vibes. Don't hedge, don't flatter.
"""


def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "No ANTHROPIC_API_KEY found. Put it in .env (see README) or export it."
        )

    client = Anthropic()

    # 1. The agent. First run: create it. Every run after: re-provision the
    #    SAME agent in place — a new immutable version, not a new agent.
    #    agents.update requires the current version (optimistic lock), so
    #    fetch it via retrieve first.
    agent_id_path = Path(".agent_id")
    if agent_id_path.exists():
        agent_id = agent_id_path.read_text().strip()
        current = client.beta.agents.retrieve(agent_id)
        agent = client.beta.agents.update(
            agent_id,
            version=current.version,
            name="Clueless",
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
        )
        print(f"Agent re-provisioned: {agent.id}  (v{agent.version}, {MODEL})")
    else:
        agent = client.beta.agents.create(
            name="Clueless",
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            metadata={"project": "clueless", "surface": "taste-loop"},
        )
        agent_id_path.write_text(agent.id)
        print(f"Agent created:        {agent.id}  (v{agent.version}, {MODEL})")

    # 2. The environment — the container the agent's tools run in. Created
    #    once; skip if we already have one on disk.
    environment_id_path = Path(".environment_id")
    if environment_id_path.exists():
        environment_id = environment_id_path.read_text().strip()
        print(f"Environment (existing): {environment_id}")
    else:
        environment = client.beta.environments.create(
            name="clueless-env",
            config={"type": "cloud", "networking": {"type": "unrestricted"}},
        )
        environment_id_path.write_text(environment.id)
        environment_id = environment.id
        print(f"Environment created:  {environment_id}")

    # 3. The memory store. Created once — its id never changes. This
    #    description is read BY THE AGENT — write it for the model, not for
    #    humans. It restates the doctrine on purpose: it's the one piece of
    #    guidance that travels with the store itself.
    memory_store_id_path = Path(".memory_store_id")
    if memory_store_id_path.exists():
        memory_store_id = memory_store_id_path.read_text().strip()
        print(f"Memory store (existing): {memory_store_id}")
    else:
        memory_store = client.beta.memory_stores.create(
            name="Clueless — taste",
            description=(
                "One person's taste in clothes, learned across sessions. "
                "taste/observations.jsonl is an APPEND-ONLY log of their reactions — "
                "these are the only real labels that exist and must never be edited "
                "or deleted. taste/beliefs.md holds beliefs DERIVED from that log, "
                "each with confidence and evidence; it is freely rewritable. "
                "Taste ACCUMULATES — it does not supersede. Newer feedback does NOT "
                "override an established belief; it is one more data point. When they "
                "conflict, mark the belief contested and ask which dimension actually "
                "drove the reaction. Do not resolve a contradiction by trusting the "
                "newer thing."
            ),
            metadata={"project": "clueless"},
        )
        memory_store_id_path.write_text(memory_store.id)
        memory_store_id = memory_store.id
        print(f"Memory store created: {memory_store_id}")

    print("\nSetup complete. IDs in .agent_id / .environment_id / .memory_store_id")
    print(f"  Console:  https://platform.claude.com/memory-stores/{memory_store_id}")
    print("\nNext:  python clueless.py")


if __name__ == "__main__":
    main()
