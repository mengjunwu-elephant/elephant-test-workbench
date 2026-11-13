"""Saved plans and read-only run resolution; no SDK imports or device requests."""
import copy
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select, update

from .case_repository import digest
from .models import PlanDocument, CaseDocument
from .storage import Case, Plan, now


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode()).hexdigest()


def save_plan(store, body: PlanDocument, creating=False):
    doc=body.model_dump()
    with store.session() as s:
        existing=s.get(Plan,body.id)
        if creating and existing:
            raise HTTPException(409,'计划ID已存在')
        if not creating and (not existing or existing.document['revision']!=body.revision):
            raise HTTPException(409,'计划版本已变化，请重新载入')
        old_definitions=existing.document.get('definitions',{}) if existing else {}
        definitions={}
        for item in doc['items']:
            current=s.get(Case,item['case_id'])
            if not current:
                raise HTTPException(409,'计划引用的用例不存在：'+item['case_id'])
            latest=CaseDocument.model_validate(current.document).model_dump()
            version=item['content_hash'] or digest(latest)
            if version==digest(latest):
                definition=latest
            elif version in old_definitions and old_definitions[version]['id']==item['case_id']:
                definition=old_definitions[version]
            else:
                raise HTTPException(409,'引用版本无法解析，请明确更新引用：'+item['case_id'])
            if definition['model']!=body.model:
                raise HTTPException(409,'计划与用例机型不一致')
            if any(index<0 or index>=len(definition['datasets']) for index in item['dataset_indices']):
                raise HTTPException(422,'数据组索引超出固定版本范围')
            if set(item['overrides'])-set(definition['parameters']):
                raise HTTPException(422,'计划仅能覆盖用例声明的默认参数')
            item['content_hash']=version
            definitions[version]=definition
        doc['definitions']=definitions
        doc['revision']=1 if creating else body.revision+1
        PlanDocument.model_validate(doc)
        if creating:
            s.add(Plan(id=body.id,document=doc))
        else:
            changed=s.execute(update(Plan).where(Plan.id==body.id,Plan.document['revision'].as_integer()==body.revision).values(document=doc,updated_at=now()))
            if changed.rowcount!=1:
                raise HTTPException(409,'计划并发编辑冲突')
    return doc


def resolve_cases(store, request):
    """Preserve order/duplicates; only saved definitions may run through the API."""
    with store.session() as s:
        saved={row.id:row.document for row in s.scalars(select(Case))}
        resolved=[]
        plan=None
        if request.kind=='plan':
            row=s.get(Plan,request.plan_id)
            if not row or row.document.get('archived'):
                raise HTTPException(409,'计划不存在或已归档')
            plan=PlanDocument.model_validate(row.document).model_dump()
            if request.plan_revision is not None and request.plan_revision!=plan['revision']:
                raise HTTPException(409,'计划版本已经变化，请重新核对')
            for item in plan['items']:
                current=saved.get(item['case_id'])
                if not current or not current.get('enabled',True):
                    raise HTTPException(409,'计划引用用例当前不可用：'+item['case_id'])
                definition=plan['definitions'].get(item['content_hash'])
                if not definition or digest(definition)!=item['content_hash'] or definition['id']!=item['case_id']:
                    raise HTTPException(409,'计划固定版本损坏或缺失')
                for number in range(item['repeat']):
                    doc=copy.deepcopy(definition)
                    indices=item['dataset_indices'] or list(range(len(doc['datasets'])))
                    overrides={**item['overrides'],**request.parameter_overrides}
                    if set(overrides)-set(doc['parameters']):
                        raise HTTPException(422,'本次参数覆盖包含未声明字段')
                    doc['datasets']=[{**doc['datasets'][index],**overrides} for index in indices]
                    doc['_entry']={'item_id':item['item_id'],'repeat_index':number+1,'content_hash':item['content_hash'],'dataset_indices':indices,'overrides':overrides}
                    resolved.append(doc)
                    if len(resolved)>10000:
                        raise HTTPException(422,'计划展开条目超过10000，请缩小重复次数')
        else:
            ids=list(request.case_ids)
            for inline in request.inline_cases:
                current=saved.get(inline.id)
                if not current or digest(current)!=digest(inline.model_dump()):
                    raise HTTPException(409,'请先保存当前用例，再准备运行')
                ids.append(inline.id)
            if request.all_cases:
                ids=[key for key,doc in sorted(saved.items()) if doc['model']==request.connection.model and doc.get('enabled',True) and (request.include_manual or doc.get('category')!='manual')]
            for index,key in enumerate(ids):
                if key not in saved:
                    raise HTTPException(409,'所选用例不存在：'+key)
                doc=CaseDocument.model_validate(saved[key]).model_dump()
                if set(request.parameter_overrides)-set(doc['parameters']):
                    raise HTTPException(422,'本次参数覆盖包含未声明字段')
                doc=copy.deepcopy(doc)
                version=digest(doc)
                doc['datasets']=[{**data,**request.parameter_overrides} for data in doc['datasets']]
                doc['_entry']={'item_id':f'selected-{index+1}','repeat_index':1,'content_hash':version,'dataset_indices':list(range(len(doc['datasets']))),'overrides':request.parameter_overrides}
                resolved.append(doc)
        if request.kind in ('cases','plan','aging') and not resolved:
            raise HTTPException(400,'没有选中可运行的已保存用例')
        if sum(len(doc['datasets']) for doc in resolved)>100000:
            raise HTTPException(422,'运行实例超过100000，请缩小参数组或重复次数')
        for doc in resolved:
            if not doc.get('enabled',True) or any(step['kind']=='review' for step in doc['steps']):
                raise HTTPException(409,'用例尚待完善或未启用：'+doc['name'])
            if doc['model']!=request.connection.model:
                raise HTTPException(409,'所选用例/计划与当前机型不一致')
        return resolved,plan
