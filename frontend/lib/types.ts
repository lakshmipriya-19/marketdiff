export type Severity = 'high' | 'meaningful' | 'watch' | 'normal';
export type FreshnessState = 'live' | 'recent' | 'stale' | 'unavailable';
export type ItemStatus = 'changed' | 'unchanged' | 'new' | 'no_data';

export interface Watchlist {
  id: number;
  name: string;
  stockCount: number;
  checkpointAt: string | null;
  checkpointVersion: number;
  lastOpenedAt: string | null;
  createdAt: string;
}

export interface Reason {
  code: string;
  text: string;
}

export interface FeedItem {
  symbol: string;
  name: string;
  status: ItemStatus;
  price: number | null;
  previousPrice: number | null;
  priceChangePct: number | null;
  priceChangeAbs: number | null;
  attentionScore: number;
  severity: Severity;
  confidence: number;
  reasons: Reason[];
  plainSummary: string;
  freshness: FreshnessState;
  freshnessLabel: string;
  ageSeconds: number | null;
  volumeRatio: number | null;
  typicalMovePct: number | null;
  source: string | null;
  isDemo: boolean;
  sourceDisagreementPct: number | null;
  severityCapped: boolean;
  observedAt: string | null;
  components: Record<string, number>;
  baselineObservedAt: string | null;
  sparkline: number[];
}

export interface DayMover {
  symbol: string;
  priceChangePct: number;
  attentionScore: number;
  severity: Severity;
  headline: string;
}

export interface DaySummary {
  day: string;
  high: number;
  meaningful: number;
  watch: number;
  quiet: number;
  changed: number;
  hasData: boolean;
  movers: DayMover[];
}

export interface FeedData {
  mode: string;
  source: string;
  isDemo: boolean;
  degraded: boolean;
  error: string | null;
  errorKind: string | null;
  missing: string[];
  fetched: number;
  skippedFresh: number;
}

export interface Feed {
  watchlist: Watchlist;
  items: FeedItem[];
  counts: Record<string, number>;
  checkpointAt: string | null;
  generatedAt: string;
  awaySeconds: number | null;
  awayLabel: string | null;
  sessionsElapsed: number;
  timeline: DaySummary[];
  data: FeedData;
}

export interface SearchHit {
  symbol: string;
  name: string;
  exchange: string;
  inWatchlist: boolean;
}

export interface Preferences {
  simpleMode: boolean;
  attentionThreshold: number;
}
