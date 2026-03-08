import os
from uuid import uuid4
from werkzeug.utils import secure_filename

from flask import Blueprint, request, redirect, flash
from flask_login import login_required, current_user
from supabase import create_client

from models import db, Contract, ContractFile
from helpers import has_role
from mail_utils import notify_event

contract_files_bp = Blueprint('contract_files', __name__)

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


def _can_auto_approve():
    return has_role('director', 'deputy_director')


@contract_files_bp.post('/contracts/<int:contract_id>/files/upload')
@login_required
def upload_contract_file(contract_id: int):
    contract = Contract.query.get_or_404(contract_id)
    f = request.files.get('file')
    kind = (request.form.get('kind') or '').strip()

    if not f or f.filename == '':
        flash('Файл не выбран', 'error')
        return redirect(request.referrer or '/')
    if kind not in ('contract', 'addendum'):
        flash('Неверный тип файла', 'error')
        return redirect(request.referrer or '/')

    safe = secure_filename(f.filename)
    if not safe.lower().endswith('.pdf'):
        flash('Можно загружать только PDF', 'error')
        return redirect(request.referrer or '/')

    if supabase is None:
        flash('Не настроено хранилище Supabase.', 'error')
        return redirect(request.referrer or '/')

    storage_key = f'contract/{contract.id}/{uuid4().hex}.pdf'
    supabase.storage.from_('contracts').upload(
        path=storage_key,
        file=f.read(),
        file_options={'content-type': 'application/pdf'}
    )

    if kind == 'contract':
        olds = ContractFile.query.filter_by(contract_id=contract.id, kind='contract').all()
        for old in olds:
            db.session.delete(old)

    auto = _can_auto_approve()
    row = ContractFile(
        contract_id=contract.id,
        kind=kind,
        title='Основной договор' if kind == 'contract' else 'Доп. соглашение',
        bucket='contracts',
        storage_key=storage_key,
        storage_path=storage_key,
        original_name=f.filename,
        approval_status='approved' if auto else 'pending',
        uploaded_by_user_id=current_user.id,
        approved_by_user_id=current_user.id if auto else None,
        approved_at=db.func.now() if auto else None,
    )
    db.session.add(row)
    db.session.commit()
    notify_event('Загружен файл договора', f'{current_user.username} загрузил {kind} для договора {contract.number}. Статус: {row.approval_status}')
    flash('PDF загружен' if auto else 'PDF загружен и отправлен на подтверждение', 'success')
    return redirect(request.referrer or '/')


@contract_files_bp.post('/contracts/files/<int:file_id>/approve')
@login_required
def approve_contract_file(file_id: int):
    if not has_role('director', 'deputy_director'):
        flash('Подтверждать может только директор или замдиректора.', 'danger')
        return redirect(request.referrer or '/')
    row = ContractFile.query.get_or_404(file_id)
    row.approval_status = 'approved'
    row.approved_by_user_id = current_user.id
    row.approved_at = db.func.now()
    db.session.commit()
    notify_event('Файл подтвержден', f'{current_user.username} подтвердил файл {row.original_name or row.id} по договору {row.contract.number}')
    flash('Файл подтвержден.', 'success')
    return redirect(request.referrer or '/')


@contract_files_bp.post('/contracts/files/<int:file_id>/delete')
@login_required
def delete_contract_file(file_id: int):
    row = ContractFile.query.get_or_404(file_id)
    try:
        if supabase is not None and row.storage_key:
            supabase.storage.from_(row.bucket or 'contracts').remove([row.storage_key])
    except Exception:
        pass
    db.session.delete(row)
    db.session.commit()
    notify_event('Файл удален', f'{current_user.username} удалил файл {row.original_name or row.id} по договору {row.contract.number}')
    flash('Файл удалён', 'success')
    return redirect(request.referrer or '/')
