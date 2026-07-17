"""Tests for the real agent path — start() / send() / rate() end to end.

tests/test_ui_smoke.py forces the "agent not provisioned" branch on purpose, so
nothing there ever runs start_session / run_turn / memory_snapshot. These tests do.
Only the Anthropic transport is faked: no network, no API key, no provisioned
agent, no browser.

The fake is deliberately faithful. Its methods mirror the SDK's real signatures
(keyword-only exactly where the SDK is keyword-only) and it emits the SDK's real
event models, validated by pydantic on construction — so if the SDK's session
event shape or argument names drift, these tests fail instead of production.

What's asserted is what the product depends on:
  - rating a look sends the reaction into the SAME session as a user message,
    i.e. the taste-feedback loop closes through the agent, not just feedback.jsonl
  - the memory store mounts read_write
  - a rating the agent never received is NOT written to feedback.jsonl

Run:
    pytest tests/test_agent_path.py
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from anthropic.types.beta.sessions import (
    BetaManagedAgentsAgentMessageEvent,
    BetaManagedAgentsAgentToolUseEvent,
    BetaManagedAgentsSessionErrorEvent,
    BetaManagedAgentsSessionStatusIdleEvent,
    BetaManagedAgentsTextBlock,
)

import app
import clueless

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------- real SDK event builders

def text_event(text):
    return BetaManagedAgentsAgentMessageEvent(
        id="evt_msg", type="agent.message", processed_at=NOW,
        content=[BetaManagedAgentsTextBlock(type="text", text=text)],
    )


def tool_use_event(name, path):
    return BetaManagedAgentsAgentToolUseEvent(
        id="evt_tool", type="agent.tool_use", processed_at=NOW,
        name=name, input={"path": path},
    )


def idle_event(stop_reason="end_turn", **extra):
    return BetaManagedAgentsSessionStatusIdleEvent(
        id="evt_idle", type="session.status_idle", processed_at=NOW,
        stop_reason={"type": stop_reason, **extra},
    )


def error_event(message):
    return BetaManagedAgentsSessionErrorEvent(
        id="evt_err", type="session.error", processed_at=NOW,
        error={"type": "unknown_error", "message": message},
    )


def a_reply(_message):
    """Default turn: some prose plus a memory write, then end_turn."""
    return [
        text_event("The camel coat over the cream trouser — the colours agree."),
        tool_use_event("str_replace_editor", "/mnt/memory/taste/observations.jsonl"),
        idle_event(),
    ]


# ------------------------------------------------------------- the fake transport

class FakeStream:
    """Stands in for anthropic.Stream: a context manager you iterate for events."""

    def __init__(self, buffer):
        self._buffer = buffer

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._buffer)


class FakeEvents:
    def __init__(self, fake):
        self._fake = fake
        self._open = None

    def stream(self, session_id, **kwargs):
        # The real stream only delivers events emitted after it opens, which is why
        # run_turn opens it BEFORE sending. Model that: send() fills this buffer.
        self._open = []
        self._fake.streamed.append(session_id)
        return FakeStream(self._open)

    def send(self, session_id, *, events):
        if self._open is None:
            raise AssertionError("send() before stream() — the turn's start is missed")
        self._fake.sent.append((session_id, events))
        message = events[0]["content"][0]["text"]
        self._open.extend(self._fake.responder(message))
        return SimpleNamespace(id="send_fake")


class FakeSessions:
    def __init__(self, fake):
        self._fake = fake
        self.events = FakeEvents(fake)

    def create(self, *, agent, environment_id, title=None, resources=None, **kwargs):
        self._fake.created.append(
            {"agent": agent, "environment_id": environment_id,
             "title": title, "resources": resources or []}
        )
        return SimpleNamespace(id="sess_fake")


class FakeMemories:
    def __init__(self, fake):
        self._fake = fake

    def list(self, memory_store_id, *, path_prefix=None, **kwargs):
        return SimpleNamespace(data=list(self._fake.memories))

    def retrieve(self, memory_id, *, memory_store_id, **kwargs):
        content = next(m.content for m in self._fake.memories if m.id == memory_id)
        return SimpleNamespace(content=content)


class FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic covering the 8 methods the repo calls."""

    def __init__(self, responder=a_reply, memories=()):
        self.responder = responder
        self.memories = list(memories)
        self.created = []    # kwargs of every sessions.create
        self.sent = []       # (session_id, events) of every events.send
        self.streamed = []   # session_id of every events.stream
        self.uploaded = []
        self.beta = SimpleNamespace(
            sessions=FakeSessions(self),
            memory_stores=SimpleNamespace(memories=FakeMemories(self)),
            files=SimpleNamespace(upload=self._upload),
        )

    def _upload(self, *, file):
        self.uploaded.append(file[0])
        return SimpleNamespace(id="file_fake")


def memory(path, content):
    return SimpleNamespace(id=f"mem_{path}", path=path, type="memory", content=content)


# -------------------------------------------------------------------- the wiring

