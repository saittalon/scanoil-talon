import os
from datetime import datetime, timezone

from flask import (
    Flask, redirect, url_for, request, jsonify, render_template, abort, flash
)
from flask_login import LoginManager, login_required
from supabase import create_client

from config import Config
from models import (
    db,
    User, Talon, BotSession, TalonRedemption, WebAppToken,
    ContractFile
)
from auth import auth_bp
from clients import clients_bp
from reports import reports_bp
from helpers import require_roles, talon_status_label, format_kz, kz_now
from mail_utils import send_daily_report
from contract_files import contract_files_bp
from security import (
    get_csrf_token,
    validate_csrf,
    check_rate_limit_or_429,
    get_client_ip,
    log_audit,
    verify_telegram_init_data,
)


ALLOWED_SITE_USERS = {
    "Erdaulet1997": (os.getenv("DIRECTOR_PASSWORD", "123456Muraz"), "director"),
    "Gulbara2002": (os.getenv("DEPUTY_PASSWORD", "123456Muraz"), "zamdirector"),
    "Erlan2003": (os.getenv("EXECUTOR_PASSWORD", "123456Muraz"), "executor"),
}


def _ensure_only_allowed_users():
    allowed_usernames = set(ALLOWED_SITE_USERS.keys())
    changed = False

    for username, (password, role) in ALLOWED_SITE_USERS.items():
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
            changed = True
        else:
            if user.role != role:
                user.role = role
                changed = True
            if password:
                user.set_password(password)
                changed = True

    db.session.flush()

    extra_users = User.query.filter(~User.username.in_(allowed_usernames)).all()
    if extra_users:
        extra_ids = [u.id for u in extra_users]

        Talon.query.filter(Talon.used_by_user_id.in_(extra_ids)).update(
            {Talon.used_by_user_id: None},
            synchronize_session=False
        )

        ContractFile.query.filter(ContractFile.uploaded_by_user_id.in_(extra_ids)).update(
            {ContractFile.uploaded_by_user_id: None},
            synchronize_session=False
        )

        ContractFile.query.filter(ContractFile.approved_by_user_id.in_(extra_ids)).update(
            {ContractFile.approved_by_user_id: None},
            synchronize_session=False
        )

        for user in extra_users:
            db.session.delete(user)

        changed = True

    if changed:
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _ensure_only_allowed_users()

    login_manager = LoginManager()
    login_manager.login_view = "auth.login_get"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.before_request
    def protect_requests():
        validate_csrf()
        if request.path == '/tg/api/scan':
            key = f"{get_client_ip()}:{(request.headers.get('User-Agent') or '')[:60]}"
            limited = check_rate_limit_or_429(
                category='tg_scan',
                key=key,
                limit=25,
                window_seconds=60,
                message='Слишком много запросов на сканирование. Подождите минуту.',
            )
            if limited:
                return limited

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
            "format_kz": format_kz,
            "csrf_token": get_csrf_token,
        }

    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(contract_files_bp)

    @app.get("/")
    def home():
        return redirect(url_for("clients.list_clients"))

    @app.errorhandler(400)
    def bad_request(err):
        if request.path.startswith('/tg/api/'):
            return jsonify({"ok": False, "error": "bad_request", "message": getattr(err, 'description', 'bad_request')}), 400
        flash(getattr(err, 'description', 'Некорректный запрос.'), 'danger')
        return redirect(request.referrer or url_for('clients.list_clients'))

    @app.errorhandler(413)
    def file_too_large(_err):
        flash('Файл слишком большой. Разрешено не более 10 МБ.', 'danger')
        return redirect(request.referrer or url_for('clients.list_clients'))

    @app.errorhandler(429)
    def too_many_requests(err):
        if request.path.startswith('/tg/api/'):
            return jsonify({"ok": False, "error": "rate_limited", "message": getattr(err, 'description', 'Слишком много запросов.')}), 429
        flash(getattr(err, 'description', 'Слишком много запросов. Подождите немного.'), 'danger')
        return redirect(request.referrer or url_for('clients.list_clients'))

    @app.errorhandler(500)
    def internal_error(_err):
        db.session.rollback()
        if request.path.startswith('/tg/api/'):
            return jsonify({"ok": False, "error": "server_error"}), 500
        flash('Внутренняя ошибка сервера. Попробуйте ещё раз.', 'danger')
        return redirect(request.referrer or url_for('clients.list_clients'))

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
    @require_roles("director", "zamdirector", "deputy_director")
    def admin_send_daily_report():
        ok = send_daily_report()
        log_audit('send_daily_report', f'Ручная отправка отчёта: {bool(ok)}')
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
        init_data = (data.get("initData") or "").strip()

        if not token or not code:
            return jsonify({"ok": False, "error": "missing_token_or_code"}), 400

        ok, reason, tg_user = verify_telegram_init_data(init_data, os.getenv('BOT_TOKEN', '').strip())
        if not ok:
            return jsonify({"ok": False, "error": "invalid_telegram_context", "reason": reason}), 401

        t = WebAppToken.query.filter_by(token=token).first()
        if t is None:
            return jsonify({"ok": False, "error": "token_expired"}), 401

        expires_at = t.expires_at
        if expires_at is None:
            return jsonify({"ok": False, "error": "token_expired"}), 401

        if getattr(expires_at, "tzinfo", None) is not None:
            now_utc = datetime.now(timezone.utc)
            if expires_at.astimezone(timezone.utc) < now_utc:
                return jsonify({"ok": False, "error": "token_expired"}), 401
        else:
            now_naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if expires_at < now_naive_utc:
                return jsonify({"ok": False, "error": "token_expired"}), 401

        tg_user_id = str((tg_user or {}).get('id') or '')
        if not tg_user_id or tg_user_id != str(t.telegram_user_id):
            return jsonify({"ok": False, "error": "telegram_user_mismatch"}), 401

        sess = BotSession.query.filter_by(
            telegram_user_id=t.telegram_user_id,
            is_active=True
        ).first()

        if sess is None:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401

        talon = Talon.query.filter_by(code=code).with_for_update().first()
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
                "used_at": last.used_at.isoformat() if last and last.used_at else None,
                "agzs": last.agzs.name if last and last.agzs else None,
            }), 409

        if getattr(talon, 'state', None) == 'blocked' or (talon.valid_from and talon.valid_from > kz_now().date()):
            return jsonify({"ok": False, "error": "not_active"}), 409

        talon.state = "used"
        talon.used_at = kz_now()
        talon.used_agzs_id = sess.agzs_id
        talon.used_telegram_user_id = str(sess.telegram_user_id)

        red = TalonRedemption(
            talon_id=talon.id,
            agzs_id=sess.agzs_id,
            telegram_user_id=str(sess.telegram_user_id),
            used_at=kz_now(),
            source="telegram_webapp",
        )

        db.session.add(red)
        log_audit('telegram_scan_success', f'Талон {talon.serial_number} использован через Telegram WebApp', 'talon', talon.id)
        db.session.commit()

        return jsonify({
            "ok": True,
            "liters": getattr(talon, "liters", None),
            "product": getattr(talon, "product_name", None),
            "serial": getattr(talon, "serial_number", None),
            "valid_from": str(getattr(talon, "valid_from", "")),
            "valid_to": str(getattr(talon, "valid_to", "")),
            "agzs": sess.agzs.name if sess.agzs else None,
        })

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
