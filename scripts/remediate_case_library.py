"""逐例静态整改迁移资产。仅解析本项目来源文本，不导入旧工程或接触设备。

python scripts/remediate_case_library.py
生成可复查台账，并将能够完整表达的 Pro450 函数/数据行转换为禁用待验收场景。
原聚合定义及参数原样保留，以 superseded_by / partial_scenarios 记录映射。
"""
from __future__ import annotations

import ast
from collections import Counter
import copy
import hashlib
import json
import operator
from pathlib import Path

from workbench.models import CaseDocument

ROOT = Path(__file__).resolve().parents[1] / 'src/workbench/case_library'


class Blocked(ValueError):
    pass


def symbol(node):
    return ast.unparse(node)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')


def simple_value(node, environment):
    """只有常量和参数过滤表达式；不使用 Python eval。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in environment:
        value=environment[node.id]
        if isinstance(value,dict) and '$ref' in value:
            raise Blocked('设备返回值不能在静态参数展开时求值')
        return value
    if isinstance(node, ast.Subscript):
        return simple_value(node.value, environment)[simple_value(node.slice, environment)]
    if isinstance(node, ast.Attribute) and symbol(node) in environment:
        return environment[symbol(node)]
    if isinstance(node, (ast.List, ast.Tuple)):
        values=[simple_value(n, environment) for n in node.elts]
        return values if isinstance(node,ast.List) else tuple(values)
    if isinstance(node, ast.Dict):
        return {simple_value(k,environment):simple_value(v,environment) for k,v in zip(node.keys,node.values)}
    if isinstance(node, ast.UnaryOp):
        value=simple_value(node.operand,environment)
        if isinstance(node.op,ast.Not):return not value
        if isinstance(node.op,ast.USub):return -value
        if isinstance(node.op,ast.UAdd):return +value
    if isinstance(node,ast.BinOp):
        operations={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}
        if type(node.op) in operations:return operations[type(node.op)](simple_value(node.left,environment),simple_value(node.right,environment))
    if isinstance(node,ast.BoolOp):
        values=[simple_value(n,environment) for n in node.values]
        return all(values) if isinstance(node.op,ast.And) else any(values)
    if isinstance(node,ast.Compare):
        ops={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}
        value=simple_value(node.left,environment)
        for op,right in zip(node.ops,node.comparators):
            if type(op) not in ops:raise Blocked('不支持的参数比较')
            next_value=simple_value(right,environment)
            if not ops[type(op)](value,next_value):return False
            value=next_value
        return True
    if isinstance(node,ast.IfExp):
        return simple_value(node.body if simple_value(node.test,environment) else node.orelse,environment)
    if isinstance(node,ast.Call):
        if isinstance(node.func,ast.Attribute) and node.func.attr=='get' and len(node.args) in (1,2):
            value=simple_value(node.func.value,environment)
            return value.get(*[simple_value(n,environment) for n in node.args])
        if isinstance(node.func,ast.Name) and node.func.id=='eval' and len(node.args)==1:
            value=simple_value(node.args[0],environment)
            if not isinstance(value,str):raise Blocked('原 eval 输入不是字符串，需核对原预期异常')
            try:return ast.literal_eval(value)
            except (ValueError,SyntaxError) as exc:raise Blocked('eval 输入不是安全字面量，需显式类型数据规则') from exc
        if isinstance(node.func,ast.Name) and node.func.id=='len' and len(node.args)==1:
            return len(simple_value(node.args[0],environment))
    raise Blocked('需受控运行期表达式：'+symbol(node))


def selected_rows(fn, rows):
    decorators=[d for d in fn.decorator_list if isinstance(d,ast.Call) and symbol(d.func)=='pytest.mark.parametrize']
    if len(decorators)!=1 or len(decorators[0].args)<2:
        raise Blocked('需核对非单一参数化或无参数化的测试函数')
    comp=decorators[0].args[1]
    if not isinstance(comp,ast.ListComp) or len(comp.generators)!=1:
        raise Blocked('非标准单层参数过滤')
    gen=comp.generators[0]
    if not isinstance(gen.target,ast.Name) or not isinstance(gen.iter,ast.Name) or gen.iter.id!='cases':
        raise Blocked('参数数据来源不是直接 cases，需显式绑定')
    result=[]
    for i,row in enumerate(rows):
        environment={gen.target.id:row}
        if all(simple_value(condition,environment) for condition in gen.ifs):result.append(i)
    return result


def sdk(node):
    return isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Attribute) and isinstance(node.func.value.value,ast.Name) and node.func.value.value.id in ('device','dev','self') and node.func.value.attr=='mc'


class Compiler:
    def __init__(self, row):
        self.environment={'case':row,'device.speed':50,'self.speed':50,'device.zero_angles':[0,0,0,0,0,0],'self.zero_angles':[0,0,0,0,0,0]}
        self.steps=[]
        self.phase='execute'
        self.methods={}
        self.expected_exception=''
        self.writes=[]
        self.assertions=0
        self.restore_writes=[]
        self.verified_restore_effects=set()

    def add(self,kind,**fields):
        phase='verify' if kind=='assert' and self.phase=='execute' else self.phase
        step={'id':f'step-{len(self.steps)+1}','kind':kind,'phase':phase,'name':fields.pop('name',kind),**fields}
        self.steps.append(step)
        return step

    def method(self,name,parameters,save_as=''):
        self.methods[name]='1.0'
        self.add('method',method=name,parameters=parameters,save_as=save_as,timeout=30,name=name)

    def call(self,node):
        name=node.func.attr
        if name in ('close','connect_socket'):
            raise Blocked('测试体含连接生命周期操作，需独立审查')
        args=[self.value(n) for n in node.args]
        if any(k.arg is None for k in node.keywords):raise Blocked('动态关键字参数')
        kwargs={k.arg:self.value(k.value) for k in node.keywords}
        variable=f'result_{len(self.steps)+1}'
        self.add('read' if name.startswith(('get_','is_')) else 'call',api=name,args=args,kwargs=kwargs,save_as=variable,expected_exception=self.expected_exception,name=name)
        if not name.startswith(('get_','is_')):
            (self.restore_writes if self.phase=='restore' else self.writes).append(name)
        return {'$ref':variable}

    def value(self,node):
        if sdk(node):return self.call(node)
        if isinstance(node,ast.Name) and node.id in self.environment:return self.environment[node.id]
        if isinstance(node,ast.Subscript):
            base=self.value(node.value); key=simple_value(node.slice,self.environment)
            if isinstance(base,dict) and set(base)=={'$ref'} and isinstance(key,(int,str)):
                return {'$ref':base['$ref']+'.'+str(key)}
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='len' and len(node.args)==1:
            v=self.value(node.args[0])
            if isinstance(v,dict) and '$ref' in v:
                variable=f'length_{len(self.steps)+1}'
                self.method('value_length',{'value':v},variable)
                return {'$ref':variable}
        return simple_value(node,self.environment)

    def restore_defaults(self):
        for name,expected in [('fresh_mode',0),('debug_state',0),('movement_type',1)]:
            self.add('call',name='恢复 '+name,api='set_'+name,args=[expected])
            variable=f'restored_{name}_{len(self.steps)}'
            self.add('read',name='读回恢复 '+name,api='get_'+name,save_as=variable)
            self.add('assert',name='验证恢复 '+name,assertion='value_eq',actual={'$ref':variable},expected=expected)
            self.restore_writes.append('set_'+name)
            self.verified_restore_effects.add('set_'+name)

    def statements(self,body):
        for n in body:
            try:self.statement(n)
            except (Blocked,ValueError,KeyError,TypeError,IndexError,ZeroDivisionError) as exc:
                raise Blocked(f'源行 {getattr(n,"lineno",0)}：{exc}') from exc

    def statement(self,n):
        if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name):
            if isinstance(n.value,ast.IfExp) and any(sdk(v) for v in ast.walk(n.value)):
                raise Blocked('运行期条件赋值需显式有界条件能力')
            self.environment[n.targets[0].id]=self.value(n.value)
        elif isinstance(n,ast.With):
            if len(n.items)!=1:raise Blocked('组合上下文需单独核对')
            c=n.items[0].context_expr
            if isinstance(c,ast.Call) and symbol(c.func)=='allure.step':self.statements(n.body)
            elif isinstance(c,ast.Call) and symbol(c.func)=='pytest.raises' and len(c.args)==1 and isinstance(c.args[0],ast.Name):
                old=self.expected_exception;self.expected_exception=c.args[0].id
                before=len(self.steps);self.statements(n.body);self.expected_exception=old
                if len(self.steps)!=before+1 or not self.steps[-1].get('expected_exception'):raise Blocked('预期异常块不是单一SDK调用')
                self.assertions+=1
            else:raise Blocked('不支持的上下文 '+symbol(c))
        elif isinstance(n,ast.If):
            self.statements(n.body if simple_value(n.test,self.environment) else n.orelse)
        elif isinstance(n,ast.Assert):
            t=n.test
            if isinstance(t,ast.Compare) and len(t.ops)==1 and isinstance(t.ops[0],ast.Eq):
                self.add('assert',name='原值相等断言',assertion='value_eq',actual=self.value(t.left),expected=self.value(t.comparators[0]))
            elif isinstance(t,ast.Call) and symbol(t.func)=='isinstance' and len(t.args)==2 and isinstance(t.args[1],ast.Name) and t.args[1].id in ('int','float','str','list','dict','bool'):
                self.add('assert',name='原类型断言',assertion='instance',actual=self.value(t.args[0]),expected=t.args[1].id)
            else:raise Blocked('未支持的原断言 '+symbol(t))
            self.assertions+=1
        elif isinstance(n,ast.Expr) and isinstance(n.value,ast.Call):
            c=n.value; name=symbol(c.func)
            if name.startswith('logger.') or name=='allure.attach':
                # 附件/日志中嵌套的真实读取也保留为证据，不执行日志Python。
                for nested in ast.walk(c):
                    if sdk(nested):self.call(nested)
            elif sdk(c):self.call(c)
            elif name in ('device.wait','dev.wait'):
                if c.args or c.keywords:raise Blocked('自定义等待参数需核对')
                self.add('wait',timeout=30,name='有界等待原运动完成')
            elif name in ('device.default_settings','dev.default_settings'):
                if self.phase!='restore':raise Blocked('前置默认设置需显式适配')
                self.restore_defaults()
            elif name in ('device.go_zero','dev.go_zero'):
                raise Blocked('原回零恢复没有可确认的读回容差，需补恢复验证依据')
            elif name=='time.sleep' and len(c.args)==1:
                seconds=simple_value(c.args[0],self.environment)
                if type(seconds) not in (int,float) or not 0<=seconds<=30:raise Blocked('原固定等待超出受控上限')
                self.method('delay',{'seconds':seconds})
            elif name=='assert_almost_equal' and len(c.args)>=3:
                self.add('assert',name='原容差断言',assertion='approx',actual=self.value(c.args[0]),expected=self.value(c.args[1]),tolerance=simple_value(c.args[2],self.environment))
                self.assertions+=1
            else:raise Blocked('未适配辅助方法 '+name)
        elif isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str):return
        elif isinstance(n,ast.Pass):return
        else:raise Blocked('未支持控制流程 '+type(n).__name__)


def fixtures(tree, compiler):
    found=[f for f in tree.body if isinstance(f,ast.FunctionDef) and any(symbol(d).startswith('pytest.fixture') for d in f.decorator_list)]
    setup,restore=[],[]
    for fn in found:
        yielded=False
        for n in fn.body:
            if isinstance(n,ast.Expr) and isinstance(n.value,ast.Yield):yielded=True;continue
            if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and symbol(n.value.func)=='Mycobot450Base':continue
            if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and symbol(n.value.func).endswith('.mc.close'):continue
            (restore if yielded else setup).append(n)
        if not yielded:raise Blocked(f'fixture {fn.name} 无明确yield边界')
    return setup,restore


def source_features(tree):
    features=set()
    for n in ast.walk(tree):
        if isinstance(n,(ast.If,ast.For,ast.While,ast.Try)):features.add(type(n).__name__)
        if isinstance(n,ast.Call):
            name=symbol(n.func)
            if name in ('eval','input','pytest.raises','prompt_continue'):features.add(name)
            if name.endswith(('.go_zero','.default_settings','.wait')):features.add(name.split('.')[-1])
        if isinstance(n,ast.Attribute) and n.attr in ('ml','mr'):features.add('dual_arm')
    return sorted(features)


def semantic_findings(tree):
    """逐个保留转换依据，而不是只给转换器遇到的第一个不支持原因。"""
    findings=[]
    for node in ast.walk(tree):
        code=message=None
        if isinstance(node,ast.If):
            code='conditional_branch';message='保留条件及两侧流程；仅参数条件可静态展开，设备返回条件需受控运行期能力'
        elif isinstance(node,(ast.For,ast.While)):
            code='bounded_iteration';message='核对循环边界、超时与取消点，不能丢弃循环体'
        elif isinstance(node,ast.Try):
            code='exception_lifecycle';message='保留try/except/finally的异常与恢复生命周期'
        elif isinstance(node,ast.Call):
            name=symbol(node.func)
            if name=='eval':code='literal_or_typed_parameter';message='旧eval只能安全解析字面量；特殊值或计算表达式需明确类型规则'
            elif name=='pytest.raises':code='expected_sdk_exception';message='保留具体异常类型、调用参数和未抛异常的失败判定'
            elif name=='input' or 'prompt_continue' in name:code='manual_confirmation';message='保留人工提示、超时、拒绝/跳过和恢复语义'
            elif name.endswith('.go_zero'):code='restore_readback_evidence';message='原回零动作需确认恢复读回与容差依据，不能仅凭调用返回标恢复成功'
            elif name.endswith('.default_settings'):code='default_state_restore';message='逐项展开默认设置并建立每个修改状态的对应读回验证'
            elif name.endswith('.wait') or name=='time.sleep':code='bounded_wait';message='保留实际等待含义，并提供超时/取消边界'
            elif isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Attribute) and node.func.value.attr in ('ml','mr'):
                code='explicit_arm_channel';message='保留左右臂通道与各自状态/恢复，不能降成当前单侧Adapter'
        if code:
            findings.append({'code':code,'line':getattr(node,'lineno',0),'expression':symbol(node)[:500],'message':message})
    return findings


def fixture_evidence(root,refs):
    evidence=[]
    for ref in refs:
        if not ref.endswith('conftest.py.txt') and not Path(ref).name.startswith('test_'):continue
        tree=ast.parse((root/ref).read_text(encoding='utf-8-sig'))
        for fn in tree.body:
            if not isinstance(fn,ast.FunctionDef) or not any(symbol(d).startswith('pytest.fixture') for d in fn.decorator_list):continue
            setup=[];restore=[];yielded=False
            for node in fn.body:
                if isinstance(node,ast.Expr) and isinstance(node.value,ast.Yield):yielded=True;continue
                calls=[symbol(n.func) for n in ast.walk(node) if isinstance(n,ast.Call) and not symbol(n.func).startswith('logger.')]
                (restore if yielded else setup).extend(calls)
            evidence.append({'source':ref,'function':fn.name,'line':fn.lineno,'setup_calls':setup,'restore_calls':restore,'findings':semantic_findings(fn)})
    return evidence


def run(root=ROOT):
    manifest_path=root/'migration-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    originals=[(p,json.loads(p.read_text(encoding='utf-8'))) for p in sorted(root.glob('cases/*/migrated-*.json'))]
    entries=[]; generated=[]; references=set(); totals=Counter()
    for path,doc in originals:
        migration=doc['migration']; refs=migration.get('source_files',[]); references.update(refs)
        record={'original_id':doc['id'],'original_model':migration['original_model'],'workbook':migration['workbook'],'sheet':migration['sheet'],'source_rows':migration['rows'],'data_row_count':len(doc['datasets']),'batch':'pro450' if migration['original_model']=='mycobot_450' else 'other_models_or_attachments','original_state':'converted_readonly' if doc.get('enabled') else 'needs_review','hardware_acceptance':'not_verified','functions':[],'issues':[],'fixture_evidence':fixture_evidence(root,refs),'generated_case_ids':[]}
        pyrefs=[ref for ref in refs if ref.startswith('sources/testcases/') and Path(ref).name.startswith('test_')]
        covered_rows=set(); failures=False
        if not pyrefs:record['issues'].append({'code':'source_binding_missing','message':'该工作表没有可静态关联的测试来源，需核对46份未引用来源与原绑定'})
        for ref in pyrefs:
            tree=ast.parse((root/ref).read_text(encoding='utf-8-sig'))
            functions=[f for f in tree.body if isinstance(f,ast.FunctionDef) and f.name.startswith('test_')]
            for fn in functions:
                item={'source':ref,'function':fn.name,'line':fn.lineno,'features':source_features(fn),'semantic_findings':semantic_findings(fn),'matching_source_rows':[],'converted_rows':[],'blockers':[]}
                try:
                    bindings=[]
                    for assignment in tree.body:
                        if isinstance(assignment,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='cases' for t in assignment.targets) and isinstance(assignment.value,ast.Call) and symbol(assignment.value.func)=='get_test_data_from_excel' and len(assignment.value.args)>1:
                            bindings.append(ast.literal_eval(assignment.value.args[1]))
                    if bindings != [migration['sheet']]:
                        raise Blocked('源 cases 的工作表绑定与当前迁移定义不一致，需核对原数据来源')
                    indices=selected_rows(fn,doc['datasets'])
                    item['matching_source_rows']=[migration['rows'][i] for i in indices]
                except (Blocked,ValueError,TypeError,KeyError,AttributeError) as exc:
                    item['blockers'].append({'code':'parameter_binding','message':str(exc)});failures=True;record['functions'].append(item);continue
                if not indices:
                    item['blockers'].append({'code':'no_matching_rows','message':'原参数过滤在该工作表没有匹配行；不臆造数据'})
                for i in indices:
                    row_number=migration['rows'][i]
                    if doc.get('enabled'):
                        item['converted_rows'].append(row_number);covered_rows.add(i);continue
                    if migration['original_model']!='mycobot_450':
                        reason='等待对应机型/宿主/左右臂能力适配及原fixture逐项转换'
                        item['blockers'].append({'code':'model_batch_pending','row':row_number,'message':reason});failures=True;continue
                    try:
                        compiler=Compiler(doc['datasets'][i])
                        before,after=fixtures(tree,compiler)
                        compiler.phase='setup';compiler.statements(before)
                        compiler.phase='execute';compiler.statements(fn.body)
                        compiler.phase='restore';compiler.statements(after)
                        if not compiler.assertions:raise Blocked('原函数没有可转换的有效断言，需明确判定依据')
                        uncovered=sorted(set(compiler.writes)-compiler.verified_restore_effects)
                        if uncovered:raise Blocked('状态修改缺少对应的finally恢复及读回验证：'+', '.join(uncovered))
                        key='scenario-'+hashlib.sha256((doc['id']+'|'+ref+'|'+fn.name+'|'+str(row_number)).encode()).hexdigest()[:24]
                        child=copy.deepcopy(doc)
                        # 原聚合条目的归档/承接状态不属于生成场景。
                        # 重跑时原条目已有 superseded_by，必须清除，避免子场景继承自引用。
                        for field in ('superseded_by','partial_scenarios','archived','archive_reason','archived_at','superseded_at'):
                            child['migration'].pop(field,None)
                        child.update(id=key,name=(str(doc['datasets'][i].get('title') or doc['name'])+' / '+fn.name)[:200],enabled=False,revision=1,category='exception' if any(s.get('expected_exception') for s in compiler.steps) else doc['category'],datasets=[copy.deepcopy(doc['datasets'][i])],steps=compiler.steps,method_versions=compiler.methods)
                        child['migration'].update(status='converted_pending_hardware',parent_id=doc['id'],source_function=fn.name,test_functions=[fn.name],rows=[row_number],source_files=refs,review_reasons=[],remediation={'state':'converted','hardware_acceptance':'not_verified','checks':['source_function_and_filter_mapped','literal_only_parameter_expansion','original_assertions_preserved','fixture_restoration_checked'],'tool':'remediate_case_library.py'})
                        child=CaseDocument.model_validate(child).model_dump()
                        dump(path.parent/(key+'.json'),child)
                        generated.append(key);record['generated_case_ids'].append(key);item['converted_rows'].append(row_number);covered_rows.add(i)
                    except (Blocked,ValueError,KeyError,TypeError,IndexError,AttributeError) as exc:
                        item['blockers'].append({'code':'semantic_conversion','row':row_number,'message':str(exc)});failures=True
                record['functions'].append(item)
        record['unmapped_source_rows']=[migration['rows'][i] for i in range(len(doc['datasets'])) if i not in covered_rows]
        if doc.get('enabled'):
            record['state']='converted_readonly_existing';totals['existing_converted']+=1
        elif record['generated_case_ids'] and not failures and len(covered_rows)==len(doc['datasets']):
            record['state']='superseded';totals['fully_remediated_originals']+=1
            migration['superseded_by']=record['generated_case_ids']
        elif record['generated_case_ids']:
            record['state']='partial';totals['partially_remediated_originals']+=1
            migration['partial_scenarios']=record['generated_case_ids']
        else:
            record['state']='blocked';totals['blocked_originals']+=1
        migration.pop('superseded_by',None)
        migration.pop('partial_scenarios',None)
        if record['state']=='superseded':migration['superseded_by']=record['generated_case_ids']
        elif record['generated_case_ids']:migration['partial_scenarios']=record['generated_case_ids']
        migration['remediation']={'state':record['state'],'hardware_acceptance':'not_verified','ledger':'remediation-ledger.json','covered_source_rows':[migration['rows'][i] for i in sorted(covered_rows)],'remaining_source_rows':record['unmapped_source_rows'],'generated_case_ids':record['generated_case_ids']}
        # 原聚合条目继续保留，原始参数和来源不被新场景覆盖。
        if not doc.get('enabled'):
            unique=sorted({b['message'] for f in record['functions'] for b in f['blockers']})
            migration['review_reasons']=unique[:12] or ['已由分场景定义承接，原聚合定义保留作为来源；请查看整改台账']
        dump(path,doc)
        entries.append(record)
    unmapped=[]
    for source in manifest['sources']:
        if source['reference'] in references:continue
        text=(root/source['reference']).read_text(encoding='utf-8-sig');tree=ast.parse(text)
        unmapped.append({'source':source['file'],'reference':source['reference'],'sha256':source['sha256'],'state':'binding_review_required','functions':[{'name':n.name,'line':n.lineno,'features':source_features(n),'semantic_findings':semantic_findings(n)} for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')],'reason':source.get('binding','来源存在但未被现有工作表定义引用；核对绑定、缺数据或过时场景，不自动丢弃')})
    summary={'original_documents':len(originals),'original_data_rows':sum(len(d['datasets']) for _,d in originals),'existing_converted':totals['existing_converted'],'fully_remediated_originals':totals['fully_remediated_originals'],'partially_remediated_originals':totals['partially_remediated_originals'],'blocked_originals':totals['blocked_originals'],'generated_scenarios':len(set(generated)),'unmapped_source_files':len(unmapped),'hardware_verified':0}
    # 旧版工具产生但本次不再满足严格恢复检查的场景必须撤回，不能遗留可误认资产。
    for path in root.glob('cases/*/scenario-*.json'):
        if path.stem not in generated:
            stale=json.loads(path.read_text(encoding='utf-8'))
            if stale.get('migration',{}).get('remediation',{}).get('tool')=='remediate_case_library.py':
                path.unlink()
    ledger={'format':'elephant-remediation-v1','scope':'全部原始定义逐函数/参数行覆盖；Pro450优先转换；未运行旧代码、SDK或设备','summary':summary,'entries':entries,'unmapped_sources':unmapped}
    dump(root/'remediation-ledger.json',ledger)
    manifest['remediation_summary']=summary
    manifest['remediation_ledger']='remediation-ledger.json'
    dump(manifest_path,manifest)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':run()
