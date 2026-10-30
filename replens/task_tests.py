"""Generate explicit behavioral cases before patch generation; freeze their source.

Cases are model-proposed assertions, not an independent correctness oracle.
Only a caller-provided frozen acceptance suite can grant VERIFIED.
"""
from __future__ import annotations
import ast
import hashlib
import json
import inspect
import textwrap
import shlex
from pathlib import Path
from .issue_understanding import model_client
from .response_schemas import chat_structured, object_schema, cases_schema
from .retriever import _is_test_path
from .testgen import _module_loader_code, _find_method_owner

TASK_FILE = "__replens_task__/test_current_issue.py"


def compile_cases(repo, cases, evidences):
    if not isinstance(cases,list) or not 1 <= len(cases) <= 12:
        raise ValueError("需要1—12个具体行为用例")
    available = {(e.symbol.path,e.symbol.name):e.symbol for e in evidences
                 if e.symbol.type in ('function','method') and not _is_test_path(e.symbol.path)}
    if not any(c.get('intent')=='issue' for c in cases if isinstance(c,dict)):
        raise ValueError("缺少针对当前问题的issue用例")
    lines = ['# Model-proposed task tests, frozen before patch generation.',
             'import unittest', '', 'class TaskTests(unittest.TestCase):']
    cleaned=[]
    for index,c in enumerate(cases):
        if not isinstance(c,dict): raise ValueError('用例必须是对象')
        key=(c.get('path'),c.get('symbol'))
        if key not in available: raise ValueError('用例引用了证据外的函数: '+str(key))
        symbol=available[key]
        args,kwargs=c.get('args',[]),c.get('kwargs',{})
        if not isinstance(args,list) or not isinstance(kwargs,dict):raise ValueError('args/kwargs格式错误')
        if any(not isinstance(k,str) or not k.isidentifier() for k in kwargs):raise ValueError('关键字参数无效')
        node=next((n for n in ast.walk(ast.parse(textwrap.dedent(symbol.code)))
                   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==symbol.name),None)
        if node is None:raise ValueError('无法读取用例目标签名')
        parameters=[]
        positional=node.args.posonlyargs+node.args.args
        default_start=len(positional)-len(node.args.defaults)
        for i,arg in enumerate(positional):
            if symbol.type=='method' and i==0 and arg.arg in ('self','cls'):continue
            kind=inspect.Parameter.POSITIONAL_ONLY if i<len(node.args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD
            parameters.append(inspect.Parameter(arg.arg,kind,default=None if i>=default_start else inspect.Parameter.empty))
        if node.args.vararg:parameters.append(inspect.Parameter(node.args.vararg.arg,inspect.Parameter.VAR_POSITIONAL))
        for arg,default in zip(node.args.kwonlyargs,node.args.kw_defaults):
            parameters.append(inspect.Parameter(arg.arg,inspect.Parameter.KEYWORD_ONLY,default=None if default is not None else inspect.Parameter.empty))
        if node.args.kwarg:parameters.append(inspect.Parameter(node.args.kwarg.arg,inspect.Parameter.VAR_KEYWORD))
        try:inspect.Signature(parameters).bind(*args,**kwargs)
        except TypeError as exc:raise ValueError('用例参数与函数签名不符；一个列表参数必须嵌套为args:[[...]]：'+str(exc))
        intent=c.get('intent')
        if intent not in ('issue','preserve'):raise ValueError('用例intent必须是issue或preserve')
        normalization=c.get('normalize','identity')
        if normalization not in ('identity','str','list','sorted'):raise ValueError('不支持的结果转换')
        if normalization == 'str' and not isinstance(c.get('expected'),str):raise ValueError('转换为str后的预期必须是字符串')
        raises=c.get('raises') or ''
        if raises and raises not in ('ValueError','TypeError','KeyError','IndexError','ZeroDivisionError','OverflowError'):
            raise ValueError('不支持的预期异常')
        if not raises and 'expected' not in c:raise ValueError('用例没有具体预期值')
        owner=_find_method_owner(repo,symbol.path,symbol.name) if symbol.type=='method' else None
        loader=_module_loader_code(repo,symbol.path,owner)
        expression=('_obj.' if owner else '_mod.')+symbol.name
        if not symbol.name.isidentifier():raise ValueError('函数名无效')
        lines.append(f'    def test_{intent}_{index:02d}(self):')
        lines.extend('        '+line for line in loader.splitlines())
        call=expression+'(*'+repr(args)+', **'+repr(kwargs)+')'
        if raises:
            lines.extend(['        with self.assertRaises('+raises+'):', '            '+call])
        else:
            lines.append('        actual = '+call)
            if normalization!='identity':lines.append('        actual = '+normalization+'(actual)')
            assertion='assertIs' if c['expected'] is None or isinstance(c['expected'],bool) else 'assertEqual'
            lines.append('        self.'+assertion+'(actual, '+repr(c['expected'])+')')
        lines.append('')
        cleaned.append(dict(c,normalize=normalization))
    code='\n'.join(lines)+'\n'
    # The source includes only a fixed harness and JSON-literal data, never model Python code.
    compile(code,TASK_FILE,'exec')
    return code,cleaned


def generate_task_tests(repo,issue,evidences,understanding,feedback=None):
    client=model_client('task_tests')
    if client is None:raise ValueError('当前问题的模型行为测试需要模型服务')
    context=[{'path':e.symbol.path,'symbol':e.symbol.name,'type':e.symbol.type,
              'is_target':e.source=='intent_target','signature':e.symbol.signature,
              'contract':e.symbol.doc.split('\n\n')[0][:240]}
             for e in evidences if e.symbol.type in ('function','method') and e.source=='intent_target'][:4]
    from .repo import import_repo
    meta=import_repo(repo)
    public=[]
    for rel in meta.python_files:
        if _is_test_path(rel):
            public.append({'path':rel,'code':(Path(repo)/rel).read_text(encoding='utf8')[:2200]})
            if len(public)>=4:break
    proposed_inputs=None
    if feedback:
        # Find new inputs using implementation, but keep the test oracle blind to that implementation.
        import copy
        from .issue_understanding import implementation_excerpt
        input_schema=copy.deepcopy(cases_schema(evidences,public))
        for alternative in input_schema['items']['anyOf']:
            for field in ('expected','raises','intent','normalize'):
                alternative['properties'].pop(field,None)
            alternative['required']=list(alternative['properties'])
        client.purpose='test_input_refinement'
        proposed=chat_structured(client,
            '之前的测试全部通过，尚未复现用户报告。请依据用户描述和实际实现，设计三个新的合法输入来覆盖被遗漏的异常边界。'
            '只输出inputs，每项只有path、symbol、args、kwargs；不能输出预期值、补丁或测试答案。'
            '请改变输入以覆盖用户明确描述的触发条件，而不是重复已通过的普通输入。',
            json.dumps({'functions':[{'path':e.symbol.path,'symbol':e.symbol.name,'implementation':implementation_excerpt(e.symbol)}
                                    for e in evidences if e.source=='intent_target'],
                        'previous_execution':feedback,'original_user_requirement':issue},ensure_ascii=False),
            object_schema({'inputs':input_schema}),temperature=0)
        proposed_inputs=proposed.get('inputs') if isinstance(proposed,dict) else None
        client.purpose='task_tests'
    case_schema=cases_schema(evidences,public)
    data=chat_structured(client,
        "为当前问题设计三个具体的行为测试。返回一个JSON对象，只有cases字段，值为数组。"
        "args是所有位置参数的列表；调用f([1,2])时必须写args:[[1,2]]，不能写args:[1,2]。"
        "每个用例必须含 path、symbol（严格使用给出的名称）、args（位置参数数组）、kwargs（对象）、"
        "intent（固定为issue）、normalize（通常identity）、expected（依据用户要求计算的具体正确返回值）。"
        "若用户要求某输入报错，改用raises字段写内置异常名，不需要expected。"
        "仅测试用户报告的行为，不添加用户未要求的功能或输入类型，不测试其他函数。"
        "选择三个不同的具体输入，覆盖问题边界；现有仓库测试另行执行用于保护已正常工作的行为。"
        "expected必须是正确的JSON字面值，例如布尔true/false、数值、字符串或数组，不能写表达式。"
        "normalize=str仅用于日期等不可JSON表示的返回值，此时expected必须是字符串。"
        "其他情况使用identity，不能通过排序等改变被检查的语义。"
        "不要输出测试源码、补丁或修复步骤。用例预期是修复后的正确行为；它们在有缺陷的代码上失败是正常且必要的。"
        "只按用户要求和公开测试约定写expected；不要把异常名写成expected字符串，预期异常应写raises字段。"
        "若无法确定预期，返回reason说明，不虚构用例。",
        json.dumps(dict({'functions':context,'existing_test_contracts':public,'original_user_requirement':issue},
                        **({'candidate_inputs':proposed_inputs,'instruction':'为这些新输入按用户需求计算正确预期；不依据实现结果改写预期。'} if proposed_inputs else {})),ensure_ascii=False),
        object_schema({'cases':case_schema}),temperature=0)
    if not isinstance(data,dict) or 'cases' not in data:raise ValueError(str((data or {}).get('reason','未生成行为用例')))
    # Review the contract, not the buggy implementation or a candidate patch.
    # Only signatures, requirements and public test assertions are visible here.
    client.purpose='test_review'
    review=chat_structured(client,
        '你是补丁生成之前的测试审核者。只依据用户原始需求、公开API和原有测试审核待测用例。'
        '找出与需求相矛盾的expected、错误的输入类型、参数个数或嵌套层次、错误的normalize转换和不存在的函数。'
        '函数接收一个列表参数时，args必须是包含该列表的数组；f([])对应args:[[]]，不是args:[]。'
        '尤其检查空输入、边界与异常约定；不能根据有缺陷的实现推定期望。'
        'approved表示这些测试是否符合修复后的用户需求，不表示原始代码目前能否通过；测试本来就应暴露缺陷。'
        '返回JSON：approved（布尔值），cases（审核后全部用例，保持path/symbol/args/kwargs/intent/normalize/expected或raises字段），'
        'changes（修改理由字符串数组）。允许修正错误预期，但不得省略用户报告的核心行为来让测试通过。'
        '仍应有三个具体用例，intent=issue。不能确认正确预期时approved=false并解释原因。'
        '未提供独立验收，也没有候选补丁；不要虚构测试通过结果。',
        json.dumps({'original_issue':issue,'api':context,'public_tests':public,'proposed_cases':data['cases']},ensure_ascii=False),
        object_schema({'approved':{'type':'boolean'},'cases':case_schema,'changes':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':160}}}),temperature=0)
    if not isinstance(review,dict) or review.get('approved') is not True:
        raise ValueError('模型测试审核未通过：'+str(review))
    code,cases=compile_cases(repo,review.get('cases'),evidences)
    return code,{'kind':'model_behavioral_cases','model':client.model,'cases':cases,
                 'review':{'approved':True,'changes':review.get('changes',[]),'independent':False},
                 'sha256':hashlib.sha256(code.encode('utf8')).hexdigest(),
                 'independent':False,'frozen_before_patch':True}


def task_command(meta):
    # Full repository tests + new issue tests in one pytest invocation: stable IDs, no duplicates.
    roots=list(meta.test_dirs)
    if '.' in roots:
        roots=[p for p in meta.python_files if Path(p).name.startswith('test_') or Path(p).name.endswith('_test.py')]
    roots=[r for r in roots if r and not r.startswith('__replens_')]
    return 'python -m pytest '+ ' '.join(shlex.quote(r) for r in roots+[TASK_FILE])


def scope_violation(repo,patch_file,evidences):
    """A candidate may change selected definitions, not unrelated callers or module setup."""
    targets=[e.symbol for e in evidences if e.source=='intent_target']
    if not targets:return ''
    try:
        rules=json.loads(Path(patch_file).read_text(encoding='utf8'))
        if not isinstance(rules,dict):return '无法核对补丁修改范围'
        allowed={s.path:{t.name for t in targets if t.path==s.path} for s in targets}
        for rel,rule in rules.items():
            if rel not in allowed:return '补丁修改了问题目标之外的文件: '+rel
            old=(Path(repo)/rel).read_text(encoding='utf8')
            before=ast.parse(old); after=ast.parse(old.replace(rule['from'],rule['to'],1))
            class Mask(ast.NodeTransformer):
                def visit_FunctionDef(self,node):
                    if node.name in allowed[rel]:
                        node.body=[ast.Pass()]
                    return node
                visit_AsyncFunctionDef=visit_FunctionDef
            # Preserve signatures/decorators and unrelated code; imports also require a scoped proposal.
            if ast.dump(Mask().visit(before),include_attributes=False)!=ast.dump(Mask().visit(after),include_attributes=False):
                return '补丁越过已确认函数范围，修改了其他函数、签名或模块代码: '+rel
        return ''
    except Exception as exc:return '补丁范围检查失败: '+str(exc)
