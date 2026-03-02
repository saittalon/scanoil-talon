import os
import json
import random
from datetime import datetime, timedelta
from io import BytesIO

import qrcode
from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, current_app
from flask_login import login_required, current_user
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from models import db, Client, Contract, Balance, Talon

clients_bp = Blueprint("clients", __name__)


def is_admin():
    return current_user.is_authenticated and current_user.role == "admin"


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
    """Установка/обновление остатка литров по договору (для выдачи талонов)."""

    client = Client.query.get_or_404(client_id)

    # на всякий случай: менять баланс лучше только админу
    if not is_admin():
        flash("Недостаточно прав.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client.id))

    contract_id_raw = (request.form.get("contract_id") or "").strip()
    contract_id = int(contract_id_raw) if contract_id_raw.isdigit() else None
    if contract_id is None:
        flash("Не выбран договор.", "danger")
        return redirect(url_for("clients.client_contracts", client_id=client.id))

    liters_raw = (request.form.get("liters_left") or "").strip()
    try:
        liters_left = float((liters_raw or "0").replace(",", "."))
    except ValueError:
        liters_left = 0.0

    # checkbox: если галочка есть — контролируем
    balance_control = bool(request.form.get("balance_control"))

    product_name = (request.form.get("product_name") or "ГАЗ").strip() or "ГАЗ"

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

    db.session.commit()
    flash("Остаток обновлён.", "success")
    return redirect(url_for("clients.client_contracts", client_id=client.id, id=contract_id))


# ---------------- Клиенты ----------------
@clients_bp.get("/clients")
@login_required
def list_clients():
    clients = Client.query.order_by(Client.id.desc()).all()
    return render_template("clients.html", clients=clients, is_admin=is_admin())


# ---------------- Новый клиент (ВАЖНО: endpoint=new_client) ----------------
@clients_bp.get("/clients/new", endpoint="new_client")
@login_required
def client_new_get():
    if not is_admin():
        flash("Только админ может добавлять клиентов", "warning")
        return redirect(url_for("clients.list_clients"))
    return render_template("client_new.html")


@clients_bp.post("/clients/new", endpoint="new_client_post")
@login_required
def client_new_post():
    if not is_admin():
        flash("Только админ может добавлять клиентов", "warning")
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
    db.session.commit()
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
    )


@clients_bp.get("/clients/<int:client_id>/contracts/new", endpoint="contract_new_get")
@login_required
def contract_new_get(client_id):
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

    tariff_name = (request.form.get("tariff_name") or "").strip() or None
    price_raw = (request.form.get("price_per_liter") or "").strip()
    price_per_liter = None
    if price_raw:
        try:
            price_per_liter = float(price_raw.replace(",", "."))
        except ValueError:
            flash("Цена должна быть числом.", "danger")
            return redirect(url_for("clients.contract_new_get", client_id=client.id))

    online = bool(request.form.get("online"))
    allow_all_stations = bool(request.form.get("allow_all_stations"))
    forbidden_groups = (request.form.get("forbidden_groups") or "").strip() or None

    contract = Contract(
        client_id=client.id,
        number=number,
        date_from=date_from_dt,
        date_to=date_to_dt,
        tariff_name=tariff_name,
        price_per_liter=price_per_liter,
        online=online,
        allow_all_stations=allow_all_stations,
        forbidden_groups=forbidden_groups,
    )
    db.session.add(contract)
    db.session.commit()

    flash("Договор создан.", "success")
    return redirect(url_for("clients.client_contracts", client_id=client.id, id=contract.id))


@clients_bp.get("/clients/<int:client_id>/contracts/<int:contract_id>/edit", endpoint="contract_edit_get")
@login_required
def contract_edit_get(client_id, contract_id):
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

    tariff_name = (request.form.get("tariff_name") or "").strip() or None
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
    contract.tariff_name = tariff_name
    contract.price_per_liter = price_per_liter
    contract.online = bool(request.form.get("online"))
    contract.allow_all_stations = bool(request.form.get("allow_all_stations"))
    contract.forbidden_groups = (request.form.get("forbidden_groups") or "").strip() or None

    db.session.commit()
    flash("Договор обновлён.", "success")
    return redirect(url_for("clients.client_contracts", client_id=client.id, id=contract.id))

