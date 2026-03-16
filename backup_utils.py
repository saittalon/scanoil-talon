import csv
import io
import json
import os
import posixpath
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


def _get_supabase_client():
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    if not supabase_url or not supabase_key:
        return None
    return create_client(supabase_url, supabase_key)


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
            sb = _get_supabase_client()
            if sb is not None:
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


def _build_cloud_key(filename: str, base_path: str = 'auto') -> str:
    stamp = datetime.utcnow()
    parts = []
    if base_path:
        parts.append(base_path.strip('/ '))
    parts.extend([str(stamp.year), f'{stamp.month:02d}', filename])
    return posixpath.join(*parts)


def _normalize_storage_list(items, prefix=''):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = item.get('name') or ''
        if not name:
            continue
        current = posixpath.join(prefix, name) if prefix else name
        metadata = item.get('metadata') or {}
        if metadata:
            normalized.append(current)
            continue
        normalized.extend(_normalize_storage_list(item.get('items') or [], current))
    return normalized


def cleanup_old_cloud_backups(sb, bucket: str, keep_last: int = 30, root_path: str = 'auto'):
    keep_last = max(1, int(keep_last or 1))
    root_path = (root_path or '').strip('/ ')
    month_prefix = root_path
    try:
        listing = sb.storage.from_(bucket).list(month_prefix or None, {'limit': 1000, 'sortBy': {'column': 'name', 'order': 'desc'}})
        all_keys = _normalize_storage_list(listing, month_prefix)
        zip_keys = sorted([k for k in all_keys if k.lower().endswith('.zip')], reverse=True)
        stale = zip_keys[keep_last:]
        if stale:
            sb.storage.from_(bucket).remove(stale)
        return {'removed': len(stale), 'kept': min(len(zip_keys), keep_last)}
    except Exception as exc:
        if current_app:
            current_app.logger.warning('Cloud backup cleanup failed: %s', exc)
        return {'removed': 0, 'kept': 0, 'error': str(exc)[:200]}


def upload_backup_bytes_to_supabase(bundle_bytes: bytes, filename: str, bucket: str = 'backups', base_path: str = 'auto', keep_last: int = 30):
    sb = _get_supabase_client()
    if sb is None:
        raise RuntimeError('SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set')

    key = _build_cloud_key(filename, base_path=base_path)
    sb.storage.from_(bucket).upload(
        path=key,
        file=bundle_bytes,
        file_options={'content-type': 'application/zip', 'cache-control': '3600', 'upsert': 'false'}
    )
    cleanup = cleanup_old_cloud_backups(sb, bucket=bucket, keep_last=keep_last, root_path=base_path)
    return {'bucket': bucket, 'key': key, 'cleanup': cleanup}
