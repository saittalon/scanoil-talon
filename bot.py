import os
import re
import secrets
from datetime import timedelta
from io import BytesIO

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app import create_app
from models import db, Talon, AGZS, BotSession, TalonRedemption, WebAppToken, Shift
from helpers import kz_now, to_kz, redeem_talon_atomic

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "").strip().rstrip("/")

LOGIN, PASSWORD = range(2)


def _auth_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔐 Войти")]],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def _shift_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🟢 ОТКРЫТЬ СМЕНУ")],
            [KeyboardButton("🔴 ЗАКРЫТЬ СМЕНУ")],
            [KeyboardButton("🚪 ВЫЙТИ")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def _main_keyboard(scan_url: str | None = None):
    rows = []

    if scan_url:
        rows.append([KeyboardButton("📷 СКАНИРОВАТЬ", web_app=WebAppInfo(url=scan_url))])
    else:
        rows.append([KeyboardButton("📷 СКАНИРОВАТЬ")])

    rows.append([KeyboardButton("📋 МЕНЮ")])
    rows.append([KeyboardButton("🔴 ЗАКРЫТЬ СМЕНУ")])
    rows.append([KeyboardButton("🚪 ВЫЙТИ")])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
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


def _get_open_shift_for_agzs(agzs_id: int):
    return Shift.query.filter_by(
        agzs_id=agzs_id,
        is_closed=False
    ).first()


def _talon_price(talon: Talon) -> float:
    contract = getattr(talon, "contract", None)
    if contract and getattr(contract, "price_per_liter", None) is not None:
        return float(contract.price_per_liter or 0)
    return 0.0


def _format_money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _make_scan_url(flask_app, tg_user_id: int) -> str | None:
    if not WEBAPP_BASE_URL:
        return None

    token = secrets.token_urlsafe(32)
    expires_at = kz_now() + timedelta(minutes=10)

    with flask_app.app_context():
        db.session.add(WebAppToken(
            telegram_user_id=str(tg_user_id),
            token=token,
            expires_at=expires_at,
        ))
        db.session.commit()

    return f"{WEBAPP_BASE_URL}/tg/scan?token={token}"


def _build_day_report(flask_app, tg_user_id: int):
    with flask_app.app_context():
        sess = _get_session(tg_user_id)
        if not sess:
            return None

        agzs = sess.agzs
        today = kz_now().date()

        redemptions = (
            TalonRedemption.query
            .filter_by(agzs_id=sess.agzs_id)
            .order_by(TalonRedemption.used_at.asc())
            .all()
        )

        items = []
        total_liters = 0.0
        total_amount = 0.0

        for red in redemptions:
            used_local = to_kz(red.used_at)
            if not used_local or used_local.date() != today:
                continue

            talon = red.talon
            if not talon:
                continue

            liters = float(talon.liters or 0)
            amount = liters * _talon_price(talon)

            total_liters += liters
            total_amount += amount

            items.append({
                "serial": talon.serial_number or "без номера",
                "code": talon.code or "—",
                "liters": liters,
                "amount": amount,
                "time": used_local.strftime("%H:%M"),
            })

        return {
            "date": today.strftime("%d.%m.%Y"),
            "agzs_name": agzs.name if agzs else "АГЗС",
            "count": len(items),
            "total_liters": total_liters,
            "total_amount": total_amount,
            "items": items,
        }


def _build_report_text(report: dict) -> str:
    lines = [
        f"📊 Отчет за {report['date']}",
        f"АГЗС: {report['agzs_name']}",
        f"Использовано талонов: {report['count']}",
        f"Всего литров: {report['total_liters']:.2f} л",
        f"Общая сумма: {_format_money(report['total_amount'])} ₸",
        "",
    ]

    if report["items"]:
        lines.append("Талоны:")
        for i, item in enumerate(report["items"], start=1):
            lines.append(
                f"{i}. №{item['serial']} | код {item['code']} | "
                f"{item['liters']:.2f} л | {_format_money(item['amount'])} ₸ | {item['time']}"
            )
    else:
        lines.append("Сегодня талонов не использовали")

    return "\n".join(lines)


def _build_report_pdf(report: dict) -> BytesIO:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4

    font_path = os.path.join("static", "fonts", "DejaVuSans.ttf")
    bold_path = os.path.join("static", "fonts", "DejaVuSans-Bold.ttf")

    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
        base_font = "DejaVuSans"
    else:
        base_font = "Helvetica"

    if os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold_path))
        bold_font = "DejaVuSans-Bold"
    else:
        bold_font = "Helvetica-Bold"

    y = height - 50

    pdf.setFont(bold_font, 14)
    pdf.drawString(40, y, f"Отчет по смене за {report['date']}")
    y -= 25

    pdf.setFont(base_font, 11)
    pdf.drawString(40, y, f"АГЗС: {report['agzs_name']}")
    y -= 18
    pdf.drawString(40, y, f"Использовано талонов: {report['count']}")
    y -= 18
    pdf.drawString(40, y, f"Всего литров: {report['total_liters']:.2f} л")
    y -= 18
    pdf.drawString(40, y, f"Общая сумма: {_format_money(report['total_amount'])} ₸")
    y -= 28

    pdf.setFont(bold_font, 11)
    pdf.drawString(40, y, "Список талонов")
    y -= 20

    pdf.setFont(base_font, 10)

    if not report["items"]:
        pdf.drawString(40, y, "Сегодня талонов не использовали")
    else:
        for i, item in enumerate(report["items"], start=1):
            line = (
                f"{i}. №{item['serial']} | код {item['code']} | "
                f"{item['liters']:.2f} л | {_format_money(item['amount'])} ₸ | {item['time']}"
            )
            pdf.drawString(40, y, line)
            y -= 16

            if y < 50:
                pdf.showPage()
                pdf.setFont(base_font, 10)
                y = height - 50

    pdf.save()
    buffer.seek(0)
    return buffer


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]

    with app.app_context():
        sess = _get_session(update.effective_user.id)

        if not sess:
            is_logged_in = False
            agzs_name = None
            has_open_shift = False
        else:
            is_logged_in = True
            agzs_name = sess.agzs.name if sess.agzs else "АГЗС"
            has_open_shift = _get_open_shift_for_agzs(sess.agzs_id) is not None

    if not is_logged_in:
        await update.message.reply_text(
            "👋 Добро пожаловать",
            reply_markup=_auth_keyboard()
        )
        return

    if has_open_shift:
        scan_url = _make_scan_url(app, update.effective_user.id)
        await update.message.reply_text(
            f"✅ Вы вошли: {agzs_name}",
            reply_markup=_main_keyboard(scan_url)
        )
    else:
        await update.message.reply_text(
            f"✅ Вы вошли: {agzs_name}\nОткройте смену.",
            reply_markup=_shift_keyboard()
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
            await update.message.reply_text(
                "❌ Неверный логин или пароль",
                reply_markup=_auth_keyboard()
            )
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

    await update.message.reply_text(
        "✅ Вход выполнен",
        reply_markup=_shift_keyboard()
    )
    return ConversationHandler.END


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]

    with app.app_context():
        sess = _get_session(update.effective_user.id)
        if sess:
            sess.is_active = False
            db.session.commit()

    await update.message.reply_text("Вы вышли", reply_markup=_auth_keyboard())



