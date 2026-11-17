"""Phase 5 unit tests."""

# ---------- Phase 5: client mentions ----------

from assistant.kb.internal import Source, uncovered_client_note


def _src(client):
    return Source(1, None, "t", None, client, None, 0.9, "x")  # type: ignore[arg-type]


def test_uncovered_client_note():
    assert uncovered_client_note(["Halcyon Manufacturing Group"], [_src(None)]) is not None
    assert (
        uncovered_client_note(
            ["Halcyon Manufacturing Group"], [_src("Halcyon Manufacturing Group")]
        )
        is None
    )
    assert uncovered_client_note([], [_src(None)]) is None


def test_send_and_post_tools_are_only_offered_when_the_person_asks():
    # Eval finding: asked to *draft* a proposal, a small model reached for send_email.
    from assistant.tools import ToolRegistry
    from assistant.tools.builtin import default_tools

    reg = ToolRegistry(default_tools())

    def names(text):
        return {s.name for s in reg.specs_for("internal", user_text=text)}

    assert not {"send_email", "notify_team"} & names("Draft a proposal for Brightwater")
    assert "send_email" in names("Please send it to grace@brightwater.example.com")
    assert "notify_team" in names("Post this to the team channel")
    assert "get_current_time" in names("Draft a proposal")


async def test_ollama_output_is_capped_by_default():
    import httpx

    from assistant.llm.openai_compat import OpenAICompatProvider

    seen = {}

    def handler(req):
        import json

        seen.update(json.loads(req.content))
        msg = {"role": "assistant", "content": "ok"}
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}]})

    p = OpenAICompatProvider(
        name="ollama",
        base_url="http://x/v1",
        model="m",
        default_max_tokens=2000,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    from assistant.llm.base import LLMMessage

    await p.chat([LLMMessage(role="user", content="hi")])
    assert seen["max_tokens"] == 2000
