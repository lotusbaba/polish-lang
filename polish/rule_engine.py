"""Bounded interpreter for the expression/statement subset used by model rules.

No eval, exec, imports, filesystem, network, reflection, or arbitrary host calls.
The only host operations available are the capabilities registered below.
"""
import ast
from collections import ChainMap, deque
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
import inspect
import math
import operator
import re


class RuleError(ValueError):
    pass


class _Return(BaseException):
    def __init__(self, value): self.value = value


class _Continue(BaseException):
    pass


class _Break(BaseException):
    pass


_current = ContextVar('polish_rule_budget', default=None)
_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.Mod: operator.mod}
_COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
            ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
            ast.Is: operator.is_, ast.IsNot: operator.is_not,
            ast.In: lambda a,b: a in b, ast.NotIn: lambda a,b: a not in b}
_ALLOWED = {
    'Module','FunctionDef','arguments','arg','Assign','AnnAssign','AugAssign',
    'Expr','If','For','While','Return','Continue','Break','Pass','Try','ExceptHandler','Raise',
    'Constant','Name','Attribute','Subscript','Slice','List','Tuple','Set','Dict',
    'BoolOp','BinOp','UnaryOp','Compare','IfExp','Call','keyword','JoinedStr','FormattedValue',
    'ListComp','SetComp','DictComp','GeneratorExp','comprehension',
    'Load','Store','And','Or','Not','USub','UAdd',
} | {c.__name__ for c in _BINARY} | {c.__name__ for c in _COMPARE}


@lru_cache(maxsize=128)
def parse_rules(source):
    if len(source) > 250_000:
        raise RuleError('Rule file exceeds size limit')
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise RuleError(f'Invalid rule syntax: {exc}') from exc
    for node in ast.walk(tree):
        if type(node).__name__ not in _ALLOWED:
            raise RuleError(f'Unsupported rule operation: {type(node).__name__}')
        for name in ([node.id] if isinstance(node,ast.Name) else [node.attr] if isinstance(node,ast.Attribute) else [node.name] if isinstance(node,ast.FunctionDef) else [node.arg] if isinstance(node,ast.arg) else []):
            if name.startswith('_'):
                raise RuleError(f'Private names are unavailable in rules: {name}')
        if isinstance(node,ast.Constant) and type(node.value) is int and node.value.bit_length() > 4096:
            raise RuleError('Integer constant exceeds rule numeric limit')
        if isinstance(node,ast.FunctionDef) and (node.decorator_list or node.returns or node.args.posonlyargs or any(a.annotation for a in node.args.args + node.args.kwonlyargs)):
            raise RuleError('Decorators, annotations, and positional-only arguments are unsupported')
        if isinstance(node,ast.comprehension) and node.is_async:
            raise RuleError('Async comprehensions are unsupported')
        if isinstance(node,ast.FormattedValue) and node.conversion not in (-1, 114, 115):
            raise RuleError('Unsupported format conversion')
    names = [n.name for n in tree.body if isinstance(n,ast.FunctionDef)]
    if len(set(names)) != len(names):
        raise RuleError('Duplicate rule function')
    if any(not isinstance(n,(ast.FunctionDef,ast.Assign,ast.Expr)) for n in tree.body):
        raise RuleError('Rule files may only declare functions and constants')
    # Top-level expressions must be documentation and constants must be literal.
    for n in tree.body:
        if isinstance(n,ast.Expr) and not isinstance(n.value,ast.Constant):
            raise RuleError('Top-level rule execution is forbidden')
        if isinstance(n,ast.Assign) and any(isinstance(x,(ast.Call,ast.Attribute,ast.Subscript)) for x in ast.walk(n.value)):
            raise RuleError('Rule constants cannot call capabilities')
    return tree


def load_rules(root, manifest):
    baseline_root = files('polish').joinpath('config/rules')
    import json
    baseline = json.loads(files('polish').joinpath('config/rules.json').read_text())['rules']
    if not isinstance(manifest,dict) or manifest.keys() != baseline.keys():
        raise RuleError('Rules manifest must preserve the engine hook modules')
    result = {}
    for name, filename in manifest.items():
        if not isinstance(filename,str) or not re.fullmatch(r'[a-z][a-z0-9_]*\.rules', filename):
            raise RuleError('Rule filenames must not contain paths')
        source = root.joinpath('rules', filename).read_text()
        tree = parse_rules(source)
        original = parse_rules(baseline_root.joinpath(baseline[name]).read_text())
        def contracts(t):
            return {n.name: ast.dump(n.args, include_attributes=False) for n in t.body if isinstance(n,ast.FunctionDef)}
        if contracts(tree) != contracts(original):
            raise RuleError(f'Rule hooks/signatures changed in {name}')
        result[name] = source
    return result


