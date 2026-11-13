"""一次性只读迁移 Excel 参数与测试逻辑依据；绝不导入或执行旧工程。

用法：项目 Python scripts/migrate_case_library.py --source <明确授权的旧目录>
未做等价转换的场景以禁用 review 步骤保存，不能伪装成可执行测试。
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from workbench.models import CaseDocument

NS = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
MODEL = {'mycobot_450':'pro450', 'mycobot_280':'mycobot280', 'UltraArm_P1':'ultraarm_p1', 'UltraArm_P1_Attachments':'ultraarm_p1', 'mercury':'mercury', 'mercury_e1':'mercury_e1', 'mercury_pro_gripper':'mercury', 'mercury_my_hand':'mercury', 'pro_gripper':'pro450'}


def excel(path):
    with zipfile.ZipFile(path) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        rels = {e.attrib['Id']:posixpath.normpath(posixpath.join('xl',e.attrib['Target'])) if not e.attrib['Target'].startswith('/') else e.attrib['Target'].lstrip('/') for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS):
            rows = []
            for row in ET.fromstring(z.read(rels[sheet.attrib[REL]])).findall('s:sheetData/s:row',NS):
                values = {}
                for cell in row.findall('s:c',NS):
                    letters = re.match(r'[A-Z]+', cell.attrib['r']).group()
                    index = 0
                    for letter in letters:
                        index = index * 26 + ord(letter)-64
                    raw = cell.find('s:v',NS)
                    value = raw.text if raw is not None else None
                    kind = cell.attrib.get('t')
                    if kind == 's' and value is not None:
                        value = strings[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(cell.find('s:is',NS).itertext())
                    elif kind == 'b' and value is not None:
                        value = value == '1'
                    elif kind not in ('str','e') and value is not None:
                        number = float(value)
                        value = int(number) if number.is_integer() else number
                    values[index-1] = value
                rows.append((int(row.attrib['r']),values))
            if not rows:
                yield sheet.attrib['name'], [], [], []
                continue
            header = rows[0][1] if rows[0][0] == 1 else {}
            width = max((i+1 for i,v in header.items() if v is not None and str(v).strip()), default=0)
            keys = [str(header[i]).strip() if header.get(i) is not None else f'__unnamed_column_{i+1}' for i in range(width)]
            if len(set(keys)) != len(keys):
                # 不静默覆盖重复列；保留所有列并记录待完善。
                counts = Counter()
                unique = []
                for key in keys:
                    counts[key] += 1
                    unique.append(key if counts[key] == 1 else f'{key}__duplicate_{counts[key]}')
                keys = unique
            data, numbers = [], []
            for number, row in rows:
                if number <= 1:
                    continue
                record = {key:row.get(i) for i,key in enumerate(keys)}
                if not record or all(v is None or isinstance(v,str) and not v.strip() for v in record.values()):
                    continue
                data.append(record)
                numbers.append(number)
            yield sheet.attrib['name'], keys, data, numbers


def migrate(source, target):
    if target.exists() and list(target.glob('cases/*/*.json')):
        raise SystemExit('目标用例库已有数据；拒绝覆盖，请先审查已有迁移资产')
    settings_tree = ast.parse((source/'settings.py').read_text(encoding='utf-8-sig'))
    mappings = {}
    base_refs = {}
    target.mkdir(parents=True, exist_ok=True)

    def save_source(relative, text):
        destination = target/'sources'/Path(relative+'.txt')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text,encoding='utf-8')
        return destination.relative_to(target).as_posix()

    for cls in (node for node in settings_tree.body if isinstance(node,ast.ClassDef)):
        for stmt in cls.body:
            if isinstance(stmt,ast.Assign):
                paths = [n.value.replace('\\','/') for n in ast.walk(stmt.value) if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.endswith('.xlsx')]
                if paths:
                    for name in stmt.targets:
                        if isinstance(name,ast.Name):
                            mappings[(cls.name,name.id)] = Path(paths[-1]).name
        # 仅保留等待/恢复等测试逻辑，不迁移设备初始化、地址或密钥配置。
        methods = [ast.unparse(n) for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name not in ('__init__', '_connect') and not n.name.startswith('resolve')]
        constants = []
        for node in cls.body:
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id in ('speed', 'zero_angles', 'init_angles') for t in node.targets):
                try:
                    ast.literal_eval(node.value)
                except ValueError:
                    continue
                constants.append(ast.unparse(node))
        if methods:
            base_refs[cls.name] = save_source('dependencies/'+cls.name, '\n\n'.join(constants+methods)+'\n')

    index, all_sources = {}, []
    paths = sorted((source/'testcases').rglob('test_*.py'))
    functions = 0
    for path in paths:
        text = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(text)
        functions += sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_') for n in ast.walk(tree))
        relative = path.relative_to(source).as_posix()
        ref = save_source(relative, text)
        refs = [ref]
        for fixture in [source/'conftest.py',path.parent/'conftest.py']:
            if fixture.is_file():
                refs.append(save_source(fixture.relative_to(source).as_posix(),fixture.read_text(encoding='utf-8-sig')))
        all_sources.append({'file':relative,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'reference':ref})
        bound = False
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.func.id != 'get_test_data_from_excel' or len(node.args)<2:
                continue
            field, sheet = node.args[:2]
            if not isinstance(field,ast.Attribute) or not isinstance(field.value,ast.Name) or not isinstance(sheet,ast.Constant) or not isinstance(sheet.value,str):
                continue
            workbook = mappings.get((field.value.id,field.attr))
            if workbook:
                bound = True
                key=(workbook,sheet.value)
                info=index.setdefault(key,{'source_files':[], 'test_functions':[], 'python_files':[]})
                info['source_files'] += refs + ([base_refs[field.value.id]] if field.value.id in base_refs else [])
                info['python_files'].append(relative)
                info['test_functions'] += [n.name for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
        if not bound:
            all_sources[-1]['binding']='没有可静态解析的 Excel 绑定；源码已保留在迁移清单'

    for name in ('assert_utils.py','test_data_handler.py','operator_input.py'):
        path=source/'common1'/name
        if path.is_file():
            save_source('dependencies/'+name,path.read_text(encoding='utf-8-sig'))
    books, sheets, row_count, empty, docs = [], 0, 0, [], []
    for book in sorted((source/'test_data').glob('*.xlsx')):
        books.append({'file':book.name,'sha256':hashlib.sha256(book.read_bytes()).hexdigest()})
        for name, keys, rows, numbers in excel(book):
            sheets += 1
            row_count += len(rows)
            info=index.get((book.name,name),{})
            if not rows:
                empty.append({'workbook':book.name,'sheet':name,'columns':keys})
                continue
            key='migrated-'+hashlib.sha256((book.name+'\0'+name).encode()).hexdigest()[:24]
            reasons=['参数已迁入；原 Python 的前置、条件、断言、异常和 fixture 恢复尚需转为本项目步骤，当前禁止执行']
            if not info:
                reasons.append('未找到可静态关联的 Python 测试文件')
            document=CaseDocument.model_validate({'id':key,'name':f'{book.stem} / {name}'[:200], 'model':MODEL[book.stem], 'description':'来自旧用例库的一次性迁移。原始字符串与 JSON 类型保持原样，未执行 eval 或旧测试。', 'enabled':False, 'datasets':rows, 'steps':[{'id':'migration-review','name':'待完善原测试逻辑','kind':'review','message':'；'.join(reasons)}], 'migration':{'workbook':book.name,'sheet':name,'rows':numbers,'columns':keys,'original_model':book.stem,'source_files':sorted(set(info.get('source_files',[]))), 'python_files':sorted(set(info.get('python_files',[]))), 'test_functions':sorted(set(info.get('test_functions',[]))), 'review_reasons':reasons,'status':'needs_review'}}).model_dump()
            path=target/'cases'/document['model']/(key+'.json')
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(document,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
            docs.append({'id':key,'model':document['model'],'workbook':book.name,'sheet':name,'rows':len(rows)})
    manifest={'format':'elephant-migration-v1','source_project':'elephant-pytest','source_python_files':len(paths),'source_test_functions':functions,'workbooks':books,'sheet_count':sheets,'data_row_count':row_count,'imported_documents':len(docs),'executable_documents':0,'empty_sheets':empty,'sources':all_sources,'documents':docs,'scope':'原始数据及逻辑依据已迁移；未等价转换的场景禁用，运行时不依赖旧工程'}
    (target/'migration-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('source_python_files','source_test_functions','sheet_count','data_row_count','imported_documents','executable_documents')},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    migrate(args.source.resolve(),Path(__file__).resolve().parents[1]/'src/workbench/case_library')
