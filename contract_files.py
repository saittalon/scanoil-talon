import os
import uuid
from datetime import timedelta, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required
from supabase import create_client

from models import db, Contract, ContractFile

contract_files_bp = Blueprint("contract_files", __name__)

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY is not set")
    return create_client(url, key)

def get_bucket():
    return os.getenv("SUPABASE_STORAGE_BUCKET", "contracts")

def _build_storage_path(contract_id: int, original_name: str) -> str:
    ext = ""
    if "." in original_name:
        ext = "." + original_name.rsplit(".", 1)[1].lower()
    fname = f"{uuid.uuid4().hex}{ext}"
    # пример: contracts/contract_12/addon/uuid.pdf  (но bucket уже contracts, поэтому внутри bucket путь такой)
    return f"contract_{contract_id}/{fname}"

@contract_files_bp.get("/contracts/<int:contract_id>/files")
@login_required
def contract_files(contract_id: int):
    contract = Contract.query.get_or_404(contract_id)
    files = (ContractFile.query
             .filter_by(contract_id=contract_id)
             .order_by(ContractFile.created_at.desc())
             .all())
    return render_template("contract_files.html", contract=contract, files=files)

@contract_files_bp.post("/contracts/<int:contract_id>/files/upload")
@login_required
def upload_contract_file(contract_id: int):
    contract = Contract.query.get_or_404(contract_id)

    kind = (request.form.get("kind") or "attachment").strip()
    title = (request.form.get("title") or "").strip()

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Файл не выбран", "error")
        return redirect(url_for("contract_files.contract_files", contract_id=contract_id))

    # только PDF
    if not f.filename.lower().endswith(".pdf"):
        flash("Можно загрузить только PDF", "error")
        return redirect(url_for("contract_files.contract_files", contract_id=contract_id))

    supabase = get_supabase()
    bucket = get_bucket()

    storage_path = _build_storage_path(contract_id, f.filename)

    # upload
    data = f.read()
    supabase.storage.from_(bucket).upload(
        storage_path,
        data,
        {"content-type": "application/pdf", "upsert": "false"}
    )

    row = ContractFile(
        contract_id=contract_id,
        kind=kind,
        title=title or None,
        storage_path=storage_path,
        original_name=f.filename
    )
    db.session.add(row)
    db.session.commit()

    flash("Файл загружен", "success")
    return redirect(url_for("contract_files.contract_files", contract_id=contract_id))

@contract_files_bp.get("/contract-files/<int:file_id>/view")
@login_required
def view_contract_file(file_id: int):
    cf = ContractFile.query.get_or_404(file_id)

    supabase = get_supabase()
    bucket = get_bucket()

    # signed url на 1 час
    signed = supabase.storage.from_(bucket).create_signed_url(cf.storage_path, 3600)
    url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")
    if not url:
        abort(404)

    # отдаём страницу с iframe
    return render_template("contract_file_view.html", cf=cf, signed_url=url)

@contract_files_bp.post("/contract-files/<int:file_id>/delete")
@login_required
def delete_contract_file(file_id: int):
    cf = ContractFile.query.get_or_404(file_id)
    contract_id = cf.contract_id

    supabase = get_supabase()
    bucket = get_bucket()

    # удаляем из storage (если не удалится — всё равно удалим запись)
    try:
        supabase.storage.from_(bucket).remove([cf.storage_path])
    except Exception:
        pass

    db.session.delete(cf)
    db.session.commit()

    flash("Файл удалён", "success")
    return redirect(url_for("contract_files.contract_files", contract_id=contract_id))

@contract_files_bp.post("/contract-files/<int:file_id>/replace")
@login_required
def replace_contract_file(file_id: int):
    cf = ContractFile.query.get_or_404(file_id)
    contract_id = cf.contract_id

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Файл не выбран", "error")
        return redirect(url_for("contract_files.contract_files", contract_id=contract_id))

    if not f.filename.lower().endswith(".pdf"):
        flash("Можно загрузить только PDF", "error")
        return redirect(url_for("contract_files.contract_files", contract_id=contract_id))

    supabase = get_supabase()
    bucket = get_bucket()

    # кладём новую версию в новый путь
    new_path = _build_storage_path(contract_id, f.filename)
    data = f.read()
    supabase.storage.from_(bucket).upload(
        new_path,
        data,
        {"content-type": "application/pdf", "upsert": "false"}
    )

    # удаляем старый файл
    try:
        supabase.storage.from_(bucket).remove([cf.storage_path])
    except Exception:
        pass

    cf.storage_path = new_path
    cf.original_name = f.filename
    cf.updated_at = datetime.utcnow()
    db.session.commit()

    flash("Файл заменён", "success")
    return redirect(url_for("contract_files.contract_files", contract_id=contract_id))
