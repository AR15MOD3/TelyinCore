"""List local consolidation progress; resume only with explicit --resume --bot."""
import argparse
import ipaddress
import json
import urllib.parse
import urllib.request


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:7799')
    parser.add_argument('--task')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--bot',help='Required for resume; must equal the active Bot')
    args=parser.parse_args(argv)
    url=urllib.parse.urlsplit(args.base_url)
    try:local=ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:local=False
    if (url.scheme!='http' or not local or not url.port or url.path not in ('','/')
            or url.username or url.password or url.query or url.fragment):
        parser.error('Only an explicit numeric loopback HTTP host:port is allowed')
    if args.resume and (not args.task or not args.bot):
        parser.error('--resume requires --task and --bot; it may generate and commit files')
    base=args.base_url.rstrip('/')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**k):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(path,body=None):
        req=urllib.request.Request(base+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Origin':base})
        with opener.open(req,timeout=8) as response:return json.load(response)
    if not args.resume:
        data=request('/consolidation/jobs')
        if args.task:data['jobs']=[r for r in data.get('jobs',[]) if r.get('task')==args.task]
    else:
        identity=request('/infinity/bots')
        if identity.get('active')!=args.bot:
            parser.error('Active Bot mismatch; no recovery request was submitted')
        data=request('/consolidation/resume',{'task':args.task})
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
