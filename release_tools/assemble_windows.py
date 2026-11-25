"""Assemble Windows candidates using the verified Windows distribution for its native runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from publish import ROOT, inventory, validate_release_version

PROGRAM = """qq_host_runtime.py qq_encounters.py startup.py scripts/windows_acceptance.py bot_launcher_lifecycle.py deployment_relocation.py bot_companion_custody.py telyincore/download_sources.py launch.py infinity_control.py bot_launcher_companion.py qq_voice_services.py
qq_conversation_privacy.py qq_poke.py qq_stickers.py qq_sticker_view.py qq_visual_request.py
qq_sticker_panel.py snowluma_status_policy.py qq_sticker_context.py qq_status_command.py
pyproject.toml requirements-windows-acceptance.txt
builtin/infinity/core-infinity-qq/index.py builtin/infinity/core-infinity-qq/README.md
telyincore/identity.py telyincore/vision_owned.py telyincore/batch_snapshot.py
telyincore/sharing_native.py telyincore/plugins.py telyincore/events.py telyincore/engine.py
telyincore/console.py telyincore/dispatch.py telyincore/generate.py telyincore/tools.py telyincore/web.py
telyincore/market.py telyincore/online_install.py telyincore/package_updates.py
telyincore/release_preview.py""".split()
WHEEL = "pillow-12.2.0-cp311-cp311-win_amd64.whl"
WHEEL_HASH = "6e6b2a0c538fc200b38ff9eb6628228b77908c319a005815f2dde585a0664b60"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def assemble(baseline, web, wheel, version, out):
    validate_release_version(version)
    with zipfile.ZipFile(baseline) as archive:
        prefix, candidate, ledger = inventory(archive)
        if prefix != "Telyincore_win/":
            raise ValueError("需要 Windows 部署包")
        files = {name: archive.read(prefix + name) for name in ledger}
        if any(digest(files[name]) != expected for name, expected in ledger.items()):
            raise ValueError("部署包文件清单不一致")
    data = wheel.read_bytes()
    if wheel.name != WHEEL or digest(data) != WHEEL_HASH:
        raise ValueError("Pillow 离线依赖不匹配")
    files["runtime/wheels/" + WHEEL] = data
    for name in PROGRAM:
        files["core/" + name] = (ROOT / "core" / name).read_bytes()
    pointer = json.loads((web / "current.json").read_bytes())
    generation = web / "generations" / pointer["generation"]
    manifest = (generation / "manifest.json").read_bytes()
    if digest(manifest) != pointer["manifest_sha256"]:
        raise ValueError("网页清单不匹配")
    for name, expected in json.loads(manifest)["files"].items():
        data = (generation / name).read_bytes()
        if digest(data) != expected["sha256"]:
            raise ValueError("网页文件不匹配")
        files["core/dist/web/" + name] = data
    for p in [web / "current.json", *generation.iterdir()]:
        files["core/dist/web/" + p.relative_to(web).as_posix()] = p.read_bytes()
    files["core/dist/web/manifest.json"] = manifest
    source = {n[5:]: digest(b) for n, b in files.items() if n.startswith("core/")}
    fingerprint = digest(json.dumps(source, sort_keys=True).encode())
    candidate.update(version=version, candidate_id="windows-core-" + fingerprint[:12],
                     source_files=len(source), source_fingerprint_sha256=fingerprint,
                     release_channel="stable", production_ready=False)
    files["candidate.json"] = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode()
    files["MANIFEST.sha256"] = "".join(digest(b)+"  "+n+"\n" for n,b in sorted(files.items())
                                       if n != "MANIFEST.sha256").encode()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"Telyincore_win_{version}.zip"
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(prefix + name, data)
    target.with_suffix(".sha256").write_text(digest(target.read_bytes())+"  "+target.name+"\n", encoding="ascii")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "web", "wheel", "out"):
        parser.add_argument(name, type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    print(assemble(args.baseline, args.web, args.wheel, args.version, args.out))
