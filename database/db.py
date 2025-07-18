from flask_sqlalchemy import SQLAlchemy
from dataclasses import dataclass, field
from sqlalchemy import Column, Integer, String, DateTime, func, ForeignKey
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship
from sqlalchemy_utils import EmailType
from datetime import datetime

import pytz
db = SQLAlchemy()


ZONA_SANTIAGO = pytz.timezone('America/Santiago')


def now_santiago():
    # devuelve un datetime con tzinfo=America/Santiago
    return datetime.now(ZONA_SANTIAGO)


def init_db(app):
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mydb.db'
    db.init_app(app)


@dataclass
class Users(db.Model):
    id: int = field(init=False)
    username: str
    password: str
    email: str
    name: str
    is_Admin: bool
    theme: str

    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    name = Column(String(120), unique=True, nullable=False)
    email = Column(EmailType(), unique=False, nullable=False)
    _password = db.Column('password', db.String(128), nullable=False)
    is_Admin = Column(db.Boolean, default=False, nullable=False)
    theme = Column(String(10), nullable=False, default='dark')

    @property
    def password(self):
        raise AttributeError("El password no es legible")

    @password.setter
    def password(self, raw_plaintext):
        # bcrypt por defecto; salt y cost incorporados
        self._password = generate_password_hash(raw_plaintext)

    def check_password(self, raw_plaintext) -> bool:
        return check_password_hash(self._password, raw_plaintext)

    def __repr__(self):
        return f"<User {self.username!r}, is_Admin={self.is_Admin}>"


@dataclass
class Product(db.Model):
    id:    int = field(init=False)
    name:  str
    brand: str
    stock: int

    __tablename__ = 'products'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, index=True)
    brand = Column(String(80),  nullable=False, index=True)
    stock = Column(Integer,     default=0, nullable=False)

    entries = relationship('InventoryEntry', back_populates='product')
    dispatches = relationship(
        'DispatchEntry',
        back_populates='product',
        lazy=True
    )

    def __repr__(self):
        return (
            f"<Product id={self.id!r} "
            f"name={self.name!r} brand={self.brand!r} "
            f"stock={self.stock!r}>"
        )


dataclass


class Client(db.Model):
    id:   int = field(init=False)
    name: str

    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False, index=True)

    orders = relationship('PurchaseOrder', back_populates='client', lazy=True)
    batches = relationship('DispatchBatch', back_populates='client')

    def __repr__(self):
        return f"<Client id={self.id!r} name={self.name!r}>"


@dataclass
class DispatchBatch(db.Model):
    id:         int = field(init=False)
    client_id:  int
    user_id:    int
    created_at: datetime = field(init=False)
    order_number = Column(String(50), nullable=True)

    __tablename__ = 'dispatch_batches'
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'),   nullable=False)
    created_at = Column(DateTime, default=now_santiago,    nullable=False)

    client = relationship('Client', back_populates='batches')
    user = relationship('Users', backref='dispatch_batches')
    entries = relationship('DispatchEntry', back_populates='batch', lazy=True)

    def __repr__(self):
        return f"<DispatchBatch id={self.id} client={self.client.name} at={self.created_at}>"


@dataclass
class DispatchEntry(db.Model):
    id:         int = field(init=False)
    batch_id:   int
    product_id: int
    quantity:   int
    order_number: str = field(default=None)

    __tablename__ = 'dispatch_entries'
    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey(
        'dispatch_batches.id'), nullable=False)
    product_id = Column(Integer, ForeignKey(
        'products.id'),         nullable=False)
    quantity = Column(Integer, nullable=False)
    order_number = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True),
                        default=now_santiago, nullable=False)

    batch = relationship('DispatchBatch', back_populates='entries')
    product = relationship('Product',       back_populates='dispatches')

    def __repr__(self):
        return f"<DispatchEntry id={self.id} prod={self.product.name} qty={self.quantity}>"


@dataclass
class IngresoBatch(db.Model):
    id:         int = field(init=False)
    user_id:    int
    created_at: datetime = field(init=False)

    __tablename__ = 'ingreso_batches'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=now_santiago,  # aquí usamos pytznullable
        nullable=False)
    user = relationship('Users', backref='ingreso_batches')
    entries = relationship('InventoryEntry', back_populates='batch', lazy=True)

    def __repr__(self):
        return f"<IngresoBatch id={self.id} by={self.user.username} on={self.created_at}>"


@dataclass
class InventoryEntry(db.Model):
    id:         int = field(init=False)
    ingreso_id: int
    product_id: int
    quantity:   int

    __tablename__ = 'inventory_entries'
    id = Column(Integer, primary_key=True)
    ingreso_id = Column(Integer,
                        ForeignKey('ingreso_batches.id'
                                   ), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, nullable=False)

    batch = relationship('IngresoBatch', back_populates='entries')
    product = relationship('Product', back_populates='entries')

    def __repr__(self):
        return f"<Entry id={self.id} prod={self.product.name} qty={self.quantity}>"


@dataclass
class Log(db.Model):
    id:           int = field(init=False)
    user_id:      int
    action:       str
    target_table: str
    target_id:    int
    details:     str = field(default=None)
    created_at:   datetime = field(init=False)

    __tablename__ = 'logs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String(50), nullable=False)
    target_table = Column(String(50), nullable=True)
    target_id = Column(Integer, nullable=True)
    details = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=now_santiago,
        nullable=False
    )

    # relación con el usuario que ejecutó la acción
    user = relationship('Users', backref='logs', lazy=True)

    def __repr__(self):
        return (
            f"<Log id={self.id!r} user={self.user.username!r} "
            f"action={self.action!r} target={
                self.target_table}({self.target_id}) "
            f"at={self.created_at}>"
        )


@dataclass
class PurchaseOrder(db.Model):
    id:          int = field(init=False)
    number:      str
    client_id:   int
    created_at:  datetime = field(init=False)

    __tablename__ = 'purchase_orders'
    id = Column(Integer, primary_key=True)
    number = Column(String(50), unique=True, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    created_at = Column(DateTime(timezone=True),
                        default=now_santiago, nullable=False)

    client = relationship('Client', back_populates='orders')
    items = relationship('PurchaseOrderItem',
                         back_populates='order', lazy=True)


@dataclass
class PurchaseOrderItem(db.Model):
    id:        int = field(init=False)
    order_id:  int
    product_id: int
    quantity:  int

    __tablename__ = 'purchase_order_items'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey(
        'purchase_orders.id'), nullable=False)
    product_id = Column(Integer, ForeignKey(
        'products.id'),         nullable=False)
    quantity = Column(Integer, nullable=False)

    order = relationship('PurchaseOrder', back_populates='items')
    product = relationship('Product')
