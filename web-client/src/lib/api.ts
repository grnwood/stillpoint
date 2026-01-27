export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export interface AuthTokens {
  access_token: string;
  refresh_token?: string;
}

type VaultRefreshRecord = {
  refresh_token: string;
  updated_at: number;
};

type VaultRefreshStore = Record<string, VaultRefreshRecord>;

const ACTIVE_VAULT_KEY = `stillpoint.activeVault.${encodeURIComponent(API_BASE_URL)}`;
const REMEMBER_ME_KEY = `stillpoint.rememberMe.${encodeURIComponent(API_BASE_URL)}`;
const VAULT_REFRESH_TOKENS_KEY = `stillpoint.vaultRefreshTokens.${encodeURIComponent(API_BASE_URL)}`;

export class APIError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message || `HTTP ${status}`);
    this.name = 'APIError';
    this.status = status;
    this.detail = detail;
  }
}

class APIClient {
  private accessToken: string | null = null;
  private refreshToken: string | null = null;
  private serverPasswordHash: string | null = null;
  private activeVaultPath: string | null = null;
  private rememberMe: boolean;

  constructor() {
    const rememberRaw = localStorage.getItem(REMEMBER_ME_KEY);
    this.rememberMe = rememberRaw === 'true';
    this.activeVaultPath = localStorage.getItem(ACTIVE_VAULT_KEY);
    if (this.rememberMe) {
      this.accessToken = localStorage.getItem('access_token');
      this.refreshToken = localStorage.getItem('refresh_token');
      if (this.activeVaultPath) {
        this.refreshToken = this.getStoredRefreshToken(this.activeVaultPath) || this.refreshToken;
      }
    }
    this.serverPasswordHash = sessionStorage.getItem('server_password_hash');
  }

  setTokens(tokens: AuthTokens) {
    this.accessToken = tokens.access_token;
    if (tokens.refresh_token) {
      this.refreshToken = tokens.refresh_token;
      if (this.rememberMe && this.activeVaultPath) {
        this.setStoredRefreshToken(this.activeVaultPath, tokens.refresh_token);
      }
    }
    if (this.rememberMe) {
      localStorage.setItem('access_token', tokens.access_token);
      if (tokens.refresh_token) {
        localStorage.setItem('refresh_token', tokens.refresh_token);
      }
    } else {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
    }
  }

  getActiveVaultPath() {
    return this.activeVaultPath;
  }

  isRemembered() {
    return this.rememberMe;
  }

  setRemembered(remember: boolean) {
    this.rememberMe = remember;
    localStorage.setItem(REMEMBER_ME_KEY, remember ? 'true' : 'false');
    if (!remember) {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      this.clearStoredRefreshTokens();
    }
  }

  clearActiveVaultPath() {
    this.activeVaultPath = null;
    this.refreshToken = null;
    localStorage.removeItem(ACTIVE_VAULT_KEY);
  }

  setActiveVaultPath(path: string) {
    this.activeVaultPath = path;
    localStorage.setItem(ACTIVE_VAULT_KEY, path);
    if (this.rememberMe) {
      this.refreshToken = this.getStoredRefreshToken(path);
    }
  }

  async setServerPassword(password: string): Promise<void> {
    // Hash the password using SHA-256
    const encoder = new TextEncoder();
    const data = encoder.encode(password);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
    this.serverPasswordHash = hashHex;
    sessionStorage.setItem('server_password_hash', hashHex);
    console.log('[API] Server password hash set:', hashHex.substring(0, 16) + '...');
  }

  clearServerPassword() {
    this.serverPasswordHash = null;
    sessionStorage.removeItem('server_password_hash');
  }

  clearTokens() {
    this.accessToken = null;
    this.refreshToken = null;
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  }

  clearActiveVaultTokens() {
    if (!this.activeVaultPath) return;
    this.clearStoredRefreshToken(this.activeVaultPath);
  }

  hasStoredVaultSession(path: string) {
    if (!this.rememberMe) return false;
    return Boolean(this.getStoredRefreshToken(path));
  }

  listStoredVaultSessions() {
    if (!this.rememberMe) return [];
    return Object.keys(this.loadVaultRefreshStore());
  }

