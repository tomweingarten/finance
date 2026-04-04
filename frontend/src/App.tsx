import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from 'recharts';
import {
  Wallet, TrendingUp, CreditCard, RefreshCw, AlertCircle,
  ArrowUpRight, ArrowDownRight, Building2, List, LayoutDashboard,
  KeyRound, ChevronDown, ChevronUp, ExternalLink,
} from 'lucide-react';

const API = process.env.REACT_APP_API_URL ?? '/api';

// ── Types ─────────────────────────────────────────────────────────────────

interface Account {
  account_id: string;
  name: string;
  nature: string | null;
  balance: number | null;
  currency_code: string | null;
  account_number: string | null;
  provider_name: string | null;
}

interface Transaction {
  transaction_id: string;
  account_id: string;
  amount: number;
  made_on: string | null;
  description: string | null;
  category: string | null;
  status: string | null;
}

interface NWPoint {
  date: string;
  net_worth: number;
  cash: number;
  investments: number;
  liabilities: number;
}

interface NWHistory {
  points: NWPoint[];
  change_amount: number | null;
  change_pct: number | null;
}

interface SpendCat { category: string; total: number; pct_of_total: number; }
interface MonthlySpend { month: string; total: number; }

type Tab = 'overview' | 'accounts' | 'transactions';
type NWPeriod = 30 | 90 | 180;

// ── Helpers ───────────────────────────────────────────────────────────────

const CASH_NATURES = new Set(['checking', 'savings', 'account', 'bonus']);
const CREDIT_NATURES = new Set(['credit', 'card', 'debit_card']);
const INVESTMENT_NATURES = new Set(['investment']);

const money = (n: number | null | undefined, compact = false): string => {
  if (n == null) return '—';
  const abs = Math.abs(n);
  if (compact) {
    if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
    return `$${n.toFixed(0)}`;
  }
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
};

const fmtDate = (d: string) => {
  const [y, m, day] = d.split('-');
  return `${m}/${day}/${y.slice(2)}`;
};

const fmtMonth = (m: string) => {
  const [y, mo] = m.split('-');
  return new Date(+y, +mo - 1).toLocaleString('en-US', { month: 'short', year: '2-digit' });
};

// ── Custom chart tooltip ──────────────────────────────────────────────────

const ChartTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-sm">
      <p className="text-gray-500 text-xs mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} className="font-semibold" style={{ color: p.color }}>
          {money(p.value)}
        </p>
      ))}
    </div>
  );
};

// ── Summary card ──────────────────────────────────────────────────────────