class _Function:
    def __init__(self, machine, node, closure):
        self.machine, self.node, self.closure = machine, node, closure
        args = node.args
        params = []
        defaults = [inspect.Parameter.empty] * (len(args.args)-len(args.defaults)) + [machine.expr(v,closure) for v in args.defaults]
        for a, default in zip(args.args,defaults):
            params.append(inspect.Parameter(a.arg, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default))
        if args.vararg: params.append(inspect.Parameter(args.vararg.arg,inspect.Parameter.VAR_POSITIONAL))
        for a, default in zip(args.kwonlyargs,args.kw_defaults):
            params.append(inspect.Parameter(a.arg,inspect.Parameter.KEYWORD_ONLY,default=inspect.Parameter.empty if default is None else machine.expr(default,closure)))
        if args.kwarg: params.append(inspect.Parameter(args.kwarg.arg,inspect.Parameter.VAR_KEYWORD))
        self.signature = inspect.Signature(params)

    def __call__(self,*args,**kwargs):
        self.machine.tick()
        bound = self.signature.bind(*args,**kwargs)
        bound.apply_defaults()
        env = self.closure.new_child(dict(bound.arguments))
        try:
            self.machine.block(self.node.body,env)
        except _Return as signal:
            return signal.value


class Machine:
    def __init__(self, arch, module):
        self.arch, self.module = arch, module
        self.capabilities = capabilities(arch)
        self.allowed_calls = {v for v in self.capabilities.values() if callable(v)}
        self.env = ChainMap({}, self.capabilities)
        source = arch.rules[module]
        self.block(parse_rules(source).body,self.env)

    def tick(self):
        budget = _current.get()
        if budget is None:
            raise RuleError('Rules require an execution budget')
        budget[0] -= 1
        if budget[0] < 0:
            raise RuleError('Rule execution exceeded its instruction budget')

    def attr(self, value, name):
        if name.startswith('_'):
            raise RuleError('Private attributes are unavailable')
        from .model import Architecture, Node, Edge, Scenario
        from .simulator import SimulationResult, RequestFailure
        from .model_inputs import ModelInputs
        if isinstance(value,ConfiguredModel):
            return getattr(value,name)
        if isinstance(value,(Architecture,Node,Edge,Scenario,SimulationResult,RequestFailure,ModelInputs)):
            # Data fields are readable; methods are restricted to graph/input access.
            result = getattr(value,name)
            if callable(result) and name not in {'outgoing','incoming','resolve','get','field','present'}:
                raise RuleError(f'Unsupported object method: {name}')
            return result
        methods = {dict: {'get','setdefault','items','keys','values','update','fromkeys'},
                   list: {'append','remove'}, set: {'add','remove'},
                   str: {'startswith','endswith','upper','find'}, deque: {'append','popleft'}}
        for cls, allowed in methods.items():
            if (isinstance(value,cls) or value is cls) and name in allowed:
                return getattr(value,name)
        if value is math and name in {'ceil','isclose','isfinite'}:
            return getattr(value,name)
        raise RuleError(f'Unsupported attribute: {name}')

    def expr(self,n,env):
        self.tick()
        if isinstance(n,ast.Constant): return n.value
        if isinstance(n,ast.Name):
            if n.id not in env: raise RuleError(f'Unknown rule name: {n.id}')
            return env[n.id]
        if isinstance(n,ast.Attribute): return self.attr(self.expr(n.value,env),n.attr)
        if isinstance(n,ast.Subscript): return self.expr(n.value,env)[self.expr(n.slice,env)]
        if isinstance(n,ast.Slice): return slice(*(self.expr(v,env) if v else None for v in (n.lower,n.upper,n.step)))
        if isinstance(n,(ast.List,ast.Tuple,ast.Set)):
            return {ast.List:list,ast.Tuple:tuple,ast.Set:set}[type(n)](self.expr(v,env) for v in n.elts)
        if isinstance(n,ast.Dict):
            result={}
            for key,value in zip(n.keys,n.values):
                if key is None: result.update(self.expr(value,env))
                else: result[self.expr(key,env)] = self.expr(value,env)
            return result
        if isinstance(n,ast.BoolOp):
            for value in n.values:
                result=self.expr(value,env)
                if isinstance(n.op,ast.And) and not result or isinstance(n.op,ast.Or) and result: return result
            return result
        if isinstance(n,ast.BinOp): return self.binary(n.op,self.expr(n.left,env),self.expr(n.right,env))
        if isinstance(n,ast.UnaryOp):
            return {ast.Not:operator.not_,ast.USub:operator.neg,ast.UAdd:operator.pos}[type(n.op)](self.expr(n.operand,env))
        if isinstance(n,ast.Compare):
            left=self.expr(n.left,env)
            for op,value in zip(n.ops,n.comparators):
                right=self.expr(value,env)
                if not _COMPARE[type(op)](left,right):return False
                left=right
            return True
        if isinstance(n,ast.IfExp):return self.expr(n.body if self.expr(n.test,env) else n.orelse,env)
        if isinstance(n,ast.Call):
            fn=self.expr(n.func,env)
            if not isinstance(fn,_Function) and fn not in self.allowed_calls:
                # Only an attribute selected by attr() may expose a bound method.
                if not isinstance(n.func,ast.Attribute):raise RuleError('Unregistered rule capability')
            args=[self.expr(v,env) for v in n.args]
            kwargs={}
            for kw in n.keywords:
                if kw.arg is None:kwargs.update(self.expr(kw.value,env))
                else:kwargs[kw.arg]=self.expr(kw.value,env)
            return fn(*args,**kwargs)
        if isinstance(n,ast.JoinedStr):return ''.join(str(self.expr(v,env)) for v in n.values)
        if isinstance(n,ast.FormattedValue):
            value=self.expr(n.value,env)
            if n.conversion==114:value=repr(value)
            elif n.conversion==115:value=str(value)
            spec=self.expr(n.format_spec,env) if n.format_spec else ''
            if len(spec)>20 or not re.fullmatch(r'[.0-9gf]*',spec):raise RuleError('Unsupported formatting specifier')
            if any(int(digits)>256 for digits in re.findall(r'\d+',spec)):
                raise RuleError('Rule formatting exceeds size limit')
            return format(value,spec)
        if isinstance(n,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
            def rows(i,local):
                if i==len(n.generators):
                    yield (self.expr(n.key,local),self.expr(n.value,local)) if isinstance(n,ast.DictComp) else self.expr(n.elt,local)
                    return
                gen=n.generators[i]
                for value in self.expr(gen.iter,local):
                    self.tick()
                    child=local.new_child()
                    self.assign(gen.target,value,child)
                    if all(self.expr(test,child) for test in gen.ifs):yield from rows(i+1,child)
            values=rows(0,env)
            if isinstance(n,ast.GeneratorExp):return values
            return {ast.ListComp:list,ast.SetComp:set,ast.DictComp:dict}[type(n)](values)
        raise RuleError(f'Unsupported expression: {type(n).__name__}')

    def binary(self,op,left,right):
        if isinstance(op,ast.Mult):
            for sequence,count in ((left,right),(right,left)):
                if isinstance(sequence,(str,list,tuple)) and type(count) is int and len(sequence)*max(0,count)>100_000:
                    raise RuleError('Rule collection exceeds size limit')
            if type(left) is int and type(right) is int and left.bit_length()+right.bit_length()>4096:
                raise RuleError('Rule integer exceeds numeric limit')
        if isinstance(op,ast.Add) and isinstance(left,(str,list,tuple)) and isinstance(right,type(left)) and len(left)+len(right)>100_000:
            raise RuleError('Rule collection exceeds size limit')
        return _BINARY[type(op)](left,right)

    def assign(self,target,value,env):
        if isinstance(target,ast.Name):env[target.id]=value
        elif isinstance(target,(ast.Tuple,ast.List)):
            values=list(value)
            if len(values)!=len(target.elts):raise RuleError('Assignment arity mismatch')
            for t,v in zip(target.elts,values):self.assign(t,v,env)
        elif isinstance(target,ast.Subscript):self.expr(target.value,env)[self.expr(target.slice,env)]=value
        elif isinstance(target,ast.Attribute):
            obj=self.expr(target.value,env)
            from .simulator import SimulationResult
            if not isinstance(obj,(ConfiguredModel,SimulationResult)) or target.attr.startswith('_'):
                raise RuleError('Unsupported attribute assignment')
            setattr(obj,target.attr,value)
        else:raise RuleError('Unsupported assignment target')

    def block(self,body,env):
        for n in body:
            self.tick()
            if isinstance(n,ast.FunctionDef):env[n.name]=_Function(self,n,env)
            elif isinstance(n,ast.Expr):self.expr(n.value,env)
            elif isinstance(n,(ast.Assign,ast.AnnAssign)):
                value=self.expr(n.value,env)
                for target in n.targets if isinstance(n,ast.Assign) else [n.target]:self.assign(target,value,env)
            elif isinstance(n,ast.AugAssign):self.assign(n.target,self.binary(n.op,self.expr(n.target,env),self.expr(n.value,env)),env)
            elif isinstance(n,ast.If):self.block(n.body if self.expr(n.test,env) else n.orelse,env)
            elif isinstance(n,(ast.For,ast.While)):
                def iterations():
                    if isinstance(n,ast.For):yield from self.expr(n.iter,env)
                    else:
                        while self.expr(n.test,env):yield None
                for value in iterations():
                    self.tick()
                    if isinstance(n,ast.For):self.assign(n.target,value,env)
                    try:self.block(n.body,env)
                    except _Continue:continue
                    except _Break:break
                else:self.block(n.orelse,env)
            elif isinstance(n,ast.Return):raise _Return(self.expr(n.value,env) if n.value else None)
            elif isinstance(n,ast.Continue):raise _Continue()
            elif isinstance(n,ast.Break):raise _Break()
            elif isinstance(n,ast.Pass):pass
            elif isinstance(n,ast.Raise):raise self.expr(n.exc,env)
            elif isinstance(n,ast.Try):
                try:
                    try:self.block(n.body,env)
                    except Exception as exc:
                        if isinstance(exc,RuleError):raise
                        for handler in n.handlers:
                            if handler.type is None or isinstance(exc,self.expr(handler.type,env)):
                                if handler.name:env[handler.name]=exc
                                self.block(handler.body,env)
                                break
                        else:raise
                    else:self.block(n.orelse,env)
                finally:self.block(n.finalbody,env)
            else:raise RuleError(f'Unsupported statement: {type(n).__name__}')


def capabilities(arch):
    from fnmatch import fnmatchcase
    from .model import Edge
    from .diagnostics import describe, diagnostic_context, require_inputs, resolve_definition
    from .errors import render_error
    from .model_inputs import ModelInputs
    from .scaling import number, CapacityModel
    from .vendors import product_profile, DynamoCapacity
    from .platforms import PlatformModel
    from .budgets import ResourceBudgets
    from .cache_connections import CacheConnections
    from .runtime_resources import RuntimeResources
    from .simulator import SimulationResult, RequestFailure
    from .cloud_products import validate_cloud_products
    result = dict(math=math,dict=dict,list=list,set=set,tuple=tuple,str=str,int=int,float=float,
                  bool=bool,type=type,isinstance=isinstance,len=len,any=any,all=all,
                  min=min,max=max,sum=sum,sorted=sorted,next=next,map=map,deque=deque,
                  fnmatchcase=fnmatchcase,number=number,describe=describe,
                  diagnostic_context=diagnostic_context,require_inputs=require_inputs,
                  resolve_definition=resolve_definition,render_error=render_error,
                  ModelInputs=ModelInputs,product_profile=product_profile,Edge=Edge,
                  CapacityModel=CapacityModel,DynamoCapacity=DynamoCapacity,
                  PlatformModel=PlatformModel,ResourceBudgets=ResourceBudgets,
                  CacheConnections=CacheConnections,RuntimeResources=RuntimeResources,
                  SimulationResult=SimulationResult,RequestFailure=RequestFailure,
                  validate_cloud_products=validate_cloud_products)
    result['hardware']=lambda a,h:run(a,'scaling','hardware',a,h)
    result['copies']=lambda p:run(arch,'storage','copies',p)
    return result


def _bounded(fn):
    existing=_current.get()
    token=_current.set([1_000_000]) if existing is None else None
    try:
        return fn()
    except RuleError:
        raise
    except (KeyError,TypeError,ValueError,AttributeError,ArithmeticError,RecursionError,StopIteration) as exc:
        raise RuleError(f'Invalid model rule execution: {type(exc).__name__}: {exc}') from exc
    finally:
        if token is not None:_current.reset(token)


def run(arch,module,hook,*args,**kwargs):
    def execute():
        machine=Machine(arch,module)
        machine.allowed_calls.update(v for v in (*args,*kwargs.values()) if callable(v))
        return machine.env[hook](*args,**kwargs)
    return _bounded(execute)


class ConfiguredModel:
    """Adapter exposing the lifecycle hooks declared in a configured rule module."""
    rule_module = None

    def __init__(self,arch,*args,**kwargs):
        def initialize():
            self._machine=Machine(arch,self.rule_module)
            self._machine.allowed_calls.update(v for v in (*args,*kwargs.values()) if callable(v))
            return self._machine.env['initialize'](self,arch,*args,**kwargs)
        _bounded(initialize)

    def __getattr__(self,name):
        machine=self.__dict__.get('_machine')
        if machine is not None and name in machine.env.maps[0] and isinstance(machine.env[name],_Function):
            # Bound hooks remain registered capabilities even when called by another model.
            def bound(*args,**kwargs):return _bounded(lambda:machine.env[name](self,*args,**kwargs))
            return bound
        raise AttributeError(name)
