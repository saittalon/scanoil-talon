import os
from flask import Flask, redirect, url_for, request, jsonify, render_template
from flask_login import LoginManager
from sqlalchemy import text
from datetime import date, datetime

from config import Config
from models import db, User, Client, Contract, Balance, Talon, AGZS, BotSession, TalonRedemption, WebAppToken

from auth import auth_bp
from clients import clients_bp
from reports import reports_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login_get"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(reports_bp)

    @app.get("/")
    def home():
        return redirect(url_for("clients.list_clients"))

    # ---------------- Telegram WebApp (QR scanner) ----------------
    @app.get("/tg/scan")
    def tg_scan():
        token = request.args.get("token", "").strip()
        return render_template("tg_scan.html", token=token)

    @app.post("/tg/api/scan")
    def tg_api_scan():
        data = request.get_json(silent=True) or {}
        token = (data.get("token") or "").strip()
        code = (data.get("code") or "").strip()

        if not token or not code:
            return jsonify({"ok": False, "error": "missing_token_or_code"}), 400

        t = WebAppToken.query.filter_by(token=token).first()
        if t is None or t.expires_at < datetime.utcnow():
            return jsonify({"ok": False, "error": "token_expired"}), 401

        # active bot session
        sess = BotSession.query.filter_by(telegram_user_id=t.telegram_user_id, is_active=True).first()
        if sess is None:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401

        talon = Talon.query.filter_by(code=code).first()
        if talon is None:
            return jsonify({"ok": False, "error": "talon_not_found"}), 404

        # already used?
        if getattr(talon, "state", None) == "used":
            last = (TalonRedemption.query
                    .filter_by(talon_id=talon.id)
                    .order_by(TalonRedemption.used_at.desc())
                    .first())
            return jsonify({
                "ok": False,
                "error": "already_used",
                "used_at": last.used_at.isoformat() if last else None,
                "agzs": last.agzs.name if last and last.agzs else None
            }), 409

        # mark used
        talon.state = "used"
        talon.used_at = datetime.utcnow()
        talon.used_agzs_id = sess.agzs_id
        talon.used_telegram_user_id = str(sess.telegram_user_id)

        red = TalonRedemption(
            talon_id=talon.id,
            agzs_id=sess.agzs_id,
            telegram_user_id=str(sess.telegram_user_id),
            used_at=datetime.utcnow(),
            source="telegram_webapp"
        )
        db.session.add(red)
        db.session.commit()

        return jsonify({
            "ok": True,
            "liters": getattr(talon, "liters", None),
            "product": getattr(talon, "product_name", None),
            "serial": getattr(talon, "serial_number", None),
            "valid_from": str(getattr(talon, "valid_from", "")),
            "valid_to": str(getattr(talon, "valid_to", "")),
            "agzs": sess.agzs.name if sess.agzs else None
        })


    def init_db():
        from datetime import date, datetime, timedelta

        with app.app_context():
            db.create_all()

            # --- lightweight auto-migration ---
            try:
                cols = [r[1] for r in db.session.execute(
                    text("PRAGMA table_info(talon)")
                ).fetchall()]

                if "used_agzs_id" not in cols:
                    db.session.execute(
                        text("ALTER TABLE talon ADD COLUMN used_agzs_id INTEGER")
                    )

                if "used_telegram_user_id" not in cols:
                    db.session.execute(
                        text("ALTER TABLE talon ADD COLUMN used_telegram_user_id VARCHAR(50)")
                    )

                db.session.commit()

            except Exception:
                db.session.rollback()

            # --- admin ---
            admin = User.query.filter_by(username="admin").first()
            if admin is None:
                admin = User(username="admin", role="admin")
                admin.set_password("admin123")
                db.session.add(admin)
                db.session.commit()

            # --- client ---
            c = Client.query.first()
            if c is None:
                c = Client(
                    name="Проверка 121212",
                    full_name="ТОО Проверка",
                    comment="проверка"
                )
                db.session.add(c)
                db.session.commit()

            # --- AGZS ---
            agzs_names = [
                "Жангельдина", "Ст город", "Капал батыр", "Миг",
                "Основная - База", "Сан Ойл", "Шнос", "Сайман",
                "Самал", "Сырым батыр", "Центральная", "Степная",
                "Мадели Кожа - Сигма", "Тассай - Аксумбе",
                "Қызылсай", "Казыгурт",
                "База 2 - Жибек Жолы",
                "Кайтпас - Толеметова",
                "Алмаз",
            ]

            for name in agzs_names:
                if AGZS.query.filter_by(name=name).first() is None:
                    a = AGZS(name=name, login=name, is_active=True)
                    a.set_password(f"{name}123")
                    db.session.add(a)

            db.session.commit()

            # --- CONTRACT ---
            contract = Contract(
                client_id=c.id,
                number="проверка от 28.01.2026",
                date_from=date(2026, 1, 28),
                date_to=date(2026, 12, 31),
                tariff_name="Газ 102тг",
                price_per_liter=102.0,
                online=True,
                allow_all_stations=False,
                forbidden_groups="МурАз (Туркестанская обл.)"
            )
            db.session.add(contract)
            db.session.commit()

            # --- BALANCE ---
            bal = Balance(
                client_id=c.id,
                contract_id=contract.id,
                product_name="ГАЗ",
                liters_left=50.0,
                balance_control=True
            )
            db.session.add(bal)

            # --- TALONS ---
            today = datetime.utcnow().date()
            till = (datetime.utcnow() + timedelta(days=60)).date()

            for i in range(4):
                is_used = i < 2
                used_at = (
                    datetime(2026, 2, 10, 12, 30, 0) if i == 0
                    else datetime(2026, 2, 18, 9, 15, 0) if i == 1
                    else None
                )

                t = Talon(
                    client_id=c.id,
                    contract_id=contract.id,
                    holder_name=c.name,
                    product_name="ГАЗ",
                    liters=10.0,
                    serial_number=str(i + 1).zfill(5),
                    code=str(1800000000 + i * 12345),
                    valid_from=today,
                    valid_to=till,
                    state="used" if is_used else "active",
                    used_at=used_at,
                    used_by_user_id=admin.id if is_used else None
                )

                db.session.add(t)

            db.session.commit()

    init_db()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
