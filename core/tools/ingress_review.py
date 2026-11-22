"""Explicit local operator CLI. Does not start/stop services or send events."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telyincore.ingress_review import Review


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',required=True)
    p.add_argument('--bot',required=True)
    p.add_argument('--source',choices=['plugin','local'],default='plugin')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('show')
    action=sub.add_parser('resolve')
    action.add_argument('--entry',required=True)
    action.add_argument('--expected-revision',required=True)
    action.add_argument('--decision',required=True,choices=['confirmed_handled','abandon_no_replay'])
    action.add_argument('--evidence-sha256',required=True)
    action.add_argument('--operator',required=True)
    action.add_argument('--confirm',action='store_true')
    a=p.parse_args(argv)
    review=Review(a.core,a.bot,source=a.source)
    try:
        result=(review.show() if a.command=='show' else review.resolve(entry=a.entry,
            expected_revision=a.expected_revision,decision=a.decision,
            evidence_sha256=a.evidence_sha256,operator=a.operator,confirm=a.confirm))
    except (ValueError,RuntimeError,OSError) as error:
        # No exception payload, source path, event body or credentials in stdout.
        print(json.dumps({'ok':False,'error_type':type(error).__name__,
                          'action':'inspect_state_and_operator_guide; do_not_auto_retry'}))
        return 2
    print(json.dumps({'ok':True,**result},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
