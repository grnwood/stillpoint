import React, { createContext, useContext, useState, useEffect } from 'react';
import { apiClient } from '../lib/api';

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: { username: string; is_admin: boolean } | null;
  authConfigured: boolean;
  vaultSelected: boolean;
  setup: (username: string, password: string) => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [authConfigured, setAuthConfigured] = useState(false);
  const [vaultSelected, setVaultSelected] = useState(false);

  useEffect(() => {
    checkAuth();
  }, []);

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
    }
  };

  const setup = async (username: string, password: string) => {
    await apiClient.setup(username, password);
    await checkAuth();
  };

  const login = async (username: string, password: string) => {
    await apiClient.login(username, password);
    await checkAuth();
  };

  const logout = async () => {
    await apiClient.logout();
    setIsAuthenticated(false);
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        isLoading,
        user,
        authConfigured,
        vaultSelected,
        setup,
        login,
        logout,
        refreshAuth: checkAuth,
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
