(
    'Deployment-only group identity policy; not in'
    'stalled or enabled by launch.\n\nThis module co'
    'ntains the protocol-specific literals intenti'
    'onally. Core only\nconsumes an explicitly supp'
    'lied trusted callable. This is not an OS sand'
    'box.\n'
)
import re
from telyincore.portrait_shadow.evidence import valid_turn
from telyincore.portrait_shadow.review import ReviewCoordinator

def valid_group_turn(turn):
    return (
        valid_turn(turn) and re.fullmatch('group:qq:[0-9]+', turn.scope) is not None and all((re
            .fullmatch('qq:[0-9]+', m.author) is not None for m in turn.messages))
    )

def make_coordinator(**kwargs):
    if 'validate_turn' in kwargs:
        raise TypeError('deployment_policy_cannot_be_overridden')
    return ReviewCoordinator(validate_turn=valid_group_turn, **kwargs)
