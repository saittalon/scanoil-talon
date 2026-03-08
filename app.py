import os
from datetime import datetime, timezone
from sqlalchemy import inspect, text

from flask import (
    Flask, redirect, url_for, request, jsonify, render_template,
    abort
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
from helpers import require_roles, talon_status_label, format_kz, kz_now
from mail_utils import send_daily_report
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

    @app.context_processor
    def inject_role_label():
        def role_label(role):
            labels = {
                "admin": "Администратор",
                "director": "Директор",
                "zamdirector": "Заместитель директора",
                "deputy_director": "Заместитель директора",
                "manager": "Менеджер",
                "operator": "Оператор",
                "executor": "Исполнитель",
            }
            return labels.get(role, role)
        return dict(role_label=role_label)

    @app.context_processor
    def inject_template_helpers():
        return {
            "talon_status_label": talon_status_label,
            "format_kz": format_kz
        }

    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(contract_files_bp)

    @app.get("/")
    def home():
        return redirect(url_for("clients.list_clients"))

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

    @app.post("/admin/send-daily-report")
    @login_required
    @require_roles("director", "deputy_director")
    def admin_send_daily_report():
        ok = send_daily_report()
        return jsonify({"ok": bool(ok)})

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
        if t is None:
            return jsonify({"ok": False, "error": "token_expired"}), 401

        expires_at = t.expires_at
        if getattr(expires_at, "tzinfo", None) is not None:
            expires_at_utc = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            expires_at_utc = expires_at

        if expires_at_utc < datetime.utcnow():
            return jsonify({"ok": False, "error": "token_expired"}), 401

        sess = BotSession.query.filter_by(
            telegram_user_id=t.telegram_user_id,
            is_active=True
        ).first()

        if sess is None:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401

        talon = Talon.query.filter_by(code=code).first()

        if talon is None:
            return jsonify({"ok": False, "error": "talon_not_found"}), 404

        if talon.valid_to and talon.valid_to < kz_now().date():
            talon.state = "expired"
            db.session.commit()
            return jsonify({"ok": False, "error": "expired"}), 409

        if getattr(talon, "state", None) == "used":
            last = (
                TalonRedemption.query
                .filter_by(talon_id=talon.id)
                .order_by(TalonRedemption.used_at.desc())
                .first()
            )

            return jsonify({
                "ok": False,
                "error": "already_used",
                "used_at": last.used_at.isoformat() if last else None,
                "agzs": last.agzs.name if last and last.agzs else None
            }), 409

        talon.state = "used"
        talon.used_at = kz_now()
        talon.used_agzs_id = sess.agzs_id
        talon.used_telegram_user_id = str(sess.telegram_user_id)

        red = TalonRedemption(
            talon_id=talon.id,
            agzs_id=sess.agzs_id,
            telegram_user_id=str(sess.telegram_user_id),
            used_at=kz_now(),
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

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
