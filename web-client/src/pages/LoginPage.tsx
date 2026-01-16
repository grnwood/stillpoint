import React, { useEffect, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { apiClient } from '../lib/api';

export const LoginPage: React.FC = () => {
  const { authConfigured, vaultSelected, setup, login, refreshAuth } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [vaults, setVaults] = useState<Array<{ name: string; path: string }>>([]);
  const [vaultsRoot, setVaultsRoot] = useState('');
  const [newVaultName, setNewVaultName] = useState('');

  useEffect(() => {
    if (!vaultSelected) {
      loadVaults().catch((err) => {
        setError(err.message || 'Failed to load vaults');
      });
    }
  }, [vaultSelected]);

  const loadVaults = async () => {
    const response = await apiClient.listVaults();
    setVaults(response.vaults);
    setVaultsRoot(response.root);
  };

  const handleSelectVault = async (path: string) => {
    setError('');
    setLoading(true);
    try {
      await apiClient.selectVault(path);
      await refreshAuth();
    } catch (err: any) {
      setError(err.message || 'Failed to select vault');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateVault = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const created = await apiClient.createVault(newVaultName.trim());
      setNewVaultName('');
      await apiClient.selectVault(created.path);
      await refreshAuth();
      await loadVaults();
    } catch (err: any) {
      setError(err.message || 'Failed to create vault');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      if (authConfigured) {
        await login(username, password);
      } else {
        await setup(username, password);
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ 
      display: 'flex', 
      flexDirection: 'column', 
      alignItems: 'center', 
      justifyContent: 'center', 
      minHeight: '100vh',
      padding: '20px'
    }}>
      <div style={{ 
        width: '100%', 
        maxWidth: '400px',
        padding: '30px',
        border: '1px solid #ddd',
        borderRadius: '8px'
      }}>
        {!vaultSelected && (
          <>
            <h1 style={{ textAlign: 'center', marginBottom: '20px' }}>Select a Vault</h1>
            <p style={{ marginBottom: '20px', color: '#666' }}>
              Vaults root: <strong>{vaultsRoot || 'Loading...'}</strong>
            </p>
            {vaults.length > 0 ? (
              <div style={{ marginBottom: '20px' }}>
                {vaults.map((vault) => (
                  <button
                    key={vault.path}
                    type="button"
                    onClick={() => handleSelectVault(vault.path)}
                    disabled={loading}
                    style={{
                      width: '100%',
                      padding: '10px',
                      marginBottom: '8px',
                      fontSize: '16px',
                      backgroundColor: '#222',
                      color: '#fff',
                      border: '1px solid #222',
                      borderRadius: '4px',
                      cursor: loading ? 'not-allowed' : 'pointer',
                      textAlign: 'left'
                    }}
                  >
                    {vault.name}
                  </button>
                ))}
              </div>
            ) : (
              <p style={{ marginBottom: '20px', color: '#666' }}>No vaults found.</p>
            )}

            <form onSubmit={handleCreateVault}>
              <div style={{ marginBottom: '12px' }}>
                <label style={{ display: 'block', marginBottom: '5px' }}>New vault name</label>
                <input
                  type="text"
                  value={newVaultName}
                  onChange={(e) => setNewVaultName(e.target.value)}
                  required
                  minLength={1}
                  style={{
                    width: '100%',
                    padding: '10px',
                    fontSize: '16px',
                    border: '1px solid #ddd',
                    borderRadius: '4px'
                  }}
                />
              </div>
              <button
                type="submit"
                disabled={loading || !newVaultName.trim()}
                style={{
                  width: '100%',
                  padding: '12px',
                  fontSize: '16px',
                  backgroundColor: '#222',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  opacity: loading ? 0.6 : 1
                }}
              >
                {loading ? 'Please wait...' : 'Create Vault'}
              </button>
            </form>
          </>
        )}

        {vaultSelected && (
          <>
            <h1 style={{ textAlign: 'center', marginBottom: '30px' }}>
              {authConfigured ? 'Login' : 'Setup'} - StillPoint
            </h1>
            
            {!authConfigured && (
              <p style={{ marginBottom: '20px', color: '#666' }}>
                Create your admin account to get started.
              </p>
            )}

            <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '5px' }}>Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              style={{
                width: '100%',
                padding: '10px',
                fontSize: '16px',
                border: '1px solid #ddd',
                borderRadius: '4px'
              }}
            />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '5px' }}>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              style={{
                width: '100%',
                padding: '10px',
                fontSize: '16px',
                border: '1px solid #ddd',
                borderRadius: '4px'
              }}
            />
          </div>

          {error && (
            <div style={{ 
              marginBottom: '20px', 
              padding: '10px', 
              backgroundColor: '#fee', 
              color: '#c00',
              borderRadius: '4px'
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '12px',
              fontSize: '16px',
              backgroundColor: '#222',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.6 : 1
            }}
          >
            {loading ? 'Please wait...' : (authConfigured ? 'Login' : 'Create Account')}
          </button>
        </form>
        </>
        )}
      </div>
    </div>
  );
};
