import os
import json
import secrets
from datetime import datetime, timedelta, date
from io import BytesIO

import qrcode
from sqlalchemy import or_
from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, current_app
from flask_login import login_required, current_user
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from models import db, Client, Contract, Balance, Talon, ContractFile, TalonRedemption
from helpers import has_role, require_roles, talon_status, talon_status_label, format_kz, kz_today, kz_now, redeem_talon_atomic
from reports import dashboard_month_links
from mail_utils import notify_event
from security import log_audit

clients_bp = Blueprint("clients", __name__)


def is_admin():
    return has_role("director", "zamdirector", "deputy_director")


def can_manage_clients():
    return has_role("director", "zamdirector", "deputy_director", "executor", "operator")


def can_edit_balances():
    return has_role("director", "zamdirector", "deputy_director")


def can_edit_contracts():
    return has_role("director", "zamdirector", "deputy_director", "executor", "operator")


def contract_is_approved(contract):
    main_ok = any(f.kind == "contract" and f.approval_status == "approved" for f in contract.files)
    return main_ok


def _client_tabs(client: Client):
    return {
        "talons": url_for("clients.client_talons", client_id=client.id),
        "profile": url_for("clients.client_profile", client_id=client.id),
        "contract": url_for("clients.client_contracts", client_id=client.id),
        "reports": url_for("clients.client_reports", client_id=client.id),
    }


