import React, { createContext, useContext, useState, useEffect } from 'react';
import { apiClient } from '../lib/api';

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: { username: string; is_admin: boolean } | null;
  authConfigured: boolean;
  vaultSelected: boolean;
  vaultAuthStatus: 'active' | 'inactive' | 'unreachable';
  setup: (username: string, password: string, remember: boolean) => Promise<void>;
  login: (username: string, password: string, remember: boolean) => Promise<void>;
  logout: () => Promise<void>;
  switchVault: () => Promise<void>;
  refreshAuth: () => Promise<void>;
  refreshVaultStatus: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [authConfigured, setAuthConfigured] = useState(false);
  const [vaultSelected, setVaultSelected] = useState(false);
  const [vaultAuthStatus, setVaultAuthStatus] = useState<'active' | 'inactive' | 'unreachable'>('inactive');

  useEffect(() => {
    checkAuth();
  }, []);

  const updateVaultAuthStatus = async () => {
    const activeVault = apiClient.getActiveVaultPath();
    if (!activeVault) {
      setVaultAuthStatus('inactive');
      return;
    }
    const reachable = await apiClient.checkServerReachable();
    if (!reachable) {
      setVaultAuthStatus('unreachable');
      return;
    }
    if (isAuthenticated || apiClient.hasStoredVaultSession(activeVault)) {
      setVaultAuthStatus('active');
      return;
    }
    setVaultAuthStatus('inactive');
  };

  useEffect(() => {
    if (!isAuthenticated) return;
    const interval = window.setInterval(() => {
      apiClient.refreshAccessToken().catch(() => undefined);
    }, 10 * 60 * 1000);
    return () => window.clearInterval(interval);
  }, [isAuthenticated]);

  useEffect(() => {
    updateVaultAuthStatus().catch(() => undefined);
  }, [isAuthenticated, vaultSelected]);

  const checkAuth = async () => {
    try {
      // Check auth status
      const status = await apiClient.authStatus();
      setAuthConfigured(status.configured);
      setVaultSelected(status.vault_selected);

      if (!status.vault_selected) {
        setIsAuthenticated(false);
        setUser(null);
        setIsLoading(false);
        return;
      }

      // If auth is disabled, consider user authenticated
      if (!status.enabled) {
        setIsAuthenticated(true);
        setUser({ username: 'admin', is_admin: true });
        setIsLoading(false);
        return;
      }

      // Try to get current user
      const currentUser = await apiClient.me();
      setUser(currentUser);
      setIsAuthenticated(true);
    } catch (error) {
      setIsAuthenticated(false);
      setUser(null);
    } finally {
      setIsLoading(false);
      await updateVaultAuthStatus();
    }
  };

  const setup = async (username: string, password: string, remember: boolean) => {
    apiClient.setRemembered(remember);
    await apiClient.setup(username, password);
    await checkAuth();
  };

  const login = async (username: string, password: string, remember: boolean) => {
    apiClient.setRemembered(remember);
    await apiClient.login(username, password);
    await checkAuth();
  };

  const logout = async () => {
    await apiClient.logout();
    setIsAuthenticated(false);
    setUser(null);
    setVaultAuthStatus('inactive');
    setVaultSelected(false);
  };

  const switchVault = async () => {
    await apiClient.unselectVault();
    apiClient.clearTokens();
    apiClient.clearActiveVaultPath();
    setIsAuthenticated(false);
    setUser(null);
    setVaultAuthStatus('inactive');
    setVaultSelected(false);
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        isLoading,
        user,
        authConfigured,
        vaultSelected,
        vaultAuthStatus,
        setup,
        login,
        logout,
        switchVault,
        refreshAuth: checkAuth,
        refreshVaultStatus: updateVaultAuthStatus,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
};
