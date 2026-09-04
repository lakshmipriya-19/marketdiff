'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import type { Feed, Watchlist } from '@/lib/types';
import { formatMoment, headline } from '@/lib/format';
import { Header } from '@/components/Header';
import { ChangeFeed, EmptyWatchlist, NothingChanged, SimpleFeed } from '@/components/Feed';
import { Timeline } from '@/components/Timeline';
import { Banner, Button, FeedSkeleton } from '@/components/ui';

const ACTIVE_LIST_KEY = 'marketdiff.activeWatchlist';
const SIMPLE_MODE_KEY = 'marketdiff.simpleMode';
const SOURCE_KEY = 'marketdiff.dataSource';
const AUTO_REFRESH_MS = 90_000;
const SAMPLE_SYMBOLS = ['RELIANCE', 'INFY', 'TCS', 'HDFCBANK', 'ITC', 'WIPRO'];

export default function Page() {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [simpleMode, setSimpleMode] = useState(false);
  const [marking, setMarking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [online, setOnline] = useState(true);
  const [filter, setFilter] = useState<'all' | 'high' | 'meaningful' | 'unchanged' | 'stale'>('all');
  const [source, setSource] = useState<'yahoo' | 'demo'>('yahoo');

  const inFlight = useRef<AbortController | null>(null);
  const channel = useRef<BroadcastChannel | null>(null);

  const active = watchlists.find((w) => w.id === activeId) ?? null;

  // --- bootstrap -------------------------------------------------------------

  useEffect(() => {
    setSimpleMode(window.localStorage.getItem(SIMPLE_MODE_KEY) === 'true');
    setSource(window.localStorage.getItem(SOURCE_KEY) === 'demo' ? 'demo' : 'yahoo');
    setOnline(navigator.onLine);

    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
    };
  }, []);

  const loadWatchlists = useCallback(async (preferId?: number) => {
    const lists = await api.listWatchlists();
    setWatchlists(lists);
    const stored = Number(window.localStorage.getItem(ACTIVE_LIST_KEY));
    const chosen =
      preferId ??
      (lists.some((l) => l.id === stored) ? stored : undefined) ??
      lists[0]?.id ??
      null;
    setActiveId(chosen);
    return chosen;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await loadWatchlists();
      } catch (err) {
        setError(err instanceof ApiError ? err : new ApiError('Could not load your watchlists.', 'unknown', 0));
      } finally {
        setLoading(false);
      }
    })();
  }, [loadWatchlists]);

  useEffect(() => {
    if (activeId) window.localStorage.setItem(ACTIVE_LIST_KEY, String(activeId));
  }, [activeId]);

  // --- feed ------------------------------------------------------------------

  const loadFeed = useCallback(
    async (id: number, opts: { force?: boolean; quiet?: boolean; source?: 'yahoo' | 'demo' } = {}) => {
      // One request at a time. A second refresh cancels the first rather than
      // letting two responses race to paint the screen out of order.
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;

      if (!opts.quiet) setRefreshing(true);
      try {
        const next = await api.feed(id, { force: opts.force, source: opts.source ?? source, signal: controller.signal });
        if (controller.signal.aborted) return;
        setFeed(next);
        setError(null);
        setWatchlists((lists) =>
          lists.map((l) => (l.id === next.watchlist.id ? next.watchlist : l)),
        );
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError) setError(err);
      } finally {
        if (!controller.signal.aborted) setRefreshing(false);
      }
    },
    [source],
  );

  useEffect(() => {
    if (!activeId) return;
    setFilter('all');
    if (source !== 'demo') {
      void loadFeed(activeId);
      return;
    }

    // Demo mode is a complete, deterministic comparison, not a first visit.
    // Prepare its stored baseline before asking for the current demo quote.
    void (async () => {
      try {
        await api.prepareDemo(activeId);
        await loadFeed(activeId, { force: true });
      } catch (err) {
        setError(err instanceof ApiError ? err : new ApiError('Could not prepare the demo.', 'demo_prepare', 0));
      }
    })();
  }, [activeId, loadFeed, source]);

  // Gentle background refresh, paused while the tab is hidden so a forgotten
  // tab does not sit there polling all day.
  useEffect(() => {
    if (!activeId) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && navigator.onLine) {
        void loadFeed(activeId, { quiet: true });
      }
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [activeId, loadFeed]);

  // --- cross-tab sync ---------------------------------------------------------

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const bc = new BroadcastChannel('marketdiff');
    channel.current = bc;
    bc.onmessage = (event) => {
      const { type, watchlistId } = event.data ?? {};
      if (type === 'checkpoint' && watchlistId === activeId) {
        setNotice('Another tab marked this feed as seen. Reloading it here too.');
        void loadFeed(watchlistId, { quiet: true });
      }
      if (type === 'watchlists') void loadWatchlists();
    };
    return () => {
      bc.close();
      channel.current = null;
    };
  }, [activeId, loadFeed, loadWatchlists]);

  // --- actions ----------------------------------------------------------------

  const markAsSeen = useCallback(async () => {
    if (!feed || !activeId) return;
    setMarking(true);
    setNotice(null);
    try {
      // Acknowledge the feed that was rendered, not "now" — anything that
      // arrived while it was on screen unread stays unread.
      const result = await api.checkpoint(activeId, feed.generatedAt);
      channel.current?.postMessage({ type: 'checkpoint', watchlistId: activeId });
      setNotice(
        result.advanced
          ? 'Checkpoint saved. MarketDiff is now watching these stocks for meaningful changes.'
          : 'Already up to date — another tab got there first.',
      );
      await loadFeed(activeId, { quiet: true });
    } catch (err) {
      setError(err instanceof ApiError ? err : null);
    } finally {
      setMarking(false);
    }
  }, [activeId, feed, loadFeed]);

  const toggleSimple = useCallback(() => {
    setSimpleMode((value) => {
      window.localStorage.setItem(SIMPLE_MODE_KEY, String(!value));
      void api.updatePreferences({ simpleMode: !value }).catch(() => undefined);
      return !value;
    });
  }, []);

  const createWatchlist = useCallback(
    async (name: string, symbols: string[] = []) => {
      try {
        const created = await api.createWatchlist(name, symbols);
        channel.current?.postMessage({ type: 'watchlists' });
        await loadWatchlists(created.id);
        return created;
      } catch (err) {
        setError(err instanceof ApiError ? err : null);
        return null;
      }
    },
    [loadWatchlists],
  );

  const removeStock = useCallback(
    async (symbol: string) => {
      if (!activeId) return;
      try {
        await api.removeStock(activeId, symbol);
        setNotice(`${symbol} removed.`);
        await loadFeed(activeId, { quiet: true });
      } catch (err) {
        setError(err instanceof ApiError ? err : null);
      }
    },
    [activeId, loadFeed],
  );

  // --- filters ----------------------------------------------------------------

  const getFilteredItems = (items: Feed['items']) => {
    if (!items) return [];
    return items.filter((item) => {
      if (filter === 'all') return true;
      if (filter === 'high') return item.severity === 'high';
      if (filter === 'meaningful') return item.severity === 'meaningful' || item.severity === 'watch';
      if (filter === 'unchanged') return item.status === 'unchanged';
      if (filter === 'stale') return item.freshness === 'stale';
      return true;
    });
  };

  const filterCounts = {
    all: feed?.items.length ?? 0,
    high: feed?.items.filter((i) => i.severity === 'high').length ?? 0,
    meaningful: feed?.items.filter((i) => i.severity === 'meaningful' || i.severity === 'watch').length ?? 0,
    unchanged: feed?.items.filter((i) => i.status === 'unchanged').length ?? 0,
    stale: feed?.items.filter((i) => i.freshness === 'stale').length ?? 0,
  };

  // --- keyboard ----------------------------------------------------------------

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === 'r' && activeId) {
        event.preventDefault();
        void loadFeed(activeId, { force: true });
      } else if (event.key === 'm') {
        event.preventDefault();
        void markAsSeen();
      } else if (event.key === 's') {
        event.preventDefault();
        toggleSimple();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeId, loadFeed, markAsSeen, toggleSimple]);

  // --- render -------------------------------------------------------------------

  const counts = feed?.counts ?? {};
  const hasStocks = (feed?.items.length ?? 0) > 0;

  return (
    <div className="min-h-screen">
      <Header
        watchlists={watchlists}
        active={active}
        onSelect={setActiveId}
        onCreate={async () => {
          const name = window.prompt('Name this watchlist', 'My watchlist');
          if (name) await createWatchlist(name.trim());
        }}
        onRename={async () => {
          if (!active) return;
          const name = window.prompt('Rename this watchlist', active.name);
          if (!name) return;
          try {
            await api.renameWatchlist(active.id, name.trim());
            channel.current?.postMessage({ type: 'watchlists' });
            await loadWatchlists(active.id);
          } catch (err) {
            setError(err instanceof ApiError ? err : null);
          }
        }}
        onDelete={async () => {
          if (!active) return;
          if (!window.confirm(`Delete “${active.name}”? This cannot be undone.`)) return;
          try {
            await api.deleteWatchlist(active.id);
            setFeed(null);
            channel.current?.postMessage({ type: 'watchlists' });
            await loadWatchlists();
          } catch (err) {
            setError(err instanceof ApiError ? err : null);
          }
        }}
        simpleMode={simpleMode}
        onToggleSimple={toggleSimple}
        onRefresh={() => activeId && loadFeed(activeId, { force: true })}
        refreshing={refreshing}
        onAdded={() => activeId && loadFeed(activeId, { force: true, quiet: true })}
        source={source}
        onSourceChange={(nextSource) => {
          setSource(nextSource);
          window.localStorage.setItem(SOURCE_KEY, nextSource);
        }}
      />

      <main id="feed" className="mx-auto max-w-5xl px-4 pb-24 sm:px-6">
        <div className="space-y-3 pt-6">
          {!online ? (
            <Banner tone="warning" title="You are offline">
              Showing the last data this browser received. Prices will update when the connection
              returns.
            </Banner>
          ) : null}

          {error ? (
            <Banner
              tone="error"
              title={error.offline ? 'Cannot reach the server' : 'Something went wrong'}
              action={
                activeId ? (
                  <Button onClick={() => loadFeed(activeId, { force: true })}>Try again</Button>
                ) : null
              }
            >
              {error.message}
            </Banner>
          ) : null}

          {source === 'demo' && feed ? (
            <p role="status" className="border-l-2 border-meaningful pl-3 text-sm text-ink-soft">
              Demo · All states — deterministic simulated data, not real market prices. The same
              moment always produces the same numbers.
            </p>
          ) : feed?.data.isDemo ? (
            <Banner
              tone="warning"
              title={
                feed.data.degraded
                  ? 'Yahoo unavailable · Demo fallback active'
                  : 'Demo data'
              }
            >
              {feed.data.degraded
                ? `Yahoo Finance could not be reached${feed.data.error ? ` (${feed.data.error})` : ''}, so MarketDiff switched to deterministic demo data. Nothing below is a real market price.`
                : 'This feed is running on generated data, not real market prices. It is deterministic, so the same moment always produces the same numbers.'}
            </Banner>
          ) : null}

          {feed && feed.data.missing.length > 0 ? (
            <Banner tone="warning" title="Some stocks did not update">
              No fresh data for {feed.data.missing.join(', ')}. Their last reliable values are shown
              below, labelled with when they were received.
            </Banner>
          ) : null}

          {notice ? (
            <p role="status" aria-live="polite" className="text-sm text-ink-soft">
              {notice}
            </p>
          ) : null}
        </div>

        {loading ? (
          <div className="pt-10">
            <div className="loading-rule h-8 w-72 bg-rule-strong" />
            <FeedSkeleton />
          </div>
        ) : watchlists.length === 0 ? (
          <FirstRun onCreate={createWatchlist} />
        ) : !feed ? (
          <FeedSkeleton />
        ) : !hasStocks ? (
          <EmptyWatchlist onAdd={() => document.querySelector<HTMLInputElement>('input[role="combobox"]')?.focus()} />
        ) : (
          <>
            {/* Editorial summary */}
            <section aria-labelledby="summary" className="pt-10">
              <h2 id="summary" className="sr-only">Summary</h2>
              
              {/* Main headline */}
              <div className="mb-6">
                <p className="font-display text-4xl leading-tight sm:text-5xl">
                  {counts.changed === 0
                    ? 'Nothing meaningful changed'
                    : counts.changed === 1
                    ? '1 thing changed'
                    : `${counts.changed} things changed`}
                </p>
              </div>

              {/* Status line and checkpoint */}
              <div className="space-y-3 border-b border-rule pb-6">
                <div className="flex flex-wrap items-baseline gap-4 text-sm text-ink-soft">
                  {counts.changed > 0 && (
                    <span className="numeral font-medium">
                      {counts.high ?? 0} <span className="font-normal">needing attention</span> ·{' '}
                      {(counts.meaningful ?? 0) + (counts.watch ?? 0)} <span className="font-normal">worth watching</span>
                    </span>
                  )}
                  {feed.items.length > 0 && (
                    <span className="text-ink-faint">
                      {feed.items.length} {feed.items.length === 1 ? 'stock' : 'stocks'} checked
                    </span>
                  )}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <p className="text-xs text-ink-faint">Last checked</p>
                    <p className="font-medium">
                      {feed.checkpointAt
                        ? formatMoment(feed.checkpointAt)
                        : 'Never — this is your first visit'}
                    </p>
                  </div>
                  <Button
                    variant="primary"
                    onClick={markAsSeen}
                    disabled={marking}
                    aria-keyshortcuts="m"
                    className="self-end"
                  >
                    {marking ? 'Saving…' : 'Mark as seen'}
                  </Button>
                </div>

                <div className="border-t border-rule-faint pt-3">
                  <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">How MarketDiff works</p>
                  <ol className="grid gap-x-5 gap-y-1 text-xs text-ink-faint sm:grid-cols-3">
                    <li><span className="font-medium text-ink-soft">1. Mark as seen</span> - sets your checkpoint</li>
                    <li><span className="font-medium text-ink-soft">2. Come back later</span> - we remember what you saw</li>
                    <li><span className="font-medium text-ink-soft">3. Refresh</span> - see what meaningfully changed</li>
                  </ol>
                </div>
              </div>

              <p className="mt-4 text-xs text-ink-faint">
                Attention scores measure unusualness, not advice. Scores are relative to each stock&apos;s own normal behavior.
              </p>
            </section>

            {/* Filters */}
            {feed.items.length > 0 && (
              <section className="mt-8 space-y-3">
                <h3 className="text-xs font-medium text-ink-faint uppercase tracking-wide">Filter</h3>
                <div className="flex flex-wrap gap-2">
                  {(['all', 'high', 'meaningful', 'unchanged', 'stale'] as const).map((f) => {
                    const count = filterCounts[f];
                    const isActive = filter === f;
                    const isDisabled = count === 0;
                    
                    const bgColor =
                      f === 'high' ? 'bg-high' :
                      f === 'meaningful' ? 'bg-accent' :
                      f === 'unchanged' ? 'bg-watch' :
                      f === 'stale' ? 'bg-high' :
                      'bg-ink';
                    
                    const labels = {
                      all: 'All',
                      high: 'Needs attention',
                      meaningful: 'Worth watching',
                      unchanged: 'Normal',
                      stale: 'Stale'
                    };

                    return (
                      <button
                        key={f}
                        onClick={() => setFilter(f)}
                        disabled={isDisabled}
                        aria-pressed={isActive}
                        className={`inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium transition-colors ${
                          isActive
                            ? `${bgColor} text-surface`
                            : isDisabled
                            ? 'bg-rule text-ink-faint cursor-not-allowed'
                            : 'bg-rule text-ink-soft hover:bg-rule-strong'
                        }`}
                      >
                        {labels[f]}
                        <span className="numeral">{count}</span>
                      </button>
                    );
                  })}
                </div>
              </section>
            )}

            {/* Timeline */}
            {feed.timeline.length > 0 ? (
              <Timeline days={feed.timeline} awayLabel={feed.awayLabel} />
            ) : null}

            {/* Feed or empty state */}
            <div aria-live="polite">
              {(() => {
                const filteredItems = getFilteredItems(feed.items);
                if (simpleMode) {
                  return filteredItems.length > 0 ? (
                    <SimpleFeed items={filteredItems} />
                  ) : (
                    <div className="mt-12 border border-rule bg-surface px-6 py-10 text-center">
                      <p className="text-sm text-ink-soft">No stocks match this filter.</p>
                    </div>
                  );
                }
                // "Nothing meaningful changed" is a claim about the stocks we
                // have a comparison for. A stock with no data at all (see
                // counts.no_data) is a separate, orthogonal fact — reported by
                // ChangeFeed's own "No data received" section below, not a
                // reason to withhold the reassurance for everything else.
                if (counts.changed === 0 && counts.new === 0) {
                  return (
                    <>
                      <NothingChanged total={feed.items.length - (counts.no_data ?? 0)} />
                      {filteredItems.length > 0 && (
                        <ChangeFeed items={filteredItems} onRemove={removeStock} />
                      )}
                    </>
                  );
                }
                return filteredItems.length > 0 ? (
                  <ChangeFeed items={filteredItems} onRemove={removeStock} />
                ) : (
                  <div className="mt-12 border border-rule bg-surface px-6 py-10 text-center">
                    <p className="text-sm text-ink-soft">No stocks match this filter.</p>
                  </div>
                );
              })()}
            </div>

            {/* Footer */}
            <footer className="mt-16 border-t border-rule pt-6 text-xs text-ink-faint">
              <p>
                Generated {formatMoment(feed.generatedAt)} from {feed.data.source} data.{' '}
                {feed.data.fetched > 0
                  ? `${feed.data.fetched} refreshed`
                  : `${feed.data.skippedFresh} still fresh`}
                .
              </p>
              <p className="mt-2">
                Keyboard: <kbd>r</kbd> refresh · <kbd>m</kbd> mark as seen · <kbd>s</kbd> simple mode
              </p>
            </footer>
          </>
        )}
      </main>
    </div>
  );
}

