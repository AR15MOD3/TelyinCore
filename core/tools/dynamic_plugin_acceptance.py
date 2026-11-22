"""Official dynamic declarations with synthetic context; NEVER start plugins.

QQ HTTP and Waves runtime requests are replaced at the final IO boundary.
Optional NativeSharing declarations use a handler fixture, not a live reviewer.
No real config, identity, accounts, indexes or vector data are loaded.
"""
from __future__ import annotations

from dataclasses import replace
import datetime as dt
import importlib.util
import inspect
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx

CORE = Path(__file__).resolve().parents[1]


def load(name):
    path = CORE / "extensions" / name / "index.py"
    spec = importlib.util.spec_from_file_location("acceptance_" + name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(n=1, *, group=False, who="900000001"):
    from telyincore.events import EventEnvelope
    return EventEnvelope(cursor=n, type="message", ts="2026-10-28T16:00:00+08:00",
        source="qq", origin="external", text="SYNTHETIC", sender_key="qq:" + who,
        meta={"conversation": "group:900000010" if group else "private:" + who,
              "message_id": str(n)})


def fixtures(root, batch):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ctx = {"root": root / "core", "bot_id": "AcceptanceFixture",
           "packageDir": CORE / "extensions/core-infinity-qq",
           "qq_config_path": root / "synthetic-qq.json", "active_events": lambda: batch,
           "log": lambda _: None}
    ctx["qq_config_path"].write_text(json.dumps({
        "privates": ["900000001", "900000002"], "groups": ["900000010", "900000011"]}), encoding="utf-8")
    qq = load("core-infinity-qq").create(ctx)
    qq.privates = {"900000001", "900000002"}
    qq.groups = {"900000010", "900000011"}
    qq.proactive_target = "900000001"
    requests = []
    def mock(request):
        requests.append({"action": request.url.path, "payload": json.loads(request.content)})
        return httpx.Response(200, json={"status": "ok", "retcode": 0,
                                        "data": {"message_id": 999}})
    qq.client = httpx.Client(base_url="http://fixture.invalid/", transport=httpx.MockTransport(mock),
                             trust_env=False)
    waves = load("core-infinity-wavesuid").create({**ctx,
        "packageDir": CORE / "extensions/core-infinity-wavesuid"})
    runtime = inspect.getclosurevars(waves.tools()[0].handler).nonlocals["runtime"]
    runtime.settings = {"scene": "group", "mediaFormat": "html", "base": "http://fixture.invalid"}
    wave_requests = []
    def wave_mock(path, payload=None, timeout=5):
        wave_requests.append((path, payload))
        if path == "/api/health":
            return {"ok": True, "command_receipts": 1, "prefixes": ["ww"]}
        return {"ok": True, "text": "SYNTHETIC FIXTURE RECEIPT"}
    runtime.request = wave_mock
    runtime.prepare_command = lambda: ((None, "synthetic", "AcceptanceFixture", "fixture-scope", "http://fixture.invalid"), "group", 90, "html")
    runtime.receipt_request_for = lambda frozen, identity, path, encoded, timeout, cancelled: wave_mock(path, json.loads(encoded), timeout)
    return NS(qq=qq, waves=waves, runtime=runtime, qq_requests=requests, wave_requests=wave_requests)


def sharing_declarations():
    from telyincore.sharing_native import NativeSharing
    # Exercise the real declaration method without constructing a reviewer,
    # reading owner credentials, or claiming privacy review was performed.
    def blocked(**kwargs):
        raise RuntimeError("sharing fixture: not reviewed, not sent")
    return NativeSharing.tools(NS(preview=blocked, share=blocked))


def combined(fixture, sharing=False):
    import launch
    if sharing:
        fixture.qq.sharing = NS(tools=sharing_declarations)
    else:
        fixture.qq.sharing = None
    dynamic = [*fixture.qq.tools(), *fixture.waves.tools()]
    return list(launch._the_deployment_tool_declarations(dynamic)), dynamic


def offline(root):
    """Run assertions with only mocked IO. Returns sanitized counts and booleans."""
    import launch
    from telyincore import affect, memory
    from telyincore.dispatch import Dispatcher
    from telyincore.identity import IdentityBook
    from telyincore.stream import ToolCall
    from telyincore.tools import build_table, ToolDeclarationError, ToolTag
    batch = [event()]
    f = fixtures(Path(root) / "fixture", batch)
    checks = []
    def passed(name):
        checks.append({"name": name, "passed": True})
    def denied(name, action):
        count = len(f.qq_requests)
        try:
            action()
        except ValueError:
            pass
        else:
            raise AssertionError(name + ": expected rejection")
        assert len(f.qq_requests) == count
        passed(name)
    inventories = []
    try:
        for enabled in (False, True):
            rows, dynamic = combined(f, enabled)
            handlers = {r.name: (r.handler or (lambda **kw: "fixture only")) for r in rows}
            table = launch._one_speech_per_turn(build_table(rows, handlers))
            wire = launch._the_sendable_declarations(rows, handlers)
            names = [r.name for r in rows]
            assert len(names) == len(set(names)) == len(wire)
            assert {"emotion_read", "emotion_update", "profile_add"} <= set(names)
            assert not {"emotion_read", "emotion_update", "profile_add"} & {r.name for r in dynamic}
            assert all(table[r.name].ends_turn and table[r.name].barrier_after
                       for r in dynamic if ToolTag.SPEAK in r.tags)
            inventories.append({"sharing_fixture": enabled, "count": len(rows),
                                "dynamic_names": [r.name for r in dynamic], "declarations": wire})
            passed("unique_names_and_wire_" + str(enabled))
            try:
                build_table([*rows, replace(dynamic[0], name="emotion_update")], handlers)
            except ToolDeclarationError:
                passed("collision_rejected_" + str(enabled))
            else:
                raise AssertionError("duplicate emotion tool accepted")
        f.qq.sharing = None
        share = next(r for r in sharing_declarations() if r.name == "qq_share_to_owner")
        assert "target" not in share.parameters["properties"]
        assert "recipient" not in share.parameters["properties"]
        passed("sharing_declaration_no_recipient_override")
        denied("private_cross_recipient", lambda: f.qq.send_text("private", "900000002", "fixture"))
        denied("private_not_allowlisted", lambda: f.qq.send_text("private", "900000099", "fixture"))
        f.qq.send_text("private", "900000001", "fixture")
        assert len(f.qq_requests) == 1
        passed("private_current_recipient_mock_io")
        batch[:] = [event(group=True)]
        denied("group_old_trigger", lambda: f.qq.send_text("group", "900000010", "fixture", "999"))
        denied("group_other_room", lambda: f.qq.send_text("group", "900000011", "fixture", "1"))
        f.qq.send_text("group", "900000010", "fixture", "1")
        assert f.qq_requests[-1]["payload"]["message"][0]["data"]["id"] == "1"
        passed("group_current_trigger_mock_io")
        f.qq._recalled[("group:900000010", "1")] = None
        denied("recalled_message", lambda: f.qq.send_text("group", "900000010", "fixture", "1"))
        f.qq._recalled.clear()
        batch[:] = [NS(origin="internal", source="heartbeat", type="tick", text="fixture")]
        denied("autonomous_wrong_owner", lambda: f.qq.send_text("private", "900000002", "fixture"))
        # Proactive-to-owner is intentionally permitted by the official policy.
        f.qq.send_text("private", "900000001", "fixture")
        passed("autonomous_fixed_owner_mock_io")
        waves = {r.name: r.handler for r in f.waves.tools()}
        async def invoke(**arguments):
            result = await Dispatcher({r.name: r for r in f.waves.tools()}).arun([
                ToolCall(call_id="synthetic-command", name="waves_command", arguments=json.dumps(arguments))])
            return result.receipts[0].text
        before = sum(path == "/api/chat" for path, _ in f.wave_requests)
        asyncio.run(invoke(command="ww签到"))
        assert sum(path == "/api/chat" for path, _ in f.wave_requests) == before
        passed("waves_internal_caller_rejected")
        batch[:] = [event(group=True)]
        asyncio.run(invoke(command="ww体力", user="900000002"))
        assert sum(path == "/api/chat" for path, _ in f.wave_requests) == before
        passed("waves_forged_user_rejected")
        asyncio.run(invoke(command="ww登录 fixture-secret"))
        assert sum(path == "/api/chat" for path, _ in f.wave_requests) == before
        passed("waves_group_credential_rejected")
        asyncio.run(invoke(command="ww体力"))
        payload = next(data for path, data in reversed(f.wave_requests) if path == "/api/chat")
        assert payload["user_id"] == "900000001" and payload["audience"] == "group:qq:900000010"
        passed("waves_host_bound_caller_mock_io")
        rows, _ = combined(f)
        h = {r.name: (r.handler or (lambda **kw: "fixture")) for r in rows}
        table = launch._one_speech_per_turn(build_table(rows, h))
        before = sum(path == "/api/chat" for path, _ in f.wave_requests)
        Dispatcher(table, permissions=lambda row: ToolTag.ACT not in row.tags).run([
            ToolCall(call_id="deny-act", name="waves_command", arguments='{"command":"ww签到"}')])
        assert sum(path == "/api/chat" for path, _ in f.wave_requests) == before
        passed("dispatcher_ACT_permission_denial_fixture")
        home = Path(root) / "memory"
        book = IdentityBook(home / "identities.json")
        ctx = lambda: launch._profile_context_for_batch(book, batch)
        handlers = memory.handlers(home, profile_context=ctx,
            affect_context=lambda who: affect.batch_context(batch, ctx(), {}, who),
            affect_clock=lambda: dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc))
        old = memory.favor_of(home, "QQ-900000001")
        try:
            refusal = handlers["profile_add"](who="QQ-900000001", favor=100)
        except ValueError:
            pass
        else:
            assert "不接受绝对 favor" in refusal
        assert memory.favor_of(home, "QQ-900000001") == old
        assert not list(home.glob("画像/**/关系.jsonl"))
        passed("legacy_favor_not_opened_by_dynamic_tools")
        assert not f.qq._started and f.qq._thread is None and f.runtime.proc is None
        passed("plugin_instances_never_started")
        return {"checks": checks, "inventories": inventories, "network_mocked": True,
                "plugin_start_called": False, "real_accounts_used": False,
                "live_runtime_table_snapshot": False,
                "sharing_privacy_handler_validated": False}
    finally:
        f.qq.client.close()