# ---------------- Балансы (остатки) ----------------
@clients_bp.post("/clients/<int:client_id>/balance/set", endpoint="balance_set")
@login_required
def balance_set(client_id):
    client = Client.query.get_or_404(client_id)

    if not can_edit_balances():
        flash("Недостаточно прав.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client.id))

    contract_id_raw = (request.form.get("contract_id") or "").strip()
    contract_id = int(contract_id_raw) if contract_id_raw.isdigit() else None
    addendum_file_id_raw = (request.form.get("addendum_file_id") or "").strip()
    addendum_file_id = int(addendum_file_id_raw) if addendum_file_id_raw.isdigit() else None

    if contract_id is None:
        flash("Не выбран договор.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client.id))

    liters_raw = (request.form.get("liters_left") or "").strip()
    try:
        liters_left = float((liters_raw or "0").replace(",", "."))
    except ValueError:
        liters_left = 0.0

    balance_control = bool(request.form.get("balance_control"))
    product_name = (request.form.get("product_name") or "ГАЗ").strip() or "ГАЗ"

    contract = Contract.query.filter_by(client_id=client.id, id=contract_id).first()
    if not contract:
        flash("Договор не найден.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    if not contract_is_approved(contract):
        flash("Основной договор не подтвержден директором/замдиректора.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    if addendum_file_id:
        addendum = ContractFile.query.filter_by(
            id=addendum_file_id,
            contract_id=contract.id,
            kind="addendum"
        ).first()
        if not addendum or addendum.approval_status != "approved":
            flash("Выбранное доп. соглашение не подтверждено.", "danger")
            return redirect(url_for("clients.client_talons", client_id=client.id))

    bal = Balance.query.filter_by(
        client_id=client.id,
        contract_id=contract_id,
        product_name=product_name,
    ).first()

    if bal is None:
        bal = Balance(
            client_id=client.id,
            contract_id=contract_id,
            product_name=product_name,
            liters_left=liters_left,
            balance_control=balance_control,
            updated_at=datetime.utcnow(),
        )
        db.session.add(bal)
    else:
        bal.liters_left = liters_left
        bal.balance_control = balance_control
        bal.updated_at = datetime.utcnow()

    log_audit("balance_set", f"{current_user.username} обновил остаток по договору #{contract_id} клиента {client.name}", "client", client.id)
    db.session.commit()
    notify_event(
        "Обновлен остаток",
        f"Пользователь {current_user.username} обновил остаток по договору #{contract_id} клиента {client.name}"
    )
    flash("Остаток обновлён.", "success")
    return redirect(url_for("clients.client_contracts", client_id=client.id, id=contract_id))


# ---------------- Клиенты ----------------
@clients_bp.get("/clients")
@login_required
def list_clients():
    q = (request.args.get("q") or "").strip()
    query = Client.query

    if q:
        query = query.filter(
            or_(
                Client.name.ilike(f"%{q}%"),
                Client.full_name.ilike(f"%{q}%"),
                Client.bin.ilike(f"%{q}%")
            )
        )

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Client.id.desc()).paginate(page=page, per_page=50, error_out=False)
    clients = pagination.items

    return render_template(
        "clients.html",
        clients=clients,
        pagination=pagination,
        is_admin=is_admin(),
        current_role=current_user.role,
        search_query=q,
        quick_report_links=dashboard_month_links()
    )


# ---------------- Новый клиент ----------------
@clients_bp.get("/clients/new", endpoint="new_client")
@login_required
def client_new_get():
    if not can_manage_clients():
        flash("Недостаточно прав для добавления клиента", "warning")
        return redirect(url_for("clients.list_clients"))
    return render_template("client_new.html")


@clients_bp.post("/clients/new", endpoint="new_client_post")
@login_required
def client_new_post():
    if not can_manage_clients():
        flash("Недостаточно прав для добавления клиента", "warning")
        return redirect(url_for("clients.list_clients"))

    name = request.form.get("name", "").strip()
    if not name:
        flash("Заполни поле: Название в системе", "danger")
        return redirect(url_for("clients.new_client"))

    c = Client(
        name=name,
        full_name=request.form.get("full_name") or None,
        bin=request.form.get("bin") or None,
        kpp=request.form.get("kpp") or None,
        ogrn=request.form.get("ogrn") or None,
        okpo=request.form.get("okpo") or None,
        legal_address=request.form.get("legal_address") or None,
        fact_address=request.form.get("fact_address") or None,
        post_address=request.form.get("post_address") or None,
        phone=request.form.get("phone") or None,
        email=request.form.get("email") or None,
        comment=request.form.get("comment") or None,
    )
    db.session.add(c)
    log_audit("create_client", f"{current_user.username} создал клиента {name}", "client", c.id)
    db.session.commit()

    notify_event("Создан новый клиент", f"Пользователь {current_user.username} создал клиента: {c.name}")
    flash("Клиент создан", "success")
    return redirect(url_for("clients.list_clients"))


# ---------------- Удаление клиента ----------------
@clients_bp.post("/clients/delete", endpoint="delete_client")
@login_required
def delete_client_post():
    if not is_admin():
        flash("Только админ может удалять клиентов", "warning")
        return redirect(url_for("clients.list_clients"))

    client_id = request.form.get("client_id")
    if not client_id or not client_id.isdigit():
        flash("Выбери клиента", "danger")
        return redirect(url_for("clients.list_clients"))

    c = Client.query.get_or_404(int(client_id))
    Talon.query.filter_by(client_id=c.id).delete()
    Balance.query.filter_by(client_id=c.id).delete()
    Contract.query.filter_by(client_id=c.id).delete()
    log_audit("delete_client", f"{current_user.username} удалил клиента {c.name}", "client", c.id)
    db.session.delete(c)
    db.session.commit()

    flash("Клиент удалён", "success")
    return redirect(url_for("clients.list_clients"))


# ---------------- Профиль клиента ----------------
@clients_bp.get("/clients/<int:client_id>/profile")
@login_required
def client_profile(client_id):
    client = Client.query.get_or_404(client_id)
    return render_template(
        "client_profile.html",
        client=client,
        tabs=_client_tabs(client),
        active_tab="profile",
    )


# ---------------- Договора ----------------
@clients_bp.get("/clients/<int:client_id>/contracts")
@login_required
def client_contracts(client_id):
    client = Client.query.get_or_404(client_id)
    contracts = Contract.query.filter_by(client_id=client.id).order_by(Contract.id.desc()).all()

    selected = None
    cid = request.args.get("id")
    if cid:
        try:
            cid_int = int(cid)
            selected = Contract.query.filter_by(client_id=client.id, id=cid_int).first()
        except ValueError:
            selected = None

    return render_template(
        "client_contracts.html",
        client=client,
        contracts=contracts,
        selected=selected,
        tabs=_client_tabs(client),
        active_tab="contract",
        timedelta=timedelta,
        current_role=current_user.role,
    )


@clients_bp.get("/clients/<int:client_id>/contracts/new", endpoint="contract_new_get")
@login_required
def contract_new_get(client_id):
    if not can_edit_contracts():
        flash("Недостаточно прав.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client_id))

    client = Client.query.get_or_404(client_id)
    return render_template(
        "contract_new.html",
        client=client,
        contracts=Contract.query.filter_by(client_id=client.id).order_by(Contract.id.desc()).all(),
        tabs=_client_tabs(client),
        active_tab="contract",
    )


@clients_bp.post("/clients/<int:client_id>/contracts/new", endpoint="contract_new_post")
@login_required
def contract_new_post(client_id):
    if not can_edit_contracts():
        flash("Недостаточно прав.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client_id))

    client = Client.query.get_or_404(client_id)

    number = (request.form.get("number") or "").strip()
    date_from = request.form.get("date_from") or ""
    date_to = request.form.get("date_to") or None

    if not number or not date_from:
        flash("Заполни обязательные поля (договор и дата от).", "danger")
        return redirect(url_for("clients.contract_new_get", client_id=client.id))

    try:
        date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
        date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError:
        flash("Неверный формат даты.", "danger")
        return redirect(url_for("clients.contract_new_get", client_id=client.id))

    tariff_name = None
    price_raw = (request.form.get("price_per_liter") or "").strip()
    price_per_liter = None
    if price_raw:
        try:
            price_per_liter = float(price_raw.replace(",", "."))
        except ValueError:
            flash("Цена должна быть числом.", "danger")
            return redirect(url_for("clients.contract_new_get", client_id=client.id))

    contract = Contract(
        client_id=client.id,
        number=number,
        date_from=date_from_dt,
        date_to=date_to_dt,
        tariff_name=tariff_name,
        price_per_liter=price_per_liter,
        online=bool(request.form.get("online")),
        allow_all_stations=bool(request.form.get("allow_all_stations")),
        forbidden_groups=(request.form.get("forbidden_groups") or "").strip() or None,
    )
    db.session.add(contract)
    log_audit("create_contract", f"{current_user.username} создал договор {number} клиента {client.name}", "contract", contract.id)
    db.session.commit()

    notify_event(
        "Создан договор",
        f"Пользователь {current_user.username} создал договор {contract.number} для клиента {client.name}"
    )
    flash("Договор создан.", "success")
    return redirect(url_for("clients.client_contracts", client_id=client.id, id=contract.id))


@clients_bp.get("/clients/<int:client_id>/contracts/<int:contract_id>/edit", endpoint="contract_edit_get")
@login_required
def contract_edit_get(client_id, contract_id):
    if not can_edit_contracts():
        flash("Недостаточно прав.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client_id, id=contract_id))

    client = Client.query.get_or_404(client_id)
    contract = Contract.query.filter_by(client_id=client.id, id=contract_id).first_or_404()

    return render_template(
        "contract_edit.html",
        client=client,
        contract=contract,
        contracts=Contract.query.filter_by(client_id=client.id).order_by(Contract.id.desc()).all(),
        tabs=_client_tabs(client),
        active_tab="contract",
    )


@clients_bp.post("/clients/<int:client_id>/contracts/<int:contract_id>/edit", endpoint="contract_edit_post")
@login_required
def contract_edit_post(client_id, contract_id):
    if not can_edit_contracts():
        flash("Недостаточно прав.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client_id, id=contract_id))

    client = Client.query.get_or_404(client_id)
    contract = Contract.query.filter_by(client_id=client.id, id=contract_id).first_or_404()

    number = (request.form.get("number") or "").strip()
    date_from = request.form.get("date_from") or ""
    date_to = request.form.get("date_to") or None

    if not number or not date_from:
        flash("Заполни обязательные поля (договор и дата от).", "danger")
        return redirect(url_for("clients.contract_edit_get", client_id=client.id, contract_id=contract.id))

    try:
        date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").date()
        date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError:
        flash("Неверный формат даты.", "danger")
        return redirect(url_for("clients.contract_edit_get", client_id=client.id, contract_id=contract.id))

    price_raw = (request.form.get("price_per_liter") or "").strip()
    price_per_liter = None
    if price_raw:
        try:
            price_per_liter = float(price_raw.replace(",", "."))
        except ValueError:
            flash("Цена должна быть числом.", "danger")
            return redirect(url_for("clients.contract_edit_get", client_id=client.id, contract_id=contract.id))

    contract.number = number
    contract.date_from = date_from_dt
    contract.date_to = date_to_dt
    contract.tariff_name = None
    contract.price_per_liter = price_per_liter
    contract.online = bool(request.form.get("online"))
    contract.allow_all_stations = bool(request.form.get("allow_all_stations"))
    contract.forbidden_groups = (request.form.get("forbidden_groups") or "").strip() or None

    log_audit("update_contract", f"{current_user.username} обновил договор {contract.number} клиента {client.name}", "contract", contract.id)
    db.session.commit()
    notify_event(
        "Договор обновлен",
        f"Пользователь {current_user.username} обновил договор {contract.number} клиента {client.name}"
    )
    flash("Договор обновлён.", "success")
    return redirect(url_for("clients.client_contracts", client_id=client.id, id=contract.id))


# ---------------- Талоны ----------------
@clients_bp.get("/clients/<int:client_id>/talons")
@login_required
def client_talons(client_id):
    client = Client.query.get_or_404(client_id)

    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    status = (request.args.get("status") or "active").strip().lower()

    q = Talon.query.filter_by(client_id=client.id)

    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
            q = q.filter(Talon.valid_from >= df)
        except ValueError:
            date_from = None

    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
            q = q.filter(Talon.valid_to <= dt)
        except ValueError:
            date_to = None

    today = kz_today()

    if status == "used":
        q = q.filter(Talon.state == "used")
    elif status == "expired":
        q = q.filter(Talon.state != "used", Talon.valid_to < today)
    else:
        status = "active"
        q = q.filter(Talon.state == "active", Talon.valid_to >= today)

    page = request.args.get("page", 1, type=int)
    pagination = q.order_by(Talon.id.desc()).paginate(page=page, per_page=100, error_out=False)
    talons = pagination.items

    grouped_talons = []
    grouped_index = {}
    for talon in talons:
        if talon.addendum_file:
            group_key = f"addendum-{talon.addendum_file.id}"
            group_title = talon.addendum_file.original_name or talon.addendum_file.title or f"Доп. соглашение #{talon.addendum_file.id}"
        elif talon.contract_id:
            group_key = f"contract-{talon.contract_id}"
            group_title = f"Без доп. соглашения — {talon.contract.number if talon.contract else 'договор'}"
        else:
            group_key = "other"
            group_title = "Без договора"

        if group_key not in grouped_index:
            grouped_index[group_key] = {"key": group_key, "title": group_title, "talons": [], "total_liters": 0.0}
            grouped_talons.append(grouped_index[group_key])

        grouped_index[group_key]["talons"].append(talon)
        grouped_index[group_key]["total_liters"] += float(talon.liters or 0)

    contracts = Contract.query.filter_by(client_id=client.id).order_by(Contract.id.desc()).all()
    balances = Balance.query.filter_by(client_id=client.id).all()

    addendums_map = {
        str(c.id): [
            {
                "id": f.id,
                "title": (f.original_name or f.title or f"Доп. соглашение #{f.id}"),
                "approved": f.approval_status == "approved"
            }
            for f in c.files if f.kind == "addendum"
        ]
        for c in contracts
    }

    balances_map = {}
    for b in balances:
        if b.contract_id is None:
            continue
        balances_map[str(b.contract_id)] = {
            "liters_left": float(b.liters_left or 0),
            "balance_control": bool(b.balance_control),
            "product_name": b.product_name,
        }

    return render_template(
        "client_talons.html",
        client=client,
        talons=talons,
        grouped_talons=grouped_talons,
        contracts=contracts,
        balances_json=json.dumps(balances_map, ensure_ascii=False),
        addendums_json=json.dumps(addendums_map, ensure_ascii=False),
        date_from=date_from,
        date_to=date_to,
        status=status,
        tabs=_client_tabs(client),
        active_tab="talons",
        pagination=pagination,
    )


# ---------------- Добавить талоны ----------------
@clients_bp.post("/clients/<int:client_id>/talons/add", endpoint="client_talons_add")
@login_required
def client_talons_add(client_id):
    client = Client.query.get_or_404(client_id)

    contract_id_raw = (request.form.get("contract_id") or "").strip()
    contract_id = int(contract_id_raw) if contract_id_raw.isdigit() else None
    addendum_file_id_raw = (request.form.get("addendum_file_id") or "").strip()
    addendum_file_id = int(addendum_file_id_raw) if addendum_file_id_raw.isdigit() else None

    product_name = (request.form.get("product_name") or "ГАЗ").strip() or "ГАЗ"

    if contract_id is None:
        flash("Выберите договор.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    contract = Contract.query.filter_by(client_id=client.id, id=contract_id).first()
    if not contract:
        flash("Договор не найден.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    if not contract_is_approved(contract):
        flash("Основной договор не подтвержден директором/замдиректора.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    if addendum_file_id:
        addendum = ContractFile.query.filter_by(
            id=addendum_file_id,
            contract_id=contract.id,
            kind="addendum"
        ).first()
        if not addendum or addendum.approval_status != "approved":
            flash("Выбранное доп. соглашение не подтверждено.", "danger")
            return redirect(url_for("clients.client_talons", client_id=client.id))

    try:
        liters = float((request.form.get("liters") or "0").replace(",", "."))
    except ValueError:
        liters = 0.0

    try:
        qty = int(request.form.get("qty") or "1")
    except ValueError:
        qty = 1

    qty = max(1, qty)
    if liters <= 0:
        flash("Укажите корректный номинал (литры).", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    def parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    valid_from = parse_date(request.form.get("valid_from") or "")
    valid_to = parse_date(request.form.get("valid_to") or "")

    if not valid_from:
        valid_from = kz_today()
    if not valid_to:
        valid_to = valid_from + timedelta(days=60)

    if valid_to < valid_from:
        flash("Дата окончания не может быть раньше даты начала.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    need = qty * liters
    bal = None

    if contract_id is not None:
        bal = Balance.query.filter_by(
            client_id=client.id,
            contract_id=contract_id,
            product_name=product_name
        ).first()
        if bal is None:
            bal = Balance.query.filter_by(client_id=client.id, contract_id=contract_id).first()

    if bal is not None and bal.balance_control:
        left = float(bal.liters_left or 0)
        if need > left + 1e-9:
            flash(f"Недостаточно остатка по договору: доступно {left:.2f} л, нужно {need:.2f} л.", "danger")
            return redirect(url_for("clients.client_talons", client_id=client.id))
        bal.liters_left = left - need
        bal.updated_at = datetime.utcnow()

    last_serial = (
        db.session.query(Talon.serial_number)
        .filter(Talon.client_id == client.id)
        .order_by(Talon.id.desc())
        .first()
    )

    try:
        base_serial = int(last_serial[0]) + 1 if last_serial and str(last_serial[0]).isdigit() else 1
    except Exception:
        base_serial = Talon.query.filter_by(client_id=client.id).count() + 1

    existing_codes = set(row[0] for row in db.session.query(Talon.code).all())

    def generate_unique_code():
        for _ in range(100):
            code_candidate = ''.join(str(secrets.randbelow(10)) for _ in range(12))
            if code_candidate not in existing_codes:
                existing_codes.add(code_candidate)
                return code_candidate
        raise RuntimeError("Не удалось сгенерировать уникальный код талона")

    for i in range(qty):
        serial_number = str(base_serial + i).zfill(5)
        code = generate_unique_code()

        t = Talon(
            client_id=client.id,
            contract_id=contract_id,
            holder_name=client.name,
            product_name=product_name,
            liters=liters,
            serial_number=serial_number,
            code=code,
            valid_from=valid_from,
            valid_to=valid_to,
            state="active",
            addendum_file_id=addendum_file_id,
        )
        db.session.add(t)

    log_audit("create_talons", f"{current_user.username} создал {qty} талонов для клиента {client.name}", "client", client.id)
    db.session.commit()
    notify_event(
        "Созданы талоны",
        f"Пользователь {current_user.username} создал {qty} талонов для клиента {client.name} по договору {contract.number}"
    )
    flash(f"Создано талонов: {qty}", "success")

    return redirect(url_for(
        "clients.client_talons",
        client_id=client.id,
        date_from=valid_from.isoformat() if valid_from else None,
        date_to=valid_to.isoformat() if valid_to else None,
        status="active",
    ))


# ---------------- Использовать талон ----------------
@clients_bp.post("/talons/<int:talon_id>/use")
@login_required
def talon_use(talon_id):
    used_time = kz_now()
    redeem_status, t = redeem_talon_atomic(
        talon_id=talon_id,
        used_at=used_time,
        user_id=current_user.id,
    )

    if redeem_status == "not_found":
        flash("Талон не найден", "danger")
        return redirect(url_for("clients.list_clients"))

    if redeem_status == "expired":
        flash("Срок действия талона истек", "danger")
        return redirect(url_for("clients.client_talons", client_id=t.client_id, status="expired"))

    if redeem_status == "already_used":
        flash("Талон уже использован", "warning")
        return redirect(url_for("clients.client_talons", client_id=t.client_id, status="used"))

    if redeem_status != "redeemed":
        flash("Талон недоступен для использования", "warning")
        return redirect(url_for("clients.client_talons", client_id=t.client_id))

    log_audit("use_talon", f"{current_user.username} использовал талон {t.serial_number}", "talon", t.id)
    db.session.commit()

    notify_event(
        "Талон использован",
        f"Талон {t.serial_number} клиента {t.client.name if t.client else t.client_id} "
        f"использован пользователем {current_user.username} в {format_kz(t.used_at)}"
    )
    flash("Талон использован", "success")
    return redirect(url_for("clients.client_talons", client_id=t.client_id, status="used"))


@clients_bp.post("/talons/<int:talon_id>/extend")
@login_required
def talon_extend(talon_id):
    t = Talon.query.get_or_404(talon_id)

    if not has_role("director", "zamdirector", "deputy_director", "executor", "operator"):
        flash("Недостаточно прав для продления талона.", "danger")
        return redirect(url_for("clients.client_talons", client_id=t.client_id))

    try:
        new_valid_to = datetime.strptime(request.form.get("new_valid_to") or "", "%Y-%m-%d").date()
    except Exception:
        flash("Укажите новую дату окончания.", "danger")
        return redirect(url_for("clients.client_talons", client_id=t.client_id, status="expired"))

    today = kz_today()
    if new_valid_to < today:
        flash("Новая дата не может быть раньше сегодняшней.", "danger")
        return redirect(url_for("clients.client_talons", client_id=t.client_id, status="expired"))

    t.valid_from = today
    t.valid_to = new_valid_to
    t.state = "active"

    log_audit("extend_talon", f"{current_user.username} продлил талон {t.serial_number} до {new_valid_to}", "talon", t.id)
    db.session.commit()
    notify_event(
        "Талон продлен",
        f"Талон {t.serial_number} продлен с {today} до {new_valid_to} пользователем {current_user.username}"
    )
    flash("Срок действия талона продлен.", "success")
    return redirect(url_for("clients.client_talons", client_id=t.client_id, status="active"))


@clients_bp.post("/clients/<int:client_id>/talons/delete-period", endpoint="talons_delete_period")
@login_required
def talons_delete_period(client_id):
    client = Client.query.get_or_404(client_id)

    if not has_role("director", "zamdirector", "deputy_director"):
        flash("Недостаточно прав для удаления талонов.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    try:
        valid_from = datetime.strptime((request.form.get("valid_from") or "").strip(), "%Y-%m-%d").date()
        valid_to = datetime.strptime((request.form.get("valid_to") or "").strip(), "%Y-%m-%d").date()
    except Exception:
        flash("Укажите период удаления талонов.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    liters_raw = (request.form.get("liters") or "").strip()
    liters = None
    if liters_raw:
        try:
            liters = float(liters_raw.replace(",", "."))
        except ValueError:
            flash("Неверный номинал для удаления.", "danger")
            return redirect(url_for("clients.client_talons", client_id=client.id, status="active"))

    q = Talon.query.filter_by(client_id=client.id).filter(
        Talon.valid_from == valid_from,
        Talon.valid_to == valid_to,
        Talon.state != "used",
    )
    if liters is not None:
        q = q.filter(Talon.liters == liters)

    talons = q.all()
    if not talons:
        flash("По выбранному периоду талоны не найдены.", "warning")
        return redirect(url_for("clients.client_talons", client_id=client.id, status="active"))

    deleted = 0
    restored = 0.0
    for t in talons:
        if t.contract_id:
            bal = Balance.query.filter_by(client_id=t.client_id, contract_id=t.contract_id, product_name=t.product_name).first()
            if bal is None:
                bal = Balance.query.filter_by(client_id=t.client_id, contract_id=t.contract_id).first()
            if bal is not None:
                bal.liters_left = float(bal.liters_left or 0) + float(t.liters or 0)
                bal.updated_at = datetime.utcnow()
                restored += float(t.liters or 0)
        db.session.delete(t)
        deleted += 1

    log_audit("delete_talons_period", f"{current_user.username} удалил {deleted} талонов клиента {client.name} за период {valid_from} - {valid_to}", "client", client.id)
    db.session.commit()
    flash(f"Удалено талонов: {deleted}. Возвращено в остаток: {restored:.2f} л", "success")
    return redirect(url_for("clients.client_talons", client_id=client.id, status="active"))


@clients_bp.post("/talons/<int:talon_id>/delete")
@login_required
def talon_delete(talon_id):
    t = Talon.query.get_or_404(talon_id)

    if not has_role("director", "zamdirector", "deputy_director"):
        flash("Недостаточно прав для удаления талона.", "danger")
        return redirect(url_for("clients.client_talons", client_id=t.client_id))

    status = talon_status(t)
    if status == "used":
        flash("Использованный талон удалять нельзя.", "danger")
        return redirect(url_for("clients.client_talons", client_id=t.client_id, status="used"))

    if t.contract_id:
        bal = Balance.query.filter_by(client_id=t.client_id, contract_id=t.contract_id, product_name=t.product_name).first()
        if bal is None:
            bal = Balance.query.filter_by(client_id=t.client_id, contract_id=t.contract_id).first()
        if bal is not None:
            bal.liters_left = float(bal.liters_left or 0) + float(t.liters or 0)
            bal.updated_at = datetime.utcnow()

    client_id = t.client_id
    log_audit("delete_talon", f"{current_user.username} удалил талон {t.serial_number}", "talon", t.id)
    db.session.delete(t)
    db.session.commit()
    flash("Талон удалён.", "success")
    return redirect(url_for("clients.client_talons", client_id=client_id, status=status))


# ---------------- QR ----------------
@clients_bp.get("/talons/<int:talon_id>/qr.png")
@login_required
def talon_qr_png(talon_id):
    t = Talon.query.get_or_404(talon_id)
    img = qrcode.make(str(t.code))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ---------------- Печать талонов (PDF A4) ----------------
@clients_bp.get("/clients/<int:client_id>/talons/print", endpoint="print_talons_choose")
@login_required
def print_talons_choose(client_id):
    client = Client.query.get_or_404(client_id)

    periods = (
        db.session.query(Talon.valid_from, Talon.valid_to)
        .filter(Talon.client_id == client.id)
        .distinct()
        .order_by(Talon.valid_from.desc(), Talon.valid_to.desc())
        .all()
    )

    periods_list = []
    for vf, vt in periods:
        periods_list.append({
            "valid_from": str(vf) if vf is not None else "",
            "valid_to": str(vt) if vt is not None else "",
        })

    return render_template("print_talons_choose.html", client=client, periods=periods_list)


@clients_bp.get("/clients/<int:client_id>/talons/print.pdf", endpoint="print_talons_pdf")
@login_required
def print_talons_pdf(client_id):
    client = Client.query.get_or_404(client_id)

    date_from_str = (request.args.get("date_from") or "").strip()
    date_to_str = (request.args.get("date_to") or "").strip()

    date_from = None
    date_to = None
    try:
        if date_from_str:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
    except Exception:
        date_from = None

    try:
        if date_to_str:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
    except Exception:
        date_to = None

    q = Talon.query.filter_by(client_id=client.id).filter(Talon.state != "used")
    if date_from:
        q = q.filter(Talon.valid_from >= date_from)
    if date_to:
        q = q.filter(Talon.valid_to <= date_to)

    talons = q.order_by(Talon.id.asc()).all()

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    try:
        fonts_dir = os.path.join(current_app.root_path, "static", "fonts")
        reg_regular = os.path.join(fonts_dir, "DejaVuSans.ttf")
        reg_bold = os.path.join(fonts_dir, "DejaVuSans-Bold.ttf")
        if os.path.exists(reg_regular) and "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DejaVuSans", reg_regular))
        if os.path.exists(reg_bold) and "DejaVuSans-Bold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", reg_bold))
        FONT_REG = "DejaVuSans"
        FONT_BOLD = "DejaVuSans-Bold"
    except Exception:
        FONT_REG = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"

    margin_x = 5 * mm
    margin_y = 6 * mm
    gap_x = 2 * mm
    gap_y = 2 * mm
    cols, rows = 4, 5

    cell_w = (page_w - 2 * margin_x - (cols - 1) * gap_x) / cols
    cell_h = (page_h - 2 * margin_y - (rows - 1) * gap_y) / rows

    logo_path = os.path.join(current_app.root_path, "static", "img", "company_logo.png")
    logo_reader = ImageReader(logo_path) if os.path.exists(logo_path) else None

    def _fmt_code(code_val):
        s = "".join(ch for ch in str(code_val) if ch.isdigit())
        if len(s) <= 3:
            return s
        parts = [s[:3]]
        if len(s) > 3:
            parts.append(s[3:6])
        if len(s) > 6:
            parts.append(s[6:9])
        if len(s) > 9:
            parts.append(s[9:])
        return " ".join([p for p in parts if p])

    def draw_ticket(x, y, w, h, t: Talon):
        c.setLineWidth(0.3)
        c.rect(x, y, w, h)

        liters = f"{t.liters} л" if str(t.liters).strip() else ""
        c.setFont(FONT_BOLD, 20)
        c.drawCentredString(x + w / 2, y + h - 9 * mm, liters)

        if logo_reader is not None:
            logo_w = w * 0.55
            logo_h = h * 0.22
            lx = x + (w - logo_w) / 2
            ly = y + h - 9 * mm - logo_h - 2 * mm
            c.drawImage(logo_reader, lx, ly, logo_w, logo_h, preserveAspectRatio=True, mask="auto")

        qr_size = min(w * 0.55, h * 0.38)
        qr_x = x + (w - qr_size) / 2
        qr_y = y + 16 * mm

        img = qrcode.make(str(t.code))
        qr_buf = BytesIO()
        img.save(qr_buf, format="PNG")
        qr_buf.seek(0)
        c.drawImage(ImageReader(qr_buf), qr_x, qr_y, qr_size, qr_size, preserveAspectRatio=True, mask="auto")

        code_str = _fmt_code(t.code)
        c.setFont(FONT_BOLD, 11)
        c.drawCentredString(x + w / 2, y + 10.5 * mm, code_str)

        date_str = f"{t.valid_from} - {t.valid_to}" if t.valid_from and t.valid_to else ""
        c.setFont(FONT_REG, 6.8)
        c.drawString(x + 2.2 * mm, y + 3.2 * mm, date_str)

        serial = str(t.serial_number) if t.serial_number is not None else ""
        c.drawRightString(x + w - 2.2 * mm, y + 3.2 * mm, serial)

    i = 0
    for t in talons:
        pos = i % (cols * rows)
        col = pos % cols
        row = pos // cols

        x = margin_x + col * (cell_w + gap_x)
        y = page_h - margin_y - (row + 1) * cell_h - row * gap_y

        draw_ticket(x, y, cell_w, cell_h, t)

        i += 1
        if i % (cols * rows) == 0:
            c.showPage()

    if len(talons) == 0:
        c.setFont("Helvetica", 14)
        c.drawString(20 * mm, page_h - 30 * mm, "Нет талонов для печати.")
        c.showPage()

    c.save()
    buf.seek(0)

    filename = f"talons_client_{client.id}.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=False, download_name=filename)


# ---------------- Отчёты клиента ----------------
@clients_bp.get("/clients/<int:client_id>/reports")
@login_required
def client_reports(client_id):
    client = Client.query.get_or_404(client_id)

    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()

    q = Talon.query.filter_by(client_id=client.id)

    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
            q = q.filter(Talon.valid_from >= df)
        except ValueError:
            date_from = ""

    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
            q = q.filter(Talon.valid_to <= dt)
        except ValueError:
            date_to = ""

    all_talons = q.order_by(Talon.id.desc()).all()
    balances = Balance.query.filter_by(client_id=client.id).order_by(Balance.updated_at.desc()).all()
    balance_liters = sum(float(b.liters_left or 0) for b in balances)

    active_count = used_count = expired_count = blocked_count = 0
    total_liters = used_liters = active_liters = expired_liters = blocked_liters = 0.0
    for t in all_talons:
        liters = float(t.liters or 0)
        total_liters += liters
        state = talon_status(t)
        if state == "used":
            used_count += 1
            used_liters += liters
        elif state == "expired":
            expired_count += 1
            expired_liters += liters
        elif state == "blocked":
            blocked_count += 1
            blocked_liters += liters
        else:
            active_count += 1
            active_liters += liters

    page = request.args.get("page", 1, type=int)
    per_page = 100
    total_count = len(all_talons)
    start_idx = max((page - 1) * per_page, 0)
    end_idx = start_idx + per_page
    talons = all_talons[start_idx:end_idx]
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total_count,
        "pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_num": page - 1,
        "next_num": page + 1,
    }

    return render_template(
        "client_reports.html",
        client=client,
        talons=talons,
        balances=balances,
        date_from=date_from,
        date_to=date_to,
        active_count=active_count,
        used_count=used_count,
        expired_count=expired_count,
        blocked_count=blocked_count,
        total_liters=total_liters,
        used_liters=used_liters,
        active_liters=active_liters,
        expired_liters=expired_liters,
        blocked_liters=blocked_liters,
        balance_liters=balance_liters,
        total_count=total_count,
        pagination=pagination,
        tabs=_client_tabs(client),
        active_tab="reports",
    )
