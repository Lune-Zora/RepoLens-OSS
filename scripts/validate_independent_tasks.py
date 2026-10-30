"""Run standard CLI repair and replay on explicitly labeled constructed tasks.

No reference patch or acceptance content is supplied to the model.
Every failed attempt is retained, and an existing output directory is never replaced.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')


def snapshot(repo):
    return {p.relative_to(repo).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in repo.rglob('*') if p.is_file() and not set(p.relative_to(repo).parts)&{'.replens','__pycache__','.pytest_cache'}}


def command(argv,out,label,env):
    t=time.time()
    with (out/(label+'.stdout.txt')).open('w',encoding='utf8') as stdout, (out/(label+'.stderr.txt')).open('w',encoding='utf8') as stderr:
        try:
            proc=subprocess.run(argv,env=env,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=900)
            code=proc.returncode
        except subprocess.TimeoutExpired:code=124
    item={'argv':argv,'exit_code':code,'seconds':round(time.time()-t,3)}
    write(out/(label+'-command.json'),item)
    return item


def main():
    parser=argparse.ArgumentParser(description='独立构造任务的实际模型验证；不是外部真实Bug基准')
    parser.add_argument('--models',nargs='+',required=True)
    parser.add_argument('--task',action='append')
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    out=Path(args.output_dir).resolve();out.mkdir(parents=True,exist_ok=False)
    base=ROOT/'examples/independent_tasks'
    catalog=json.loads((base/'catalog.json').read_text(encoding='utf8'))
    if args.task:catalog=[e for e in catalog if e['id'] in args.task]
    if not catalog:raise SystemExit('没有选中任务')
    write(out/'protocol.json',{'kind':'constructed_independent_tasks','models':args.models,'tasks':catalog,
                              'candidate_count':2,'max_repair_rounds':1,'max_test_design_rounds':2,'temperature_candidates':[0.2,0.35],
                              'understanding_temperature':0,'tests_temperature':0,'reference_patch':None,
                              'acceptance_outside_input':True,'time':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    results=[]
    for model in args.models:
        for entry in catalog:
            run=out/(re.sub(r'[^A-Za-z0-9._-]','_',model)+'--'+entry['id']);run.mkdir()
            repo=base/entry['repo']; frozen=snapshot(repo)
            write(run/'source-before.json',frozen)
            env=os.environ.copy()
            env.update(REPLENS_LLM_BASE_URL='http://127.0.0.1:11434/v1',REPLENS_LLM_MODEL=model,
                       REPLENS_REQUIRE_LLM='1',REPLENS_LLM_N_CANDIDATES='2',
                       REPLENS_LLM_MAX_REPAIR_ROUNDS='1',REPLENS_LLM_TIMEOUT='180',
                       REPLENS_LLM_MAX_TOKENS='2200',REPLENS_LLM_REASONING_EFFORT='none',
                       REPLENS_LLM_TRACE_DIR=str(run/'model-requests'),PYTHONIOENCODING='utf-8')
            argv=[sys.executable,'-m','replens','run',str(repo),'--issue',entry['issue'],
                  '--auto-patch','--test-cmd',entry['test_cmd'],'--acceptance-suite',str(base/entry['acceptance_suite']),
                  '--require-verified','--format','json','--output',str(run/'report.json')]
            ran=command(argv,run,'run',env)
            report=json.loads((run/'report.json').read_text(encoding='utf8')) if (run/'report.json').exists() else {}
            logs=(run/'run.stdout.txt').read_text(encoding='utf8')+(run/'run.stderr.txt').read_text(encoding='utf8')
            match=re.search(r'证据包已生成: (.+)',logs)
            replay={}
            if match:
                shutil.copytree(Path(match.group(1).strip()),run/'provenance')
            if report.get('status')=='VERIFIED' and (run/'provenance').is_dir():
                command([sys.executable,'-m','replens','verify',str(repo),'--provenance',str(run/'provenance'),
                         '--require-verified','--format','json','--output',str(run/'replay.json')],run,'verify',env)
                if (run/'replay.json').exists():replay=json.loads((run/'replay.json').read_text(encoding='utf8'))
            traces=[]
            for path in (run/'model-requests').glob('*.json'):
                d=json.loads(path.read_text(encoding='utf8'));traces.append({'file':path.name,'purpose':d.get('purpose'),
                    'requested_model':d.get('request',{}).get('model'),'response_model':d.get('response_model'),'status':d.get('status')})
            item={'model':model,'task':entry['id'],'seconds':ran['seconds'],'exit_code':ran['exit_code'],
                  'repair_status':report.get('status'),'blocked_reason':report.get('blocked_reason'),
                  'reasons':report.get('reasons'),'patch_note':report.get('patch_note'),
                  'replay_status':replay.get('replay_status'),'source_unchanged':snapshot(repo)==frozen,
                  'calls':traces,'pass':ran['exit_code']==0 and report.get('status')=='VERIFIED'
                                  and replay.get('replay_status')=='PASS' and snapshot(repo)==frozen}
            results.append(item);write(run/'summary.json',item);write(out/'results.json',results)
            print(json.dumps({k:v for k,v in item.items() if k!='calls'},ensure_ascii=False),flush=True)
    return 0 if all(r['pass'] for r in results) else 2


if __name__=='__main__':raise SystemExit(main())
