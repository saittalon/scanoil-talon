import csv
import io
import json
import os
import zipfile
from datetime import date, datetime

from flask import current_app
from supabase import create_client

from models import (
    db,
    User, Client, Contract, ContractFile, Balance, Talon,
    AGZS, WebAppToken, BotSession, TalonRedemption, Shift,
    AuditLog, RateLimitEvent,
)

EXPORT_MODELS = [
    (User, 'users'),
    (Client, 'clients'),
    (Contract, 'contracts'),
    (ContractFile, 'contract_files'),
    (Balance, 'balances'),
    (Talon, 'talons'),
    (AGZS, 'agzs'),
    (WebAppToken, 'webapp_tokens'),
    (BotSession, 'bot_sessions'),
    (TalonRedemption, 'talon_redemptions'),
    (Shift, 'shifts'),
    (AuditLog, 'audit_logs'),
    (RateLimitEvent, 'rate_limit_events'),
]


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value



def _row_to_dict(obj):
    data = {}
    for column in obj.__table__.columns:
        data[column.name] = _serialize(getattr(obj, column.name))
    return data



def _csv_bytes(rows, fieldnames):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return stream.getvalue().encode('utf-8-sig')



def build_backup_zip(include_files: bool = True):
    timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    mem = io.BytesIO()
    manifest = {
        'created_at_utc': datetime.utcnow().isoformat() + 'Z',
        'include_files': bool(include_files),
        'files_exported': 0,
        'files_failed': [],
        'tables': {},
    }

    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for model, folder in EXPORT_MODELS:
            rows = [_row_to_dict(item) for item in model.query.order_by(model.id.asc()).all()]
            manifest['tables'][folder] = len(rows)
            zf.writestr(f'db/{folder}.json', json.dumps(rows, ensure_ascii=False, indent=2))
            fieldnames = list(rows[0].keys()) if rows else [c.name for c in model.__table__.columns]
            zf.writestr(f'db/{folder}.csv', _csv_bytes(rows, fieldnames))

        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))

        if include_files:
            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
            if supabase_url and supabase_key:
                sb = create_client(supabase_url, supabase_key)
                for row in ContractFile.query.order_by(ContractFile.id.asc()).all():
                    key = row.storage_key or row.storage_path
                    if not key:
                        manifest['files_failed'].append({'id': row.id, 'reason': 'missing_key'})
                        continue
                    bucket = row.bucket or 'contracts'
                    safe_name = row.original_name or f'{row.id}.pdf'
                    safe_name = os.path.basename(safe_name).replace('/', '_')
                    arcname = f'files/contracts/{row.contract_id}/{row.id}_{safe_name}'
                    try:
                        payload = sb.storage.from_(bucket).download(key)
                        if payload:
                            zf.writestr(arcname, payload)
                            manifest['files_exported'] += 1
                        else:
                            manifest['files_failed'].append({'id': row.id, 'reason': 'empty_payload'})
                    except Exception as exc:
                        current_app.logger.exception('Backup file export failed for ContractFile id=%s', row.id)
                        manifest['files_failed'].append({'id': row.id, 'reason': str(exc)[:200]})
            else:
                manifest['files_failed'].append({'id': None, 'reason': 'SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set'})

        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))

    mem.seek(0)
    return mem, f'scanoil-backup-{timestamp}.zip', manifest
