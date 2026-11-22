"""Bind local management-login scope to an authenticated owned server generation.

Not an account/mutation grant or reconciliation/reset of any historical ledger.
Same retained generation always reuses its scope; only original HOST retirement
and a new native birth can yield another management-server resource.
"""
import hashlib
import json


def owned_management_resource(instance,publisher):
    from snowluma_instance import SnowInstance,load_instance
    from bot_instance_publisher import InstancePublisher
    from bot_launcher_companion import CompanionProcess
    if (type(instance) is not SnowInstance or type(publisher) is not InstancePublisher
            or publisher.core!=instance.core or publisher.bot!=instance.bot
            or load_instance(instance.core,instance.bot)!=instance):
        raise ValueError('exact private management resource required')
    current=publisher._companion
    if (type(current) is not CompanionProcess or current.publisher is not publisher
            or current.retired or current.poll() is not None
            or publisher._companion is not current):
        raise ValueError('live original HOST management receipt required')
    identity={k:current.identity[k] for k in ('pid','created','exe')}
    digest=hashlib.sha256(json.dumps(dict(bot=instance.bot,core=str(instance.core),
        generation=current.generation,native=identity,ports=list(instance.ports)),sort_keys=True).encode()).hexdigest()
    return current,instance.root/'admin'/digest/'a'
