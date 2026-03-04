import os
from uuid import uuid4
from werkzeug.utils import secure_filename

from flask import Blueprint, request, redirect, flash, current_app, url_for
from flask_login import login_required

from models import db, Contract, ContractFile

contract_files_bp = Blueprint("contract_files", __name__)


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
    safe_name = secure_filename(original)

    if not safe_name.lower().endswith(".pdf"):
        flash("Можно загружать только PDF", "error")
        return redirect(request.referrer)

    # папка на диск: uploads/contracts/<contract_id>/
    base_dir = os.path.join(current_app.config["UPLOAD_ROOT"], "contracts", str(contract.id))
    os.makedirs(base_dir, exist_ok=True)

    stored_name = f"{uuid4().hex}.pdf"
    full_path = os.path.join(base_dir, stored_name)
    f.save(full_path)

    # Если загружаем основной договор — оставляем только 1 файл kind='contract'
    if kind == "contract":
        olds = ContractFile.query.filter_by(contract_id=contract.id, kind="contract").all()
        for old in olds:
            # удалить файл с диска
            old_path = os.path.join(base_dir, os.path.basename(old.storage_path))
            try:
                if os.path.exists(old_path):
                    os.remove(old_path)
            except Exception:
                pass
            db.session.delete(old)

    row = ContractFile(
        contract_id=contract.id,
        kind=kind,
        title="Основной договор" if kind == "contract" else "Доп. соглашение",
        storage_path=stored_name,     # храним только имя файла
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

    base_dir = os.path.join(current_app.config["UPLOAD_ROOT"], "contracts", str(row.contract_id))
    full_path = os.path.join(base_dir, os.path.basename(row.storage_path))

    try:
        if os.path.exists(full_path):
            os.remove(full_path)
    except Exception:
        pass

    db.session.delete(row)
    db.session.commit()
    flash("Файл удалён", "success")
    return redirect(request.referrer)
