'use client';

import { useState } from 'react';
import type { DaySummary } from '@/lib/types';
import { formatDay, money, signedPct } from '@/lib/format';
import { SeverityLabel } from './ui';

/**
 * The compact answer to "you have been away for a week".
 *
 * One row per trading session rather than a dump of every event, built only
 * from snapshots we actually stored — a day with no stored data says so instead
 * of implying nothing happened.
 */
export function Timeline({ days, awayLabel }: { days: DaySummary[]; awayLabel: string | null }) {
  const [expandedDays, setExpandedDays] = useState<Set<string>>(new Set());
  
  if (days.length === 0) return null;
  const busiest = Math.max(1, ...days.map((d) => d.changed));

  const toggleDay = (day: string) => {
    const newSet = new Set(expandedDays);
    if (newSet.has(day)) {
      newSet.delete(day);
    } else {
      newSet.add(day);
    }
    setExpandedDays(newSet);
  };

  return (
    <section aria-labelledby="timeline-heading" className="mt-8 border border-rule bg-surface">
      <div className="border-b border-rule px-4 py-3 sm:px-5">
        <h2 id="timeline-heading" className="text-sm font-medium">
          While you were away{awayLabel ? ` — ${awayLabel}` : ''}
        </h2>
        <p className="mt-1 max-w-reading text-sm text-ink-soft">
          A session-by-session count of what crossed the attention threshold, so a long absence
          does not turn into a wall of events.
        </p>
      </div>

      <ol className="divide-y divide-rule">
        {days.map((day) => {
          const isExpandable = day.changed > 0;
          const isExpanded = expandedDays.has(day.day);
          const detailId = `day-details-${day.day}`;

          return (
            <li key={day.day} className="divide-y divide-rule">
              {isExpandable ? (
                <button
                  onClick={() => toggleDay(day.day)}
                  aria-expanded={isExpanded}
                  aria-controls={detailId}
                  className="w-full flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3 sm:px-5 text-left hover:bg-rule-faint transition-colors"
                >
                  <span className="w-28 text-sm font-medium">{formatDay(day.day)}</span>

                  <span className="flex w-24 items-center gap-1" aria-hidden="true">
                    <span
                      className={`block h-[3px] ${day.high > 0 ? 'bg-high' : 'bg-meaningful'}`}
                      style={{ width: `${(day.changed / busiest) * 100}%` }}
                    />
                  </span>

                  <span className="text-sm text-ink-soft">
                    {`${day.changed} ${day.changed === 1 ? 'change' : 'changes'}`}
                  </span>

                  <span className="flex gap-2 text-xs text-ink-faint">
                    {day.high > 0 ? <span>{day.high} needs attention</span> : null}
                    {day.meaningful + day.watch > 0 ? <span>{day.meaningful + day.watch} watching</span> : null}
                  </span>

                  <span className="ml-auto flex flex-wrap gap-x-4 text-xs text-ink-faint">
                    {day.movers.map((mover) => (
                      <span key={mover.symbol} className="numeral">
                        {mover.symbol} {signedPct(mover.priceChangePct)}
                      </span>
                    ))}
                  </span>
                </button>
              ) : (
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3 sm:px-5">
                  <span className="w-28 text-sm font-medium">{formatDay(day.day)}</span>

                  <span className="flex w-24 items-center gap-1" aria-hidden="true">
                    {day.changed > 0 ? (
                      <span
                        className={`block h-[3px] ${day.high > 0 ? 'bg-high' : 'bg-meaningful'}`}
                        style={{ width: `${(day.changed / busiest) * 100}%` }}
                      />
                    ) : (
                      <span className="block h-[3px] w-3 bg-rule-strong" />
                    )}
                  </span>

                  <span className="text-sm text-ink-soft">
                    {!day.hasData
                      ? 'No data recorded'
                      : day.changed === 0
                      ? 'Nothing significant'
                      : `${day.changed} ${day.changed === 1 ? 'change' : 'changes'}`}
                  </span>
                </div>
              )}

              {isExpanded && isExpandable && (
                <div id={detailId} className="px-4 py-3 sm:px-5 bg-rule-faint">
                  <ul className="space-y-2">
                    {day.movers.map((mover) => (
                      <li key={mover.symbol} className="flex items-center justify-between text-sm">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="font-medium">{mover.symbol}</span>
                            <SeverityLabel severity={mover.severity} capped={false} />
                          </div>
                          <p className="text-xs text-ink-soft">{mover.headline}</p>
                        </div>
                        <span className="numeral ml-4 text-right text-ink-soft">
                          {signedPct(mover.priceChangePct)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