# ---------------- Талоны ----------------
@clients_bp.get("/clients/<int:client_id>/talons")
@login_required
def client_talons(client_id):
    client = Client.query.get_or_404(client_id)
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None

    q = Talon.query.filter_by(client_id=client.id)
    if date_from:
        q = q.filter(Talon.valid_from >= date_from)
    if date_to:
        q = q.filter(Talon.valid_to <= date_to)
    talons = q.order_by(Talon.id.desc()).all()

    contracts = Contract.query.filter_by(client_id=client.id).order_by(Contract.id.desc()).all()
    balances = Balance.query.filter_by(client_id=client.id).all()

    # mapping contract_id -> info (used by JS in template)
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
        contracts=contracts,
        balances_json=json.dumps(balances_map, ensure_ascii=False),
        date_from=date_from,
        date_to=date_to,
        tabs=_client_tabs(client),
        active_tab="talons",
    )




# ---------------- Добавить талоны ----------------
@clients_bp.post("/clients/<int:client_id>/talons/add", endpoint="client_talons_add")
@login_required
def client_talons_add(client_id):
    client = Client.query.get_or_404(client_id)

    contract_id_raw = (request.form.get("contract_id") or "").strip()
    contract_id = int(contract_id_raw) if contract_id_raw.isdigit() else None

    product_name = (request.form.get("product_name") or "ГАЗ").strip() or "ГАЗ"

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

    # даты
    def parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    valid_from = parse_date(request.form.get("valid_from") or "")
    valid_to = parse_date(request.form.get("valid_to") or "")

    if not valid_from:
        valid_from = datetime.utcnow().date()
    if not valid_to:
        valid_to = valid_from + timedelta(days=60)

    if valid_to < valid_from:
        flash("Дата окончания не может быть раньше даты начала.", "danger")
        return redirect(url_for("clients.client_talons", client_id=client.id))

    # баланс (если контролируется) — списываем литры
    need = qty * liters
    bal = None
    if contract_id is not None:
        bal = Balance.query.filter_by(client_id=client.id, contract_id=contract_id, product_name=product_name).first()
        # если не нашли по продукту — попробуем любой по договору
        if bal is None:
            bal = Balance.query.filter_by(client_id=client.id, contract_id=contract_id).first()

    if bal is not None and bal.balance_control:
        left = float(bal.liters_left or 0)
        if need > left + 1e-9:
            flash(f"Недостаточно остатка по договору: доступно {left:.2f} л, нужно {need:.2f} л.", "danger")
            return redirect(url_for("clients.client_talons", client_id=client.id))
        bal.liters_left = left - need
        bal.updated_at = datetime.utcnow()

    # генерация серий и кодов
    existing = Talon.query.filter_by(client_id=client.id).count()
    base_serial = existing + 1

    for i in range(qty):
        serial_number = str(base_serial + i).zfill(5)
        code = str(random.randint(1000000000, 9999999999))

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
        )
        db.session.add(t)

    db.session.commit()
    flash(f"Создано талонов: {qty}", "success")
    return redirect(url_for(
        "clients.client_talons",
        client_id=client.id,
        date_from=valid_from.isoformat() if valid_from else None,
        date_to=valid_to.isoformat() if valid_to else None,
    ))

