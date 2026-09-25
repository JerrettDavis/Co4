"""Live-HTTP UI walkthrough. Run against a fresh `co4 demo` database only."""
import argparse
import json
import httpx
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://localhost:8765')
    parser.add_argument('--chromium',default=os.getenv('CHROMIUM_PATH'))
    parser.add_argument('--output',default='docs/images')
    parser.add_argument('--api-bridge',action='store_true',help='Render local assets and bridge fetch to live HTTP when browser navigation is prohibited; not a browser-network test')
    args=parser.parse_args()
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        launch={'headless':True}
        if args.chromium:launch.update(executable_path=args.chromium,args=['--no-sandbox'])
        browser=p.chromium.launch(**launch)
        page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        if args.api_bridge:
            client=httpx.Client(base_url=args.url,timeout=20)
            def request(_, data):
                if not data['url'].startswith(('/api/','/auth/')):raise ValueError('Only Co4 API paths may use the bridge')
                response=client.request(data['method'],data['url'],headers=data.get('headers'),content=data.get('body'))
                return {'status':response.status_code,'body':response.text}
            page.expose_binding('__co4_request',request)
            assets=Path(__file__).resolve().parents[1]/'src/co4/static'
            html=(assets/'index.html').read_text()
            html=html.replace('<script src="/static/app.js" defer></script>','').replace('<link rel="stylesheet" href="/static/app.css">','').replace('<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">','')
            page.set_content(html)
            page.add_style_tag(content=(assets/'app.css').read_text())
            page.add_script_tag(content="window.fetch=async (url,o={})=>{const r=await window.__co4_request({url,method:o.method||'GET',headers:o.headers,body:o.body});return new Response(r.body,{status:r.status});};")
            page.add_script_tag(content=(assets/'app.js').read_text())
        else:
            page.goto(args.url)
        page.get_by_role('button',name='Explore as maintainer').wait_for()
        checks=[]
        page.screenshot(path=str(folder/'landing.png'),full_page=True)
        page.get_by_role('button',name='Explore as maintainer').click()
        expect(page.get_by_role('heading',name='Build together, deliberately.')).to_be_visible()
        checks.append('maintainer sign-in and dashboard')
        page.screenshot(path=str(folder/'overview.png'),full_page=True)
        page.locator('.nav').get_by_role('link',name='Work queue').click()
        expect(page.get_by_role('heading',name='Work queue')).to_be_visible()
        page.locator('#search').fill('rounding')
        expect(page.locator('tbody tr')).to_have_count(1)
        page.get_by_role('button',name='Validate',exact=True).click()
        expect(page.locator('tbody tr .badge')).to_have_text('Queued')
        checks.append('queue filtering and human validation')
        page.locator('.nav').get_by_role('link',name='Projects',exact=True).click()
        page.get_by_role('button',name='Edit policy').click()
        expect(page.get_by_role('heading',name='Contribution policy')).to_be_visible()
        expect(page.locator('#access')).to_have_value('verified')
        page.get_by_role('button',name='Close dialog').click()
        checks.append('maintainer policy editor')
        page.locator('.nav').get_by_role('link',name='Governance',exact=True).click()
        page.get_by_role('button',name='Permissions',exact=True).click()
        expect(page.locator('#dialog')).to_contain_text('contributor')
        page.get_by_role('button',name='Close dialog').click()
        checks.append('project membership UI')
        page.locator('.nav').get_by_role('link',name='My devices',exact=True).click()
        page.get_by_role('button',name='Connect a device',exact=True).click()
        page.get_by_label('Device name',exact=True).fill('Browser smoke device')
        page.get_by_label('Harness',exact=True).select_option('copilot')
        page.get_by_role('button',name='Create device credential').click()
        expect(page.get_by_role('heading',name='Save this device credential')).to_be_visible()
        expect(page.locator('.token')).to_contain_text('co4_device_')
        page.get_by_role('button',name='Close dialog').click()
        checks.append('one-time device credential creation')
        page.get_by_label('Demo identity').select_option('contributor')
        expect(page.get_by_label('Demo identity')).to_have_value('contributor')
        with tempfile.TemporaryDirectory(prefix='co4-browser-worker-') as root:
            result=subprocess.run([sys.executable,'-m','co4','demo-worker','--server',args.url,'--root',root],
                capture_output=True,text=True,timeout=60)
            assert result.returncode==0,result.stdout+result.stderr
            assert 'Review ready:' in result.stdout
        checks.append('live-HTTP CLI worker: real Git and red/green tests')
        page.get_by_role('button',name='Refresh',exact=True).click()
        page.locator('.nav').get_by_role('link',name='Reviews').click()
        page.locator('[data-action=work]').first.click()
        expect(page.locator('#dialog')).to_contain_text('Fail → pass')
        page.get_by_role('tab',name='Diff',exact=True).click()
        expect(page.locator('#review-tab')).to_contain_text('if values else 0')
        page.screenshot(path=str(folder/'review.png'),full_page=True)
        page.get_by_role('tab',name='Trace',exact=True).click()
        expect(page.locator('#trace-content')).to_contain_text('ZeroDivisionError')
        page.get_by_role('tab',name='Summary',exact=True).click()
        page.get_by_role('button',name='Approve this commit').click()
        expect(page.locator('#form-error')).to_contain_text('Confirm that you reviewed')
        checks.append('full diff, private traces, and affirmative review gate')
        page.locator('#review-confirm').check()
        page.get_by_role('button',name='Approve this commit').click()
        for _ in range(12):
            page.wait_for_timeout(700)
            # Approving closes the review dialog; reopen it before polling for publication.
            if not page.locator('#dialog[open]').count():
                page.locator('.nav').get_by_role('link',name='Reviews').click()
                page.locator('[data-action=work]').first.click()
            else:
                page.get_by_role('button',name='Reload review').click()
            if page.get_by_role('link',name='View offline submission').count():break
        expect(page.get_by_role('link',name='View offline submission')).to_be_visible()
        checks.append('human-approved fixture publication through dispatcher')
        page.get_by_role('button',name='Close dialog').click()
        page.locator('.nav').get_by_role('link',name='Overview',exact=True).click()
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(100)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile viewport overflow'
        page.screenshot(path=str(folder/'mobile.png'),full_page=True)
        checks.append('390px responsive layout without horizontal overflow')
        assert not errors,errors
        report={'mode':'DOM browser with live HTTP API bridge' if args.api_bridge else 'native browser HTTP',
                'checks':checks,'check_count':len(checks),'javascript_errors':errors,'status':'passed'}
        (folder.parent/'browser-test-report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        browser.close()

if __name__=='__main__':main()
