"""Conservative return-shape contracts from annotations and public assertions.

Unknown expressions remain unknown; this is not a complete Python type checker.
"""
import ast
from pathlib import Path
from .repo import import_repo
from .retriever import _is_test_path

KINDS=('boolean','number','string','sequence','mapping','object','null','unknown')
BUILTINS={'bool':'boolean','int':'number','float':'number','complex':'number',
          'str':'string','list':'sequence','tuple':'sequence','set':'sequence','dict':'mapping',
          'None':'null','NoneType':'null','object':'object'}
CONSTRUCTORS={'datetime.date':'object','datetime.datetime':'object','datetime.time':'object',
              'datetime.timedelta':'object','decimal.Decimal':'number','fractions.Fraction':'number',
              'uuid.UUID':'object','pathlib.Path':'object'}


def aliases(tree):
    result={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            for entry in node.names:result[entry.asname or entry.name.split('.')[0]]=entry.name
        elif isinstance(node,ast.ImportFrom) and node.module:
            for entry in node.names:result[entry.asname or entry.name]=node.module+'.'+entry.name
    return result


def dotted(node,names):
    if isinstance(node,ast.Name):return names.get(node.id,node.id)
    if isinstance(node,ast.Attribute):return dotted(node.value,names)+'.'+node.attr
    return ''


def annotation_kind(node,names):
    if node is None:return set()
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return {BUILTINS[node.value]} if node.value in BUILTINS else set()
    if isinstance(node,ast.Constant) and node.value is None:return {'null'}
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.BitOr):return annotation_kind(node.left,names)|annotation_kind(node.right,names)
    if isinstance(node,ast.Subscript):
        key=dotted(node.value,names)
        if key.split('.')[-1] in ('Optional','Union'):
            parts=node.slice.elts if isinstance(node.slice,ast.Tuple) else [node.slice]
            kinds=set().union(*(annotation_kind(part,names) for part in parts))
            return kinds|({'null'} if key.endswith('Optional') else set())
        key=key.split('.')[-1].lower()
        return {BUILTINS[key]} if key in BUILTINS else set()
    key=dotted(node,names)
    kind=BUILTINS.get(key) or CONSTRUCTORS.get(key)
    return {kind} if kind else set()


def value_kind(node,names):
    if isinstance(node,ast.Call):return annotation_kind(node.func,names)
    try:value=ast.literal_eval(node)
    except (ValueError,TypeError):return set()
    if value is None:return {'null'}
    if isinstance(value,bool):return {'boolean'}
    if isinstance(value,(int,float,complex)):return {'number'}
    if isinstance(value,str):return {'string'}
    if isinstance(value,(list,tuple,set)):return {'sequence'}
    if isinstance(value,dict):return {'mapping'}
    return set()


def output_kinds(repo,symbol):
    root=Path(repo);tree=ast.parse((root/symbol.path).read_text(encoding='utf8'))
    names=aliases(tree);kinds=set()
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.lineno==symbol.start_line:
            kinds |= annotation_kind(node.returns,names)
    module=Path(symbol.path).with_suffix('').as_posix().replace('/','.')
    modules={module}
    if module.startswith(('src.','lib.')):modules.add(module.split('.',1)[1])
    targets={name+'.'+symbol.name for name in modules}
    for relative in import_repo(repo).python_files:
        if not _is_test_path(relative):continue
        try:test=ast.parse((root/relative).read_text(encoding='utf8'))
        except (SyntaxError,OSError):continue
        imports=aliases(test)
        def is_target(node):return isinstance(node,ast.Call) and dotted(node.func,imports) in targets
        for node in ast.walk(test):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                method=node.func.attr
                if method in ('assertTrue','assertFalse') and node.args and is_target(node.args[0]):kinds.add('boolean')
                elif method in ('assertEqual','assertIs') and len(node.args)>=2:
                    if is_target(node.args[0]):kinds |= value_kind(node.args[1],imports)
                    if is_target(node.args[1]):kinds |= value_kind(node.args[0],imports)
                elif method=='assertIsInstance' and len(node.args)>=2 and is_target(node.args[0]):kinds |= annotation_kind(node.args[1],imports)
            elif isinstance(node,ast.Compare) and len(node.ops)==1 and isinstance(node.ops[0],ast.Eq):
                if is_target(node.left):kinds |= value_kind(node.comparators[0],imports)
    return kinds
