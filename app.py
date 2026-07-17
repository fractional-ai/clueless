"""
Cher's Closet — a minimal web UI for the Clueless taste agent.

This is a thin front end over clueless.py. It reuses the SAME managed agent,
memory store, closet, and kickoff the CLI uses (clueless.start_session /
build_kickoff / run_turn), so anything you do here shows up in the same memory
the CLI reads next time.

The loop, on one page:
  1. Pick who you're dressing (a persona, or "interview me") and meet the stylist.
  2. Chat with it — it suggests a look and explains it across the five dimensions.
  3. Rate the look 1-5 with optional written feedback. That reaction is sent back
     into the session, so the agent logs it and (if it conflicts with a belief)
     contests the belief and asks you the attribution question.
  4. Watch "what it believes about you" update live from the memory store.

Run it:
    pip install -r requirements.txt
    # put ANTHROPIC_API_KEY in .env, then:
    python create_agent.py        # once — provisions the agent + memory store
    python app.py                 # opens http://127.0.0.1:7860
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

import clueless

load_dotenv()

FEEDBACK_LOG = Path("feedback.jsonl")
INTERVIEW = "(interview me)"


def persona_choices() -> list[str]:
    personas = sorted(p.stem for p in clueless.PERSONAS_DIR.glob("*.md") if p.stem != "README")
    return [INTERVIEW, *personas]


def _client():
    from anthropic import Anthropic

    return Anthropic()


def _notes_md(notes: list[str]) -> str:
    if not notes:
        return ""
    return "\n\n<sub>📝 memory: " + " · ".join(notes) + "</sub>"


def _beliefs() -> str:
    """Live snapshot of the memory store for the 'what it believes' panel."""
    if not clueless.ids_present():
        return "_(agent not set up yet)_"
    try:
        _, _, memory_store_id = clueless.read_ids()
        return clueless.memory_snapshot(_client(), memory_store_id)
    except Exception as exc:  # never let the panel break the page
        return f"_(couldn't read memory: {exc})_"


def _thinking(text):
    return {"role": "assistant", "content": text}


def start(persona_choice):
    """Open a fresh session for the chosen persona and get the first look.

    A generator: it yields an instant 'thinking' bubble so the click gives
    immediate feedback, then yields the real first look. The whole body is
    guarded so any failure shows up in the chat instead of silently doing
    nothing.
    """
    if not clueless.ids_present():
        note = (
            "⚠️ The agent isn't provisioned yet. Run `python create_agent.py` "
            "(with `ANTHROPIC_API_KEY` set) first, then reload this page."
        )
        yield [_thinking(note)], None, _beliefs()
        return

    if not clueless.api_key_present():
        # Same check and wording the CLI uses; without it the SDK's own
        # "Could not resolve authentication method" surfaces instead.
        yield [_thinking(f"⚠️ {clueless.API_KEY_MISSING}")], None, gr.update()
        return

    yield (
        [_thinking("👗 Reading your memory and putting a first look together… "
                   "the first turn can take up to a minute.")],
        None,
        gr.update(),
    )

    try:
        persona = None if persona_choice == INTERVIEW else persona_choice
        client = _client()
        agent_id, environment_id, memory_store_id = clueless.read_ids()
        session = clueless.start_session(
            client, agent_id, environment_id, memory_store_id,
            title=f"Cher's Closet — {persona or 'interview'}",
        )
        persona_text = clueless.load_persona(persona)
        items, closet_src = clueless.load_closet(persona or "none")
        using_fallback = "FALLBACK" in closet_src
        kickoff = clueless.build_kickoff(persona_text, items, using_fallback)

        text_parts, notes = clueless.run_turn(client, session.id, kickoff)
        reply = "".join(text_parts).strip() or "_(no reply)_"
        yield [{"role": "assistant", "content": reply + _notes_md(notes)}], session.id, _beliefs()
    except Exception as exc:
        yield [{"role": "assistant", "content": f"⚠️ Something went wrong: {exc}"}], None, gr.update()


def send(message, history, session_id):
    message = (message or "").strip()
    if not message:
        yield history, session_id, "", gr.update()
        return
    if session_id is None:
        yield (
            history + [_thinking("Pick who I'm dressing above and hit **Meet my stylist** first. 💁")],
            session_id, "", gr.update(),
        )
        return

    base = history + [{"role": "user", "content": message}]
    yield base + [_thinking("💭 thinking…")], session_id, "", gr.update()

    try:
        text_parts, notes = clueless.run_turn(_client(), session_id, message)
        reply = "".join(text_parts).strip() or "_(no reply)_"
    except Exception as exc:
        reply, notes = f"⚠️ {exc}", []

    yield base + [{"role": "assistant", "content": reply + _notes_md(notes)}], session_id, "", _beliefs()


def rate(score, comment, history, session_id):
    if session_id is None:
        yield history, "", gr.update()
        return

    score = int(score)
    comment = (comment or "").strip()
    reaction = f"score: {score}" + (f', text: "{comment}"' if comment else "")

    base = history + [{"role": "user", "content": f"⭐ {reaction}"}]
    yield base + [_thinking("💭 logging that and thinking…")], "", gr.update()

    prompt = (
        f"Here's my reaction to that outfit — {reaction}. Log it to "
        "observations.jsonl. If it conflicts with something you already believe, "
        "contest the belief rather than flipping it, and ask me the one attribution "
        "question that would tell you which dimension actually drove the reaction."
    )
    try:
        text_parts, notes = clueless.run_turn(_client(), session_id, prompt)
    except Exception as exc:
        # The agent never got the reaction, so don't log it: a local record that
        # claims a rating the agent never saw is worse than no record at all.
        failed = (f"⚠️ {exc}\n\nYour rating didn't reach the stylist, so nothing was "
                  "logged. Try sending it again.")
        yield base + [{"role": "assistant", "content": failed}], "", gr.update()
        return

    # Delivered — only now is a local record honest. Secondary to the agent's memory.
    with FEEDBACK_LOG.open("a") as f:
        f.write(json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "score": score, "text": comment}
        ) + "\n")

    reply = "".join(text_parts).strip() or "_(no reply)_"
    yield base + [{"role": "assistant", "content": reply + _notes_md(notes)}], "", _beliefs()


CUTE_THEME = gr.themes.Soft(
    primary_hue="pink",
    secondary_hue="yellow",
    neutral_hue="rose",
    radius_size="lg",
    font=[gr.themes.GoogleFont("Poppins"), "sans-serif"],
)

CUTE_CSS = """
.gradio-container { background: linear-gradient(135deg, #fff0f6 0%, #fffbe6 100%) !important; }
#card {
    max-width: 720px; margin: 0 auto;
    background: rgba(255, 255, 255, 0.72); border-radius: 26px;
    padding: 10px 26px 26px; box-shadow: 0 10px 34px rgba(214, 51, 105, 0.16);
}
#title { text-align: center; }
#title h1 { color: #d6336c; margin-bottom: 2px; }
#start_btn {
    background: linear-gradient(90deg, #ff8fab, #ffd670) !important;
    color: #5a2a3a !important; font-weight: 600 !important; border: none !important;
}
"""

with gr.Blocks(title="Cher's Closet") as demo:
    with gr.Column(elem_id="card"):
        gr.Markdown(
            "# 💛 Cher's Closet\n"
            "#### *Ugh, as if you'd wear nothing cute.* Meet your stylist, get a look, rate it. ✨",
            elem_id="title",
        )

        session_id = gr.State(None)

        with gr.Row():
            persona_dd = gr.Dropdown(
                persona_choices(), value=INTERVIEW, label="Who am I dressing? 💁", scale=3
            )
            start_btn = gr.Button("Meet my stylist 💖", variant="primary", elem_id="start_btn", scale=1)

        chat = gr.Chatbot(height=380, label="Your stylist 👗")

        with gr.Row():
            msg = gr.Textbox(
                scale=4, label="Say something 💬", container=True,
                placeholder="tell it what you wear, ask for another look, or answer its question...",
            )
            send_btn = gr.Button("Send", scale=1)

        gr.Markdown("**Rate the last look:**")
        with gr.Row():
            score = gr.Slider(1, 5, value=3, step=1, label="1 = as if · 5 = obsessed 💅", scale=3)
            rate_btn = gr.Button("Send rating 💌", scale=1)
        comment = gr.Textbox(
            label="Spill the tea 🫖 (optional)", placeholder="e.g. pretty good, but a little generic"
        )

        with gr.Accordion("🧠 What it believes about you", open=False):
            beliefs = gr.Markdown("_(start a session to see this)_")

    start_btn.click(start, [persona_dd], [chat, session_id, beliefs])
    send_btn.click(send, [msg, chat, session_id], [chat, session_id, msg, beliefs])
    msg.submit(send, [msg, chat, session_id], [chat, session_id, msg, beliefs])
    rate_btn.click(rate, [score, comment, chat, session_id], [chat, comment, beliefs])


if __name__ == "__main__":
    demo.launch(theme=CUTE_THEME, css=CUTE_CSS)
