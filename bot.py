import os
import re
import asyncio
import secrets
from datetime import datetime, timedelta

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from app import create_app
from models import db, Talon, AGZS, BotSession, TalonRedemption, WebAppToken

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "").strip().rstrip("/")

LOGIN, PASSWORD, ENTER_CODE = range(3)


def _main_keyboard(scan_url: str | None = None):
    rows = [
        [KeyboardButton("⌨️ Ввести код талона")],
    ]

    # Telegram WebApp button (opens camera scanner page)
    if scan_url:
        rows.append([KeyboardButton("📷 Сканировать QR", web_app=WebAppInfo(url=scan_url))])

    rows.append([KeyboardButton("🚪 Выйти")])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _auth_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔐 Войти")]],
        resize_keyboard=True,
    )


def _only_digits(text):
    if not text:
        return None
    m = re.search(r"(\d{6,})", text)
    return m.group(1) if m else None


def _get_session(tg_id):
    return BotSession.query.filter_by(
        telegram_user_id=str(tg_id),
        is_active=True
    ).first()


def _make_scan_url(flask_app, tg_user_id: int) -> str | None:
    """Creates a short-lived token that allows the Telegram WebApp page to redeem a talon.

    NOTE: Telegram WebApp normally requires HTTPS to open inside Telegram.
    For local testing, you can still open /tg/scan in a normal browser.
    """
    if not WEBAPP_BASE_URL:
        return None

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    with flask_app.app_context():
        db.session.add(WebAppToken(
            telegram_user_id=str(tg_user_id),
            token=token,
            expires_at=expires_at,
        ))
        db.session.commit()

    return f"{WEBAPP_BASE_URL}/tg/scan?token={token}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]
    with app.app_context():
        sess = _get_session(update.effective_user.id)
    if sess:
        scan_url = _make_scan_url(app, update.effective_user.id)
        await update.message.reply_text(
            f"✅ Вы вошли: {sess.agzs.name}",
            reply_markup=_main_keyboard(scan_url)
        )
    else:
        await update.message.reply_text(
            "👋 Добро пожаловать\nНажмите 🔐 Войти",
            reply_markup=_auth_keyboard()
        )


async def login_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите логин АГЗС:")
    return LOGIN


async def login_got(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login"] = update.message.text.strip()
    await update.message.reply_text("Введите пароль:")
    return PASSWORD


async def password_got(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]
    login = context.user_data["login"]
    pwd = update.message.text.strip()

    with app.app_context():
        agzs = AGZS.query.filter_by(login=login, is_active=True).first()
        if not agzs or not agzs.check_password(pwd):
            await update.message.reply_text("❌ Неверный логин или пароль", reply_markup=_auth_keyboard())
            return ConversationHandler.END

        sess = BotSession.query.filter_by(
            telegram_user_id=str(update.effective_user.id)
        ).first()

        if not sess:
            sess = BotSession(
                telegram_user_id=str(update.effective_user.id),
                agzs_id=agzs.id,
                is_active=True
            )
            db.session.add(sess)
        else:
            sess.agzs_id = agzs.id
            sess.is_active = True

        db.session.commit()

    scan_url = _make_scan_url(app, update.effective_user.id)
    await update.message.reply_text("✅ Вход выполнен", reply_markup=_main_keyboard(scan_url))
    return ConversationHandler.END


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]
    with app.app_context():
        sess = _get_session(update.effective_user.id)
        if sess:
            sess.is_active = False
            db.session.commit()

    await update.message.reply_text("Вы вышли", reply_markup=_auth_keyboard())


async def enter_code_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите код талона:")
    return ENTER_CODE


async def enter_code_got(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = _only_digits(update.message.text)
    if not code:
        await update.message.reply_text("❌ Неверный код")
        return ENTER_CODE

    app = context.application.bot_data["flask_app"]
    with app.app_context():
        sess = _get_session(update.effective_user.id)
        if not sess:
            await update.message.reply_text("Сначала войдите")
            return ConversationHandler.END

        talon = Talon.query.filter_by(code=code).first()
        if not talon or talon.state == "used":
            await update.message.reply_text("❌ Талон недоступен")
            return ConversationHandler.END

        talon.state = "used"
        talon.used_at = datetime.utcnow()
        talon.used_agzs_id = sess.agzs_id

        db.session.add(TalonRedemption(
            talon_id=talon.id,
            agzs_id=sess.agzs_id,
            telegram_user_id=str(sess.telegram_user_id),
            used_at=datetime.utcnow(),
            source="telegram"
        ))
        db.session.commit()

    scan_url = _make_scan_url(app, update.effective_user.id)
    await update.message.reply_text("✅ Талон принят", reply_markup=_main_keyboard(scan_url))
    return ConversationHandler.END


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a WebApp scanner link (useful for debugging / when keyboard button is not available)."""
    app = context.application.bot_data["flask_app"]
    with app.app_context():
        sess = _get_session(update.effective_user.id)

    if not sess:
        await update.message.reply_text("Сначала войдите", reply_markup=_auth_keyboard())
        return

    scan_url = _make_scan_url(app, update.effective_user.id)
    if not scan_url:
        await update.message.reply_text(
            "⚠️ WEBAPP_BASE_URL не задан.\n"
            "Для локального теста откройте в браузере: http://127.0.0.1:5000/tg/scan\n"
            "А чтобы открыть сканер внутри Telegram — нужен HTTPS (например, через ngrok)."
        )
        return

    await update.message.reply_text(
        f"📷 Сканер (ссылка для копирования):\n{scan_url}",
        reply_markup=_main_keyboard(scan_url)
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")

    flask_app = create_app()

    application = Application.builder().token(BOT_TOKEN).build()
    application.bot_data["flask_app"] = flask_app

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan))

    application.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔐 Войти$"), login_begin)],
        states={
            LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_got)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_got)],
        },
        fallbacks=[CommandHandler("start", start)],
    ))

    application.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⌨️ Ввести код талона$"), enter_code_begin)],
        states={
            ENTER_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_code_got)],
        },
        fallbacks=[CommandHandler("start", start)],
    ))

    application.add_handler(MessageHandler(filters.Regex("^🚪 Выйти$"), logout))

    application.run_polling()


if __name__ == "__main__":
    main()
