import type {
  Feed,
  Preferences,
  SearchHit,
  Watchlist,
} from './types';

const USER_KEY_STORAGE = 'marketdiff.userKey';

/**
 * Identity is a random key generated in the browser and kept in localStorage.
 * No account, no email, no password — a watchlist does not need one, and not
 * collecting the data is the only way to be sure of not leaking it.
 */
export function getUserKey(): string {
  if (typeof window === 'undefined') return '';
  let key = window.localStorage.getItem(USER_KEY_STORAGE);
  if (!key) {
    key =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `u-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
    window.localStorage.setItem(USER_KEY_STORAGE, key);
  }
  return key;
}

export class ApiError extends Error {
  code: string;
  status: number;
  offline: boolean;

  constructor(message: string, code: string, status: number, offline = false) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.offline = offline;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = 15000 } = options;

  // Every request is bounded. A hung backend should surface as a readable
  // message, not a spinner that never resolves.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort);

  try {
    const response = await fetch(path, {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-User-Key': getUserKey(),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: 'no-store',
    });

    if (response.status === 204) return undefined as T;

    const text = await response.text();
    const payload = text ? safeParse(text) : null;

    if (!response.ok) {
      const err = payload?.error;
      throw new ApiError(
        err?.message || 'Something went wrong. Try again in a moment.',
        err?.code || 'unknown',
        response.status,
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error).name === 'AbortError') {
      if (signal?.aborted) throw error;
      throw new ApiError(
        'The server took too long to respond.',
        'timeout',
        504,
        true,
      );
    }
    throw new ApiError(
      'Cannot reach the server. Check your connection and try again.',
      'network',
      0,
      true,
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

function safeParse(text: string): any {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const api = {
  listWatchlists: (signal?: AbortSignal) =>
    request<Watchlist[]>('/api/watchlists', { signal }),

  createWatchlist: (name: string, symbols: string[] = []) =>
    request<Watchlist>('/api/watchlists', { method: 'POST', body: { name, symbols } }),

  renameWatchlist: (id: number, name: string) =>
    request<Watchlist>(`/api/watchlists/${id}`, { method: 'PATCH', body: { name } }),

  deleteWatchlist: (id: number) =>
    request<void>(`/api/watchlists/${id}`, { method: 'DELETE' }),

  addStock: (id: number, symbol: string) =>
    request<{ symbol: string; name: string }>(`/api/watchlists/${id}/stocks`, {
      method: 'POST',
      body: { symbol },
    }),

  removeStock: (id: number, symbol: string) =>
    request<void>(`/api/watchlists/${id}/stocks/${encodeURIComponent(symbol)}`, {
      method: 'DELETE',
    }),

  feed: (id: number, opts: { force?: boolean; source?: 'yahoo' | 'demo'; signal?: AbortSignal } = {}) =>
    request<Feed>(
      `/api/watchlists/${id}/feed?refresh=true${opts.force ? '&force=true' : ''}${opts.source ? `&source=${opts.source}` : ''}`,
      { signal: opts.signal, timeoutMs: 20000 },
    ),

  /**
   * Acknowledge the feed the user actually saw. Sending the rendered timestamp
   * rather than "now" is what makes a double click, a retry, and a second tab
   * converge on one checkpoint instead of racing.
   */
  checkpoint: (id: number, seenThrough: string) =>
    request<{ checkpointAt: string; checkpointVersion: number; advanced: boolean }>(
      `/api/watchlists/${id}/checkpoint`,
      { method: 'POST', body: { seenThrough } },
    ),

  search: (q: string, watchlistId?: number, signal?: AbortSignal) =>
    request<SearchHit[]>(
      `/api/stocks/search?q=${encodeURIComponent(q)}${watchlistId ? `&watchlistId=${watchlistId}` : ''}`,
      { signal, timeoutMs: 8000 },
    ),

  preferences: () => request<Preferences>('/api/preferences'),

  updatePreferences: (body: Partial<Preferences>) =>
    request<Preferences>('/api/preferences', { method: 'PATCH', body }),

  prepareDemo: (watchlistId: number, checkpointDaysAgo = 2) =>
    request<{ snapshotsWritten: number }>('/api/demo/prepare', {
      method: 'POST',
      body: { watchlist_id: watchlistId, days: 8, checkpoint_days_ago: checkpointDaysAgo },
      timeoutMs: 30000,
    }),

  health: () =>
    request<{ status: string; provider: { mode: string; configured: string } }>(
      '/api/health',
    ),
};
