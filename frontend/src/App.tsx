import React, { useState, useEffect, useCallback } from 'react';
import { usePlaidLink } from 'react-plaid-link';
import axios from 'axios';
import { Wallet, CreditCard, TrendingUp, AlertCircle, RefreshCw } from 'lucide-react';

const API_BASE_URL = 'http://localhost:8000/api';

const App: React.FC = () => {
  const [linkToken, setLinkToken] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generateToken = useCallback(async () => {
    try {
      const response = await axios.post(`${API_BASE_URL}/create_link_token`);
      setLinkToken(response.data.link_token);
    } catch (err) {
      setError('Failed to generate Plaid link token. Make sure the backend is running.');
    }
  }, []);

  useEffect(() => {
    generateToken();
  }, [generateToken]);

  const onSuccess = useCallback(async (public_token: string) => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE_URL}/exchange_public_token`, { public_token });
      // In a real app, we would then fetch the updated accounts
      setAccounts([
        { name: 'Schwab Checking', balance: 1250.45, type: 'depository' },
        { name: 'Vanguard Brokerage', balance: 45200.12, type: 'investment' },
        { name: 'Amex Credit Card', balance: 1850.00, type: 'credit' }
      ]);
    } catch (err) {
      setError('Failed to exchange token.');
    } finally {
      setLoading(false);
    }
  }, []);

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess,
  });

  return (
    <div className="min-h-screen bg-gray-50 p-8 font-sans">
      <div className="max-w-4xl mx-auto">
        <header className="mb-8 flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold text-gray-900">Finance Dashboard</h1>
            <p className="text-gray-600">Syncing Schwab & Vanguard</p>
          </div>
          <button 
            onClick={() => open()} 
            disabled={!ready || loading}
            className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
            Connect Bank
          </button>
        </header>

        {error && (
          <div className="mb-6 p-4 bg-red-100 border-l-4 border-red-500 text-red-700 flex items-center gap-3">
            <AlertCircle size={20} />
            <p>{error}</p>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <div className="flex items-center gap-3 text-gray-500 mb-2">
              <Wallet size={20} />
              <span className="font-medium">Total Cash</span>
            </div>
            <div className="text-2xl font-bold text-gray-900">$1,250.45</div>
          </div>
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <div className="flex items-center gap-3 text-gray-500 mb-2">
              <TrendingUp size={20} />
              <span className="font-medium">Investments</span>
            </div>
            <div className="text-2xl font-bold text-blue-600">$45,200.12</div>
          </div>
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <div className="flex items-center gap-3 text-gray-500 mb-2">
              <CreditCard size={20} />
              <span className="font-medium">Liabilities</span>
            </div>
            <div className="text-2xl font-bold text-red-500">$1,850.00</div>
          </div>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100 bg-gray-50">
            <h2 className="font-semibold text-gray-900">Connected Accounts</h2>
          </div>
          <div className="divide-y divide-gray-100">
            {accounts.length > 0 ? accounts.map((acc, i) => (
              <div key={i} className="px-6 py-4 flex justify-between items-center hover:bg-gray-50 transition-colors">
                <div>
                  <p className="font-medium text-gray-900">{acc.name}</p>
                  <p className="text-sm text-gray-500 capitalize">{acc.type}</p>
                </div>
                <div className="text-right">
                  <p className={`font-semibold ${acc.type === 'credit' ? 'text-red-500' : 'text-gray-900'}`}>
                    ${acc.balance.toLocaleString()}
                  </p>
                </div>
              </div>
            )) : (
              <div className="px-6 py-12 text-center text-gray-500">
                <p>No accounts connected yet. Click "Connect Bank" to start.</p>
              </div>
            )}
          </div>
        </div>

        <div className="mt-8 p-4 bg-yellow-50 rounded-lg border border-yellow-200 flex gap-3">
          <AlertCircle className="text-yellow-600 shrink-0" size={20} />
          <div>
            <p className="text-sm font-medium text-yellow-800">Liquidity Alert</p>
            <p className="text-sm text-yellow-700 mt-1">
              Your checking account ($1,250.45) is below your current credit card liabilities ($1,850.00). 
              Consider transferring funds to avoid interest charges.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default App;
