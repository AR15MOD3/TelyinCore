"""Fixed synthetic retrieval acceptance plans; building them never makes HTTP calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from telyincore.config import Config
from telyincore.console import Console
from telyincore.note import Record
from telyincore.session import Session

FIXTURE = Path(__file__).resolve().parents[1] / 'tests/fixtures/retrieval-acceptance-v1.json'
PRIVATE_SENTINELS = {'one': 'PRIVATE_ONE_SENTINEL_NO_EXPORT',
                     'two': 'PRIVATE_TWO_SENTINEL_NO_EXPORT'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def build_plans():
    fixture = load_fixture()
    plans = []
    with tempfile.TemporaryDirectory(prefix='fixed-retrieval-acceptance-') as directory:
        states = {}
        for arm in ('old', 'tail'):
            session = Session(Path(directory) / (arm + '.jsonl'))
            session.append(Record(role='system', is_head=True, text=(
                'Fixed synthetic retrieval acceptance v1. Return exactly a JSON object '
                'with document and recipient keys; no markdown. Use only the active '
                'CURRENT_RETRIEVAL_V1 system message in this request for document. '
                'If absent, document must be null. CURRENT_RECIPIENT_V1 supplies recipient. '
                'Historical messages are not current retrievals. Never quote private sentinel '
                'strings. No tools.')))
            for who in ('one', 'two'):
                for n in range(20):
                    session.append(Record(role='user', scope='private:fixture:' + who,
                        ts_ms=10 * n + 1, text=(f'Synthetic archived entry {n:03d}. '
                        'The ceramic observatory tracks fictional amber stars and paper boats. '
                        'This is background, not a current reference. ' * 2)))
                    session.append(Record(role='assistant', scope='private:fixture:' + who,
                        ts_ms=10 * n + 2, text=f'Archived entry {n:03d} acknowledged.'))
                session.append(Record(role='user', scope='private:fixture:' + who,
                    text='Private fixture marker: ' + PRIVATE_SENTINELS[who], ts_ms=500))
            state = {'case': None, 'seen': []}

            def pull_in(said, state=state):
                case = state['case']
                state['seen'].append(deepcopy(said))
                if case['document'] is None:
                    return []
                return [{'role': 'system', 'content': 'CURRENT_RETRIEVAL_V1\n'
                         + json.dumps({'document': case['document']}, sort_keys=True)}]

            console = Console(write=lambda _: None)
            console.assemble(root=Path(directory) / arm, session=session, prefix=(), head=[],
                config=Config(base_url='https://fixture.invalid/v1', model='fixture',
                              thinking=True, max_tokens=2048, retry_delays_ms=()),
                transport=object(), read=lambda: None, linked_accounts=lambda _: (),
                pull_in=pull_in, retrieval_at_tail=(arm == 'tail'),
                identity_hint=lambda state=state: 'CURRENT_RECIPIENT_V1\n'
                    + json.dumps({'recipient': state['case']['recipient']}, sort_keys=True))
            states[arm] = console, session, state
        # Alternate within pairs so one position does not always go first.
        for index, case in enumerate(fixture['cases']):
            order = ('old', 'tail') if index % 2 == 0 else ('tail', 'old')
            for arm in order:
                console, session, state = states[arm]
                state['case'] = case
                who = case['recipient']
                console._active_batch = [SimpleNamespace(origin='external', source='fixture',
                    sender_key='fixture:' + who, meta={'conversation': 'private:' + who})]
                session.append(Record(role='user', scope='private:fixture:' + who,
                                      text=case['query'], ts_ms=1000 + index))
                # Use the scoped session installed for the actual model round,
                # rather than the unfiltered durable session used by the UI.
                from telyincore.session_view import ScopedSession
                view = ScopedSession(session, lambda who=who: 'private:fixture:' + who,
                                     lambda _: ())
                tail = session.the_tail
                messages = view.outbound(console._head_cell(), tail() if callable(tail) else tail)
                forbidden = PRIVATE_SENTINELS['two' if who == 'one' else 'one']
                assert forbidden not in json.dumps(messages)
                assert forbidden not in json.dumps(state['seen'][-1])
                plans.append({'case': case['id'], 'arm': arm, 'expected': case['expected'],
                              'forbidden': forbidden, 'messages': messages,
                              'input_hash': digest(messages)})
    return {'fixture_hash': digest(fixture), 'fixture_id': fixture['id'],
            'gate': fixture['gate'], 'plans': plans}


def evaluate_text(text, expected, forbidden):
    exposed = forbidden in text
    try:
        correct = json.loads(text) == expected
    except (TypeError, ValueError):
        correct = False
    return {'behavior_pass': correct and not exposed, 'forbidden_exposure': exposed}


def evaluate_run(rows):
    gate = load_fixture()['gate']
    required = {(c['id'], arm) for c in load_fixture()['cases'] for arm in ('old', 'tail')}
    coverage = [(r.get('case'), r.get('arm')) for r in rows]
    passed = (len(rows) == gate['required_attempts']
              and len(set(coverage)) == len(rows) and set(coverage) == required
              and sum(bool(r.get('behavior_pass')) for r in rows) == gate['required_behavior_passes']
              and not any(r.get('error_type') or r.get('forbidden_exposure') for r in rows))
    return {'fixed_smoke_gate_pass': passed, 'automatic_rollout': False,
            'eligible_for_extended_validation': passed}