async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]

    with app.app_context():
        sess = _get_session(update.effective_user.id)

        if not sess:
            has_session = False
            has_open_shift = False
        else:
            has_session = True
            has_open_shift = _get_open_shift_for_agzs(sess.agzs_id) is not None

    if not has_session:
        await update.message.reply_text("Сначала войдите", reply_markup=_auth_keyboard())
        return

    if not has_open_shift:
        await update.message.reply_text("Сначала откройте смену", reply_markup=_shift_keyboard())
        return

    scan_url = _make_scan_url(app, update.effective_user.id)
    if not scan_url:
        await update.message.reply_text("⚠️ Не задан WEBAPP_BASE_URL")
        return

    await update.message.reply_text(
        f"📷 Сканер:\n{scan_url}",
        reply_markup=_main_keyboard(scan_url)
    )

async def open_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]

    with app.app_context():

        sess = _get_session(update.effective_user.id)

        if not sess:
            await update.message.reply_text("Сначала войдите.")
            return

        agzs_name = sess.agzs.name

        shift = Shift.query.filter_by(
            agzs_id=sess.agzs_id,
            is_closed=False
        ).first()

        if shift:
            already_open = True
        else:
            new_shift = Shift(
                agzs_id=sess.agzs_id,
                opened_at=kz_now(),
                is_closed=False
            )

            db.session.add(new_shift)
            db.session.commit()

            already_open = False

    scan_url = _make_scan_url(app, update.effective_user.id)

    if already_open:
        await update.message.reply_text(
            f"⚠️ Смена уже открыта: {agzs_name}",
            reply_markup=_main_keyboard(scan_url)
        )
    else:
        await update.message.reply_text(
            f"🟢 Смена открыта: {agzs_name}",
            reply_markup=_main_keyboard(scan_url)
        )



