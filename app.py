import os
from datetime import datetime

from flask import (
    Flask, redirect, url_for, request, jsonify, render_template,
    abort, current_app
)
from flask_login import LoginManager, login_required
from supabase import create_client

from config import Config
from models import (
    db,
    User, Client, Contract, Balance, Talon, AGZS,
    BotSession, TalonRedemption, WebAppToken,
    ContractFile
)

from auth import auth_bp
from clients import clients_bp
from reports import reports_bp

# ✅ Файлы договоров (PDF) — блюпринт
from contract_files import contract_files_bp


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

    # ✅ Регистрируем блюпринты
    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(contract_files_bp)

    @app.get("/")
    def home():
        return redirect(url_for("clients.list_clients"))

    # ✅ Открытие PDF через Supabase Storage (signed url)
    @app.get("/files/contracts/<int:file_id>")
    @login_required
    def download_contract_file(file_id: int):
        f = ContractFile.query.get_or_404(file_id)

        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

        if not supabase_url or not supabase_key:
            abort(500, description="SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")

        sb = create_client(supabase_url, supabase_key)

        bucket = getattr(f, "bucket", None) or "contracts"
        key = getattr(f, "storage_key", None) or getattr(f, "storage_path", None)

        if not key:
            abort(404)

        signed = sb.storage.from_(bucket).create_signed_url(key, 60)
        signed_url = signed.get("signedURL") or signed.get("signedUrl")

        if not signed_url:
            abort(404)

        return redirect(signed_url)

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
        sess = BotSession.query.filter_by(
            telegram_user_id=t.telegram_user_id,
            is_active=True
        ).first()
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

    # ---------------- DB init (SAFE) ----------------
    def init_db(seed: bool = False):
        """
        SAFE MODE (default): only creates tables, DOES NOT insert test data.
        SEED MODE: inserts initial data (admin/agzs/etc). Run ONLY when INIT_DB=1.
        """
        from datetime import date, timedelta

        with app.app_context():
            db.create_all()

            if not seed:
                return

            admin = User.query.filter_by(username="admin").first()
            if admin is None:
                admin = User(username="admin", role="admin")
                admin.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
                db.session.add(admin)
                db.session.commit()

            c = Client.query.first()
            if c is None:
                c = Client(name="Проверка 121212", full_name="ТОО Проверка", comment="проверка")
                db.session.add(c)
                db.session.commit()

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

            contract = Contract.query.first()
            if contract is None:
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

            bal = Balance.query.filter_by(client_id=c.id, contract_id=contract.id).first()
            if bal is None:
                bal = Balance(
                    client_id=c.id,
                    contract_id=contract.id,
                    product_name="ГАЗ",
                    liters_left=50.0,
                    balance_control=True
                )
                db.session.add(bal)
                db.session.commit()

            if Talon.query.count() == 0:
                today = datetime.utcnow().date()
                till = (datetime.utcnow() + timedelta(days=60)).date()

                for i in range(4):
                    is_used = i < 2
                    used_at = (
                        datetime(2026, 2, 10, 12, 30, 0) if i == 0
                        else datetime(2026, 2, 18, 9, 15, 0) if i == 1
                        else None
                    )

                    tln = Talon(
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
                    db.session.add(tln)

                db.session.commit()

    init_db(seed=False)

    if os.getenv("INIT_DB", "0") == "1":
        init_db(seed=True)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
