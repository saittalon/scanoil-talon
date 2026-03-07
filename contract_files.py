import os
from uuid import uuid4
from werkzeug.utils import secure_filename

from supabase import create_client
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from models import db, Contract, ContractFile

contract_files_bp = Blueprint("contract_files", __name__)


def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


@contract_files_bp.post("/contracts/<int:contract_id>/files/upload")
@login_required
def upload_contract_file(contract_id: int):
    contract = Contract.query.get_or_404(contract_id)
    f = request.files.get("file")
    kind = (request.form.get("kind") or "").strip()

    if not f or f.filename == "":
        flash("Файл не выбран", "danger")
        return redirect(request.referrer or f"/clients/{contract.client_id}/contracts?id={contract.id}")

    if kind not in ("contract", "addendum"):
        flash("Неверный тип файла", "danger")
        return redirect(request.referrer or f"/clients/{contract.client_id}/contracts?id={contract.id}")

    original = f.filename
    safe = secure_filename(original)
    if not safe.lower().endswith(".pdf"):
        flash("Можно загружать только PDF", "danger")
        return redirect(request.referrer or f"/clients/{contract.client_id}/contracts?id={contract.id}")

    supabase = get_supabase()
    if supabase is None:
        flash("Не настроено хранилище файлов SUPABASE.", "danger")
        return redirect(request.referrer or f"/clients/{contract.client_id}/contracts?id={contract.id}")

    bucket = "contracts"
    storage_key = f"contract/{contract.id}/{uuid4().hex}.pdf"
    content = f.read()

    try:
        supabase.storage.from_(bucket).upload(
            path=storage_key,
            file=content,
            file_options={"content-type": "application/pdf"}
        )
    except Exception as e:
        flash(f"Ошибка загрузки PDF: {e}", "danger")
        return redirect(request.referrer or f"/clients/{contract.client_id}/contracts?id={contract.id}")

    if kind == "contract":
        olds = ContractFile.query.filter_by(contract_id=contract.id, kind="contract").all()
        for old in olds:
            try:
                if old.storage_key:
                    supabase.storage.from_(old.bucket or bucket).remove([old.storage_key])
            except Exception:
                pass
            db.session.delete(old)

    row = ContractFile(
        contract_id=contract.id,
        kind=kind,
        title="Основной договор" if kind == "contract" else "Доп. соглашение",
        bucket=bucket,
        storage_key=storage_key,
        storage_path=storage_key,
        original_name=original
    )
    db.session.add(row)
    db.session.commit()

    flash("PDF загружен", "success")
    return redirect(request.referrer or f"/clients/{contract.client_id}/contracts?id={contract.id}")


@contract_files_bp.post("/contracts/files/<int:file_id>/delete")
@login_required
def delete_contract_file(file_id: int):
    row = ContractFile.query.get_or_404(file_id)
    supabase = get_supabase()
    if supabase is not None:
        try:
            if row.storage_key:
                supabase.storage.from_(row.bucket or "contracts").remove([row.storage_key])
        except Exception:
            pass

    contract_id = row.contract_id
    client_id = row.contract.client_id if row.contract else None
    db.session.delete(row)
    db.session.commit()
    flash("Файл удалён", "success")
    if client_id:
        return redirect(f"/clients/{client_id}/contracts?id={contract_id}")
    return redirect(request.referrer or "/clients")
