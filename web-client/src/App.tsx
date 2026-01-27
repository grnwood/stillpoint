import { useEffect, useState } from 'react';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { HomePage } from './pages/HomePage';
import './App.css';

function AppContent() {
  const { isAuthenticated, isLoading, logout, switchVault, vaultAuthStatus } = useAuth();
  const [syncStarted, setSyncStarted] = useState(false);

  useEffect(() => {
    if (isAuthenticated && !syncStarted) {
      // Start sync when authenticated
      setSyncStarted(true);
      import('./lib/sync').then(({ syncManager }) => {
        syncManager.startSync().catch((err) => {
          console.error('Sync start failed:', err);
        });
      });
    }
  }, [isAuthenticated, syncStarted]);

  if (isLoading) {
    return (
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center', 
        minHeight: '100vh' 
      }}>
        Loading...
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div>
      <HomePage
        headerLeft={<div style={{ fontWeight: 'bold' }}>StillPoint</div>}
        vaultAuthStatus={vaultAuthStatus}
        onLogout={logout}
        onSwitchVault={switchVault}
      />
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;
