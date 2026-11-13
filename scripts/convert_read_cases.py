"""对迁移资产中的简单只读场景做保守的 AST 等价转换，不运行旧代码。

仅覆盖单函数、全部参数行均匹配、无状态变更/条件/循环/恢复依赖的场景。
其余资产继续明确禁用；不根据 Excel 的 api 名猜测完整测试逻辑。
"""
import ast
from collections import Counter
import json
from pathlib import Path

from workbench.models import CaseDocument


class Unsupported(ValueError):
    pass


def sdk_call(node):
    return isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Attribute) and isinstance(node.func.value.value,ast.Name) and node.func.value.value.id == 'device' and node.func.value.attr == 'mc'


def compile_case(doc, library):
    refs = [p for p in doc['migration']['source_files'] if p.startswith('sources/testcases/') and Path(p).name.startswith('test_')]
    if len(refs) != 1 or doc['model'] == 'mercury' or 'gripper' in doc['migration']['original_model'] or 'Attachments' in doc['migration']['original_model'] or 'hand' in doc['migration']['original_model']:
        raise Unsupported('非单文件标准单臂场景，保留待完善')
    tree = ast.parse((library/refs[0]).read_text(encoding='utf-8-sig'))
    functions = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
    if len(functions) != 1:
        raise Unsupported('一个工作表关联多个测试函数，需保留各参数过滤与执行分支')
    fn = functions[0]
    # 单函数的参数过滤必须覆盖全部行；不能丢弃未匹配数据。
    parameterized = False
    for dec in fn.decorator_list:
        if isinstance(dec,ast.Call) and ast.unparse(dec.func) == 'pytest.mark.parametrize':
            parameterized = True
            if len(dec.args)<2 or not isinstance(dec.args[1],ast.ListComp):
                raise Unsupported('参数化表达式需人工转换')
            comp=dec.args[1]
            if len(comp.generators) != 1 or not isinstance(comp.generators[0].iter,ast.Name) or comp.generators[0].iter.id != 'cases':
                raise Unsupported('非标准 cases 参数过滤')
            for condition in comp.generators[0].ifs:
                if not isinstance(condition,ast.Compare) or len(condition.ops)!=1 or not isinstance(condition.ops[0],ast.Eq) or not isinstance(condition.left,ast.Subscript) or not isinstance(condition.left.value,ast.Name) or condition.left.value.id != comp.generators[0].target.id:
                    raise Unsupported('参数条件无法完整静态映射')
                key=ast.literal_eval(condition.left.slice)
                expected=ast.literal_eval(condition.comparators[0])
                if any(row.get(key) != expected for row in doc['datasets']):
                    raise Unsupported('工作表包含该函数未覆盖的数据行')
    if not parameterized:
        raise Unsupported('没有标准参数化绑定')
    # 文件内 fixture 可能包含状态设置、恢复或额外等待，不能静默忽略。
    fixtures=[n for n in tree.body if isinstance(n,ast.FunctionDef) and any(ast.unparse(d).startswith('pytest.fixture') for d in n.decorator_list)]
    for fixture in fixtures:
        for call in (n for n in ast.walk(fixture) if isinstance(n,ast.Call)):
            name=ast.unparse(call.func)
            if name.startswith('logger.') or name.endswith('Base') or name.endswith('.mc.close'):
                continue
            raise Unsupported('文件 fixture 含额外前置/恢复逻辑')
    refs_fixture=[r for r in doc['migration']['source_files'] if r.startswith('sources/testcases/') and r.endswith('conftest.py.txt')]
    for ref in refs_fixture:
        fixture_tree=ast.parse((library/ref).read_text(encoding='utf-8-sig'))
        for call in (n for n in ast.walk(fixture_tree) if isinstance(n,ast.Call)):
            name=ast.unparse(call.func)
            if ('.mc.' in name and not name.endswith('.mc.close')) or name.endswith(('.go_zero','.default_settings','.wait','.power_on')):
                raise Unsupported('机型 fixture 有设备操作依赖')
    steps, aliases = [], {}
    def value(node):
        if isinstance(node,ast.Constant):
            return node.value
        if isinstance(node,(ast.List,ast.Tuple,ast.Dict,ast.UnaryOp)):
            return ast.literal_eval(node)
        if isinstance(node,ast.Name) and node.id in aliases:
            return aliases[node.id]
        if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and node.value.id == 'case':
            key=ast.literal_eval(node.slice)
            if not isinstance(key,str) or any(key not in row for row in doc['datasets']):
                raise Unsupported('参数字段缺失')
            return {'$ref':'params.'+key}
        raise Unsupported('表达式需人工转换：'+ast.unparse(node))
    def add(kind, **fields):
        steps.append({'id':f'converted-{len(steps)+1}','name':fields.pop('name', '读取 SDK' if kind=='read' else '核对原断言'),'kind':kind,**fields})
    def read(call, target='result'):
        if not sdk_call(call) or not call.func.attr.startswith(('get_','is_')):
            raise Unsupported('含状态修改或非标准 SDK 调用')
        if any(k.arg is None for k in call.keywords):
            raise Unsupported('动态关键字参数')
        add('read',name=call.func.attr,api=call.func.attr,args=[value(a) for a in call.args],kwargs={k.arg:value(k.value) for k in call.keywords},save_as=target)
        aliases[target]={'$ref':target}
    def statements(body):
        for node in body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                if sdk_call(node.value):
                    read(node.value,node.targets[0].id)
                else:
                    aliases[node.targets[0].id]=value(node.value)
            elif isinstance(node,ast.With) and all(isinstance(item.context_expr,ast.Call) and ast.unparse(item.context_expr.func)=='allure.step' for item in node.items):
                statements(node.body)
            elif isinstance(node,ast.Assert):
                test=node.test
                if isinstance(test,ast.Compare) and len(test.ops)==1 and isinstance(test.ops[0],ast.Eq):
                    add('assert',phase='verify',assertion='value_eq',actual=value(test.left),expected=value(test.comparators[0]))
                elif isinstance(test,ast.Call) and isinstance(test.func,ast.Name) and test.func.id=='isinstance' and len(test.args)==2 and isinstance(test.args[1],ast.Name) and test.args[1].id in ('int','float','str','list','dict','bool'):
                    add('assert',phase='verify',assertion='instance',actual=value(test.args[0]),expected=test.args[1].id)
                else:
                    raise Unsupported('断言表达式需人工转换')
            elif isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
                call=node.value
                name=ast.unparse(call.func)
                if name.startswith('logger.') or name=='allure.attach':
                    if any(sdk_call(n) for n in ast.walk(call)):
                        raise Unsupported('日志表达式内含设备调用')
                    continue
                if sdk_call(call):
                    read(call)
                elif name=='assert_almost_equal' and len(call.args)>=3:
                    add('assert',phase='verify',assertion='approx',actual=value(call.args[0]),expected=value(call.args[1]),tolerance=value(call.args[2]))
                else:
                    raise Unsupported('辅助方法需人工转换：'+name)
            elif isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
                continue
            else:
                raise Unsupported('控制流程需人工转换：'+type(node).__name__)
    statements(fn.body)
    if not any(s['kind']=='read' for s in steps) or not any(s['kind']=='assert' for s in steps):
        raise Unsupported('缺少完整的读取与断言')
    doc.update(steps=steps, enabled=True)
    doc['migration'].update(status='converted_readonly', review_reasons=[], conversion='单函数只读流程完整转换，连接建立/关闭由本项目会话管理；尚未实机验收')
    return CaseDocument.model_validate(doc).model_dump()


def convert(library):
    converted=0
    reasons=Counter()
    for path in sorted(library.glob('cases/*/*.json')):
        doc=json.loads(path.read_text(encoding='utf-8'))
        if not doc.get('migration',{}).get('workbook'):
            continue
        try:
            result=compile_case(doc,library)
        except (Unsupported,ValueError,TypeError,AttributeError,KeyError) as exc:
            reasons[str(exc)]+=1
            continue
        path.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        converted+=1
    manifest_path=library/'migration-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.update(executable_documents=converted, review_documents=manifest['imported_documents']-converted, conversion_limits=dict(reasons))
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'converted_readonly':converted,'needs_review':manifest['review_documents'],'limits':dict(reasons)},ensure_ascii=False))


if __name__ == '__main__':
    convert(Path(__file__).resolve().parents[1]/'src/workbench/case_library')
