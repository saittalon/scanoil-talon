import os
from datetime import datetime, timezone

from sqlalchemy import text

from flask import (
    Flask, redirect, url_for, request, jsonify, render_template, abort, flash, send_file, g
)
from flask_login import LoginManager, login_required
from supabase import create_client

from config import Config
from backup_utils import build_backup_zip, upload_backup_bytes_to_supabase
from models import (
    db,
    User, Talon, BotSession, TalonRedemption, WebAppToken,
    ContractFile
)
from auth import auth_bp
from clients import clients_bp
from reports import reports_bp
from helpers import require_roles, talon_status_label, format_kz, kz_now, redeem_talon_atomic
from models import AuditLog
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



def _ensure_performance_indexes():
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_talon_client_state_valid_to ON talon (client_id, state, valid_to)",
        "CREATE INDEX IF NOT EXISTS ix_talon_contract_state_created ON talon (contract_id, state, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_talon_code_unique_lookup ON talon (code)",
        "CREATE INDEX IF NOT EXISTS ix_talon_created_at ON talon (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_talon_valid_from_to ON talon (valid_from, valid_to)",
        "CREATE INDEX IF NOT EXISTS ix_balance_client_contract_product ON balance (client_id, contract_id, product_name)",
        "CREATE INDEX IF NOT EXISTS ix_contract_client_number ON contract (client_id, number)",
        "CREATE INDEX IF NOT EXISTS ix_rate_limit_category_key_created ON rate_limit_events (category, key, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_redemption_talon_used_at ON talon_redemptions (talon_id, used_at)",
    ]
    for stmt in statements:
        try:
            db.session.execute(text(stmt))
        except Exception:
            db.session.rollback()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()



import logging
from logging.handlers import RotatingFileHandler


def _configure_logging(app):
    os.makedirs(os.path.dirname(app.config.get("APP_LOG_FILE", "logs/app.log")), exist_ok=True)
    if app.logger.handlers:
        for h in list(app.logger.handlers):
            app.logger.removeHandler(h)
    file_handler = RotatingFileHandler(app.config.get("APP_LOG_FILE", "logs/app.log"), maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8')
    fmt = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.addHandler(stream_handler)
    app.logger.propagate = False

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
    _configure_logging(app)

    with app.app_context():
        db.create_all()
        _ensure_performance_indexes()
        _ensure_only_allowed_users()

    login_manager = LoginManager()
    login_manager.login_view = "auth.login_get"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.before_request
    def protect_requests():
        g.request_started_at = datetime.utcnow()
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

    @app.after_request
    def write_access_log(response):
        started = getattr(g, 'request_started_at', None)
        duration_ms = 0
        if started:
            duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
        log_line = '%s %s -> %s in %sms ip=%s'
        if response.status_code >= 400:
            app.logger.warning(log_line, request.method, request.path, response.status_code, duration_ms, get_client_ip())
        else:
            app.logger.info(log_line, request.method, request.path, response.status_code, duration_ms, get_client_ip())
        return response

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
            "cloud_backup_enabled": app.config.get('BACKUP_UPLOAD_TO_SUPABASE', True),
            "cloud_backup_bucket": app.config.get('BACKUP_SUPABASE_BUCKET', 'backups'),
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
        max_mb = max(1, int(app.config.get('MAX_CONTENT_LENGTH', 0) / (1024 * 1024)))
        flash(f'Файл слишком большой. Разрешено не более {max_mb} МБ.', 'danger')
        return redirect(request.referrer or url_for('clients.list_clients'))

    @app.errorhandler(429)
    def too_many_requests(err):
        if request.path.startswith('/tg/api/'):
            return jsonify({"ok": False, "error": "rate_limited", "message": getattr(err, 'description', 'Слишком много запросов.')}), 429
        flash(getattr(err, 'description', 'Слишком много запросов. Подождите немного.'), 'danger')
        return redirect(request.referrer or url_for('clients.list_clients'))

    @app.errorhandler(500)
    def internal_error(err):
        app.logger.exception('Unhandled server error on %s %s', request.method, request.path)
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


    @app.get("/admin/audit-logs")
    @login_required
    @require_roles("director", "zamdirector", "deputy_director")
    def admin_audit_logs():
        q = AuditLog.query.order_by(AuditLog.created_at.desc())
        username = (request.args.get('username') or '').strip()
        action = (request.args.get('action') or '').strip()
        search = (request.args.get('q') or '').strip()
        if username:
            q = q.filter(AuditLog.username.ilike(f'%{username}%'))
        if action:
            q = q.filter(AuditLog.action.ilike(f'%{action}%'))
        if search:
            q = q.filter(AuditLog.message.ilike(f'%{search}%'))
        logs = q.limit(500).all()
        return render_template('admin_audit_logs.html', logs=logs)

    @app.get("/admin/backup/download")
    @login_required
    @require_roles("director", "zamdirector", "deputy_director")
    def admin_backup_download():
        bundle, filename, manifest = build_backup_zip(include_files=app.config.get('BACKUP_INCLUDE_FILES', True))
        log_audit('backup_download', f'Скачан backup. Таблиц: {len(manifest.get("tables", {}))}, файлов: {manifest.get("files_exported", 0)}')
        db.session.commit()
        return send_file(bundle, as_attachment=True, download_name=filename, mimetype='application/zip')

    @app.post("/admin/backup/upload-cloud")
    @login_required
    @require_roles("director", "zamdirector", "deputy_director")
    def admin_backup_upload_cloud():
        bundle, filename, manifest = build_backup_zip(include_files=app.config.get('BACKUP_INCLUDE_FILES', True))
        result = upload_backup_bytes_to_supabase(
            bundle_bytes=bundle.getvalue(),
            filename=filename,
            bucket=app.config.get('BACKUP_SUPABASE_BUCKET', 'backups'),
            base_path=app.config.get('BACKUP_SUPABASE_PATH', 'auto'),
            keep_last=app.config.get('BACKUP_KEEP_LAST', 30),
        )
        log_audit('backup_cloud_upload_manual', f'Backup вручную загружен в облако: {result["bucket"]}/{result["key"]}. Таблиц: {len(manifest.get("tables", {}))}, файлов: {manifest.get("files_exported", 0)}')
        db.session.commit()
        flash(f'Бэкап загружен в облако: {result["bucket"]}/{result["key"]}', 'success')
        return redirect(url_for('admin_audit_logs'))

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

        used_time = kz_now()
        redeem_status, talon = redeem_talon_atomic(
            code=code,
            used_at=used_time,
            agzs_id=sess.agzs_id,
            telegram_user_id=str(sess.telegram_user_id),
        )
        if redeem_status == "not_found":
            return jsonify({"ok": False, "error": "talon_not_found"}), 404

        if redeem_status == "expired":
            return jsonify({"ok": False, "error": "expired"}), 409

        if redeem_status == "already_used":
            last = (
                TalonRedemption.query
                .filter_by(talon_id=talon.id)
                .order_by(TalonRedemption.used_at.desc())
                .first()
            )
            return jsonify({
                "ok": False,
                "error": "already_used",
                "used_at": last.used_at.isoformat() if last and last.used_at else talon.used_at.isoformat() if talon.used_at else None,
                "agzs": last.agzs.name if last and last.agzs else talon.used_agzs.name if talon.used_agzs else None,
            }), 409

        if redeem_status != "redeemed":
            return jsonify({"ok": False, "error": "not_active"}), 409

        red = TalonRedemption(
            talon_id=talon.id,
            agzs_id=sess.agzs_id,
            telegram_user_id=str(sess.telegram_user_id),
            used_at=used_time,
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