@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A provisioned-looking app whose only fake is the transport."""
    client = FakeAnthropic()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(app, "_client", lambda: client)
    monkeypatch.setattr(app.clueless, "ids_present", lambda: True)
    monkeypatch.setattr(
        app.clueless, "read_ids", lambda: ("agent_fake", "env_fake", "mem_store_fake")
    )
    monkeypatch.setattr(app, "FEEDBACK_LOG", tmp_path / "feedback.jsonl")
    return client


def last(gen):
    """Drain a Gradio generator handler and return its final yield."""
    return list(gen)[-1]


def user_texts(client):
    """The text of every user.message the UI sent, per turn."""
    return [events[0]["content"][0]["text"] for _, events in client.sent]


# --------------------------------------------------------------------- the tests

def test_start_mounts_memory_read_write(fake):
    """The whole product rests on the store being writable by the agent."""
    last(app.start("priya"))

    assert len(fake.created) == 1
    session = fake.created[0]
    assert session["agent"] == "agent_fake"
    assert session["environment_id"] == "env_fake"

    store = next(r for r in session["resources"] if r["type"] == "memory_store")
    assert store["memory_store_id"] == "mem_store_fake"
    assert store["access"] == "read_write"
    assert store["instructions"] == clueless.MEMORY_INSTRUCTIONS

    # Wada's colours ride along as a mounted file.
    assert fake.uploaded == ["colors.json"]
    assert any(r.get("mount_path") == "/workspace/colors.json" for r in session["resources"])


def test_start_sends_the_kickoff_and_returns_the_reply(fake):
    chat, session_id, _ = last(app.start("priya"))

    assert session_id == "sess_fake"
    assert len(fake.sent) == 1
    kickoff = user_texts(fake)[0]
    assert "/mnt/memory/" in kickoff          # read memory first
    assert "=== THE CLOSET" in kickoff        # the closet went with it
    assert "camel coat" in chat[-1]["content"]
    assert "memory:" in chat[-1]["content"]   # the memory write was surfaced


def test_rate_sends_the_reaction_into_the_same_session(fake):
    """The taste loop must close through the AGENT, not just feedback.jsonl."""
    _, session_id, _ = last(app.start("priya"))
    fake.sent.clear()

    last(app.rate(2, "too much pattern", [], session_id))

    assert len(fake.sent) == 1, "the rating must reach the agent in exactly one turn"
    rated_session, events = fake.sent[0]
    assert rated_session == session_id == "sess_fake"  # SAME session as start()
    assert fake.streamed[-1] == session_id
    assert events[0]["type"] == "user.message"

    prompt = user_texts(fake)[0]
    assert "score: 2" in prompt
    assert "too much pattern" in prompt
    assert "observations.jsonl" in prompt
    assert "contest the belief" in prompt


def test_rate_logs_feedback_once_delivered(fake):
    last(app.rate(5, "obsessed", [], "sess_fake"))

    rows = [json.loads(line) for line in app.FEEDBACK_LOG.read_text().splitlines()]
    assert rows == [{"ts": rows[0]["ts"], "score": 5, "text": "obsessed"}]


def test_rate_does_not_log_when_the_agent_never_got_it(fake):
    """DEFECT-2: the local log must not claim a rating the agent never received."""
    fake.responder = lambda _m: [error_event("agent unavailable")]

    chat, _, _ = last(app.rate(5, "obsessed", [], "sess_fake"))

    assert not app.FEEDBACK_LOG.exists(), "an undelivered rating was logged as delivered"
    assert "agent unavailable" in chat[-1]["content"]   # failure is visible, not swallowed
    assert "nothing was logged" in chat[-1]["content"]


def test_rate_needs_a_session_before_it_touches_anything(fake):
    last(app.rate(3, "nice", [], None))

    assert fake.sent == []
    assert not app.FEEDBACK_LOG.exists()


def test_send_continues_the_same_session(fake):
    _, session_id, _ = last(app.start("priya"))
    fake.sent.clear()

    chat, returned_id, _, _ = last(app.send("what about the boots?", [], session_id))

    assert returned_id == session_id
    assert fake.sent[0][0] == session_id
    assert user_texts(fake) == ["what about the boots?"]
    assert "camel coat" in chat[-1]["content"]


def test_send_surfaces_a_failed_turn(fake):
    fake.responder = lambda _m: [error_event("model overloaded")]

    chat, _, _, _ = last(app.send("hi", [], "sess_fake"))

    assert "model overloaded" in chat[-1]["content"]


def test_run_turn_keeps_going_through_a_requires_action_idle(fake):
    """Idle is transient — it also fires while the agent waits on a tool. Only a
    non-requires_action stop_reason ends the turn."""
    fake.responder = lambda _m: [
        text_event("thinking about it… "),
        idle_event("requires_action", event_ids=["evt_1"]),
        text_event("the camel coat."),
        idle_event(),
    ]

    text_parts, _ = clueless.run_turn(fake, "sess_fake", "hi")

    assert "".join(text_parts) == "thinking about it… the camel coat."


def test_start_without_an_api_key_says_so(fake, monkeypatch):
    """DEFECT-3: not the SDK's 'Could not resolve authentication method'."""
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    chat, session_id, _ = last(app.start("priya"))

    assert session_id is None
    assert clueless.API_KEY_MISSING in chat[-1]["content"]
    assert "ANTHROPIC_API_KEY" in chat[-1]["content"]
    assert fake.created == [], "no session should be opened without a key"


def test_memory_snapshot_renders_the_store(fake):
    fake.memories = [
        memory("/taste/beliefs.md", "loves neutrals (confidence: high)"),
        memory("/taste/observations.jsonl", '{"reaction": "too much pattern"}'),
    ]

    snapshot = clueless.memory_snapshot(fake, "mem_store_fake")

    assert "/taste/beliefs.md" in snapshot
    assert "loves neutrals" in snapshot
    assert "too much pattern" in snapshot


def test_memory_snapshot_when_empty(fake):
    assert "empty" in clueless.memory_snapshot(fake, "mem_store_fake")
