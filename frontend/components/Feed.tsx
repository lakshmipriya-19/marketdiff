'use client';

import { useState } from 'react';
import type { FeedItem } from '@/lib/types';
import {
  confidenceWord,
  direction,
  gutterMark,
  money,
  signedPct,
  SEVERITY_LABEL,
} from '@/lib/format';
import { Button, FreshnessTag, ScoreMeter, SeverityLabel } from './ui';

const TONE_CLASS = {
  up: 'text-accent',
  down: 'text-high',
  flat: 'text-ink-soft',
} as const;

function PriceBlock({ item }: { item: FeedItem }) {
  const dir = direction(item.priceChangePct);
  return (
    <div className="text-right">
      <div className="numeral text-base font-medium">
        {item.freshness === 'unavailable' ? '—' : money(item.price)}
      </div>
      {item.priceChangePct !== null ? (
        <div className={`numeral text-sm ${TONE_CLASS[dir.tone]}`}>
          <span aria-hidden="true">{dir.glyph} </span>
          {signedPct(item.priceChangePct)}
          <span className="sr-only"> {dir.word} since your last check</span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * A small trace of observations already stored for this exact diff window.
 *
 * The diff metaphor is drawn, not just described: a hollow ring marks the
 * checkpoint (before), a dashed rule holds that level steady across the
 * width, and a filled dot marks now (after) — so "how far from before" reads
 * at a glance even before the row is expanded.
 */
function Sparkline({ item }: { item: FeedItem }) {
  // A backend restart or a cached feed can briefly carry the pre-sparkline
  // shape. Treat that exactly like insufficient history: render nothing.
  const values = item.sparkline ?? [];
  if (values.length < 3) return null;

  const width = 88;
  const height = 28;
  const inset = 3;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const toXY = (value: number, index: number) => {
    const x = inset + (index / (values.length - 1)) * (width - inset * 2);
    const y = height - inset - ((value - low) / span) * (height - inset * 2);
    return [x, y] as const;
  };
  const points = values.map((v, i) => toXY(v, i).map((n) => n.toFixed(1)).join(',')).join(' ');
  const [startX, startY] = toXY(values[0], 0);
  const [endX, endY] = toXY(values[values.length - 1], values.length - 1);
  const tone = direction(item.priceChangePct).tone;
  const stroke = tone === 'up' ? 'var(--accent)' : tone === 'down' ? 'var(--high)' : 'var(--watch)';

  return (
    <svg
      role="img"
      aria-label={`${item.symbol} price movement, checkpoint to now`}
      viewBox={`0 0 ${width} ${height}`}
      className="h-7 w-[88px] flex-none"
    >
      <line
        x1={inset}
        y1={startY}
        x2={width - inset}
        y2={startY}
        stroke="var(--ink-faint)"
        strokeWidth="1"
        strokeDasharray="1.5 2"
        opacity={0.6}
      />
      <polyline fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" points={points} />
      <circle cx={startX} cy={startY} r="1.8" fill="var(--surface)" stroke="var(--ink-faint)" strokeWidth="1" />
      <circle cx={endX} cy={endY} r="2" fill={stroke} />
    </svg>
  );
}

const COMPONENT_WEIGHTS = {
  move: 45,
  volume: 25,
  volatility: 15,
  gap: 15,
} as const;

const COMPONENT_NAMES = {
  move: 'Move vs its normal',
  volume: 'Volume',
  volatility: 'Volatility',
  gap: 'Opening gap',
} as const;

const COMPONENT_COLORS = {
  move: 'bg-ink',
  volume: 'bg-accent',
  volatility: 'bg-high',
  gap: 'bg-rule-strong',
} as const;

function ScoreBreakdown({ item }: { item: FeedItem }) {
  const components = item.components || {};
  const keys = Object.keys(COMPONENT_WEIGHTS) as (keyof typeof COMPONENT_WEIGHTS)[];
  
  return (
    <div className="space-y-2">
      {keys.map((key) => {
        const weight = COMPONENT_WEIGHTS[key];
        const value = components[key] ?? 0;
        const width = weight > 0 ? (value / weight) * 100 : 0;
        const colorClass = COMPONENT_COLORS[key];
        
        return (
          <div key={key}>
            <div className="flex items-center justify-between text-xs text-ink-soft mb-1">
              <span>{COMPONENT_NAMES[key]}</span>
              <span className="numeral text-ink">{value.toFixed(1)} / {weight}</span>
            </div>
            <div className="h-2 bg-rule-strong rounded-sm overflow-hidden">
              <div
                className={`h-full ${colorClass} transition-all`}
                style={{ width: `${Math.min(width, 100)}%` }}
                role="progressbar"
                aria-valuenow={value}
                aria-valuemin={0}
                aria-valuemax={weight}
                aria-label={`${COMPONENT_NAMES[key]}: ${value.toFixed(1)} out of ${weight}`}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ChangeRow({
  item,
  onRemove,
}: {
  item: FeedItem;
  onRemove?: (symbol: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailId = `detail-${item.symbol}`;

  return (
    <li className="settle border-t border-rule py-5 sm:py-6">
      <div className="flex gap-4 px-4 sm:px-5">
        <span
          aria-hidden="true"
          className={`numeral select-none flex-shrink-0 pt-0.5 text-sm font-medium ${
            item.severity === 'high' ? 'text-high' : item.severity === 'meaningful' ? 'text-meaningful' : 'text-ink-faint'
          }`}
        >
          {gutterMark(item.severity, item.status)}
        </span>

        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls={detailId}
          className="min-w-0 flex-1 text-left hover:bg-paper/30 -mx-3 rounded-sm px-3 py-1 transition-colors"
        >
          {/* Header row */}
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
            <div className="min-w-0">
              <h3 className="text-lg font-medium tracking-tight">
                {item.symbol}
                <span className="ml-2 text-sm font-normal text-ink-soft">
                  {item.name}
                </span>
              </h3>
            </div>
            <div className="flex flex-wrap items-baseline gap-3">
              <span className={`numeral text-xl font-medium ${
                item.severity === 'high' ? 'text-high' : 
                item.severity === 'meaningful' ? 'text-meaningful' : 
                'text-ink-soft'
              }`}>
                {item.attentionScore}
              </span>
              <Sparkline item={item} />
              <PriceBlock item={item} />
            </div>
          </div>

          {/* Primary reason */}
          {item.reasons.length > 0 && (
            <p className="mt-2 max-w-reading text-sm text-ink-soft">
              {item.reasons[0].text}
            </p>
          )}

          {/* Metadata row */}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
            <SeverityLabel severity={item.severity} capped={item.severityCapped} />
            <FreshnessTag state={item.freshness} label={item.freshnessLabel} />
            <span className="text-ink-faint">{confidenceWord(item.confidence)}</span>
            {item.reasons.length > 1 && (
              <span className="text-ink-faint">+{item.reasons.length - 1} more reason{item.reasons.length > 2 ? 's' : ''}</span>
            )}
          </div>
        </button>

        {onRemove ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove(item.symbol);
            }}
            className="flex-shrink-0 text-xs text-ink-faint hover:text-high transition-colors"
            title={`Remove ${item.symbol} from this watchlist`}
          >
            ✕
          </button>
        ) : null}
      </div>

      {expanded && (
        <div id={detailId} className="mt-6 border-t border-rule-faint px-4 pt-4 sm:px-5">
          <div className="space-y-6">
            {/* Score breakdown */}
            <div>
              <h4 className="mb-3 text-xs font-medium text-ink-faint uppercase tracking-wide">Score breakdown</h4>
              <ScoreBreakdown item={item} />
            </div>

            {/* Price history */}
            <div>
              <h4 className="mb-3 text-xs font-medium text-ink-faint uppercase tracking-wide">Price history</h4>
              <div className="space-y-1">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-ink-faint">Before</span>
                  <span className="numeral text-ink-soft">{money(item.previousPrice)}</span>
                </div>
                {item.baselineObservedAt && (
                  <div className="text-xs text-ink-faint">{new Date(item.baselineObservedAt).toLocaleString()}</div>
                )}
                <div className="my-2 border-t border-rule-faint" />
                <div className="flex items-center justify-between text-sm">
                  <span className="text-ink-faint">Now</span>
                  <span className="numeral text-ink-soft">{money(item.price)}</span>
                </div>
                {item.observedAt && (
                  <div className="text-xs text-ink-faint">{new Date(item.observedAt).toLocaleString()}</div>
                )}
              </div>
            </div>

            {/* All reasons */}
            {item.reasons.length > 0 && (
              <div>
                <h4 className="mb-3 text-xs font-medium text-ink-faint uppercase tracking-wide">Why this was flagged</h4>
                <ul className="space-y-2">
                  {item.reasons.map((reason) => (
                    <li key={reason.code} className="text-sm text-ink-soft">
                      {reason.text}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Additional metrics */}
            <div className="grid grid-cols-2 gap-4 text-xs sm:grid-cols-4">
              <div>
                <dt className="text-ink-faint">Typical daily move</dt>
                <dd className="numeral mt-1 text-sm font-medium text-ink-soft">
                  {item.typicalMovePct ? `±${item.typicalMovePct.toFixed(2)}%` : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-ink-faint">Volume ratio</dt>
                <dd className="numeral mt-1 text-sm font-medium text-ink-soft">
                  {item.volumeRatio ? `${item.volumeRatio.toFixed(1)}×` : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-ink-faint">Source</dt>
                <dd className="mt-1 text-sm font-medium text-ink-soft">{item.source ?? '—'}</dd>
              </div>
              {item.sourceDisagreementPct && (
                <div>
                  <dt className="text-ink-faint">Source disagreement</dt>
                  <dd className="numeral mt-1 text-sm font-medium text-high">±{item.sourceDisagreementPct.toFixed(2)}%</dd>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

function SectionHeading({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <h2 className="flex items-baseline gap-3 px-1 pb-2 pt-8 text-sm font-medium text-ink-soft">
      {children}
      {count !== undefined ? <span className="numeral text-ink-faint">{count}</span> : null}
    </h2>
  );
}

export function ChangeFeed({
  items,
  onRemove,
}: {
  items: FeedItem[];
  onRemove: (symbol: string) => void;
}) {
  const changed = items.filter((i) => i.status === 'changed');
  const quiet = items.filter((i) => i.status === 'unchanged');
  const fresh = items.filter((i) => i.status === 'new');
  const missing = items.filter((i) => i.status === 'no_data');

  return (
    <div>
      {changed.length > 0 ? (
        <>
          <SectionHeading>Worth your attention</SectionHeading>
          <ul className="border-b border-rule">
            {changed.map((item) => (
              <ChangeRow key={item.symbol} item={item} onRemove={onRemove} />
            ))}
          </ul>
        </>
      ) : null}

      {fresh.length > 0 ? (
        <>
          <SectionHeading count={fresh.length}>Added since your last check</SectionHeading>
          <p className="max-w-reading px-1 pb-3 text-sm text-ink-soft">
            There is no earlier reading to compare these against yet. They will appear in the feed
            from your next visit.
          </p>
          <ul className="border-b border-rule">
            {fresh.map((item) => (
              <ChangeRow key={item.symbol} item={item} onRemove={onRemove} />
            ))}
          </ul>
        </>
      ) : null}

      {missing.length > 0 ? (
        <>
          <SectionHeading count={missing.length}>No data received</SectionHeading>
          <ul className="border-b border-rule">
            {missing.map((item) => (
              <ChangeRow key={item.symbol} item={item} onRemove={onRemove} />
            ))}
          </ul>
        </>
      ) : null}

      {quiet.length > 0 ? <QuietList items={quiet} onRemove={onRemove} /> : null}
    </div>
  );
}

export function QuietList({
  items,
  onRemove,
}: {
  items: FeedItem[];
  onRemove: (symbol: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="pt-8">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="quiet-list"
        className="flex w-full items-baseline justify-between border-t border-rule px-1 py-3 text-left text-sm text-ink-soft hover:text-ink"
      >
        <span>
          {items.length} {items.length === 1 ? 'stock' : 'stocks'} had no meaningful change
        </span>
        <span aria-hidden="true">{open ? '−' : '+'}</span>
      </button>

      {open ? (
        <ul id="quiet-list" className="border-b border-rule">
          {items.map((item) => {
            const dir = direction(item.priceChangePct);
            return (
              <li
                key={item.symbol}
                className="flex items-baseline gap-4 border-t border-rule bg-surface px-4 py-2.5 text-sm sm:px-5"
              >
                <span className="w-24 font-medium">{item.symbol}</span>
                <span className="hidden flex-1 truncate text-ink-faint sm:block">{item.name}</span>
                <span className="text-xs text-ink-faint">{item.plainSummary}</span>
                <span className={`numeral w-20 text-right ${TONE_CLASS[dir.tone]}`}>
                  <span aria-hidden="true">{dir.glyph} </span>
                  {signedPct(item.priceChangePct)}
                </span>
                <span className="numeral w-24 text-right">{money(item.price)}</span>
                <button
                  type="button"
                  onClick={() => onRemove(item.symbol)}
                  className="text-xs text-ink-faint underline decoration-rule underline-offset-4 hover:text-high"
                >
                  <span className="sr-only">Remove {item.symbol} from this watchlist</span>
                  <span aria-hidden="true">×</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}

/**
 * Simple Mode.
 *
 * Not a smaller version of the same screen — a different answer to the same
 * question. No score, no confidence, no volume multiples, no charts: one line
 * per stock in plain words, and a sentence confirming everything else is fine.
 */
export function SimpleFeed({ items }: { items: FeedItem[] }) {
  const changed = items.filter((i) => i.status === 'changed');
  const missing = items.filter((i) => i.status === 'no_data');
  const rest = items.length - changed.length - missing.length;

  return (
    <div className="mt-6">
      {changed.length === 0 ? (
        <p className="font-display text-2xl">Nothing needs your attention right now.</p>
      ) : (
        <ul className="space-y-px">
          {changed.map((item) => {
            const dir = direction(item.priceChangePct);
            return (
              <li key={item.symbol} className="settle border-t border-rule bg-surface px-4 py-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-lg font-medium">
                    <span aria-hidden="true" className="mr-2">
                      {item.severity === 'high' ? '!' : item.severity === 'meaningful' ? '~' : '·'}
                    </span>
                    {item.symbol}
                  </span>
                  <span className={`numeral text-lg ${TONE_CLASS[dir.tone]}`}>
                    <span aria-hidden="true">{dir.glyph} </span>
                    {signedPct(item.priceChangePct)}
                    <span className="sr-only"> {dir.word}</span>
                  </span>
                </div>
                <p className="mt-1 text-base text-ink-soft">
                  {item.plainSummary}. Now {money(item.price)}.
                </p>
                {item.freshness === 'stale' ? (
                  <p className="mt-1 text-sm text-high">
                    This price may be out of date. {item.freshnessLabel}.
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {rest > 0 ? (
        <p className="mt-6 border-t border-rule pt-4 text-base text-ink-soft">
          Everything else looks normal ({rest} {rest === 1 ? 'stock' : 'stocks'}).
        </p>
      ) : null}
      {missing.length > 0 ? (
        <p className={`${rest > 0 ? 'mt-2' : 'mt-6 border-t border-rule pt-4'} text-base text-high`}>
          No data right now for {missing.map((i) => i.symbol).join(', ')} — not a price, just
          missing.
        </p>
      ) : null}
    </div>
  );
}

export function EmptyWatchlist({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="mt-8 border border-dashed border-rule-strong bg-surface px-6 py-12 text-center">
      <p className="font-display text-2xl">This watchlist is empty.</p>
      <p className="mx-auto mt-2 max-w-reading text-sm text-ink-soft">
        Add a few stocks and MarketDiff starts keeping track. From your next visit it will tell you
        what moved unusually, and leave the rest alone.
      </p>
      <div className="mt-5">
        <Button variant="primary" onClick={onAdd}>
          Add a stock
        </Button>
      </div>
    </div>
  );
}

export function NothingChanged({ total }: { total: number }) {
  return (
    <div className="mt-8 border border-rule bg-surface px-6 py-10">
      <p className="font-display text-2xl">Nothing meaningful changed.</p>
      <p className="mt-2 max-w-reading text-sm text-ink-soft">
        That&apos;s intentional — MarketDiff only surfaces changes that stand out. All {total}{' '}
        {total === 1 ? 'stock is moving within its normal range' : 'stocks are moving within their normal range'}{' '}
        since your last checkpoint. We will flag something here the moment that stops being true.
      </p>
    </div>
  );
}
