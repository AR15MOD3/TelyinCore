"""Read-only view of the current registered plugin tools; never invokes a plugin hook."""
from telyincore import plugins


def snapshot(root, scope, declarations, provider="core-infinity-qq"):
    with plugins.lifecycle_transaction():
        local=plugins.installed(root,scope=scope)['plugins']
        states={p['name']:{'enabled':bool(p.get('enabled')),'loaded':bool(p.get('loaded'))} for p in local if p.get('installed')}
        if provider != 'core-infinity-qq' and provider not in states:
            raise ValueError('插件未安装')
        _,manifest=plugins._plugin_folder(root,provider)
        guide=(manifest or {}).get('telyincore',{}).get('toolGuide',[])
        guide=[dict(row) for row in guide if isinstance(row,dict) and all(isinstance(row.get(k),str) for k in ('name','group','title'))]
        tools=[]
        for tool in declarations:
            owner=getattr(getattr(tool,'handler',None),'__core_plugin_owner__',None)
            if not isinstance(owner,tuple) or len(owner)!=2: continue
            name,instance=owner
            if (name!=provider or plugins._loaded.get(name) is not instance
                    or not plugins._same_setting_scope(name,instance,scope)
                    or plugins._lifecycle_state(name)[0]!='running'): continue
            schema=tool.parameters or {}
            tools.append({'name':tool.name,'provider':name,'description':tool.description,
                'parameters':list(schema.get('properties',{})), 'required':list(schema.get('required',[]))})
        return {'plugins':states,'tools':tools,'guide':guide}
