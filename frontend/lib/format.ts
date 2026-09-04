import type { FreshnessState, Severity } from './types';

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 2,
});

export function money(value: number | null): string {
  if (value === null || Number.isNaN(value)) return '—';
  return inr.format(value);
}

export function signedPct(value: number | null): string {
  if (value === null || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

/** Direction is spelled out as well as drawn, so colour is never the only cue. */
export function direction(value: number | null): {
  glyph: string;
  word: string;
  tone: 'up' | 'down' | 'flat';
} {
  if (value === null || Math.abs(value) < 0.005) {
    return { glyph: '•', word: 'unchanged', tone: 'flat' };
  }
  return value > 0
    ? { glyph: '▲', word: 'up', tone: 'up' }
    : { glyph: '▼', word: 'down', tone: 'down' };
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  high: 'Needs attention',
  meaningful: 'Meaningful change',
  watch: 'Worth watching',
  normal: 'Normal',
};

export const SEVERITY_SHORT: Record<Severity, string> = {
  high: 'High',
  meaningful: 'Meaningful',
  watch: 'Watch',
  normal: 'Normal',
};

/** The diff gutter marker, borrowed from a unified diff. */
export function gutterMark(severity: Severity, status: string): string {
  if (status === 'no_data') return '?';
  if (status === 'new') return '+';
  if (severity === 'high') return '!';
  if (severity === 'meaningful') return '~';
  if (severity === 'watch') return '·';
  return ' ';
}

export const FRESHNESS_LABEL: Record<FreshnessState, string> = {
  live: 'Live',
  recent: 'Recent',
  stale: 'Stale',
  unavailable: 'Unavailable',
};

export function confidenceWord(confidence: number): string {
  if (confidence >= 0.85) return 'High confidence';
  if (confidence >= 0.6) return 'Moderate confidence';
  if (confidence >= 0.35) return 'Low confidence';
  return 'Very low confidence';
}

export function formatMoment(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function formatDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' });
}

/** "3 things changed" — the headline sentence, assembled from real counts. */
export function headline(counts: Record<string, number>, awayLabel: string | null): string {
  const changed = counts.changed ?? 0;
  const when = awayLabel ? ` since you last checked, ${awayLabel} ago` : '';
  if (changed === 0) return `Nothing needs your attention${when}.`;
  if (changed === 1) return `One thing changed${when}.`;
  return `${changed} things changed${when}.`;
}
