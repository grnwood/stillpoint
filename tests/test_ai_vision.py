from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy

import httpx

from sp.app.ui.ai_api import is_unsupported_vision_response, with_vision_images
from sp.app.ui.ai_chat_panel import ApiWorker
from sp.app.ui.agent_tool_loop import AgentLoopConfig, AgentToolChatWorker


DATA_URL = "data:image/png;base64,aGVsbG8="


def response(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body, request=httpx.Request("POST", "http://model/v1/chat/completions"))


def test_vision_parts_are_added_only_to_latest_user_message() -> None:
    messages = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "describe this"},
    ]
    result = with_vision_images(messages, [DATA_URL])
    assert messages[-1]["content"] == "describe this"
    assert result[0]["content"] == "earlier"
    assert result[-1]["content"] == [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": DATA_URL}},
    ]


def test_only_image_rejection_triggers_ocr_retry() -> None:
    assert is_unsupported_vision_response(response(400, "model does not support image input"))
    assert is_unsupported_vision_response(response(422, "message content must be a string"))
    assert not is_unsupported_vision_response(response(401, "invalid API key"))
    assert not is_unsupported_vision_response(response(500, "server failed"))


def test_streaming_chat_retries_with_ocr_when_image_is_rejected(monkeypatch) -> None:
    payloads: list[dict] = []

    @contextmanager
    def fake_stream(_method, _url, *, json, **_kwargs):
        payloads.append(json)
        if len(payloads) == 1:
            yield response(400, "image input unsupported")
        else:
            yield response(200, 'data: {"choices":[{"delta":{"content":"OCR answer"}}]}\n\ndata: [DONE]\n')

    monkeypatch.setattr(httpx, "stream", fake_stream)
    worker = ApiWorker(
        {"base_url": "http://model"},
        [{"role": "system", "content": "vision"}, {"role": "user", "content": "describe"}],
        "model", vision_images=[DATA_URL],
        ocr_fallback_messages=[{"role": "system", "content": "OCR text"}, {"role": "user", "content": "describe"}],
    )
    finished: list[str] = []
    failed: list[str] = []
    fallbacks: list[bool] = []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.visionFallback.connect(lambda: fallbacks.append(True))
    worker.run()

    assert not failed
    assert fallbacks == [True]
    assert finished == ["OCR answer"]
    assert isinstance(payloads[0]["messages"][-1]["content"], list)
    assert payloads[1]["messages"][0]["content"] == "OCR text"
    assert payloads[1]["messages"][-1]["content"] == "describe"


def test_streaming_chat_uses_vision_when_model_accepts_image(monkeypatch) -> None:
    payloads: list[dict] = []

    @contextmanager
    def fake_stream(_method, _url, *, json, **_kwargs):
        payloads.append(json)
        yield response(200, 'data: {"choices":[{"delta":{"content":"I see a chart"}}]}\n\ndata: [DONE]\n')

    monkeypatch.setattr(httpx, "stream", fake_stream)
    worker = ApiWorker(
        {"base_url": "http://model"},
        [{"role": "system", "content": "vision"}, {"role": "user", "content": "describe"}],
        "model", vision_images=[DATA_URL],
        ocr_fallback_messages=[{"role": "system", "content": "OCR text"}, {"role": "user", "content": "describe"}],
    )
    finished: list[str] = []
    fallbacks: list[bool] = []
    worker.finished.connect(finished.append)
    worker.visionFallback.connect(lambda: fallbacks.append(True))
    worker.run()

    assert finished == ["I see a chart"]
    assert not fallbacks
    assert len(payloads) == 1
    assert payloads[0]["messages"][-1]["content"][1]["image_url"]["url"] == DATA_URL


def test_agent_chat_retries_with_ocr_and_keeps_text_for_next_step(monkeypatch) -> None:
    payloads: list[dict] = []

    def fake_post(_url, *, json, **_kwargs):
        payloads.append(deepcopy(json))
        if len(payloads) == 1:
            return response(400, "vision not supported")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "done"}}]},
            request=httpx.Request("POST", "http://model/v1/chat/completions"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with httpx.Client() as client:
        worker = AgentToolChatWorker(
            config=AgentLoopConfig({"base_url": "http://model"}, "model", "vision prompt"),
            client=client, user_prompt="describe", context={},
            vision_images=[DATA_URL], ocr_fallback_system_prompt="OCR prompt",
        )
        messages = with_vision_images([
            {"role": "system", "content": "vision prompt"},
            {"role": "user", "content": "describe"},
        ], [DATA_URL])
        assert worker._send_llm(messages) == "done"
        assert worker._send_llm(messages) == "done"

    assert len(payloads) == 3
    assert isinstance(payloads[0]["messages"][1]["content"], list)
    assert payloads[1]["messages"] == [
        {"role": "system", "content": "OCR prompt"},
        {"role": "user", "content": "describe"},
    ]
    assert payloads[2]["messages"] == payloads[1]["messages"]
