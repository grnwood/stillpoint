export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

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

  constructor() {
    // Load tokens from localStorage
    this.accessToken = localStorage.getItem('access_token');
    this.refreshToken = localStorage.getItem('refresh_token');
  }

  setTokens(tokens: AuthTokens) {
    this.accessToken = tokens.access_token;
    this.refreshToken = tokens.refresh_token;
    localStorage.setItem('access_token', tokens.access_token);
    localStorage.setItem('refresh_token', tokens.refresh_token);
  }

  clearTokens() {
    this.accessToken = null;
    this.refreshToken = null;
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
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

    const response = await fetch(url, {
      ...options,
      headers,
    });

    // Handle token refresh on 401
    if (response.status === 401 && this.refreshToken) {
      const refreshed = await this.refreshAccessToken();
      if (refreshed) {
        // Retry the original request
        headers.set('Authorization', `Bearer ${this.accessToken}`);
        const retryResponse = await fetch(url, { ...options, headers });
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
    if (!this.refreshToken) return false;

    try {
      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.refreshToken}`,
        },
      });

      if (response.ok) {
        const tokens: AuthTokens = await response.json();
        this.setTokens(tokens);
        return true;
      }
    } catch (error) {
      console.error('Token refresh failed:', error);
    }

    this.clearTokens();
    return false;
  }

  // Auth endpoints
  async authStatus() {
    return this.request<{ configured: boolean; enabled: boolean; vault_selected: boolean }>('/auth/status');
  }

  async setup(username: string, password: string) {
    const tokens = await this.request<AuthTokens>('/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    this.setTokens(tokens);
    return tokens;
  }

  async login(username: string, password: string) {
    const tokens = await this.request<AuthTokens>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    this.setTokens(tokens);
    return tokens;
  }

  async logout() {
    await this.request('/auth/logout', { method: 'POST' });
    this.clearTokens();
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
    return this.request<{ root: string }>('/api/vault/select', {
      method: 'POST',
      body: JSON.stringify({ path }),
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
