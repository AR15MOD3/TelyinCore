"""Read-only bounded SOURCE inventory, never a runtime coverage authority.

Candidate call sites are lexical observations, not a call graph, guard proof or
alias analysis. No imports of inspected modules, runtime/data discovery, process
queries, requests, configuration reads or deployment operations are performed.
Exit 2 is intentional: this tool cannot authorize a production release.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from tools.bot_source_snapshot import _checked

ANCHORS = {
    'startup.py': ('main',),
    'launch.py': ('main', '_main_impl', '_start_the_local', '_stop_the_local'),
    'infinity_control.py': ('QQControl.__init__', 'QQControl.action',
                            'QQControl._start', 'QQControl.close', 'QQControl.save'),
    'snowluma_recovery.py': ('qq_processes', 'find_qq', 'launch_qq', 'Recovery.start',
                            'Recovery.run', 'Recovery.save'),
    'tts_install.py': ('TTSInstaller.start', 'TTSInstaller._run'),
    'builtin/infinity/core-infinity-qq/index.py': ('create',),
    'bot_controller_bootstrap.py': ('ControllerBootstrap.start',
                                   'ControllerBootstrap.wait_and_retire'),
    'bot_lifecycle_channel.py': ('LifecycleBroker.serve_once',),
}
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_NODES = 250000
MAX_CANDIDATES = 8192
SINK_NAMES = frozenset(('Popen', 'Process', 'run', 'call', 'check_output',
    'check_call', 'system', 'startfile', 'start', 'terminate', 'kill', 'Client',
    'AsyncClient', 'connect', 'request', 'get', 'post', 'stream', 'write_text',
    'write_bytes', 'replace', 'unlink'))
BLOCKERS = (
    'static_inventory_is_not_exclusive_host_authority',
    'mandatory_legacy_and_dynamic_entrypoint_coverage_not_proven',
    'long_lived_parent_supervision_not_accepted',
    'production_phase_projection_not_accepted',
    'backup_acl_power_loss_and_distribution_acceptance_not_proven',
)


def _read_sources(core):
    root = Path(core).absolute()
    result = {}
    total = 0
    for name in ANCHORS:
        path = _checked(root, Path(name))
        if path is None:
            raise ValueError('required entrypoint source missing')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError('entrypoint source byte limit')
        result[name] = raw
    return result


def _callee(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _callee(node.value) + '.' + node.attr
    return '<dynamic>'  # never serialize arguments or source string literals


class _Inventory(ast.NodeVisitor):
    def __init__(self):
        self.scope = []
        self.definitions = {}
        self.candidates = []
        self.dynamic_calls = 0

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        name = '.'.join(self.scope)
        self.definitions.setdefault(name, []).append(node.lineno)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        callee = _callee(node.func)
        if '<dynamic>' in callee:
            self.dynamic_calls += 1
        if callee.rsplit('.', 1)[-1] in SINK_NAMES or '<dynamic>' in callee:
            if len(self.candidates) >= MAX_CANDIDATES:
                raise ValueError('entrypoint candidate limit')
            self.candidates.append(dict(line=node.lineno, scope='.'.join(self.scope),
                                        callee=callee, classification='unproven_candidate'))
        self.generic_visit(node)


def audit(core):
    before = _read_sources(core)
    files = []
    for name, raw in before.items():
        tree = ast.parse(raw, filename=name)
        if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
            raise ValueError('entrypoint AST node limit')
        visitor = _Inventory()
        visitor.visit(tree)
        anchors = []
        for symbol in ANCHORS[name]:
            lines = visitor.definitions.get(symbol, [])
            if len(lines) != 1:
                raise ValueError('entrypoint anchor missing or ambiguous')
            anchors.append(dict(symbol=symbol, line=lines[0]))
        files.append(dict(path=name, sha256=hashlib.sha256(raw).hexdigest(),
                          anchors=anchors, candidates=visitor.candidates,
                          dynamic_calls=visitor.dynamic_calls))
    # A whole second bounded read detects cooperative edits during this scan.
    # This does not eliminate OS read/link races or later source changes.
    if before != _read_sources(core):
        raise ValueError('entrypoint source changed during audit')
    return dict(schema='bot-entrypoint-source-inventory-v1', source_audit_passed=True,
                source_stable=True, host_coverage_complete=False, production_ready=False,
                production_apply_supported=False, blockers=list(BLOCKERS), files=files)


def run(core, destination):
    destination = Path(destination).absolute()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        report = audit(core)
    except Exception:
        report = dict(schema='bot-entrypoint-source-inventory-v1', source_audit_passed=False,
                      source_stable=False, host_coverage_complete=False,
                      production_ready=False, production_apply_supported=False,
                      blockers=['source_audit_unavailable', *BLOCKERS], files=[])
    # No caller-supplied complete=true, receipt, environment toggle or approval
    # field can turn this static source report into host/release authority.
    with (destination/'entrypoint-audit.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
    return 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.source_root, args.output))
