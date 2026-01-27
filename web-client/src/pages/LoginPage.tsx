import React, { useEffect, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { apiClient } from '../lib/api';

export const LoginPage: React.FC = () => {
  const { authConfigured, vaultSelected, setup, login, refreshAuth } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [serverPassword, setServerPassword] = useState('');
  const [showServerPasswordPrompt, setShowServerPasswordPrompt] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [vaults, setVaults] = useState<Array<{ name: string; path: string }>>([]);
  const [vaultStatuses, setVaultStatuses] = useState<Record<string, 'active' | 'inactive' | 'unreachable'>>({});
  const [vaultsRoot, setVaultsRoot] = useState('');
  const [newVaultName, setNewVaultName] = useState('');
  const [rememberMe, setRememberMe] = useState(() => apiClient.isRemembered());

  useEffect(() => {
    if (!vaultSelected) {
      loadVaults().catch((err) => {
        // If 403, need server password
        if (err.status === 403) {
          setShowServerPasswordPrompt(true);
          setError('Master password required');
        } else {
          setError(err.message || 'Failed to load vaults');
        }
      });
    }
  }, [vaultSelected]);

  const loadVaults = async () => {
    const response = await apiClient.listVaults();
    setVaults(response.vaults);
    setVaultsRoot(response.root);
    setShowServerPasswordPrompt(false);
    await updateVaultStatuses(response.vaults);
  };

  const updateVaultStatuses = async (vaultList: Array<{ name: string; path: string }>) => {
    if (vaultList.length === 0) {
      setVaultStatuses({});
      return;
    }
    const reachable = await apiClient.checkServerReachable();
    if (!reachable) {
      const unreachableStatuses = vaultList.reduce<Record<string, 'unreachable'>>((acc, vault) => {
        acc[vault.path] = 'unreachable';
        return acc;
      }, {});
      setVaultStatuses(unreachableStatuses);
      return;
    }

    const statusEntries = await Promise.all(
      vaultList.map(async (vault) => {
        const status = await apiClient.getVaultSessionStatus(vault.path);
        return [vault.path, status] as const;
      })
    );
    const nextStatuses = statusEntries.reduce<Record<string, 'active' | 'inactive' | 'unreachable'>>((acc, [path, status]) => {
      acc[path] = status;
      return acc;
    }, {});
    setVaultStatuses(nextStatuses);
  };

  const handleServerPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await apiClient.setServerPassword(serverPassword);
      await loadVaults();
    } catch (err: any) {
      setError(err.message || 'Invalid server password');
    } finally {
      setLoading(false);
    }
  };

  const handleSelectVault = async (path: string) => {
    setError('');
    setLoading(true);
    try {
      await apiClient.selectVault(path);
      await apiClient.applyStoredVaultSession(path);
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
        await login(username, password, rememberMe);
      } else {
        await setup(username, password, rememberMe);
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
        border: '1px solid #e5e7eb',
        borderRadius: '12px',
        backgroundColor: '#fff',
        boxShadow: '0 8px 24px rgba(15, 23, 42, 0.08)'
      }}>
        {showServerPasswordPrompt && (
          <>
            <h1 style={{ textAlign: 'center', marginBottom: '20px' }}>Master Password Required</h1>
            <p style={{ marginBottom: '20px', color: '#666' }}>
              This server requires a master password before vault operations.
            </p>
            <form onSubmit={handleServerPasswordSubmit}>
              <div style={{ marginBottom: '12px' }}>
                <label style={{ display: 'block', marginBottom: '5px' }}>Master Password</label>
                <input
                  type="password"
                  value={serverPassword}
                  onChange={(e) => setServerPassword(e.target.value)}
                  required
                  style={{
                    width: '100%',
                    padding: '10px',
                    fontSize: '16px',
                    border: '1px solid #ddd',
                    borderRadius: '4px'
                  }}
                />
              </div>
              {error && <p style={{ color: 'red', marginBottom: '12px' }}>{error}</p>}
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
                {loading ? 'Verifying...' : 'Continue'}
              </button>
            </form>
          </>
        )}

        {!vaultSelected && !showServerPasswordPrompt && (
          <>
            <h1 style={{ textAlign: 'center', marginBottom: '20px' }}>Select a Vault</h1>
            <p style={{ marginBottom: '20px', color: '#666' }}>
              Vaults root: <strong>{vaultsRoot || 'Loading...'}</strong>
            </p>
            <p style={{ marginBottom: '16px', color: '#666', fontSize: '14px' }}>
              Vaults with active sessions are highlighted with a green dot.
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
                      textAlign: 'left',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '10px'
                    }}
                  >
                    <span>{vault.name}</span>
                    <span
                      className={`status-dot status-dot--${vaultStatuses[vault.path] || 'inactive'}`}
                      aria-label={`Vault status: ${vaultStatuses[vault.path] || 'inactive'}`}
                    />
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

          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px', fontSize: '14px', color: '#444' }}>
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
            />
            Keep me logged in
          </label>

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
