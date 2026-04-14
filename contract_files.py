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

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
MAX_PDF_BYTES = int(os.getenv('MAX_CONTENT_LENGTH', str(10 * 1024 * 1024)))


APPROVER_ROLES = {'director', 'deputy_director', 'zamdirector', 'accountant'}
UPLOAD_ROLES = APPROVER_ROLES | {'executor', 'operator'}


def _has_exact_role(*roles):
    return bool(getattr(current_user, 'is_authenticated', False)) and getattr(current_user, 'role', None) in set(roles)


def _can_manage_approvals():
    return _has_exact_role(*APPROVER_ROLES)


def _ensure_upload_rights():
    if not _has_exact_role(*UPLOAD_ROLES):
        abort(403)


def _contract_category(contract):
    category = ((getattr(getattr(contract, 'client', None), 'category', None) or '')).strip().lower()
    if category in ('counterparty', 'employee'):
        return category

    name = (getattr(getattr(contract, 'client', None), 'name', None) or '').lower()
    markers = ('тоо', 'ип', 'too', 'ip', 'llp', 'тОО')
    return 'counterparty' if any(marker in name for marker in markers) else 'employee'


def _can_auto_approve(contract):
    # Для сотрудников файл подтверждается сразу.
    # Для контрагентов всегда требуется отдельное подтверждение.
    return _contract_category(contract) == 'employee'


def _validate_pdf(file_storage):
    original_name = (file_storage.filename or '').strip()
    if not original_name.lower().endswith('.pdf'):
        return False, 'Можно загружать только PDF.'

    raw = file_storage.read()
    file_storage.seek(0)

    if len(raw) == 0:
        return False, 'Файл пустой.'
    if len(raw) > MAX_PDF_BYTES:
        max_mb = max(1, round(MAX_PDF_BYTES / (1024 * 1024)))
        return False, f'Файл слишком большой. Разрешено не более {max_mb} МБ.'

    header = raw[:1024].lstrip()
    if b'%PDF' not in header:
        return False, 'Файл не похож на PDF.'
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
    if kind not in ('contract', 'addendum', 'power_of_attorney'):
        flash('Неверный тип файла', 'danger')
        return redirect(request.referrer or '/')

    valid, payload = _validate_pdf(f)
    if not valid:
        flash(payload, 'danger')
        return redirect(request.referrer or '/')

    if supabase is None:
        flash('Не настроено хранилище Supabase.', 'danger')
        return redirect(request.referrer or '/')

    safe_name = secure_filename(f.filename or '')
    ext = '.pdf' if not safe_name.lower().endswith('.pdf') else ''
    storage_key = f'contract/{contract.id}/{uuid4().hex}{ext}'
    try:
        supabase.storage.from_('contracts').upload(
            path=storage_key,
            file=payload,
            file_options={'content-type': 'application/pdf'}
        )
    except Exception as e:
        flash(f'Ошибка загрузки в хранилище: {e}', 'danger')
        return redirect(request.referrer or '/')

    if kind == 'contract':
        olds = ContractFile.query.filter_by(contract_id=contract.id, kind='contract').all()
        for old in olds:
            db.session.delete(old)

    auto = _can_auto_approve(contract)
    row = ContractFile(
        contract_id=contract.id,
        kind=kind,
        title=(
            'Основной договор' if kind == 'contract' else
            'Доп. соглашение' if kind == 'addendum' else
            'Доверенность'
        ),
        bucket='contracts',
        storage_key=storage_key,
        storage_path=storage_key,
        original_name=(f.filename or '').strip(),
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
    if not _can_manage_approvals():
        flash('Подтверждать может только директор, замдиректора или бухгалтер.', 'danger')
        return redirect(request.referrer or '/')
    row = ContractFile.query.get_or_404(file_id)
    contract_number = row.contract.number if row.contract else '—'
    file_name = row.original_name or str(row.id)
    row.approval_status = 'approved'
    row.approved_by_user_id = current_user.id
    row.approved_at = db.func.now()
    log_audit('approve_contract_file', f'{current_user.username} подтвердил файл {file_name}', 'contract_file', row.id)
    db.session.commit()
    notify_event('Файл подтвержден', f'{current_user.username} подтвердил файл {file_name} по договору {contract_number}')
    flash('Файл подтвержден.', 'success')
    return redirect(request.referrer or '/')


@contract_files_bp.post('/contracts/files/<int:file_id>/delete')
@login_required
def delete_contract_file(file_id: int):
    if not _can_manage_approvals():
        flash('Удалять файл может только директор, замдиректора или бухгалтер.', 'danger')
        return redirect(request.referrer or '/')

    row = ContractFile.query.get_or_404(file_id)
    contract_number = row.contract.number if row.contract else '—'
    file_name = row.original_name or str(row.id)
    bucket = row.bucket or 'contracts'
    storage_key = row.storage_key

    try:
        if supabase is not None and storage_key:
            supabase.storage.from_(bucket).remove([storage_key])
    except Exception:
        pass

    log_audit('delete_contract_file', f'{current_user.username} удалил файл {file_name}', 'contract_file', row.id)
    db.session.delete(row)
    db.session.commit()
    notify_event('Файл удален', f'{current_user.username} удалил файл {file_name} по договору {contract_number}')
    flash('Файл удалён', 'success')
    return redirect(request.referrer or '/')
