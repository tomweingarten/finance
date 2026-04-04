# Personal Finance Tracker

A self-hosted personal finance dashboard that syncs with Empower (Personal Capital). Pulls account balances and transactions, stores them locally, and provides net worth tracking, spending breakdowns, and monthly trends.

## Setup

### Prerequisites
- Python 3.10+
- Node.js 18+

### 1. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Frontend

```bash
cd frontend
npm install
```

### 3. Configure

```bash
cp backend/.env.template backend/.env
```

Edit `backend/.env` with your Empower credentials (optional — you can also import browser cookies via the UI).

### 4. Run

```bash
./start.sh
```

This starts the backend on `:8000` and frontend on `:3000`. Open `http://localhost:3000` in your browser.

To bind to a specific IP (e.g., to access from other devices on your LAN):

```bash
BIND_HOST=192.168.1.100 ./start.sh
```

### 5. Sync data

Click **Sync** in the dashboard. If Empower requires 2FA, enter the code when prompted.

If direct login doesn't work (e.g., Cloudflare blocks it), use the **Session** button to import cookies from your browser:
1. Log into Empower in your browser
2. Install a cookie export extension (e.g., Cookie-Editor)
3. Export cookies as JSON
4. Paste into the Session import dialog

## Architecture

| Layer    | Tech                          |
|----------|-------------------------------|
| Frontend | React + TypeScript + Tailwind |
| Backend  | Python / FastAPI              |
| Database | SQLite                        |
| Sync     | Empower API (via session cookies) |
