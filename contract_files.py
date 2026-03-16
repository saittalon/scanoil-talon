import os
from uuid import uuid4

from werkzeug.utils import secure_filename

from flask import Blueprint, request, redirect, flash, abort
from flask_login import login_required, current_user
from supabase import create_client

from models import db, Contract, ContractFile
from helpers import has_role
from mail_utils import notify_event
from security import log_audit

contract_files_bp = Blueprint('contract_files', __name__)

MAX_PDF_BYTES = int(os.getenv('MAX_CONTENT_LENGTH', str(10 * 1024 * 1024)))


def _supabase_client():
    supabase_url = os.getenv('SUPABASE_URL', '').strip()
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
    if not supabase_url or not supabase_key:
        return None
    return create_client(supabase_url, supabase_key)


def _can_auto_approve():
    return has_role('director', 'deputy_director', 'zamdirector')


def _ensure_upload_rights():
    if not has_role('director', 'deputy_director', 'zamdirector', 'executor', 'operator'):
        abort(403)


def _validate_pdf(file_storage):
    safe = secure_filename(file_storage.filename or '')
    if not safe.lower().endswith('.pdf'):
        return False, 'Можно загружать только PDF.'

    raw = file_storage.read()
    file_storage.seek(0)

    if len(raw) == 0:
        return False, 'Файл пустой.'
    if len(raw) > MAX_PDF_BYTES:
        return False, 'Файл слишком большой. Разрешено не более 10 МБ.'
    if not raw.startswith(b'%PDF'):
        return False, 'Файл не похож на PDF.'
    if b'/JavaScript' in raw[:200000] or b'/JS' in raw[:200000]:
        return False, 'PDF с активным скриптом запрещен.'
    return True, raw


@contract_files_bp.post('/contracts/<int:contract_id>/files/upload')
@login_required
def upload_contract_file(contract_id: int):
    _ensure_upload_rights()
    contract = Contract.query.get_or_404(contract_id)
    f = request.files.get('file')
    kind = (request.form.get('kind') or '').strip()

    if not f or f.filename == '':
        flash('Файл не выбран', 'danger')
        return redirect(request.referrer or '/')
    if kind not in ('contract', 'addendum'):
        flash('Неверный тип файла', 'danger')
        return redirect(request.referrer or '/')

    valid, payload = _validate_pdf(f)
    if not valid:
        flash(payload, 'danger')
        return redirect(request.referrer or '/')

    supabase = _supabase_client()
    if supabase is None:
        flash('Не настроено хранилище Supabase.', 'danger')
        return redirect(request.referrer or '/')

    storage_key = f'contract/{contract.id}/{uuid4().hex}.pdf'
    try:
        supabase.storage.from_('contracts').upload(
            path=storage_key,
            file=payload,
            file_options={'content-type': 'application/pdf', 'x-upsert': 'false'}
        )
    except Exception:
        flash('Не удалось загрузить PDF в хранилище.', 'danger')
        return redirect(request.referrer or '/')

    if kind == 'contract':
        olds = ContractFile.query.filter_by(contract_id=contract.id, kind='contract').all()
        for old in olds:
            try:
                if old.storage_key:
                    supabase.storage.from_(old.bucket or 'contracts').remove([old.storage_key])
            except Exception:
                pass
            db.session.delete(old)

    auto = _can_auto_approve()
    row = ContractFile(
        contract_id=contract.id,
        kind=kind,
        title='Основной договор' if kind == 'contract' else 'Доп. соглашение',
        bucket='contracts',
        storage_key=storage_key,
        storage_path=storage_key,
        original_name=secure_filename(f.filename)[:250],
        approval_status='approved' if auto else 'pending',
        uploaded_by_user_id=current_user.id,
        approved_by_user_id=current_user.id if auto else None,
        approved_at=db.func.now() if auto else None,
    )
    db.session.add(row)
    db.session.flush()
    log_audit('upload_contract_file', f'{current_user.username} загрузил {kind} для договора {contract.number}. Статус: {row.approval_status}', 'contract_file', row.id)
    db.session.commit()
    notify_event('Загружен файл договора', f'{current_user.username} загрузил {kind} для договора {contract.number}. Статус: {row.approval_status}')
    flash('PDF загружен' if auto else 'PDF загружен и отправлен на подтверждение', 'success')
    return redirect(request.referrer or '/')


@contract_files_bp.post('/contracts/files/<int:file_id>/approve')
@login_required
def approve_contract_file(file_id: int):
    if not has_role('director', 'deputy_director', 'zamdirector'):
        flash('Подтверждать может только директор или замдиректора.', 'danger')
        return redirect(request.referrer or '/')
    row = ContractFile.query.get_or_404(file_id)
    row.approval_status = 'approved'
    row.approved_by_user_id = current_user.id
    row.approved_at = db.func.now()
    log_audit('approve_contract_file', f'{current_user.username} подтвердил файл {row.original_name or row.id}', 'contract_file', row.id)
    db.session.commit()
    notify_event('Файл подтвержден', f'{current_user.username} подтвердил файл {row.original_name or row.id} по договору {row.contract.number}')
    flash('Файл подтвержден.', 'success')
    return redirect(request.referrer or '/')


@contract_files_bp.post('/contracts/files/<int:file_id>/delete')
@login_required
def delete_contract_file(file_id: int):
    if not has_role('director', 'deputy_director', 'zamdirector'):
        flash('Удалять файл может только директор или замдиректора.', 'danger')
        return redirect(request.referrer or '/')

    row = ContractFile.query.get_or_404(file_id)
    supabase = _supabase_client()
    try:
        if supabase is not None and row.storage_key:
            supabase.storage.from_(row.bucket or 'contracts').remove([row.storage_key])
    except Exception:
        pass
    log_audit('delete_contract_file', f'{current_user.username} удалил файл {row.original_name or row.id}', 'contract_file', row.id)
    db.session.delete(row)
    db.session.commit()
    notify_event('Файл удален', f'{current_user.username} удалил файл {row.original_name or row.id} по договору {row.contract.number}')
    flash('Файл удалён', 'success')
    return redirect(request.referrer or '/')