  async checkServerReachable() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/health`, {
        headers: this.buildServerHeaders(),
      });
      return response.ok;
    } catch (error) {
      return false;
    }
  }

  async refreshStoredVaultSession(path: string, updateGlobal: boolean = false): Promise<boolean> {
    if (!this.rememberMe) return false;
    const storedRefresh = this.getStoredRefreshToken(path);
    if (!storedRefresh) return false;
    try {
      const tokens = await this.refreshWithToken(storedRefresh);
      if (!tokens) {
        this.clearStoredRefreshToken(path);
        return false;
      }
      if (tokens.refresh_token) {
        this.setStoredRefreshToken(path, tokens.refresh_token);
      }
      if (updateGlobal) {
        this.refreshToken = tokens.refresh_token || storedRefresh;
        this.setActiveVaultPath(path);
        this.setTokens({
          ...tokens,
          refresh_token: tokens.refresh_token || storedRefresh,
        });
      }
      return true;
    } catch (error) {
      if (error instanceof TypeError) {
        throw error;
      }
    }
    return false;
  }

  async applyStoredVaultSession(path: string): Promise<boolean> {
    this.setActiveVaultPath(path);
    return this.refreshStoredVaultSession(path, true);
  }

  async getVaultSessionStatus(path: string): Promise<'active' | 'inactive' | 'unreachable'> {
    return this.hasStoredVaultSession(path) ? 'active' : 'inactive';
  }

  private loadVaultRefreshStore(): VaultRefreshStore {
    const raw = localStorage.getItem(VAULT_REFRESH_TOKENS_KEY);
    if (!raw) return {};
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        return parsed as VaultRefreshStore;
      }
      return {};
    } catch (error) {
      return {};
    }
  }

  private saveVaultRefreshStore(store: VaultRefreshStore) {
    localStorage.setItem(VAULT_REFRESH_TOKENS_KEY, JSON.stringify(store));
  }

  private getStoredRefreshToken(path: string): string | null {
    const store = this.loadVaultRefreshStore();
    return store[path]?.refresh_token || null;
  }

  private setStoredRefreshToken(path: string, token: string) {
    const store = this.loadVaultRefreshStore();
    store[path] = { refresh_token: token, updated_at: Date.now() };
    this.saveVaultRefreshStore(store);
  }

  private clearStoredRefreshToken(path: string) {
    const store = this.loadVaultRefreshStore();
    if (store[path]) {
      delete store[path];
      this.saveVaultRefreshStore(store);
    }
  }

  private clearStoredRefreshTokens() {
    localStorage.removeItem(VAULT_REFRESH_TOKENS_KEY);
  }

  private buildServerHeaders(): HeadersInit {
    const headers = new Headers();
    if (this.serverPasswordHash) {
      headers.set('X-Server-Admin-Password', this.serverPasswordHash);
    }
    return headers;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`;
    const isFormData = options.body instanceof FormData;
    const headers = new Headers(options.headers || {});
    if (!headers.has('Content-Type') && !isFormData) {
      headers.set('Content-Type', 'application/json');
    }

    if (this.accessToken) {
      headers.set('Authorization', `Bearer ${this.accessToken}`);
    }

    // Add server admin password header if available
    if (this.serverPasswordHash) {
      headers.set('X-Server-Admin-Password', this.serverPasswordHash);
      console.log('[API] Sending server password hash:', this.serverPasswordHash.substring(0, 16) + '...');
    }

    const response = await fetch(url, {
      ...options,
      headers,
      credentials: 'include',
    });

