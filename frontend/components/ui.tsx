'use client';

import type { FreshnessState, Severity } from '@/lib/types';
import { FRESHNESS_LABEL, SEVERITY_SHORT } from '@/lib/format';

const SEVERITY_COLOR: Record<Severity, string> = {
  high: 'text-high',
  meaningful: 'text-meaningful',
  watch: 'text-watch',
  normal: 'text-ink-soft',
};

/**
 * The attention score, drawn as a filled rule.
 * The number is always present in text; the bar is a second, redundant channel
 * rather than the only one.
 */
export function ScoreMeter({ score, severity }: { score: number; severity: Severity }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`numeral text-sm font-medium ${SEVERITY_COLOR[severity]}`}>{score}</span>
      <span
        className={`relative block h-[3px] w-16 bg-rule ${SEVERITY_COLOR[severity]}`}
        aria-hidden="true"
      >
        <span
          className="absolute inset-y-0 left-0 bg-current"
          style={{ width: `${Math.max(score, 2)}%` }}
        />
      </span>
      <span className="sr-only">Attention score {score} out of 100</span>
    </div>
  );
}

export function FreshnessTag({
  state,
  label,
}: {
  state: FreshnessState;
  label: string;
}) {
  const dotted = state === 'stale' || state === 'unavailable';
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs ${
        dotted ? 'text-high' : 'text-ink-faint'
      }`}
      title={label}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-[7px] w-[7px] border border-current ${
          state === 'live' ? 'bg-current' : dotted ? 'border-dashed' : ''
        }`}
      />
      {FRESHNESS_LABEL[state]} · {label}
    </span>
  );
}

export function Banner({
  tone = 'info',
  title,
  children,
  action,
}: {
  tone?: 'info' | 'warning' | 'error';
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const border =
    tone === 'error'
      ? 'border-l-high'
      : tone === 'warning'
        ? 'border-l-meaningful'
        : 'border-l-accent';
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={`border border-rule ${border} border-l-[3px] bg-surface px-4 py-3`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="text-sm font-medium">{title}</p>
        {action}
      </div>
      {children ? <div className="mt-1 max-w-reading text-sm text-ink-soft">{children}</div> : null}
    </div>
  );
}

export function Button({
  children,
  variant = 'secondary',
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'quiet';
}) {
  const base =
    'inline-flex items-center gap-2 px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50';
  const styles = {
    primary: 'bg-ink text-paper hover:opacity-90',
    secondary: 'border border-rule-strong bg-surface hover:border-ink-soft',
    quiet: 'text-ink-soft hover:text-ink underline underline-offset-4 decoration-rule-strong',
  }[variant];
  return (
    <button className={`${base} ${styles}`} {...rest}>
      {children}
    </button>
  );
}

export function SeverityLabel({
  severity,
  capped,
}: {
  severity: Severity;
  capped?: boolean;
}) {
  return (
    <span className={`text-xs font-medium ${SEVERITY_COLOR[severity]}`}>
      {SEVERITY_SHORT[severity]}
      {capped ? ' (held back — thin data)' : ''}
    </span>
  );
}

export function FeedSkeleton() {
  return (
    <div aria-hidden="true" className="mt-6 space-y-px">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex gap-4 border-t border-rule bg-surface px-4 py-5">
          <div className="loading-rule h-3 w-3 bg-rule-strong" />
          <div className="flex-1 space-y-3">
            <div className="loading-rule h-3 w-32 bg-rule-strong" />
            <div className="loading-rule h-3 w-full max-w-md bg-rule" />
          </div>
          <div className="loading-rule h-3 w-16 bg-rule-strong" />
        </div>
      ))}
    </div>
  );
}
