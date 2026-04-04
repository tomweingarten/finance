from pydantic import BaseModel
from typing import List, Optional


class AccountResponse(BaseModel):
    account_id: str
    name: str
    nature: Optional[str] = None
    balance: Optional[float] = None
    currency_code: Optional[str] = None
    account_number: Optional[str] = None
    provider_name: Optional[str] = None  # mapped from firm_name

    class Config:
        from_attributes = True


class TransactionResponse(BaseModel):
    transaction_id: str
    account_id: str
    amount: float
    made_on: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None

    class Config:
        from_attributes = True


class NetWorthPoint(BaseModel):
    date: str
    net_worth: float
    cash: float
    investments: float
    liabilities: float


class NetWorthHistory(BaseModel):
    points: List[NetWorthPoint]
    change_amount: Optional[float] = None   # current minus oldest in window
    change_pct: Optional[float] = None


class SpendingCategory(BaseModel):
    category: str
    total: float
    pct_of_total: float


class MonthlySpend(BaseModel):
    month: str   # YYYY-MM
    total: float
