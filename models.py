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

    # Важно: у тебя таблица Client по умолчанию будет "client"
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

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ContractFile(db.Model):
    """
    Таблица файлов договора (как "папка" в 1С):
    - kind='contract'   -> основной договор (обычно 1 PDF)
    - kind='addendum'   -> доп. соглашения (PDF, много)
    - kind='attachment' -> прочие вложения (если нужно)
    """
    __tablename__ = "contract_files"

    id = db.Column(db.Integer, primary_key=True)

    contract_id = db.Column(db.Integer, db.ForeignKey("contract.id"), nullable=False, index=True)
    contract = db.relationship(
        "Contract",
        backref=db.backref("files", lazy=True, order_by="ContractFile.id.desc()")
    )

    kind = db.Column(db.String(50), nullable=False, default="attachment")  # contract / addendum / attachment
    title = db.Column(db.String(300), nullable=True)

    # куда сохранил на сервере, например: uploads/contracts/12/xxxx.pdf
    storage_path = db.Column(db.String(500), nullable=False)

    # оригинальное имя файла из загрузки
    original_name = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    bucket = db.Column(db.String(100), nullable=False, default="contracts")
    storage_key = db.Column(db.String(500), nullable=True)


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
        return check_password_hash(password, self.password_hash)


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
