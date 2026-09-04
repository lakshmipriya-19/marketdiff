'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import type { SearchHit, Watchlist } from '@/lib/types';
import { SourceIndicator } from './SourceIndicator';
import { Button } from './ui';

export function AddStock({
  watchlistId,
  onAdded,
  autoFocus,
}: {
  watchlistId: number;
  onAdded: () => void;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  // Debounced, and every in-flight search is cancelled by the next keystroke so
  // a slow response cannot overwrite results for a newer query.
  useEffect(() => {
    const term = query.trim();
    if (term.length < 1) {
      setHits([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        setHits(await api.search(term, watchlistId, controller.signal));
        setActiveIndex(-1);
      } catch (error) {
        if (!controller.signal.aborted) setHits([]);
      }
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, watchlistId]);

  async function add(symbol: string) {
    setBusy(true);
    setMessage(null);
    try {
      await api.addStock(watchlistId, symbol);
      setQuery('');
      setHits([]);
      setMessage(`${symbol} added.`);
      onAdded();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Could not add that stock.');
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, hits.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, -1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const chosen = activeIndex >= 0 ? hits[activeIndex] : hits[0];
      if (chosen && !chosen.inWatchlist) void add(chosen.symbol);
      else if (!chosen && query.trim()) void add(query.trim().toUpperCase());
    } else if (event.key === 'Escape') {
      setQuery('');
      setHits([]);
    }
  }

  return (
    <div className="relative">
      <label htmlFor={`${listId}-input`} className="sr-only">
        Search for a stock to add
      </label>
      <input
        id={`${listId}-input`}
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={hits.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        autoComplete="off"
        value={query}
        disabled={busy}
        placeholder="Add a stock — try RELIANCE"
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        className="w-full border border-rule-strong bg-surface px-3 py-1.5 text-sm placeholder:text-ink-faint sm:w-72"
      />

      {hits.length > 0 ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto border border-rule-strong bg-surface shadow-sm sm:w-72"
        >
          {hits.map((hit, index) => (
            <li
              key={hit.symbol}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              aria-disabled={hit.inWatchlist}
            >
              <button
                type="button"
                disabled={hit.inWatchlist || busy}
                onClick={() => add(hit.symbol)}
                className={`flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left text-sm ${
                  index === activeIndex ? 'bg-paper' : ''
                } ${hit.inWatchlist ? 'cursor-not-allowed text-ink-faint' : 'hover:bg-paper'}`}
              >
                <span className="font-medium">{hit.symbol}</span>
                <span className="min-w-0 flex-1 truncate text-ink-soft">{hit.name}</span>
                {hit.inWatchlist ? <span className="text-xs">on this list</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <p role="status" aria-live="polite" className="sr-only">
        {message}
      </p>
      {message ? <p className="mt-1 text-xs text-ink-soft">{message}</p> : null}
    </div>
  );
}

export function Header({
  watchlists,
  active,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  simpleMode,
  onToggleSimple,
  onRefresh,
  refreshing,
  onAdded,
  source,
  onSourceChange,
}: {
  watchlists: Watchlist[];
  active: Watchlist | null;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onRename: () => void;
  onDelete: () => void;
  simpleMode: boolean;
  onToggleSimple: () => void;
  onRefresh: () => void;
  refreshing: boolean;
  onAdded: () => void;
  source: 'yahoo' | 'demo';
  onSourceChange: (source: 'yahoo' | 'demo') => void;
}) {
  return (
    <header className="border-b border-rule">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-xl tracking-tight">MarketDiff</h1>
          <p className="hidden text-sm text-ink-soft sm:block">
            What changed since you last looked
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <SourceIndicator source={source} onChange={onSourceChange} />
          {watchlists.length > 0 && active ? (
            <>
              <label htmlFor="watchlist-select" className="sr-only">
                Choose a watchlist
              </label>
              <select
                id="watchlist-select"
                value={active.id}
                onChange={(e) => onSelect(Number(e.target.value))}
                className="border border-rule-strong bg-surface px-2 py-1.5 text-sm"
              >
                {watchlists.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name} ({w.stockCount})
                  </option>
                ))}
              </select>
              <Button onClick={onRename}>Rename</Button>
              <Button onClick={onDelete}>Delete</Button>
            </>
          ) : null}
          <Button onClick={onCreate}>New list</Button>
          <Button onClick={onRefresh} disabled={refreshing} aria-keyshortcuts="r">
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </Button>
          <Button
            onClick={onToggleSimple}
            aria-pressed={simpleMode}
            aria-keyshortcuts="s"
            variant={simpleMode ? 'primary' : 'secondary'}
          >
            Simple mode
          </Button>
        </div>
      </div>

      {active ? (
        <div className="mx-auto max-w-5xl px-4 pb-4 sm:px-6">
          <AddStock watchlistId={active.id} onAdded={onAdded} />
        </div>
      ) : null}
    </header>
  );
}
