"""Semantic tool-call groups and receipt diagnostics; presentation only."""
from collections import Counter
import sys

from . import markers
from .dispatch import DispatchResult


_PORTRAIT_REVIEW = {
    'portrait_needs_review': '这段原话的摘录还需核对。',
    'portrait_no_candidate': '本次复核没有选出可记入画像的原话。',
    'portrait_author_has_no_candidate': '这位发言者的原话还不足以支持这项记录。',
    'portrait_source_uncertain': '原话的归属或含义还不清楚。',
    'portrait_no_supported_source': '暂时没有可核实这项说法的原话。',
    'portrait_independent_review_unknown': '现有证据还不足以确认这项说法。',
    'portrait_independent_review_fail': '拟写内容和原话不一致。',
}


def _portrait_review_notice(receipt):
    if receipt.name != 'profile_add' or receipt.stage != 'handler':
        return None
    prefix = '[tool failed] PermissionError: 画像写前复核未通过（'
    text = receipt.text
    if not text.startswith(prefix) or not text.endswith('），本次未写入。'):
        return None
    reason = text[len(prefix):-len('），本次未写入。')].partition('，')[0]
    return _PORTRAIT_REVIEW.get(reason)


def _name(call):
    return str(getattr(call, "name", "?")).replace("\r", " ").replace("\n", " ")


def _names(calls):
    return "、".join(name if count == 1 else f"{name} ×{count}"
                    for name, count in Counter(_name(c) for c in calls).items())


def log_requests(calls):
    """Only one actual model reply is a group; never buffer by wall-clock time."""
    ordinary = [c for c in calls if _name(c) != "finish_turn"]
    controls = [c for c in calls if _name(c) == "finish_turn"]
    if ordinary:
        print("   [call] 调用 · " + _names(ordinary), file=sys.stderr, flush=True)
    if controls:
        print("   [batch] 请求结束（" + _names(controls) + "）", file=sys.stderr, flush=True)


def _problem(receipt):
    text = str(receipt.text)
    stage = receipt.stage
    if text.startswith("[tool failed]"):
        label = "参数校验失败" if stage == "validate" else "执行失败"
        return label + "：" + text[len("[tool failed]"):].strip()
    if text == markers.UNKNOWN_TOOL:
        return "权限拒绝，未执行" if stage == "area" else "工具不存在，未执行"
    reasons = {
        markers.NOT_EXECUTED_BARRIER: "被屏障阻止，未执行",
        markers.NOT_EXECUTED_INCOMPLETE: "调用不完整，未执行",
        markers.NOT_EXECUTED_STREAM_ABORTED: "流已中断，未执行",
        markers.NOT_EXECUTED_LOOP_STOPPED: "循环已停止，未执行",
        markers.SHUTDOWN_INTERRUPTED: "关机中断，结果不可用",
        markers.MISSING_RESULT: "结果缺失",
        markers.MISSING_RESULT_RESTART: "重启后结果缺失",
    }
    return reasons.get(text)


def log_dispatch(calls, result):
    # Only the real dispatch contract guarantees that missing receipts are queued.
    # Injected/unknown runners must not be interpreted as pending or successful.
    if not isinstance(result, DispatchResult):
        return
    receipts = {one.call_id: one for one in result.receipts}
    successful_end = failed_end = False
    for call in calls:
        one = receipts.get(getattr(call, "call_id", None))
        name = _name(call)
        if one is None:
            print("   [call] 待确认 · " + name, file=sys.stderr, flush=True)
            continue
        if name == 'profile_add' and (notice := _portrait_review_notice(one)):
            # The full unsuccessful receipt still reaches the model and the audit.
            print(f"   [call] 画像未保存 · {notice}", file=sys.stderr, flush=True)
            continue
        problem = _problem(one)
        if problem:
            detail = problem.replace("\r", " ").replace("\n", " ")[:200]
            print(f"   [call] 异常 · {name} · {detail}", file=sys.stderr, flush=True)
        if name == "finish_turn" and one.stage == "handler" and result.ended:
            if problem:
                failed_end = True
            else:
                successful_end = True
    if successful_end:
        print("   [batch] 本批结束（finish_turn）", file=sys.stderr, flush=True)
    elif failed_end:
        # Existing dispatcher may stop even when the end handler raises. Do not
        # change that behavior, or misreport its failed execution as success.
        print("   [batch] 本批已停止（finish_turn；结束工具执行失败）",
              file=sys.stderr, flush=True)