const StatCard = ({
  icon, label, value, sub, color = 'text-gray-900',
}: {
  icon: React.ReactNode; label: string; value: string; sub?: string; color?: string;
}) => (
  <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
    <div className="flex items-center gap-2 text-gray-400 mb-3">
      {icon}
      <span className="text-sm font-medium text-gray-500">{label}</span>
    </div>
    <p className={`text-2xl font-bold ${color}`}>{value}</p>
    {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
  </div>
);

// ── Main App ──────────────────────────────────────────────────────────────

export default function App() {
  // Data state
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [nwHistory, setNwHistory] = useState<NWHistory>({ points: [], change_amount: null, change_pct: null });
  const [categories, setCategories] = useState<SpendCat[]>([]);
  const [monthly, setMonthly] = useState<MonthlySpend[]>([]);

  // UI state
  const [tab, setTab] = useState<Tab>('overview');
  const [nwPeriod, setNwPeriod] = useState<NWPeriod>(90);
  const [catFilter, setCatFilter] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [twoFactor, setTwoFactor] = useState({ pending: false, code: '', verifying: false });
  const [lastSynced, setLastSynced] = useState<string | null>(null);
  const [sessionImport, setSessionImport] = useState({
    open: false,
    cookieText: '',
    importing: false,
    success: false,
  });

  // ── Data fetching ─────────────────────────────────────────────────────

  const loadAccounts = useCallback(async () => {
    const { data } = await axios.get<Account[]>(`${API}/accounts`);
    setAccounts(data);
  }, []);

  const loadHistory = useCallback(async (period: NWPeriod) => {
    const { data } = await axios.get<NWHistory>(`${API}/networth/history?days=${period}`);
    setNwHistory(data);
  }, []);

  const loadSpending = useCallback(async () => {
    const [cats, mo] = await Promise.all([
      axios.get<SpendCat[]>(`${API}/spending/categories?days=30`),
      axios.get<MonthlySpend[]>(`${API}/spending/monthly?months=6`),
    ]);
    setCategories(cats.data);
    setMonthly(mo.data);
  }, []);

  const loadTransactions = useCallback(async () => {
    const { data } = await axios.get<Transaction[]>(`${API}/transactions?limit=200`);
    setTransactions(data);
  }, []);

  useEffect(() => {
    loadAccounts().catch(() => {});
    loadHistory(nwPeriod).catch(() => {});
  }, []); // eslint-disable-line

  useEffect(() => {
    loadHistory(nwPeriod).catch(() => {});
  }, [nwPeriod, loadHistory]);

  // Load spending/transactions when those tabs first open
  useEffect(() => {
    if (tab === 'overview' && categories.length === 0) loadSpending().catch(() => {});
    if (tab === 'transactions' && transactions.length === 0) loadTransactions().catch(() => {});
  }, [tab]); // eslint-disable-line

  // ── Sync ──────────────────────────────────────────────────────────────

  const handleSync = async () => {
    setSyncing(true);
    setError(null);
    try {
      const { data } = await axios.post<{ status: string }>(`${API}/sync`);
      if (data.status === '2fa_required') {
        setTwoFactor(tf => ({ ...tf, pending: true }));
      } else {
        const now = new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        setLastSynced(now);
        await Promise.all([loadAccounts(), loadHistory(nwPeriod), loadSpending(), loadTransactions()]);
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? 'Sync failed. Use the Session button to import cookies from your Empower browser session.');
    } finally {
      setSyncing(false);
    }
  };

  const handleVerify2FA = async () => {
    if (!twoFactor.code.trim()) return;
    setTwoFactor(tf => ({ ...tf, verifying: true }));
    setError(null);
    try {
      await axios.post(`${API}/auth/verify`, { code: twoFactor.code });
      setTwoFactor({ pending: false, code: '', verifying: false });
      await handleSync();
    } catch {
      setError('Invalid 2FA code. Please try again.');
      setTwoFactor(tf => ({ ...tf, verifying: false }));
    }
  };
  const handleImportSession = async () => {
    const raw = sessionImport.cookieText.trim();
    if (!raw) return;
    setSessionImport(s => ({ ...s, importing: true }));
    setError(null);
    try {
      // Send raw string to backend; it now handles both JSON and raw cookie text
      await axios.post(`${API}/import-session`, { cookies: raw });
      setSessionImport(s => ({ ...s, importing: false, success: true, cookieText: '' }));
      setTimeout(() => setSessionImport(s => ({ ...s, open: false, success: false })), 2000);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? (err as Error)?.message
        ?? 'Import failed.';
      setError(msg);
      setSessionImport(s => ({ ...s, importing: false }));
    }
  };

  // ── Derived values ────────────────────────────────────────────────────

  const cashAccounts = useMemo(() => accounts.filter(a => CASH_NATURES.has(a.nature ?? '')), [accounts]);
  const investAccounts = useMemo(() => accounts.filter(a => INVESTMENT_NATURES.has(a.nature ?? '')), [accounts]);
  const debtAccounts = useMemo(() => accounts.filter(a => CREDIT_NATURES.has(a.nature ?? '')), [accounts]);

  const totalCash = cashAccounts.reduce((s, a) => s + (a.balance ?? 0), 0);
  const totalInvest = investAccounts.reduce((s, a) => s + (a.balance ?? 0), 0);
  const totalLiabilities = debtAccounts.reduce((s, a) => s + (a.balance ?? 0), 0);
  const netWorth = totalCash + totalInvest + totalLiabilities;
  const liquidityAlert = accounts.length > 0 && totalCash < Math.abs(totalLiabilities);

  const currentNW = nwHistory.points.length > 0
    ? nwHistory.points[nwHistory.points.length - 1].net_worth
    : (accounts.length > 0 ? netWorth : null);

  const filteredTxns = useMemo(() =>
    catFilter
      ? transactions.filter(t => (t.category ?? '').toLowerCase().includes(catFilter.toLowerCase()))
      : transactions,
    [transactions, catFilter]
  );

  const txnCategories = useMemo(() =>
    Array.from(new Set(transactions.map(t => t.category).filter(Boolean))).sort(),
    [transactions]
  );

  const accountsByFirm = useMemo(() =>
    accounts.reduce((acc, a) => {
      const key = a.provider_name || 'Other';
      if (!acc[key]) acc[key] = [];
      acc[key].push(a);
      return acc;
    }, {} as Record<string, Account[]>),
    [accounts]
  );

  // ── Render ────────────────────────────────────────────────────────────

  const nwUp = (nwHistory.change_amount ?? 0) >= 0;

  return (
    <div className="min-h-screen bg-slate-50 font-sans">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
              <TrendingUp size={16} className="text-white" />
            </div>
            <span className="font-semibold text-gray-900 text-lg">Finance</span>
          </div>
          <div className="flex items-center gap-3">
            {lastSynced && (
              <span className="text-xs text-gray-400 hidden sm:block">
                Last synced {lastSynced}
              </span>
            )}
            <button
              onClick={loadAccounts}
              className="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
              title="Refresh"
            >
              <RefreshCw size={16} />
            </button>
            <button
              onClick={() => setSessionImport(s => ({ ...s, open: !s.open, success: false }))}
              className="flex items-center gap-1.5 border border-gray-200 text-gray-600 text-sm font-medium px-3 py-2 rounded-lg hover:bg-gray-50 transition-colors"
              title="Import browser session cookies"
            >
              <KeyRound size={14} />
              <span className="hidden sm:inline">Session</span>
              {sessionImport.open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </button>
            <button
              onClick={handleSync}
              disabled={syncing}
              className="flex items-center gap-2 bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing…' : 'Sync'}
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">

        {/* ── Session import panel ───────────────────────────────────── */}
        {sessionImport.open && (
          <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <KeyRound size={16} className="text-blue-600" />
              <h3 className="font-semibold text-gray-900">Import Browser Session</h3>
            </div>

            {sessionImport.success ? (
              <div className="flex items-center gap-2 text-emerald-600 text-sm font-medium">
                <span>Session imported successfully! Click Sync to load your accounts.</span>
              </div>
            ) : (
              <>
                <ol className="text-sm text-gray-600 space-y-1.5 list-decimal list-inside">
                  <li>
                    Install the{' '}
                    <a
                      href="https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm"
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-600 hover:underline inline-flex items-center gap-0.5"
                    >
                      Cookie-Editor extension <ExternalLink size={11} />
                    </a>{' '}
                    (Chrome) or{' '}
                    <a
                      href="https://addons.mozilla.org/en-US/firefox/addon/cookie-editor/"
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-600 hover:underline inline-flex items-center gap-0.5"
                    >
                      Firefox version <ExternalLink size={11} />
                    </a>
                  </li>
                  <li>
                    Log into{' '}
                    <strong>participant.empower-retirement.com</strong>{' '}
                    in your browser (complete any 2FA there)
                  </li>
                  <li>
                    Click the Cookie-Editor icon → <strong>Export</strong> → <strong>Export as JSON</strong>
                  </li>
                  <li>Paste the copied JSON below and click Import</li>
                </ol>

                <textarea
                  value={sessionImport.cookieText}
                  onChange={e => setSessionImport(s => ({ ...s, cookieText: e.target.value }))}
                  placeholder='[{"name":"JSESSIONID","value":"...","domain":".empower-retirement.com",...}]'
                  rows={5}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                />

                <div className="flex items-center gap-3">
                  <button
                    onClick={handleImportSession}
                    disabled={sessionImport.importing || !sessionImport.cookieText.trim()}
                    className="bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
                  >
                    {sessionImport.importing ? 'Importing…' : 'Import Session'}
                  </button>
                  <button
                    onClick={() => setSessionImport(s => ({ ...s, open: false, cookieText: '' }))}
                    className="text-sm text-gray-500 hover:text-gray-700"
                  >
                    Cancel
                  </button>
                  <span className="text-xs text-gray-400 ml-auto">
                    Sessions typically last 30–60 days
                  </span>
                </div>
              </>
            )}
          </div>
        )}

        {/* ── 2FA prompt ─────────────────────────────────────────────── */}
        {twoFactor.pending && (
          <div className="bg-blue-50 border border-blue-200 rounded-2xl p-5">
            <p className="font-semibold text-blue-900 mb-1">Two-factor authentication required</p>
            <p className="text-sm text-blue-700 mb-4">
              Empower sent a verification code to your phone.
            </p>
            <div className="flex items-center gap-3">
              <input
                type="text"
                inputMode="numeric"
                value={twoFactor.code}
                onChange={e => setTwoFactor(tf => ({ ...tf, code: e.target.value }))}
                onKeyDown={e => e.key === 'Enter' && handleVerify2FA()}
                placeholder="000000"
                className="w-28 border border-blue-300 rounded-lg px-3 py-2 text-sm tracking-widest focus:outline-none focus:ring-2 focus:ring-blue-500"
                autoFocus
              />
              <button
                onClick={handleVerify2FA}
                disabled={twoFactor.verifying || !twoFactor.code.trim()}
                className="bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {twoFactor.verifying ? 'Verifying…' : 'Verify'}
              </button>
              <button
                onClick={() => setTwoFactor({ pending: false, code: '', verifying: false })}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* ── Error banner ───────────────────────────────────────────── */}
        {error && (
          <div className="bg-red-50 border-l-4 border-red-500 rounded-r-2xl p-4 flex items-start gap-3">
            <AlertCircle size={18} className="text-red-500 shrink-0 mt-0.5" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {/* ── Net worth hero ─────────────────────────────────────────── */}
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
          <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
            <div>
              <p className="text-sm text-gray-500 mb-1">Net Worth</p>
              <div className="flex items-end gap-3">
                <h2 className="text-4xl font-bold text-gray-900 tabular-nums">
                  {currentNW != null ? money(currentNW) : '—'}
                </h2>
                {nwHistory.change_amount != null && (
                  <div className={`flex items-center gap-1 text-sm font-medium mb-1 ${nwUp ? 'text-emerald-600' : 'text-red-500'}`}>
                    {nwUp ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
                    <span>{money(Math.abs(nwHistory.change_amount), true)}</span>
                    {nwHistory.change_pct != null && (
                      <span className="text-xs font-normal">
                        ({nwHistory.change_pct > 0 ? '+' : ''}{nwHistory.change_pct.toFixed(1)}%)
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
            {/* Period selector */}
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
              {([30, 90, 180] as NWPeriod[]).map(p => (
                <button
                  key={p}
                  onClick={() => setNwPeriod(p)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                    nwPeriod === p
                      ? 'bg-white text-blue-600 shadow-sm'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {p}D
                </button>
              ))}
            </div>
          </div>

          {/* Area chart */}
          {nwHistory.points.length > 1 ? (
            <div className="h-48 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={nwHistory.points} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="nwGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.2} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(d: string) => fmtDate(d)}
                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                    axisLine={false}
                    tickLine={false}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tickFormatter={(v: number) => money(v, true)}
                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                    axisLine={false}
                    tickLine={false}
                    width={52}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="net_worth"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fill="url(#nwGrad)"
                    dot={false}
                    activeDot={{ r: 4, fill: '#3b82f6' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-48 flex items-center justify-center text-gray-400 text-sm bg-slate-50 rounded-xl">
              {accounts.length > 0
                ? 'Sync again tomorrow to see your net worth trend'
                : 'Sync your accounts to see net worth history'}
            </div>
          )}
        </div>

        {/* ── Summary cards ──────────────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StatCard
            icon={<Wallet size={16} />}
            label="Cash"
            value={money(totalCash)}
            sub={`${cashAccounts.length} account${cashAccounts.length !== 1 ? 's' : ''}`}
          />
          <StatCard
            icon={<TrendingUp size={16} />}
            label="Investments"
            value={money(totalInvest)}
            sub={`${investAccounts.length} account${investAccounts.length !== 1 ? 's' : ''}`}
            color="text-blue-600"
          />
          <StatCard
            icon={<CreditCard size={16} />}
            label="Liabilities"
            value={money(Math.abs(totalLiabilities))}
            sub={`${debtAccounts.length} account${debtAccounts.length !== 1 ? 's' : ''}`}
            color="text-rose-500"
          />
        </div>

        {/* ── Liquidity alert ────────────────────────────────────────── */}
        {liquidityAlert && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4 flex gap-3">
            <AlertCircle size={18} className="text-amber-500 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-amber-800">Liquidity Alert</p>
              <p className="text-sm text-amber-700 mt-0.5">
                Cash ({money(totalCash)}) is below credit card balances ({money(Math.abs(totalLiabilities))}).
                Consider a balance transfer to avoid interest.
              </p>
            </div>
          </div>
        )}

        {/* ── Tabs ───────────────────────────────────────────────────── */}
        <div>
          <div className="flex gap-1 bg-gray-100 rounded-xl p-1 w-fit mb-5">
            {([
              { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={14} /> },
              { id: 'accounts', label: 'Accounts', icon: <Building2 size={14} /> },
              { id: 'transactions', label: 'Transactions', icon: <List size={14} /> },
            ] as { id: Tab; label: string; icon: React.ReactNode }[]).map(({ id, label, icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  tab === id
                    ? 'bg-white text-blue-600 shadow-sm'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {icon}
                {label}
              </button>
            ))}
          </div>

          {/* ── Overview tab ─────────────────────────────────────────── */}
          {tab === 'overview' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Spending by category */}
              <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
                <h3 className="font-semibold text-gray-900 mb-4 text-sm">Spending by Category (30d)</h3>
                {categories.length > 0 ? (
                  <div className="space-y-3">
                    {categories.slice(0, 8).map(cat => (
                      <div key={cat.category} className="flex items-center gap-3">
                        <span className="w-32 text-sm text-gray-600 truncate shrink-0">{cat.category}</span>
                        <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                          <div
                            className="bg-blue-500 h-1.5 rounded-full transition-all"
                            style={{ width: `${cat.pct_of_total}%` }}
                          />
                        </div>
                        <span className="text-sm font-medium text-gray-900 w-20 text-right shrink-0">
                          {money(cat.total)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-400">No spending data yet. Sync to load transactions.</p>
                )}
              </div>

              {/* Monthly spend trend */}
              <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
                <h3 className="font-semibold text-gray-900 mb-4 text-sm">Monthly Spending</h3>
                {monthly.length > 0 ? (
                  <div className="h-52 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={monthly} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                        <XAxis
                          dataKey="month"
                          tickFormatter={fmtMonth}
                          tick={{ fontSize: 10, fill: '#94a3b8' }}
                          axisLine={false}
                          tickLine={false}
                        />
                        <YAxis
                          tickFormatter={(v: number) => money(v, true)}
                          tick={{ fontSize: 10, fill: '#94a3b8' }}
                          axisLine={false}
                          tickLine={false}
                          width={48}
                        />
                        <Tooltip content={<ChartTooltip />} />
                        <Bar dataKey="total" fill="#6366f1" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <p className="text-sm text-gray-400">No monthly data yet.</p>
                )}
              </div>
            </div>
          )}

          {/* ── Accounts tab ─────────────────────────────────────────── */}
          {tab === 'accounts' && (
            <div className="space-y-4">
              {Object.keys(accountsByFirm).length > 0 ? (
                Object.entries(accountsByFirm)
                  .sort((a, b) => {
                    const sumA = a[1].reduce((s, acc) => s + Math.abs(acc.balance ?? 0), 0);
                    const sumB = b[1].reduce((s, acc) => s + Math.abs(acc.balance ?? 0), 0);
                    return sumB - sumA;
                  })
                  .map(([firm, accs]) => (
                    <div key={firm} className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                      <div className="px-5 py-3 bg-slate-50 border-b border-gray-100 flex items-center gap-2">
                        <Building2 size={14} className="text-gray-400" />
                        <span className="font-semibold text-sm text-gray-700">{firm}</span>
                        <span className="text-xs text-gray-400 ml-1">
                          {accs.length} account{accs.length !== 1 ? 's' : ''}
                        </span>
                      </div>
                      <div className="divide-y divide-gray-50">
                        {accs.map(acc => {
                          const isDebt = CREDIT_NATURES.has(acc.nature ?? '');
                          return (
                            <div key={acc.account_id} className="px-5 py-3.5 flex justify-between items-center">
                              <div>
                                <p className="font-medium text-gray-900 text-sm">{acc.name}</p>
                                <div className="flex items-center gap-2 mt-0.5">
                                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                    INVESTMENT_NATURES.has(acc.nature ?? '')
                                      ? 'bg-blue-50 text-blue-600'
                                      : isDebt
                                      ? 'bg-rose-50 text-rose-500'
                                      : 'bg-emerald-50 text-emerald-600'
                                  }`}>
                                    {acc.nature ?? 'account'}
                                  </span>
                                  {acc.account_number && (
                                    <span className="text-xs text-gray-400">{acc.account_number}</span>
                                  )}
                                </div>
                              </div>
                              <p className={`font-semibold tabular-nums ${isDebt ? 'text-rose-500' : 'text-gray-900'}`}>
                                {money(isDebt ? Math.abs(acc.balance ?? 0) : acc.balance)}
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))
              ) : (
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-12 text-center text-gray-400">
                  <p>No accounts yet.</p>
                  <p className="text-xs mt-1">Click Sync to pull your Empower accounts.</p>
                </div>
              )}
            </div>
          )}

          {/* ── Transactions tab ──────────────────────────────────────── */}
          {tab === 'transactions' && (
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
              {/* Filter bar */}
              <div className="px-5 py-3 border-b border-gray-100 bg-slate-50 flex items-center gap-3">
                <span className="text-sm text-gray-500 shrink-0">Filter:</span>
                <select
                  value={catFilter}
                  onChange={e => setCatFilter(e.target.value)}
                  className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">All categories</option>
                  {txnCategories.map(c => (
                    <option key={c!} value={c!}>{c}</option>
                  ))}
                </select>
                <span className="text-xs text-gray-400 ml-auto">
                  {filteredTxns.length} transaction{filteredTxns.length !== 1 ? 's' : ''}
                </span>
              </div>

              {/* Table */}
              <div className="overflow-x-auto">
                {filteredTxns.length > 0 ? (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-gray-400 border-b border-gray-100">
                        <th className="px-5 py-2.5 font-medium">Date</th>
                        <th className="px-3 py-2.5 font-medium">Description</th>
                        <th className="px-3 py-2.5 font-medium hidden sm:table-cell">Category</th>
                        <th className="px-5 py-2.5 font-medium text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {filteredTxns.map(t => {
                        const isExpense = (t.amount ?? 0) < 0;
                        return (
                          <tr key={t.transaction_id} className="hover:bg-slate-50 transition-colors">
                            <td className="px-5 py-3 text-gray-500 whitespace-nowrap">
                              {t.made_on ? fmtDate(t.made_on) : '—'}
                            </td>
                            <td className="px-3 py-3 text-gray-900 max-w-xs truncate">
                              {t.description || '—'}
                            </td>
                            <td className="px-3 py-3 hidden sm:table-cell">
                              {t.category ? (
                                <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                                  {t.category}
                                </span>
                              ) : '—'}
                            </td>
                            <td className={`px-5 py-3 text-right font-medium tabular-nums whitespace-nowrap ${
                              isExpense ? 'text-rose-500' : 'text-emerald-600'
                            }`}>
                              {money(t.amount)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <div className="p-12 text-center text-gray-400">
                    <p>{transactions.length === 0 ? 'No transactions yet. Sync to load.' : 'No transactions match this filter.'}</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
