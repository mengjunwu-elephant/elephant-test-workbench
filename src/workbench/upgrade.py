"""One-time archival of obsolete development preview records; never read another project."""
import json
from sqlalchemy import select
from .storage import Case, Task, DeviceLock, Setting, now


def archive_obsolete_preview(store):
    with store.session() as session:
        if session.get(Setting, 'standalone_hardware_v2'):
            return
        cases = [row for row in session.scalars(select(Case)) if row.id in {'demo-read', 'demo-angle', 'demo-invalid'} and row.document.get('revision') == 1 and row.document.get('name', '').startswith('模拟 ·')]
        tasks = [row for row in session.scalars(select(Task)) if row.request.get('connection', {}).get('mode') == 'simulation' or row.kind == 'legacy']
        obsolete = session.get(Setting, 'legacy')
        connection = session.get(Setting, 'connection')
        def record(row):
            return {column.name: getattr(row, column.name) for column in row.__table__.columns}
        payload = {'archived_at': now(), 'cases': [record(row) for row in cases], 'tasks': [record(row) for row in tasks], 'settings': [record(obsolete)] if obsolete else []}
        if cases or tasks or obsolete:
            archive = store.root / 'archives' / 'obsolete-preview-v1.json'
            archive.parent.mkdir(parents=True, exist_ok=True)
            # Write evidence before removing preview entries from active tables.
            archive.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        for row in cases:
            session.delete(row)
        for row in tasks:
            for lock in session.scalars(select(DeviceLock).where(DeviceLock.task_id == row.id)):
                session.delete(lock)
            session.delete(row)
        if obsolete:
            session.delete(obsolete)
        if connection and connection.value.get('mode') != 'hardware':
            connection.value = {**connection.value, 'mode': 'hardware'}
        session.add(Setting(key='standalone_hardware_v2', value={'completed_at': now()}))
