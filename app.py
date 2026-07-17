"""
Minimal UI for the Clueless outfit-picker agent.

Three things, one page:
  1. Enter your fashion preferences.
  2. See the managed agent's outfit recommendation.
  3. Rate it (1-5) with optional written feedback — which is logged and sent
     back to the agent so its memory of your taste improves.

Run it:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY="sk-ant-..."   # your hackathon workspace key
    python app.py                           # opens http://127.0.0.1:7860

If the agent hasn't been created yet (no .agent_id / .environment_id /
.memory_store_id — see create_agent.py), the app still runs in DEMO MODE so
you can click through the UI; it just returns a placeholder outfit.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr

FEEDBACK_LOG = Path("feedback.jsonl")
AGENT_FILES = (".agent_id", ".environment_id", ".memory_store_id")


def agent_configured() -> bool:
    """True once create_agent.py has provisioned the managed agent."""
    return all(Path(f).exists() for f in AGENT_FILES)


def ask_managed_agent(message: str, session_id: str | None = None):
    """Send a message to the managed agent, return (reply_text, session_id).

    A fresh session is created (with the memory store attached read-write) when
    session_id is None; pass an existing id to continue the same conversation.
    """
    from anthropic import Anthropic

    client = Anthropic()
    if session_id is None:
        session = client.beta.sessions.create(
            agent=Path(".agent_id").read_text().strip(),
            environment_id=Path(".environment_id").read_text().strip(),
            title="Outfit picker UI",
            resources=[
                {
                    "type": "memory_store",
                    "memory_store_id": Path(".memory_store_id").read_text().strip(),
                    "access": "read_write",
                    "instructions": (
                        "The user's clothing taste and their feedback on outfits. "
                        "Check it before suggesting; update it after feedback."
                    ),
                }
            ],
        )
        session_id = session.id

    parts: list[str] = []
    # Stream-first: open the stream, then send, so we don't miss early events.
    with client.beta.sessions.events.stream(session_id) as stream:
        client.beta.sessions.events.send(
            session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        parts.append(block.text)
            elif event.type == "session.status_idle":
                break

    return "".join(parts).strip(), session_id


def demo_stub(preferences: str) -> str:
    return (
        "**Demo mode** — the managed agent isn't set up yet, so here's a placeholder.\n\n"
        f"Based on *“{preferences}”*, an outfit might be: a relaxed neutral top, "
        "comfortable bottoms in a color you mentioned, and simple shoes.\n\n"
        "_Run `python create_agent.py` (with your API key set) to get real, "
        "memory-backed recommendations._"
    )


def get_recommendation(preferences: str, session_id: str | None):
    preferences = (preferences or "").strip()
    if not preferences:
        return "Enter your fashion preferences above, then click **Get an outfit**.", session_id

    if not agent_configured():
        return demo_stub(preferences), None

    prompt = (
        "Here are my fashion preferences:\n"
        f"{preferences}\n\n"
        "Suggest ONE specific outfit for me. Be concrete about the pieces, colors, "
        "and briefly why it fits my taste."
    )
    try:
        reply, sid = ask_managed_agent(prompt)
        return (reply or "_(the agent returned no text)_"), sid
    except Exception as exc:  # keep the demo alive even if the agent call fails
        return (
            f"⚠️ Couldn't reach the agent: `{exc}`\n\n"
            "Check that `ANTHROPIC_API_KEY` is set to your hackathon workspace key "
            "and that `create_agent.py` has been run.",
            None,
        )


def submit_feedback(preferences, recommendation, score, text, session_id):
    if not (recommendation or "").strip():
        return "Get an outfit first, then rate it."

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "preferences": preferences,
        "recommendation": recommendation,
        "score": int(score),
        "text": (text or "").strip(),
    }
    with FEEDBACK_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")

    # Send the feedback back into the same session so the agent updates its memory.
    if session_id and agent_configured():
        comment = f' Comment: "{record["text"]}"' if record["text"] else ""
        try:
            ask_managed_agent(
                f"Feedback on that outfit — I rate it {record['score']}/5.{comment} "
                "Please update what you remember about my taste accordingly.",
                session_id=session_id,
            )
        except Exception:
            pass  # feedback is already saved to disk; don't fail the UI

    return f"✅ Saved your feedback (score {record['score']}/5). Thanks!"


with gr.Blocks(title="Clueless Outfit Picker") as demo:
    gr.Markdown("# 👗 Clueless Outfit Picker\nEnter your taste → get an outfit → rate it.")

    session_id = gr.State(None)

    preferences = gr.Textbox(
        label="Your fashion preferences",
        lines=4,
        placeholder="e.g. I like linen and natural colors, loose comfortable fits, no heels...",
    )
    get_btn = gr.Button("Get an outfit", variant="primary")

    recommendation = gr.Markdown(label="Suggested outfit")

    score = gr.Slider(1, 5, value=3, step=1, label="How much do you like it? (1 = no, 5 = love it)")
    feedback_text = gr.Textbox(
        label="Optional comments",
        lines=2,
        placeholder='e.g. pretty good, but a little generic',
    )
    submit_btn = gr.Button("Submit feedback")
    status = gr.Markdown()

    get_btn.click(get_recommendation, [preferences, session_id], [recommendation, session_id])
    submit_btn.click(
        submit_feedback,
        [preferences, recommendation, score, feedback_text, session_id],
        [status],
    )


if __name__ == "__main__":
    demo.launch()
