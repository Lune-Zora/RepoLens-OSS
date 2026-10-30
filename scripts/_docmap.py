import pathlib, sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
ROOT = pathlib.Path('docs')
ORDER = ['01_需求','02_架构','03_数据库','04_接口','05_测试','06_运维','07_归档','99_进度']
NAME = {'01_需求':'需求','02_架构':'架构','03_数据库':'数据库','04_接口':'接口','05_测试':'测试与评测','06_运维':'运维','07_归档':'归档','99_进度':'进度与历史'}
out = []
for d in ORDER:
    p = ROOT / d
    if not p.exists(): continue
    files = sorted([f for f in p.rglob('*') if f.is_file()])
    mds = [f for f in files if f.suffix.lower() == '.md']
    others = [f for f in files if f.suffix.lower() != '.md']
    out.append((d, NAME[d], mds, others))
res = {}
for d, n, mds, others in out:
    items = []
    for f in mds:
        rel = f.relative_to(ROOT).as_posix()
        title = ''
        for line in f.read_text(encoding='utf-8', errors='replace').splitlines()[:5]:
            if line.startswith('# '):
                title = line[2:].strip(); break
        items.append((rel, title, f.stat().st_size))
    res[d] = {'name': n, 'items': items, 'others': [f.relative_to(ROOT).as_posix() for f in others]}
print(json.dumps(res, ensure_ascii=False, indent=1))
