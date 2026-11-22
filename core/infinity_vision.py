"""Explicit secondary image requests for public plugin image capabilities."""

from __future__ import annotations

from typing import Any

import httpx

from telyincore import protocol


def _answer(kind: str, payload: dict[str, Any]) -> str:
    if kind == protocol.KIND_CHAT:
        choices = payload.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(str(part.get("text") or "") for part in content
                             if isinstance(part, dict)).strip()
    blocks = payload.get("output") if kind == protocol.KIND_RESPONSES else payload.get("content")
    if kind == protocol.KIND_RESPONSES and isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    texts: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            texts.append(block["text"])
        for part in block.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts).strip()


def inspect(engine: Any, profile_name: str, image_url: str, question: str,
            *, client: httpx.Client | None = None, cancelled=None) -> str:
    """Called only by explicit plugin image capabilities, never by the main image path."""
    def _check_cancelled():
        if cancelled is not None:
            from telyincore.vision_owned import check_cancelled
            check_cancelled(cancelled)
    _check_cancelled()
    profile = engine.profile(profile_name)
    if profile.multimodal is not True or not profile.model:
        raise ValueError("所选档案没有启用图片输入或尚未选模型")
    if not image_url.startswith("data:image/") or ";base64," not in image_url:
        raise ValueError("需要已缓存的图片数据")
    kind = profile.kind
    body = protocol.shape(kind, {
        "model": profile.model,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": question},
            {"type": "input_image", "image_url": image_url},
        ]}],
        "max_output_tokens": profile.max_tokens,
    }, base_url=profile.base_url, multimodal=True,
       thinking=getattr(profile, "thinking", None),
       temperature=getattr(profile, "temperature", None),
       reasoning_effort=getattr(profile, "reasoning_effort", None))
    headers: dict[str, str] = {}
    secret_name = str(profile.secret or "").strip()
    if secret_name:
        secret = str(engine._read_secret_lines().get(secret_name, "")).strip()
        if not secret:
            raise ValueError("所选视觉档案缺少密钥")
        if kind == protocol.KIND_ANTHROPIC:
            headers = {"x-api-key": secret,
                       "anthropic-version": protocol.ANTHROPIC_VERSION}
        else:
            headers = {"Authorization": "Bearer " + secret}
    _check_cancelled()
    owns = client is None
    connection = client or httpx.Client(timeout=60.0, trust_env=False)
    try:
        _check_cancelled()
        response = connection.post(profile.base_url.rstrip("/") + protocol.endpoint_for(kind),
                                   json=body, headers=headers)
        _check_cancelled()
        response.raise_for_status()
        result = response.json()
    finally:
        if owns:
            connection.close()
    _check_cancelled()
    answer = _answer(kind, result if isinstance(result, dict) else {})
    if not answer:
        raise RuntimeError("视觉模型没有返回可读文字")
    _check_cancelled()
    return answer
