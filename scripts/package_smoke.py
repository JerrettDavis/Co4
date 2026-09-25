"""Exercise an installed wheel outside the source tree, with no external provider calls."""
from pathlib import Path
import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--python',required=True,help='Python executable in the environment where the wheel is installed')
    parser.add_argument('--output',default='docs/package-test-report.json')
    args=parser.parse_args()
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH' and not k.startswith(('CO4_','GITHUB_APP_','GITHUB_CLIENT_'))}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='co4-package-smoke-') as tmp:
        root=Path(tmp)
        help_result=subprocess.run([args.python,'-m','co4','--help'],env=env,cwd=root,capture_output=True,text=True,check=True)
        assert 'demo-worker' in help_result.stdout
        log=open(root/'server.log','w')
        proc=subprocess.Popen([args.python,'-m','co4','demo','--port',str(port),'--database',f'sqlite:///{root}/app.db'],
                              env=env,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        url=f'http://localhost:{port}'
        try:
            with httpx.Client(base_url=url,headers={'X-Co4-CSRF':'1'},timeout=20) as c:
                for _ in range(100):
                    try:
                        if c.get('/healthz').status_code==200:break
                    except httpx.ConnectError:pass
                    time.sleep(.1)
                else:
                    log.flush()
                    raise RuntimeError('Installed server did not start: '+(root/'server.log').read_text())
                assert c.get('/').status_code==200
                assert 'function render' in c.get('/static/app.js').text
                result=subprocess.run([args.python,'-m','co4','demo-worker','--server',url,'--root',str(root/'worker')],
                    cwd=root,env=env,capture_output=True,text=True,timeout=60)
                assert result.returncode==0,result.stdout+result.stderr
                assert 'Review ready:' in result.stdout
                assert c.post('/auth/demo',json={'login':'contributor'}).status_code==200
                data=c.get('/api/dashboard').json()
                lease=next(l for l in data['leases'] if l['state']=='awaiting_review')
                assert not lease['pr_url']
                assert c.post(f"/api/leases/{lease['id']}/approve",json={'sha':lease['checkpoint']['sha'],
                    'review_digest':lease['review_digest'],'confirm_reviewed':True}).status_code==200
                for _ in range(100):
                    detail=c.get('/api/work/'+lease['work_id']).json()
                    final=detail['leases'][-1]
                    if final['state']=='submitted':break
                    time.sleep(.1)
                assert final['state']=='submitted' and final['pr_url'].startswith('/demo/')
                assert final['evidence']['baseline_exit']==0 and final['evidence']['red_exit']==1
                assert final['evidence']['green_exit']==0 and final['evidence']['verify_exit']==0
                report={'status':'passed','installed_package':'co4-orchestrator 0.1.0a2','mode':'offline fixture over real HTTP',
                        'checks':['installed entry point','server startup outside source tree','packaged static assets',
                            'installed CLI worker with real Git/test workflow','no publication before authenticated approval',
                            'dispatcher publication after approval'],'live_providers':False,
                        'dependency_environment':'isolated wheel installation with existing runtime dependencies made available via site-packages; no fresh network dependency download'}
                Path(args.output).resolve().write_text(json.dumps(report,indent=2)+'\n')
                print(json.dumps(report,indent=2))
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            log.close()

if __name__=='__main__':main()