async def close_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]

    with app.app_context():
        sess = _get_session(update.effective_user.id)

        if not sess:
            await update.message.reply_text("Сначала войдите.")
            return

        shift = _get_open_shift_for_agzs(sess.agzs_id)

        if not shift:
            await update.message.reply_text("Нет открытой смены.", reply_markup=_shift_keyboard())
            return

        shift_opened_at = shift.opened_at
        shift_closed_at = kz_now()

        shift.closed_at = shift_closed_at
        shift.is_closed = True

        redemptions = (
            TalonRedemption.query
            .filter(
                TalonRedemption.agzs_id == sess.agzs_id,
                TalonRedemption.used_at >= shift_opened_at,
                TalonRedemption.used_at <= shift_closed_at
            )
            .order_by(TalonRedemption.used_at.asc())
            .all()
        )

        total_liters = 0.0
        talon_lines = []

        for i, r in enumerate(redemptions, start=1):
            talon = r.talon
            if not talon:
                continue

            liters = float(talon.liters or 0)
            total_liters += liters
            time_str = to_kz(r.used_at).strftime("%H:%M") if r.used_at else "--:--"

            talon_lines.append(
                f"{i}. №{talon.serial_number or 'без номера'} | код {talon.code or '—'} | {liters:.2f} л | {time_str}"
            )

        shift.total_talons = len(redemptions)
        shift.total_liters = total_liters

        report = (
            f"📊 Отчет по смене\n"
            f"АГЗС: {sess.agzs.name}\n"
            f"Использовано талонов: {len(redemptions)}\n"
            f"Всего литров: {total_liters:.2f} л\n\n"
        )

        if talon_lines:
            report += "Талоны:\n" + "\n".join(talon_lines)
        else:
            report += f"Общая сумма: {_format_money(0)} ₸\n\nСегодня талоны не использовали"

        shift.report_text = report
        db.session.commit()

    await update.message.reply_text(report)
    await update.message.reply_text("🔴 Смена закрыта", reply_markup=_shift_keyboard())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.bot_data["flask_app"]
    tg_user_id = update.effective_user.id

    with app.app_context():
        sess = _get_session(tg_user_id)

        if not sess:
            has_session = False
            has_open_shift = False
        else:
            has_session = True
            has_open_shift = _get_open_shift_for_agzs(sess.agzs_id) is not None

    if not has_session:
        await update.message.reply_text("Сначала войдите", reply_markup=_auth_keyboard())
        return

    if not has_open_shift:
        await update.message.reply_text("Сначала откройте смену", reply_markup=_shift_keyboard())
        return

    scan_url = _make_scan_url(app, tg_user_id)
    await update.message.reply_text("📋 Меню открыто", reply_markup=_main_keyboard(scan_url))


async def scan_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await scan(update, context)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")

    flask_app = create_app()

    application = Application.builder().token(BOT_TOKEN).build()
    application.bot_data["flask_app"] = flask_app

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan))

    application.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^🔐 Войти$"), login_begin)],
        states={
            LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_got)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_got)],
        },
        fallbacks=[CommandHandler("start", start)],
    ))


    application.add_handler(MessageHandler(filters.Regex(r"^🟢 ОТКРЫТЬ СМЕНУ$"), open_shift))
    application.add_handler(MessageHandler(filters.Regex(r"^📋 МЕНЮ$"), show_menu))
    application.add_handler(MessageHandler(filters.Regex(r"^🔴 ЗАКРЫТЬ СМЕНУ$"), close_shift))
    application.add_handler(MessageHandler(filters.Regex(r"^📷 СКАНИРОВАТЬ$"), scan_button))
    application.add_handler(MessageHandler(filters.Regex(r"^🚪 ВЫЙТИ$"), logout))

    application.run_polling()


if __name__ == "__main__":
    app = create_app()

    with app.app_context():
        db.create_all()

    main()
