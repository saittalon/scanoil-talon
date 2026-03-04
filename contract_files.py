import os
from uuid import uuid4
from werkzeug.utils import secure_filename

from supabase import create_client
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from models import db, Contract, ContractFile

contract_files_bp = Blueprint("contract_files", __name__)


import os
from supabase import create_client

def sb():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    print("SUPABASE_URL =", url)
    print("SUPABASE_KEY_SET =", bool(key))

    if not url:
        raise Exception("SUPABASE_URL not set")

    if not key:
        raise Exception("SUPABASE_SERVICE_ROLE_KEY not set")

    return create_client(url, key)

@contract_files_bp.post("/contracts/<int:contract_id>/files/upload")
@login_required
def upload_contract_file(contract_id: int):
    contract = Contract.query.get_or_404(contract_id)

    f = request.files.get("file")
    kind = (request.form.get("kind") or "").strip()  # contract / addendum

    if not f or f.filename == "":
        flash("Файл не выбран", "error")
        return redirect(request.referrer)

    if kind not in ("contract", "addendum"):
        flash("Неверный тип файла", "error")
        return redirect(request.referrer)

    original = f.filename
    safe = secure_filename(original)

    if not safe.lower().endswith(".pdf"):
        flash("Можно загружать только PDF", "error")
        return redirect(request.referrer)

    bucket = "contracts"
    storage_key = f"contract/{contract.id}/{uuid4().hex}.pdf"

    content = f.read()
    sb().storage.from_(bucket).upload(
        path=storage_key,
        file=content,
        file_options={"content-type": "application/pdf"}
    )

    # если основной договор — оставляем только 1
    if kind == "contract":
        olds = ContractFile.query.filter_by(contract_id=contract.id, kind="contract").all()
        for old in olds:
            # удаляем из storage
            try:
                if old.storage_key:
                    sb().storage.from_(old.bucket or bucket).remove([old.storage_key])
            except Exception:
                pass
            db.session.delete(old)

    row = ContractFile(
        contract_id=contract.id,
        kind=kind,
        title="Основной договор" if kind == "contract" else "Доп. соглашение",
        bucket=bucket,
        storage_key=storage_key,
        storage_path=storage_key,   # можно так, чтобы старый код не ломался
        original_name=original
    )
    db.session.add(row)
    db.session.commit()

    flash("PDF загружен", "success")
    return redirect(request.referrer)


@contract_files_bp.post("/contracts/files/<int:file_id>/delete")
@login_required
def delete_contract_file(file_id: int):
    row = ContractFile.query.get_or_404(file_id)

    try:
        if row.storage_key:
            sb().storage.from_(row.bucket or "contracts").remove([row.storage_key])
    except Exception:
        pass

    db.session.delete(row)
    db.session.commit()
    flash("Файл удалён", "success")
    return redirect(request.referrer)
