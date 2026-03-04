from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="operator")  # admin / operator

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)        # "Название в системе"
    full_name = db.Column(db.String(300), nullable=True)    # "Полное название"

    bin = db.Column(db.String(30), nullable=True)
    kpp = db.Column(db.String(30), nullable=True)
    ogrn = db.Column(db.String(30), nullable=True)
    okpo = db.Column(db.String(30), nullable=True)

    legal_address = db.Column(db.String(400), nullable=True)
    fact_address = db.Column(db.String(400), nullable=True)
    post_address = db.Column(db.String(400), nullable=True)

    phone = db.Column(db.String(80), nullable=True)
    email = db.Column(db.String(120), nullable=True)

    comment = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Contract(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    client = db.relationship("Client", backref=db.backref("contracts", lazy=True))

    number = db.Column(db.String(120), nullable=False)
    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date, nullable=True)

    tariff_name = db.Column(db.String(200), nullable=True)
    price_per_liter = db.Column(db.Float, nullable=True)
    online = db.Column(db.Boolean, default=False)

    allow_all_stations = db.Column(db.Boolean, default=True)
    forbidden_groups = db.Column(db.String(500), nullable=True)

    # (Можно оставить для совместимости, но дальше лучше использовать contract_files)
    contract_pdf_path = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ✅ Список файлов договора (договор/допники/вложения)
    files = db.relationship(
        "ContractFile",
        backref="contract",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ContractFile.id.desc()"
    )


class ContractFile(db.Model):
    """
    Универсальные файлы для договора:
    - kind = 'contract' (сам договор)
    - kind = 'addon' (доп соглашение)
    - kind = 'attachment' (прочие вложения)
    """
    __tablename__ = "contract_files"

    id = db.Column(db.Integer, primary_key=True)

    contract_id = db.Column(db.Integer, db.ForeignKey("contract.id", ondelete="CASCADE"), nullable=False)

    kind = db.Column(db.String(20), nullable=False, default="attachment")
    title = db.Column(db.String(300), nullable=True)

    storage_path = db.Column(db.String(700), nullable=False)   # путь в Supabase Storage
    original_name = db.Column(db.String(300), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ContractAddendum(db.Model):
    """
    Оставляем как "учёт доп. соглашения" (номер/даты/литры).
    PDF для допника будем хранить в ContractFile(kind='addon'),
    поэтому pdf_path можно оставить, но лучше больше не использовать.
    """
    __tablename__ = "contract_addendum"

    id = db.Column(db.Integer, primary_key=True)

    contract_id = db.Column(db.Integer, db.ForeignKey("contract.id"), nullable=False)
    contract = db.relationship(
        "Contract",
        backref=db.backref("addendums", lazy=True, order_by="ContractAddendum.id.desc()")
    )

    number = db.Column(db.String(120), nullable=False)  # "Доп.соглашение №1"
    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date, nullable=True)

    liters_total = db.Column(db.Float, default=0.0)
    comment = db.Column(db.String(500), nullable=True)

    # оставляем, чтобы не ломать старое, но лучше использовать ContractFile
    pdf_path = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Balance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    client = db.relationship("Client", backref=db.backref("balances", lazy=True))

    contract_id = db.Column(db.Integer, db.ForeignKey("contract.id"), nullable=True)
    contract = db.relationship("Contract", backref=db.backref("balances", lazy=True))

    product_name = db.Column(db.String(50), default="ГАЗ")
    liters_left = db.Column(db.Float, default=0.0)
    balance_control = db.Column(db.Boolean, default=True)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Talon(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    client = db.relationship("Client", backref=db.backref("talons", lazy=True))

    contract_id = db.Column(db.Integer, db.ForeignKey("contract.id"), nullable=True)
    contract = db.relationship("Contract", backref=db.backref("talons", lazy=True))

    holder_name = db.Column(db.String(200), nullable=False)
    product_name = db.Column(db.String(50), default="ГАЗ")
    liters = db.Column(db.Float, nullable=False)

    serial_number = db.Column(db.String(20), nullable=False)
    code = db.Column(db.String(60), nullable=False)

    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date, nullable=False)

    state = db.Column(db.String(20), default="active")  # active/blocked/used
    used_at = db.Column(db.DateTime, nullable=True)
    used_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    used_by = db.relationship("User", foreign_keys=[used_by_user_id])

    used_agzs_id = db.Column(db.Integer, db.ForeignKey("agzs.id"), nullable=True)
    used_agzs = db.relationship("AGZS", foreign_keys=[used_agzs_id])
    used_telegram_user_id = db.Column(db.String(50), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AGZS(db.Model):
    __tablename__ = "agzs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    login = db.Column(db.String(200), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class WebAppToken(db.Model):
    __tablename__ = "webapp_tokens"

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), nullable=False, index=True)
    token = db.Column(db.String(80), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BotSession(db.Model):
    __tablename__ = "bot_sessions"

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), nullable=False, unique=True)
    agzs_id = db.Column(db.Integer, db.ForeignKey("agzs.id"), nullable=False)
    agzs = db.relationship("AGZS")
    logged_in_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)


class TalonRedemption(db.Model):
    __tablename__ = "talon_redemptions"

    id = db.Column(db.Integer, primary_key=True)
    talon_id = db.Column(db.Integer, db.ForeignKey("talon.id"), nullable=False)
    talon = db.relationship("Talon")
    agzs_id = db.Column(db.Integer, db.ForeignKey("agzs.id"), nullable=False)
    agzs = db.relationship("AGZS")
    telegram_user_id = db.Column(db.String(50), nullable=True)
    used_at = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(20), default="telegram")
