from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from .database import Base
import datetime

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    access_token = Column(String, unique=True)
    item_id = Column(String, unique=True)
    institution_id = Column(String)
    institution_name = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, unique=True)
    name = Column(String)
    mask = Column(String)
    official_name = Column(String)
    type = Column(String)
    subtype = Column(String)
    balance_available = Column(Float)
    balance_current = Column(Float)
    balance_limit = Column(Float)
    iso_currency_code = Column(String)
    unofficial_currency_code = Column(String)
    item_id = Column(Integer, ForeignKey("items.id"))

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True)
    account_id = Column(String)
    amount = Column(Float)
    date = Column(DateTime)
    name = Column(String)
    merchant_name = Column(String)
    category = Column(String)
    pending = Column(Integer)
