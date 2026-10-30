"""Constrain response structure, never provide answers or reference patches."""
import ast
import textwrap


def object_schema(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def strings(values=None):
    item={'type':'string'}
    if values:item['enum']=list(values)
    return {'type':'array','items':item}


def chat_structured(client,system,user,schema,temperature=0):
    if getattr(client,'supports_schema',False) is True:
        return client.chat_json(system,user,temperature=temperature,schema=schema)
    return client.chat_json(system,user,temperature=temperature)


def cases_schema(evidences,public_tests):
    alternatives=[]
    for evidence in evidences:
        s=evidence.symbol
        if evidence.source!='intent_target' or s.type not in ('function','method'):continue
        node=next((n for n in ast.walk(ast.parse(textwrap.dedent(s.code)))
                   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==s.name),None)
        if node is None:continue
        args=node.args.posonlyargs+node.args.args
        required=len(args)-len(node.args.defaults)
        if s.type=='method' and args and args[0].arg in ('self','cls'):
            args=args[1:];required=max(0,required-1)
        observed=[set() for _ in args]
        for test in public_tests:
            try:tree=ast.parse(test['code'])
            except SyntaxError:continue
            for call in ast.walk(tree):
                if not isinstance(call,ast.Call):continue
                name=call.func.attr if isinstance(call.func,ast.Attribute) else call.func.id if isinstance(call.func,ast.Name) else ''
                if name!=s.name:continue
                for index,value in enumerate(call.args[:len(args)]):
                    try:value=ast.literal_eval(value)
                    except (ValueError,TypeError):continue
                    kind=('boolean' if isinstance(value,bool) else 'number' if isinstance(value,(int,float)) else
                          'string' if isinstance(value,str) else 'array' if isinstance(value,(list,tuple)) else
                          'object' if isinstance(value,dict) else 'null' if value is None else '')
                    if kind:observed[index].add(kind)
        positional={'type':'array','minItems':required,'maxItems':12 if node.args.vararg else len(args),
                    'prefixItems':[({'type':next(iter(kinds))} if len(kinds)==1 else {'anyOf':[{'type':k} for k in sorted(kinds)]}) if kinds else {} for kinds in observed]}
        keywords={'type':'object','properties':{a.arg:{} for a in node.args.kwonlyargs},
                  'required':[a.arg for a,d in zip(node.args.kwonlyargs,node.args.kw_defaults) if d is None],
                  'additionalProperties':bool(node.args.kwarg)}
        alternatives.append(object_schema({'path':{'type':'string','enum':[s.path]},
            'symbol':{'type':'string','enum':[s.name]},'args':positional,'kwargs':keywords,
            'intent':{'type':'string','enum':['issue']},'normalize':{'type':'string','enum':['identity','str','list']},
            'expected':{},'raises':{'type':'string','enum':['','ValueError','TypeError','KeyError','IndexError','ZeroDivisionError','OverflowError']}}))
    if not alternatives:raise ValueError('没有支持参数绑定的目标函数')
    return {'type':'array','minItems':3,'maxItems':3,'items':{'anyOf':alternatives}}
