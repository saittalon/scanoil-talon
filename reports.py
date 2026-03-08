from flask import Blueprint, send_file, render_template, request
from flask_login import login_required
from io import BytesIO
import pandas as pd
from models import Client, Talon
from helpers import talon_status_label, format_kz

reports_bp = Blueprint('reports', __name__)


def _client_rows(talons, client):
    rows = []
    for t in talons:
        rows.append({
            '№': t.serial_number,
            'Клиент': client.name,
            'Держатель': t.holder_name,
            'Товар': t.product_name,
            'Номинал': float(t.liters or 0),
            'С': t.valid_from.strftime('%d.%m.%Y') if t.valid_from else '',
            'По': t.valid_to.strftime('%d.%m.%Y') if t.valid_to else '',
            'Статус': talon_status_label(t),
            'Дата и время использования': format_kz(t.used_at),
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
            'Талон': t.code,
        })
    return rows


@reports_bp.get('/clients/<int:client_id>/report.xlsx')
@login_required
def client_report_excel(client_id: int):
    client = Client.query.get_or_404(client_id)
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    q = Talon.query.filter_by(client_id=client.id)
    if date_from:
        try:
            q = q.filter(Talon.valid_from >= pd.to_datetime(date_from).date())
        except Exception:
            pass
    if date_to:
        try:
            q = q.filter(Talon.valid_to <= pd.to_datetime(date_to).date())
        except Exception:
            pass

    talons = q.order_by(Talon.id.asc()).all()
    df = pd.DataFrame(_client_rows(talons, client))
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='clients_coupons')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'report_{client.name}.xlsx'.replace(' ', '_'), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@reports_bp.get('/reports/all.xlsx')
@login_required
def all_clients_report_excel():
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    q = Talon.query
    if date_from:
        try:
            q = q.filter(Talon.created_at >= pd.Timestamp(pd.to_datetime(date_from).date()))
        except Exception:
            pass
    if date_to:
        try:
            dt = pd.to_datetime(date_to).date()
            q = q.filter(Talon.created_at <= pd.Timestamp(dt) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        except Exception:
            pass
    talons = q.order_by(Talon.created_at.desc()).all()
    rows = []
    for t in talons:
        contract = t.contract
        price = contract.price_per_liter if (contract and contract.price_per_liter is not None) else 0.0
        rows.append({
            'Клиент': t.client.name if t.client else '',
            'Договор': contract.number if contract else '',
            '№ талона': t.serial_number,
            'Код': t.code,
            'Статус': talon_status_label(t),
            'Дата и время использования': format_kz(t.used_at),
            'АГЗС': t.used_agzs.name if t.used_agzs else '',
            'Услуга': t.product_name,
            'Количество': float(t.liters or 0),
            'Цена': float(price),
            'Стоимость': float(t.liters or 0) * float(price),
            'Доп. соглашение': t.addendum_file.original_name if getattr(t, 'addendum_file', None) else '',
        })
    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='scanoilcard_report')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='allclients_report.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@reports_bp.get('/reports/all')
@login_required
def reports_all_page():
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    q = Talon.query
    if date_from:
        try:
            q = q.filter(Talon.created_at >= pd.Timestamp(pd.to_datetime(date_from).date()))
        except Exception:
            pass
    if date_to:
        try:
            dt = pd.to_datetime(date_to).date()
            q = q.filter(Talon.created_at <= pd.Timestamp(dt) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        except Exception:
            pass
    talons = q.order_by(Talon.created_at.desc()).all()
    rows = []
    for t in talons:
        price = t.contract.price_per_liter if (t.contract and t.contract.price_per_liter is not None) else 0.0
        op_dt = t.used_at or t.created_at
        rows.append({
            'Дата': format_kz(op_dt, '%d.%m.%Y') if op_dt else '',
            'Время': format_kz(op_dt, '%H:%M:%S') if op_dt else '',
            'Владелец': t.holder_name,
            'Клиент': t.client.name if t.client else '',
            'Операция': 'Талон',
            'Услуга': t.product_name,
            'Количество': float(t.liters or 0),
            'Цена': float(price),
            'Стоимость': float(t.liters or 0) * float(price),
            'АЗС': t.used_agzs.name if t.used_agzs else '',
            'Адрес': '',
            'Статус': talon_status_label(t),
        })
    return render_template('reports_all.html', rows=rows, date_from=date_from, date_to=date_to)


@reports_bp.get('/reports')
@login_required
def reports_index():
    clients = Client.query.order_by(Client.name.asc()).all()
    return render_template('reports_index.html', clients=clients)