function FirstRun({
  onCreate,
}: {
  onCreate: (name: string, symbols?: string[]) => Promise<Watchlist | null>;
}) {
  const [busy, setBusy] = useState(false);

  return (
    <section className="mt-12 max-w-reading">
      <p className="font-display text-3xl leading-tight sm:text-4xl">
        Stop reading every price. Read what changed.
      </p>
      <p className="mt-4 text-base leading-relaxed text-ink-soft">
        MarketDiff remembers the state of your watchlist when you last looked at it. When you come
        back, it compares that against now and shows you only what moved unusually for that
        particular stock — with the numbers behind the judgement, and how much it trusts them.
      </p>
      <div className="mt-6 flex flex-wrap gap-3">
        <Button
          variant="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            // The quick-start always uses the deterministic demo, not whatever
            // Yahoo happens to do right now: a first impression should show
            // the product working, not gamble on a third-party API being up.
            window.localStorage.setItem(SOURCE_KEY, 'demo');
            const created = await onCreate('Sample watchlist', SAMPLE_SYMBOLS);
            if (created) {
              window.localStorage.setItem(ACTIVE_LIST_KEY, String(created.id));
              // Seeds history and rewinds the checkpoint so the diff has
              // something honest to compare against on the very first run.
              await api.prepareDemo(created.id).catch(() => undefined);
            }
            setBusy(false);
            window.location.reload();
          }}
        >
          {busy ? 'Setting up…' : 'Start with the demo'}
        </Button>
        <Button
          disabled={busy}
          onClick={async () => {
            const name = window.prompt('Name this watchlist', 'My watchlist');
            if (name) await onCreate(name.trim());
          }}
        >
          Create an empty one
        </Button>
      </div>
      <p className="mt-3 text-xs text-ink-faint">
        The demo uses deterministic simulated data for six NSE stocks — not real prices. The same
        moment always produces the same numbers.
      </p>
    </section>
  );
}