    // Handle token refresh on 401
    if (response.status === 401 && endpoint !== '/auth/login' && endpoint !== '/auth/setup' && endpoint !== '/auth/refresh') {
      const refreshed = await this.refreshAccessToken();
      if (refreshed) {
        // Retry the original request
        headers.set('Authorization', `Bearer ${this.accessToken}`);
        const retryResponse = await fetch(url, { ...options, headers, credentials: 'include' });
        if (!retryResponse.ok) {
          throw new Error(`HTTP ${retryResponse.status}: ${retryResponse.statusText}`);
        }
        return retryResponse.json();
      }
    }

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      const detail = (error as { detail?: unknown }).detail;
      const message = typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      throw new APIError(response.status, detail, message);
    }

    return response.json();
  }

  async refreshAccessToken(): Promise<boolean> {
    if (!this.refreshToken && this.activeVaultPath && this.rememberMe) {
      this.refreshToken = this.getStoredRefreshToken(this.activeVaultPath);
    }
    if (!this.refreshToken) {
      return false;
    }
    try {
      const tokens = await this.refreshWithToken(this.refreshToken);
      if (tokens) {
        this.setTokens({
          ...tokens,
          refresh_token: tokens.refresh_token || this.refreshToken || undefined,
        });
        return true;
      }
    } catch (error) {
      console.error('Token refresh failed:', error);
    }

    this.clearTokens();
    return false;
  }

  private async refreshWithToken(refreshToken: string): Promise<AuthTokens | null> {
    const headers: HeadersInit = {
      'Authorization': `Bearer ${refreshToken}`,
    };
    const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers,
      credentials: 'include',
    });

    if (response.ok) {
      const tokens: AuthTokens = await response.json();
      return tokens;
    }
    return null;
  }

  // Auth endpoints
  async authStatus() {
    return this.request<{ configured: boolean; enabled: boolean; vault_selected: boolean }>('/auth/status');
  }

  async setup(username: string, password: string) {
    const tokens = await this.request<AuthTokens>('/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ username, password, remember: this.rememberMe }),
    });
    this.setTokens(tokens);
    return tokens;
  }

  async login(username: string, password: string) {
    const tokens = await this.request<AuthTokens>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, remember: this.rememberMe }),
    });
    this.setTokens(tokens);
    return tokens;
  }

  async logout() {
    await this.request('/auth/logout', { method: 'POST' });
    this.clearTokens();
    this.clearActiveVaultTokens();
  }

  async me() {
    return this.request<{ username: string; is_admin: boolean }>('/auth/me');
  }

  // Vault endpoints
  async listVaults() {
    return this.request<{ root: string; vaults: Array<{ name: string; path: string }> }>('/api/vaults');
  }

  async createVault(name: string) {
    return this.request<{ ok: boolean; name: string; path: string }>('/api/vaults/create', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  }

  async selectVault(path: string) {
    const response = await this.request<{ root: string }>('/api/vault/select', {
      method: 'POST',
      body: JSON.stringify({ path }),
    });
    this.setActiveVaultPath(path);
    return response;
  }

  async unselectVault() {
    return this.request<{ ok: boolean }>('/api/vault/unselect', {
      method: 'POST',
    });
  }

  async getTree(path: string = '/', recursive: boolean = true) {
    return this.request<{ tree: any[]; version: number }>(
      `/api/vault/tree?path=${encodeURIComponent(path)}&recursive=${recursive}`
    );
  }

  // Page endpoints
  async readPage(path: string) {
    return this.request<{ content: string; rev?: number; mtime_ns?: number }>('/api/file/read', {
      method: 'POST',
      body: JSON.stringify({ path }),
    });
  }

  async writePage(path: string, content: string, ifMatch?: number | string) {
    const headers: HeadersInit = {};
    if (ifMatch !== undefined) {
      headers['If-Match'] = String(ifMatch);
    }

    return this.request<{ ok: boolean; rev?: number; mtime_ns?: number }>('/api/file/write', {
      method: 'POST',
      headers,
      body: JSON.stringify({ path, content }),
    });
  }

  async listAttachments(pagePath: string) {
    const url = `/files/?page_path=${encodeURIComponent(pagePath)}`;
    return this.request<{ attachments: Array<{ attachment_path: string; stored_path: string; updated: number }> }>(url);
  }

  async attachFiles(pagePath: string, files: File[]) {
    const formData = new FormData();
    formData.append('page_path', pagePath);
    for (const file of files) {
      formData.append('files', file);
    }
    return this.request<{ ok: boolean; page: string; attachments: string[] }>('/files/attach', {
      method: 'POST',
      body: formData,
    });
  }

  // Sync endpoints
  async syncChanges(sinceRev: number = 0) {
    return this.request<{
      sync_revision: number;
      changes: Array<{
        page_id: string;
        path: string;
        title: string;
        updated: number;
        rev: number;
        deleted: boolean;
        pinned: boolean;
      }>;
      has_more: boolean;
    }>(`/sync/changes?since_rev=${sinceRev}`);
  }

  async getRecent(limit: number = 20) {
    return this.request<{
      pages: Array<{
        page_id: string;
        path: string;
        title: string;
        updated: number;
        rev: number;
      }>;
    }>(`/recent?limit=${limit}`);
  }

  async getTags() {
    return this.request<{ tags: Array<{ tag: string; count: number }> }>('/tags');
  }

  // Search
  async search(query: string, subtree?: string, limit: number = 50) {
    let url = `/api/search?q=${encodeURIComponent(query)}&limit=${limit}`;
    if (subtree) {
      url += `&subtree=${encodeURIComponent(subtree)}`;
    }
    return this.request<{ results: any[] }>(url);
  }

  // Tasks
  async getTasks(query?: string, tags?: string[], status?: string) {
    let url = '/api/tasks?';
    if (query) url += `query=${encodeURIComponent(query)}&`;
    if (tags) url += tags.map(t => `tags=${encodeURIComponent(t)}`).join('&') + '&';
    if (status) url += `status=${status}`;
    return this.request<{ tasks: any[] }>(url);
  }
}

export const apiClient = new APIClient();
