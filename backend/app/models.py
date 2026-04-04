from sqlalchemy import Column, Integer, String, Float
from .database import Base


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, unique=True)   # Empower userAccountId (as string)
    name = Column(String)
    firm_name = Column(String)                 # Bank/brokerage name (e.g. "Chase")
    nature = Column(String)                    # checking, savings, credit, investment
    balance = Column(Float)
    currency_code = Column(String)
    account_number = Column(String)            # Masked (e.g. ****1234)


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True)  # Empower userTransactionId
    account_id = Column(String)                   # Empower userAccountId
    amount = Column(Float)
    made_on = Column(String)                      # YYYY-MM-DD
    description = Column(String)
    category = Column(String)
    status = Column(String)                       # "posted" | "pending"


class NetWorthSnapshot(Base):
    """One row per day (upserted on sync) tracking net worth over time."""
    __tablename__ = "networth_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, unique=True, index=True)  # YYYY-MM-DD
    net_worth = Column(Float)
    cash = Column(Float)
    investments = Column(Float)
    liabilities = Column(Float)                     # stored as negative number
