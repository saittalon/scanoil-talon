from flask import Blueprint, send_file, render_template, request
from flask_login import login_required
from io import BytesIO
import pandas as pd
from models import Client, Talon, Contract, AGZS

reports_bp = Blueprint("reports", __name__)


@reports_bp.get("/clients/<int:client_id>/report.xlsx")
@login_required
def client_report_excel(client_id: int):
    from flask import request
    client = Client.query.get_or_404(client_id)

    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    q = Talon.query.filter_by(client_id=client.id)
    if date_from:
        try:
            df = pd.to_datetime(date_from).date()
            q = q.filter(Talon.valid_from >= df)
        except:
            pass
    if date_to:
        try:
            dt = pd.to_datetime(date_to).date()
            q = q.filter(Talon.valid_to <= dt)
        except:
            pass

    talons = q.order_by(Talon.id.asc()).all()

    rows = []
    for t in talons:
        rows.append({
            "№": t.serial_number,
            "Клиент": client.name,
            "Держатель": t.holder_name,
            "Товар": t.product_name,
            "Номинал": float(t.liters),
            "С": t.valid_from.strftime("%d.%m.%Y"),
            "По": t.valid_to.strftime("%d.%m.%Y"),
            "Талон": t.code,
        })

    df = pd.DataFrame(rows)

    # добавить АГЗС по пробитию (если есть)
    try:
        agzs_map = {a.id: a.name for a in AGZS.query.all()}
        if "used_agzs_id" in df.columns:
            df["agzs_name"] = df["used_agzs_id"].map(agzs_map)
    except Exception:
        pass

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="clients_coupons")
    output.seek(0)

    filename = f"report_{client.name}.xlsx".replace(" ", "_")
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@reports_bp.get("/reports/all.xlsx")
@login_required
def all_clients_report_excel():
    """Отчёт по всем клиентам (как выгрузка операций). Берём только использованные талоны."""
    from flask import request
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    q = Talon.query.filter(Talon.state == "used")
    if date_from:
        try:
            df = pd.to_datetime(date_from).date()
            q = q.filter(Talon.used_at >= pd.Timestamp(df))
        except:
            pass
    if date_to:
        try:
            dt = pd.to_datetime(date_to).date()
            q = q.filter(Talon.used_at <= pd.Timestamp(dt) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        except:
            pass

    talons = q.order_by(Talon.used_at.desc()).all()

    rows = []
    for t in talons:
        client = t.client
        contract = t.contract
        price = contract.price_per_liter if (contract and contract.price_per_liter is not None) else 0.0
        cost = float(t.liters) * float(price)
        used_dt = t.used_at
        rows.append({
            "Дата": used_dt.strftime("%d.%m.%Y") if used_dt else "",
            "Время": used_dt.strftime("%H:%M:%S") if used_dt else "",
            "Карта": "",
            "Владелец": t.holder_name,
            "Клиент": client.name if client else "",
            "Операция": "Талон",
            "Услуга": t.product_name,
            "Количество": float(t.liters),
            "Цена": float(price),
            "Стоимость": float(cost),
            "АЗС": "",
            "Адрес": "",
        })

    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="scanoilcard_report")
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="allclients_report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@reports_bp.get("/reports/all")
@login_required
def reports_all_page():
    """Страница отчёта по всем клиентам (фильтр + таблица + скачивание Excel)."""
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    preset = request.args.get("preset", "").strip()

    # presets (demo: 2026). Если preset задан и даты пустые — подставляем
    if preset and not (date_from or date_to):
        if preset == "jan":
            date_from, date_to = "2026-01-01", "2026-01-31"
        elif preset == "feb":
            date_from, date_to = "2026-02-01", "2026-02-29"

    q = Talon.query.filter(Talon.state == "used")

    if date_from:
        try:
            df = pd.to_datetime(date_from).date()
            q = q.filter(Talon.used_at >= pd.Timestamp(df))
        except:
            pass

    if date_to:
        try:
            dt = pd.to_datetime(date_to).date()
            q = q.filter(Talon.used_at <= pd.Timestamp(dt) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        except:
            pass

    talons = q.order_by(Talon.used_at.desc()).all()

    rows = []
    for t in talons:
        client = t.client
        contract = t.contract
        price = contract.price_per_liter if (contract and contract.price_per_liter is not None) else 0.0
        cost = float(t.liters) * float(price)
        used_dt = t.used_at
        rows.append({
            "Дата": used_dt.strftime("%d.%m.%Y") if used_dt else "",
            "Время": used_dt.strftime("%H:%M:%S") if used_dt else "",
            "Владелец": t.holder_name,
            "Клиент": client.name if client else "",
            "Операция": "Талон",
            "Услуга": t.product_name,
            "Количество": float(t.liters),
            "Цена": float(price),
            "Стоимость": float(cost),
            "АЗС": "",
            "Адрес": "",
        })

    return render_template("reports_all.html", rows=rows, date_from=date_from, date_to=date_to)


@reports_bp.get("/reports")
@login_required
def reports_index():
    """Страница выбора отчётов."""
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template("reports_index.html", clients=clients)
