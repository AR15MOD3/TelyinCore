"""A single observation through the Bot's pinned multimodal model and transport."""
from dataclasses import replace

from telyincore.media_budget import fit_images_to_context
from telyincore.protocol import ChatTransport


async def observe(running, image, prompt, generate, *, frames=(), pinned=None):
    pinned = pinned if pinned is not None else running._branch_config
    request = pinned.request_snapshot()
    if not request.values.get('multimodal'):
        raise ValueError('当前模型档案未启用视觉输入')
    base = request.config
    cfg = replace(base, tools=(), retry_delays_ms=(), max_tokens=min(base.max_tokens, 1024),
                  thinking=False, reasoning_budget_tokens=None)
    messages = [{'role': 'user', 'content': [{'type':'input_text','text':prompt},
                                            {'type':'input_image','image_url':image}]}]
    fitted = fit_images_to_context(messages, cfg)
    if not any(p['type'] == 'input_image' for p in fitted[0]['content']):
        raise ValueError('当前视觉请求可用上下文不足以放入一张预览')
    if frames:
        # One request, no speculative inference. Separate frames retain more pixels
        # for an explicit question; if any frame would be dropped, use the sheet.
        parts = [{'type':'input_text', 'text':prompt + ' 以下为按时间顺序单列的抽样画面。'}]
        for ms, url in frames:
            parts.extend(({'type':'input_text','text':f'{ms} ms'},
                          {'type':'input_image','image_url':url}))
        candidate = fit_images_to_context([{'role':'user', 'content':parts}], cfg)
        if sum(p['type']=='input_image' for p in candidate[0]['content']) == len(frames):
            fitted = candidate
    wire = running.transport
    if isinstance(wire, ChatTransport):
        wire = wire.for_request(cfg, bound_request=request)
    result = await generate(cfg, fitted, wire, signal=running.signal)
    if result.status != 'completed' or result.calls or not result.text:
        raise ValueError('本次视觉观察未完成')
    return result.text
