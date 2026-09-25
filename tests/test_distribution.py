from pathlib import Path
import json
import tomllib
import pytest
from co4.config import Settings
from co4.schemas import Policy
from co4.worker import load_config, WorkerError

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('name',['bugfix.v1','feature.v1','maintenance.v1'])
def test_reusable_policy_templates_validate(name):
    data=json.loads((ROOT/'examples/workflows'/f'{name}.json').read_text())
    assert Policy(**data).require_validation


def test_example_worker_requires_explicit_execution_consent(tmp_path):
    path=ROOT/'examples/worker.toml'
    with pytest.raises(WorkerError,match='explicitly enable'):load_config(path)
    editable=tmp_path/'worker.toml'
    editable.write_text(path.read_text().replace('acknowledge_code_execution = false','acknowledge_code_execution = true')
        .replace('allow_checkpoint_push = false','allow_checkpoint_push = true'))
    cfg=load_config(editable)
    assert cfg['repositories']['YOUR-ORG/YOUR-REPO']['test_command'][0]=='dotnet'


def test_production_never_silently_falls_back_to_demo():
    with pytest.raises(ValueError,match='Missing production settings'):Settings(demo=False,data_key='',app_id='').validate()


def test_distribution_documents_and_static_assets_exist():
    for name in ['README.md','LICENSE','SECURITY.md','docs/architecture.md','docs/github-app.md','Dockerfile',
                 'src/co4/static/index.html','src/co4/static/app.js','src/co4/static/app.css']:
        assert (ROOT/name).stat().st_size>0


def test_serve_refuses_demo_mode_even_with_ambient_demo_flag(tmp_path):
    import os
    import subprocess
    import sys
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'CO4_DEMO':'true','CO4_DATA_KEY':'','GITHUB_APP_ID':''}
    result=subprocess.run([sys.executable,'-m','co4','serve'],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==1 and 'Missing production settings' in result.stderr
