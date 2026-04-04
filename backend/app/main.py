import logging
import datetime
import os
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from . import models, schemas, database
from .empower_client import get_client

load_dotenv()

app = FastAPI(title="Finance API")

# Create all DB tables on startup (including new networth_snapshots table)
models.Base.metadata.create_all(bind=database.engine)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Nature buckets — must match frontend constants
CASH_NATURES = {"checking", "savings", "account", "bonus"}
CREDIT_NATURES = {"credit", "card", "debit_card"}
INVESTMENT_NATURES = {"investment"}

# Categories to exclude from spending analysis
EXCLUDED_CATEGORIES = {"Transfer", "Investment", "Credit Card Payment", "Paycheck", ""}


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _upsert_accounts(db: Session, accounts: list):
    for acc in accounts:
        existing = db.query(models.Account).filter_by(account_id=acc["account_id"]).first()
        if existing:
            existing.balance = acc["balance"]
            existing.firm_name = acc["firm_name"]
        else:
            db.add(models.Account(
                account_id=acc["account_id"],
                name=acc["name"],
                firm_name=acc["firm_name"],
                nature=acc["nature"],
                balance=acc["balance"],
                currency_code=acc["currency_code"],
                account_number=acc["account_number"],
            ))
    db.commit()


def _save_networth_snapshot(db: Session):
    """Compute net worth from current account balances and upsert today's snapshot."""
    accounts = db.query(models.Account).all()
    cash = sum(a.balance or 0 for a in accounts if a.nature in CASH_NATURES)
    investments = sum(a.balance or 0 for a in accounts if a.nature in INVESTMENT_NATURES)
    liabilities = sum(a.balance or 0 for a in accounts if a.nature in CREDIT_NATURES)
    net_worth = cash + investments + liabilities  # liabilities are negative in Empower

    today = datetime.date.today().isoformat()
    existing = db.query(models.NetWorthSnapshot).filter_by(date=today).first()
    if existing:
        existing.net_worth = net_worth
        existing.cash = cash
        existing.investments = investments
        existing.liabilities = liabilities
    else:
        db.add(models.NetWorthSnapshot(
            date=today, net_worth=net_worth,
            cash=cash, investments=investments, liabilities=liabilities,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status():
    return {"authenticated": get_client()._authenticated}


@app.post("/api/auth/verify")
def verify_2fa(body: dict):
    code = body.get("code", "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    try:
        return get_client().verify_2fa(code)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.post("/api/auth/logout")
def logout():
    """Force re-authentication on next sync (clears saved session)."""
    get_client().clear_session()
    return {"status": "ok"}


@app.post("/api/import-session")
def import_session(body: dict):
    """
    Import browser session cookies exported from Empower.
    Body: {"cookies": [...], "api_base": "optional"}
    Cookies should be in Cookie-Editor JSON array format.
    """
    import logging
    import json as _json
    cookies = body.get("cookies")
    api_base = body.get("api_base", "").strip() or None

    logging.error(f"DEBUG: import_session called. api_base={api_base}")

    if not cookies:
        raise HTTPException(status_code=400, detail="cookies field is required")

    # Accept JSON string, list, or RAW cookie string
    if isinstance(cookies, str):
        try:
            # Try parsing as JSON first
            cookies_data = _json.loads(cookies)
            if isinstance(cookies_data, dict):
                cookies_list = cookies_data.get("cookies", [])
            else:
                cookies_list = cookies_data
        except Exception:
            # Not JSON, treat as raw "name=val; name2=val2" string
            logging.error("DEBUG: Treating input as raw cookie string")
            cookies_list = []
            for item in cookies.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    cookies_list.append({
                        "name": k,
                        "value": v,
                        "domain": "participant.empower-retirement.com", 
                        "path": "/"
                    })
        cookies = cookies_list

    logging.error(f"DEBUG: Number of cookies processed: {len(cookies) if isinstance(cookies, list) else 'N/A'}")

    try:
        result = get_client().import_session(cookies, api_base)
        return result
    except Exception as exc:
        logging.error(f"DEBUG: import_session route exception: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/accounts", response_model=List[schemas.AccountResponse])
def get_accounts(db: Session = Depends(get_db)):
    return [
        schemas.AccountResponse(
            account_id=a.account_id,
            name=a.name,
            nature=a.nature,
            balance=a.balance,
            currency_code=a.currency_code,
            account_number=a.account_number,
            provider_name=a.firm_name,
        )
        for a in db.query(models.Account).all()
    ]


@app.post("/api/sync")
def sync(db: Session = Depends(get_db)):
    """
    Authenticate with Empower (if needed) then pull accounts + 90-day transactions.
    If 2FA is required, returns {"status": "2fa_required"} — caller must POST /api/auth/verify.
    """
    import sys
    import traceback
    try:
        client = get_client()
        print(f"DEBUG: Sync requested. client.authenticated={client._authenticated}, api_base={client._api_base}, has_csrf={bool(client._csrf)}, has_session={bool(client._session)}", file=sys.stderr)
        
        is_valid = client._is_session_valid()
        print(f"DEBUG: Session validity check: {is_valid}", file=sys.stderr)
        
        # EXPERIMENTAL: Even if is_valid is False, if we just imported cookies,
        # let's try to proceed to getAccounts. Some portals block querySession.
        if is_valid:
            client._authenticated = True
        
        # If we have no cookies at all, we MUST log in.
        # But if we have cookies, let's try to fetch once before giving up.
        if not (client._authenticated or client._session):
            print("DEBUG: No session and not authenticated, attempting login()", file=sys.stderr)
            try:
                result = client.login()
                print(f"DEBUG: login() result: {result}", file=sys.stderr)
                if result["status"] == "2fa_required":
                    return result
            except Exception as exc:
                print(f"DEBUG: login() failed: {exc}", file=sys.stderr)
                raise HTTPException(status_code=500, detail=str(exc))

        print("DEBUG: Proceeding to fetch data...", file=sys.stderr)
        print(f"DEBUG: Starting sync. First sync: {client.is_first_sync()}", file=sys.stderr)
        accounts = client.get_accounts()
        print(f"DEBUG: Fetched {len(accounts)} accounts from Empower.", file=sys.stderr)
        _upsert_accounts(db, accounts)
        _save_networth_snapshot(db)

        end_date = datetime.date.today()
        # First sync after fresh login: pull up to 2 years of history.
        # Subsequent syncs: rolling 90 days to catch new transactions.
        if client.is_first_sync():
            start_date = end_date - datetime.timedelta(days=730)
            client.clear_first_sync_flag()
            first = True
        else:
            start_date = end_date - datetime.timedelta(days=90)
            first = False

        print(f"DEBUG: Fetching transactions from {start_date.isoformat()} to {end_date.isoformat()}", file=sys.stderr)
        txns = client.get_transactions(start_date.isoformat(), end_date.isoformat())
        print(f"DEBUG: Fetched {len(txns)} transactions from Empower.", file=sys.stderr)

        new_count = 0
        for txn in txns:
            if not db.query(models.Transaction).filter_by(transaction_id=txn["transaction_id"]).first():
                db.add(models.Transaction(**txn))
                new_count += 1
        db.commit()
        print(f"DEBUG: Committed {new_count} new transactions to DB.", file=sys.stderr)

        return {
            "status": "ok",
            "synced": new_count,
            "accounts": len(accounts),
            "history_days": 730 if first else 90,
        }
    except Exception as exc:
        print(f"DEBUG: Sync route global exception: {exc}", file=sys.stderr)
        traceback.print_exc()
        if not isinstance(exc, HTTPException):
            client._authenticated = False
            raise HTTPException(status_code=500, detail=str(exc))
        raise exc


@app.get("/api/transactions", response_model=List[schemas.TransactionResponse])
def get_transactions(limit: int = 100, db: Session = Depends(get_db)):
    rows = (
        db.query(models.Transaction)
        .order_by(models.Transaction.made_on.desc())
        .limit(limit)
        .all()
    )
    return [schemas.TransactionResponse(**{c.key: getattr(t, c.key) for c in t.__table__.columns}) for t in rows]


# ---------------------------------------------------------------------------
# Net worth history
# ---------------------------------------------------------------------------

@app.get("/api/networth/history", response_model=schemas.NetWorthHistory)
def networth_history(days: int = 90, db: Session = Depends(get_db)):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    points = (
        db.query(models.NetWorthSnapshot)
        .filter(models.NetWorthSnapshot.date >= cutoff)
        .order_by(models.NetWorthSnapshot.date.asc())
        .all()
    )
    pts = [schemas.NetWorthPoint(
        date=p.date, net_worth=p.net_worth,
        cash=p.cash, investments=p.investments, liabilities=p.liabilities,
    ) for p in points]

    change_amount = change_pct = None
    if len(pts) >= 2:
        oldest, latest = pts[0].net_worth, pts[-1].net_worth
        change_amount = round(latest - oldest, 2)
        change_pct = round((change_amount / abs(oldest)) * 100, 2) if oldest else None

    return schemas.NetWorthHistory(points=pts, change_amount=change_amount, change_pct=change_pct)


# ---------------------------------------------------------------------------
# Spending analysis
# ---------------------------------------------------------------------------

@app.get("/api/spending/categories", response_model=List[schemas.SpendingCategory])
def spending_categories(days: int = 30, db: Session = Depends(get_db)):
    """Top spending categories for the last N days (expenses only, transfers excluded)."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

    rows = (
        db.query(models.Transaction.category, func.sum(models.Transaction.amount).label("total"))
        .filter(
            models.Transaction.made_on >= cutoff,
            models.Transaction.amount < 0,
        )
        .group_by(models.Transaction.category)
        .order_by(func.sum(models.Transaction.amount).asc())
        .all()
    )

    # Filter excluded categories and convert to positive spend totals
    results = [
        {"category": cat or "Uncategorized", "total": abs(total)}
        for cat, total in rows
        if (cat or "") not in EXCLUDED_CATEGORIES
    ]

    grand_total = sum(r["total"] for r in results) or 1
    return [
        schemas.SpendingCategory(
            category=r["category"],
            total=round(r["total"], 2),
            pct_of_total=round((r["total"] / grand_total) * 100, 1),
        )
        for r in results
    ]


@app.get("/api/spending/monthly", response_model=List[schemas.MonthlySpend])
def spending_monthly(months: int = 6, db: Session = Depends(get_db)):
    """Monthly spending totals for the last N months."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=months * 31)).isoformat()

    # strftime works for SQLite; for PostgreSQL use DATE_TRUNC('month', made_on::date)
    rows = (
        db.query(
            func.strftime("%Y-%m", models.Transaction.made_on).label("month"),
            func.sum(models.Transaction.amount).label("total"),
        )
        .filter(
            models.Transaction.made_on >= cutoff,
            models.Transaction.amount < 0,
            models.Transaction.category.notin_(list(EXCLUDED_CATEGORIES)),
        )
        .group_by(text("month"))
        .order_by(text("month asc"))
        .all()
    )

    return [
        schemas.MonthlySpend(month=month, total=round(abs(total), 2))
        for month, total in rows
        if month
    ]


# ---------------------------------------------------------------------------
# AI agent endpoints (token-efficient)
# ---------------------------------------------------------------------------

@app.get("/api/ai/summary")
def ai_summary(days: int = 30, db: Session = Depends(get_db)):
    """
    Complete financial snapshot for AI agents.
    All data needed for a briefing in one call.
    """
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=days)).isoformat()

    # Net worth trend
    latest_snap = (
        db.query(models.NetWorthSnapshot)
        .order_by(models.NetWorthSnapshot.date.desc())
        .first()
    )
    past_snap = (
        db.query(models.NetWorthSnapshot)
        .filter(models.NetWorthSnapshot.date <= cutoff)
        .order_by(models.NetWorthSnapshot.date.desc())
        .first()
    )

    nw_current = latest_snap.net_worth if latest_snap else None
    nw_past = past_snap.net_worth if past_snap else None
    nw_change = round(nw_current - nw_past, 2) if (nw_current is not None and nw_past is not None) else None
    nw_change_pct = round((nw_change / abs(nw_past)) * 100, 2) if (nw_change is not None and nw_past) else None

    # Accounts
    accounts = db.query(models.Account).all()

    # Spending
    spend_rows = (
        db.query(models.Transaction.category, func.sum(models.Transaction.amount).label("total"))
        .filter(
            models.Transaction.made_on >= cutoff,
            models.Transaction.amount < 0,
        )
        .group_by(models.Transaction.category)
        .order_by(func.sum(models.Transaction.amount).asc())
        .all()
    )
    spend_by_cat = [
        {"category": cat or "Uncategorized", "amount": round(abs(total), 2)}
        for cat, total in spend_rows
        if (cat or "") not in EXCLUDED_CATEGORIES
    ]
    total_spend = sum(r["amount"] for r in spend_by_cat)

    # Monthly trend (last 6 months)
    six_mo_cutoff = (today - datetime.timedelta(days=180)).isoformat()
    monthly_rows = (
        db.query(
            func.strftime("%Y-%m", models.Transaction.made_on).label("month"),
            func.sum(models.Transaction.amount).label("total"),
        )
        .filter(
            models.Transaction.made_on >= six_mo_cutoff,
            models.Transaction.amount < 0,
            models.Transaction.category.notin_(list(EXCLUDED_CATEGORIES)),
        )
        .group_by(text("month"))
        .order_by(text("month asc"))
        .all()
    )

    return {
        "as_of": today.isoformat(),
        "period_days": days,
        "net_worth": {
            "current": round(nw_current, 2) if nw_current is not None else None,
            f"change_{days}d": nw_change,
            f"change_{days}d_pct": nw_change_pct,
        },
        "balances": {
            "cash": round(latest_snap.cash, 2) if latest_snap else None,
            "investments": round(latest_snap.investments, 2) if latest_snap else None,
            "liabilities": round(abs(latest_snap.liabilities), 2) if latest_snap else None,
        },
        "spending": {
            "total": round(total_spend, 2),
            "top_categories": spend_by_cat[:10],
        },
        "monthly_spend_trend": [
            {"month": month, "spend": round(abs(total), 2)}
            for month, total in monthly_rows
            if month
        ],
        "accounts": [
            {
                "name": a.name,
                "institution": a.firm_name,
                "type": a.nature,
                "balance": round(a.balance, 2) if a.balance is not None else None,
            }
            for a in accounts
        ],
        "last_synced": latest_snap.date if latest_snap else None,
    }


@app.get("/api/ai/briefing")
def ai_briefing(days: int = 30, db: Session = Depends(get_db)):
    """
    Plain-text financial briefing (~150 words) for AI agents to read directly.
    Minimizes tokens while preserving all actionable context.
    """
    data = ai_summary(days=days, db=db)
    nw = data["net_worth"]
    bal = data["balances"]
    spend = data["spending"]

    def money(n):
        if n is None:
            return "N/A"
        if abs(n) >= 1_000_000:
            return f"${n/1_000_000:.2f}M"
        if abs(n) >= 1_000:
            return f"${n/1_000:.1f}K"
        return f"${n:.0f}"

    # Net worth line
    nw_line = f"NET WORTH: {money(nw['current'])}"
    if nw.get(f"change_{days}d") is not None:
        arrow = "▲" if nw[f"change_{days}d"] >= 0 else "▼"
        nw_line += f" ({arrow} {money(abs(nw[f'change_{days}d']))} / {nw.get(f'change_{days}d_pct', 0):+.1f}% in {days}d)"

    # Balances
    bal_line = f"ASSETS: {money(bal['cash'])} cash, {money(bal['investments'])} invested | DEBT: {money(bal['liabilities'])}"

    # Liquidity ratio
    liquidity = ""
    if bal["cash"] and bal["liabilities"] and bal["liabilities"] > 0:
        ratio = bal["cash"] / bal["liabilities"]
        liquidity = f"Liquidity ratio: {ratio:.1f}x (cash-to-debt)."

    # Spending
    top_cats = ", ".join(f"{c['category']} {money(c['amount'])}" for c in spend["top_categories"][:5])
    spend_line = f"SPENDING ({days}d): {money(spend['total'])} total. Top: {top_cats}."

    # Accounts
    acct_list = ", ".join(
        f"{a['name']} {money(a['balance'])}"
        for a in sorted(data["accounts"], key=lambda x: abs(x["balance"] or 0), reverse=True)[:6]
    )
    acct_line = f"ACCOUNTS ({len(data['accounts'])}): {acct_list}."

    # Monthly trend (last 3 months)
    trend = data["monthly_spend_trend"][-3:]
    trend_str = " → ".join(f"{t['month']} {money(t['spend'])}" for t in trend)
    trend_line = f"MONTHLY SPEND: {trend_str}." if trend_str else ""

    lines = [nw_line, bal_line, spend_line, acct_line]
    if trend_line:
        lines.append(trend_line)
    if liquidity:
        lines.append(liquidity)
    lines.append(f"[as of {data['as_of']}, last synced {data['last_synced'] or 'never'}]")

    return {"briefing": "\n".join(lines)}


@app.get("/api/ai/transactions")
def ai_transactions(
    limit: int = 50,
    category: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Filtered transaction list for AI agents.
    Params: limit (max 200), category (partial match), start/end (YYYY-MM-DD).
    """
    limit = min(limit, 200)
    q = db.query(models.Transaction).order_by(models.Transaction.made_on.desc())
    if category:
        q = q.filter(models.Transaction.category.ilike(f"%{category}%"))
    if start:
        q = q.filter(models.Transaction.made_on >= start)
    if end:
        q = q.filter(models.Transaction.made_on <= end)
    rows = q.limit(limit).all()
    return [
        {
            "date": t.made_on,
            "description": t.description,
            "amount": t.amount,
            "category": t.category,
            "status": t.status,
            "account_id": t.account_id,
        }
        for t in rows
    ]
