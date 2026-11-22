"""Local operator commands for explicit platform-account links.

This script does not infer identities from names or message contents.  It does
not rewrite message history or merge existing profile files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telyincore.identity import IdentityBook  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="管理已确认的跨平台身份绑定")
    parser.add_argument("--home", type=Path, required=True,
                        help="该 Bot 的私有 memory 目录")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="创建一个独立人物 ID")
    create.add_argument("label", help="仅用于管理界面的显示名，不作为归并依据")
    commands.add_parser("list", help="列出本地人物与账号绑定")
    bind = commands.add_parser("bind", help="明确把账号绑定到现有 ID")
    bind.add_argument("person_id")
    bind.add_argument("account", help="例如 qq:123 或 wechat:some-id")
    bind.add_argument("--confirm", action="store_true", required=True)
    unbind = commands.add_parser("unbind", help="撤销账号绑定，不改历史")
    unbind.add_argument("account")
    unbind.add_argument("--expected-person-id", required=True,
                        help="从同一次 list 快照确认的人物 ID")
    unbind.add_argument("--revision", required=True,
                        help="该次 list 快照的 revision；变更后须重新确认")
    unbind.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args(argv)
    book = IdentityBook(args.home / "身份绑定.json")
    try:
        if args.command == "create":
            result = book.create(args.label)
        elif args.command == "list":
            people, revision = book.snapshot()
            result = {"people": [row.__dict__ for row in people], "revision": revision}
        elif args.command == "bind":
            result = book.bind(args.person_id, args.account, confirmed=args.confirm)
        else:
            result = {"removed": book.unbind(
                args.account, expected_person_id=args.expected_person_id,
                revision=args.revision, confirmed=args.confirm)}
    except (ValueError, KeyError, PermissionError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"身份绑定失败：{exc}\n")
    if isinstance(result, list):
        value = [row.__dict__ for row in result]
    elif isinstance(result, dict):
        value = result
    else:
        value = result.__dict__
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