# ---------------- Использовать талон ----------------
@clients_bp.post("/talons/<int:talon_id>/use")
@login_required
def talon_use(talon_id):
    t = Talon.query.get_or_404(talon_id)
    if t.state == "used":
        flash("Талон уже использован", "warning")
        return redirect(url_for("clients.client_talons", client_id=t.client_id))

    t.state = "used"
    t.used_at = datetime.utcnow()
    t.used_by_user_id = current_user.id
    db.session.commit()

    flash("Талон использован", "success")
    return redirect(url_for("clients.client_talons", client_id=t.client_id))


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

    # список периодов, которые реально есть у талонов клиента
    periods = (
        db.session.query(Talon.valid_from, Talon.valid_to)
        .filter(Talon.client_id == client.id)
        .distinct()
        .order_by(Talon.valid_from.desc(), Talon.valid_to.desc())
        .all()
    )

    # преобразуем в список dict для шаблона
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

    # фильтр по периоду (чтобы печатать талоны "по мере создания")
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

    # печатаем только неиспользованные талоны (active/blocked)
    q = Talon.query.filter_by(client_id=client.id).filter(Talon.state != "used")
    if date_from:
        q = q.filter(Talon.valid_from >= date_from)
    if date_to:
        q = q.filter(Talon.valid_to <= date_to)
    talons = q.order_by(Talon.id.asc()).all()

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4  # points

    # Шрифт с поддержкой кириллицы (чтобы "л" не становилась чёрным квадратиком)
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

    # Как в образце "МурАз": 4 колонки, 5 рядов (20 талонов на лист)
    margin_x = 5 * mm
    margin_y = 6 * mm
    gap_x = 2 * mm
    gap_y = 2 * mm
    cols, rows = 4, 5

    cell_w = (page_w - 2 * margin_x - (cols - 1) * gap_x) / cols
    cell_h = (page_h - 2 * margin_y - (rows - 1) * gap_y) / rows

    # логотип (если есть)
    logo_path = os.path.join(current_app.root_path, "static", "img", "company_logo.png")
    logo_reader = ImageReader(logo_path) if os.path.exists(logo_path) else None

    def _fmt_code(code_val):
        s = "".join(ch for ch in str(code_val) if ch.isdigit())
        # формат как "011 365 109 7" (3-3-3-остаток)
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
        # тонкая рамка
        c.setLineWidth(0.3)
        c.rect(x, y, w, h)

        # верхний текст (литры)
        liters = f"{t.liters} л" if str(t.liters).strip() else ""
        c.setFont(FONT_BOLD, 20)
        c.drawCentredString(x + w / 2, y + h - 9 * mm, liters)

        # логотип под литрами
        if logo_reader is not None:
            logo_w = w * 0.55
            logo_h = h * 0.22
            lx = x + (w - logo_w) / 2
            ly = y + h - 9 * mm - logo_h - 2 * mm
            c.drawImage(logo_reader, lx, ly, logo_w, logo_h, preserveAspectRatio=True, mask="auto")
        else:
            # если логотипа нет, чуть смещаем блок ниже
            ly = y + h - 22 * mm

        # QR (вместо штрихкода) — по центру, крупный, в квадрате
        qr_size = min(w * 0.55, h * 0.38)
        qr_x = x + (w - qr_size) / 2
        qr_y = y + 16 * mm  # от низа

        img = qrcode.make(str(t.code))
        qr_buf = BytesIO()
        img.save(qr_buf, format="PNG")
        qr_buf.seek(0)
        c.drawImage(ImageReader(qr_buf), qr_x, qr_y, qr_size, qr_size, preserveAspectRatio=True, mask="auto")

        # код (крупный) под QR
        code_str = _fmt_code(t.code)
        c.setFont(FONT_BOLD, 11)
        c.drawCentredString(x + w / 2, y + 10.5 * mm, code_str)

        # срок действия и номер серии внизу
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
        # координаты в ReportLab от низа, поэтому считаем ряд сверху вниз
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

    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    q = Talon.query.filter_by(client_id=client.id)
    if date_from:
        q = q.filter(Talon.valid_from >= date_from)
    if date_to:
        q = q.filter(Talon.valid_to <= date_to)

    talons = q.order_by(Talon.id.desc()).all()

    return render_template(
        "client_reports.html",
        client=client,
        talons=talons,
        date_from=date_from,
        date_to=date_to,
        tabs=_client_tabs(client),
        active_tab="reports",
    )