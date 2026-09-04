# MarketDiff

**A watchlist that tells you what changed, not what everything costs.**

---

## The 100-word pitch

Every watchlist app answers "what are my stocks worth?" Nobody asks that. Returning users
ask something else: *what did I miss?* MarketDiff treats your watchlist like a diff. It
remembers the market state at your last checkpoint, compares it against now, and surfaces
only what moved unusually — judged against each stock's own normal range, not a flat
percentage. Every flag is explained in numbers you can check: how far the move sits outside
normal, how heavy the volume is, how fresh the data is, how much we trust it. Everything
quiet is collapsed into one line. Attention scores, never advice.

---

## Problem interpretation

The brief asks for a watchlist that helps users "understand what has meaningfully changed."
The word doing the work is *meaningfully*. Three interpretations were available:

1. **Show more data** — charts, indicators, news. This is what every existing app does, and
   it makes the problem worse: more to read, not less.
2. **Predict** — "AI says INFY will drop." Dishonest and unfalsifiable.
3. **Compare against the user's own last visit and filter hard.** This is the only reading
   that makes the user's time the scarce resource rather than the data.

MarketDiff takes the third. Two consequences shape everything else:

- **"Meaningful" is relative to the instrument, not to a constant.** A 2% move in HDFCBANK,
  which typically moves 0.9% a day, is a bigger event than a 4% move in TATAMOTORS, which
  typically moves 1.8%. A flat `if change > 5%` threshold flags the same volatile names
  every day and buries the genuine surprises. Every signal in the engine is scaled by that
  stock's own recent behaviour.
- **"Since I last checked" must be a real, durable, per-user fact** — not "today's change,"
  which is what every other app shows and which is useless if you last looked on Tuesday.

The product's success condition is the size of the feed. If a normal day produces two rows
and fourteen collapsed into "no meaningful change," it worked.

---

## What it does

- **Change feed.** Stocks that crossed the attention threshold since your last checkpoint,
  ranked, each with the reasoning that flagged it. Everything else collapses into one line.
- **Explicit checkpoints.** Reading the feed never marks it as read. You press "Mark as
  seen," and only then does the comparison point move.
- **While you were away.** Long absences collapse into one row per trading session instead
  of a wall of events.
- **Honest data states.** Every price carries `LIVE / RECENT / STALE / UNAVAILABLE` and the
  age that produced it. A failed fetch shows the last reliable value and says how old it is.
  It never shows ₹0.
- **Simple Mode.** Not a smaller version of the same screen — a different answer. Plain
  sentences, no scores, no volume multiples, no jargon.

---

## Architecture

```
Next.js (App Router, TypeScript, Tailwind)
    │  /api/* proxied by Next → FastAPI (one origin, no CORS in dev)
    ▼
FastAPI
    ├── api/         thin HTTP layer: validation, ownership, serialisation
    ├── services/    ingest · diff · checkpoint · refresh · freshness · timeline
    ├── engine/      scoring — pure functions, no DB, no clock, no network
    └── providers/   MarketDataProvider
                        ├── YahooMarketDataProvider   (real, no API key)
                        ├── DemoMarketDataProvider    (deterministic)
                        └── ProviderRouter            (fallback + circuit breaker)
    ▼
SQLAlchemy 2.0 → SQLite (default) or PostgreSQL (DATABASE_URL)
```

Three boundaries are load-bearing:

**The engine is pure.** `engine/scoring.py` takes a `ChangeSignals` dataclass and returns a
`ScoreResult`. No database, no network, no `datetime.now()`. That is why the scoring rules
can be tested exhaustively in milliseconds and why the same inputs always produce the same
output.

**Nothing depends on a vendor.** Everything upstream of `providers/` sees `Quote` objects.
Swapping Yahoo for a paid feed means writing one class.

**Snapshots are immutable observations.** Nothing is ever updated in place. This is what
makes ingestion idempotent and out-of-order arrivals harmless.

---

## The meaningful-change algorithm

Four weighted components summing to 100:

```
attention = 45·move + 25·volume + 15·volatility_regime + 15·gap
```

Each component is normalised to 0..1 before weighting.

| Component | Measure | Saturates at |
|---|---|---|
| **Move** (45) | `max( |Δ%| / σ ÷ 3 , |Δ%| ÷ 6 )` | 3σ, or a 6% absolute move |
| **Volume** (25) | `(volume ÷ expected − 1) ÷ 2` | 3× the normal pace |
| **Volatility** (15) | `(recent_vol ÷ baseline_vol − 1) ÷ 1.5` | 2.5× its calmer periods |
| **Gap** (15) | `|gap%| / σ ÷ 2.5` | a 2.5σ opening gap |

**σ** is the standard deviation of the stock's recent daily returns — its own normal range —
floored at 0.35% so a suspiciously quiet history cannot turn rounding error into a 40σ event.

Three details that matter more than the weights:

- **The move component takes the *larger* of the relative and absolute readings.** Relative
  alone would under-flag a genuinely large move in an already-volatile stock; absolute alone
  is the flat threshold we set out to avoid.
- **Volume is compared against the pace of a typical session, not a full day.** Cumulative
  volume at 10:00 AM is meaningless against a full-day average; without the pace adjustment
  the volume signal would only ever fire after lunch.
- **A multi-session gap is scaled by √time.** Your last checkpoint might be three sessions
  ago, but σ is a per-session figure. Comparing a three-day move against a one-day yardstick
  would make every stock look like an emergency after a long weekend, so the baseline is
  scaled by `σ · √sessions`. Trading time is counted in sessions, not wall-clock hours —
  overnight and weekend hours contribute nothing.

### Attention score

| Score | Band | Meaning |
|---|---|---|
| 0–25 | Normal | Within this stock's usual behaviour |
| 26–50 | Worth watching | Something is mildly unusual |
| 51–75 | Meaningful | Clearly outside its normal range |
| 76–100 | Needs attention | Large and unusual on more than one signal |

**This is an attention score, not a rating.** It says *"this is unusual for this stock,"*
never *"this is good"* or *"buy this."* It is direction-agnostic by construction: a −4% move
and a +4% move score identically. There is no recommendation, no forecast, and no sentiment
anywhere in the system. The UI states this under the summary line.

### Confidence

Confidence is a separate 0..1 figure describing **data quality only** — never market
direction. It is reduced by stale data (×0.55), thin history (×0.6 with no σ at all),
missing volume (×0.85), and disagreement between sources (up to ×0.55).

Low confidence *demotes* severity by one band but **never below "worth watching."** A large
move on shaky data still reaches you, flagged for what it is. Silently hiding it would be
the worse failure.

### Explainability

Every reason is generated from a signal that actually fired, with its real numbers in the
sentence. Nothing is templated onto a stock that did not earn it, and there is no language
model anywhere in the explanation path.

> "Price moved up 4.2%, about 3.8× its typical daily move of 1.1% — far beyond its usual
> daily range."
> "Trading volume is 2.7× the normal pace for this point in the session."
> "Data sources disagree by 0.62% on the current price, so treat this as indicative."

A component must contribute at least 4 points before it earns a sentence, so the reasoning
never pads itself with trivia. A stock that did nothing gets an explicit reassurance rather
than silence.

---

## "Last checked" semantics and race conditions

**Definition.** `watchlist.checkpoint_at` is the instant of the newest market observation
the user has *explicitly acknowledged*.

**It does not move when you load the feed.** This is a deliberate product decision with a
correctness argument behind it: if reading advanced the checkpoint, opening the app would
erase the thing you opened it to see, and a background refresh in a forgotten tab would
silently mark three days of changes as read.

Four guarantees:

| Guarantee | Mechanism |
|---|---|
| **Monotonic** — never moves backwards | Target is rejected if `≤` the current value |
| **Bounded** — never moves past now | Clamped to server time, so a skewed client clock cannot acknowledge the future |
| **Race-free under concurrent tabs** | Single conditional `UPDATE … WHERE checkpoint_version = :seen`; `rowcount == 0` means another writer won, and we adopt their value rather than retrying |
| **Idempotent** | The client sends the `generatedAt` of the feed it actually rendered. A double-click, a retry, and a second tab replaying the same request all converge on one checkpoint |

Sending the rendered timestamp rather than "now" is also the honest semantic: anything that
arrived while the feed sat unread on screen stays unread.

**Out-of-order market data.** Providers return late and out of order. Snapshots are unique
on `(stock, source, observed_at)`, so re-ingesting an observation is a no-op. "Latest" is not
`ORDER BY observed_at DESC LIMIT 1` — it is an explicit `stock_state` pointer that only ever
moves *forward* in observation time, updated with the same version-guarded conditional
UPDATE. A slow request carrying a 10:02 price cannot land after a fast one carrying 10:05
and rewind the user's view of the market. The late snapshot is still stored as history; it
just never becomes current.

**Cross-tab UI.** A `BroadcastChannel` tells other open tabs when a checkpoint moves, so a
second tab reloads instead of showing a feed that is quietly out of date.

---

## Data freshness

Every snapshot records the provider's own timestamp for when the data was *true*, never our
clock. That single distinction is what makes stale detection possible.

| State | Age | Shown as |
|---|---|---|
| `live` | ≤ 3 min | "Updated 45s ago" |
| `recent` | ≤ 30 min, or a closing print after hours | "At the closing bell, 2 hours ago" |
| `stale` | older | "Last reliable update 5 hours ago" |
| `unavailable` | never received | "No data received" |

The after-hours grace applies **only to an actual closing print**. A feed that stopped
updating at lunchtime is stale no matter what time you look at it — otherwise every stalled
feed would look healthy after 3:30 PM.

---

## Failure handling

| Failure | Behaviour |
|---|---|
| Provider unavailable / timeout / rate-limited | Falls back to demo data for the whole refresh, banner names the failure kind |
| Repeated provider failures | Circuit opens for 90s — the outage costs one slow request, not one per page load |
| Malformed provider response | Treated exactly like a 500; a 200 full of nonsense is not more trustworthy than an error |
| Partial provider failure | Resolved symbols render; unresolved ones keep their last reliable value and are named in a banner |
| Stale data | Labelled, confidence reduced, reason attached — never rendered as current |
| Conflicting sources | Disagreement surfaced in the explanation with the exact percentage |
| Empty watchlist | Purposeful empty state, not a zero-state dashboard |
| Invalid symbol | Rejected at add time — we refuse to add a symbol we cannot price |
| Duplicate stock | 409 with a plain-language message; concurrent duplicate adds resolve to the same 409 |
| No previous checkpoint | Items marked "new" — no false comparison is invented |
| Very old checkpoint | Collapsed into a session-by-session timeline |
| Two tabs | Version-guarded checkpoint + BroadcastChannel sync |
| Network loss | Detected client-side; last received feed stays on screen with an offline banner |
| Database error | Single handler → 503 with a message that says the data is safe |

**Never ₹0.** A failed fetch never produces a zero or a blank price. It produces the last
value we trust, with its age attached.

---

## Data model

| Table | Purpose |
|---|---|
| `users` | An opaque browser-generated key. No email, no name, no password |
| `watchlists` | Name, `checkpoint_at`, `checkpoint_version` (concurrency token) |
| `watchlist_stocks` | Membership, unique on `(watchlist, stock)` |
| `stocks` | Symbol, name, exchange |
| `market_snapshots` | Immutable observations, unique on `(stock, source, observed_at)` |
| `stock_state` | Monotonic pointer to the newest trusted snapshot, with a version |
| `change_events` | Materialised diffs, unique on the snapshot pair (replay-safe) |
| `user_preferences` | Simple Mode, attention threshold |

Two deviations from the schema sketched in the brief, both deliberate:

- **`stock_state` was added.** Without an explicit guarded pointer, "latest" is a query, and
  a query cannot reject an out-of-order write.
- **`reasons` is stored as JSON text** rather than a related table. Reasons are always read
  as a whole and never queried across, so a join table would be structure without benefit —
  and it keeps the model byte-identical on SQLite and Postgres.

---

## Market data

No API key is required to run this project.

`YahooMarketDataProvider` uses Yahoo Finance's public chart endpoint (verified working
against live NSE data). It computes σ, average volume, and the volatility regime from the
daily history in the same response, so the engine gets identical inputs whichever provider
is active. Requests are concurrent behind a semaphore of 6, every response is validated
before use, and quotes are cached for 45 seconds so rapid refreshes and multiple tabs do not
each hit the upstream.

`DemoMarketDataProvider` is a **pure function of (symbol, timestamp)**. Random data would be
untestable and unrehearsable; this is neither. Each symbol has a fixed character, each
trading day draws a return from a fat-tailed distribution seeded by `md5(symbol + date)`, and
volume correlates with the size of the move the way it does in real markets. Restarting the
server, replaying Tuesday, or running the test suite all produce identical numbers.

Six symbols are pinned to specific situations for the current session, so the demo always
shows the cases the product exists for:

| Symbol | Situation |
|---|---|
| RELIANCE | Large move + 3.4× volume → high attention |
| INFY | Large adverse move + **sources disagreeing by 0.62%** |
| TCS | Moderate move + unusual volume |
| HDFCBANK | An ordinary day |
| ITC | **Feed stalled 5.5 hours ago** → detected and labelled stale |
| WIPRO | Genuinely nothing happened |

When demo data is in play the UI says so in a banner, on every affected row, and in the feed
footer. Real and synthetic data are never mixed within one refresh.

---

## Setup

Requires Python 3.11+ and Node 18+.

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000> and choose **Start with a sample watchlist**.

Or run both at once: `./scripts/dev.sh`

The database is created on first start. No migration step, no seed command, no API key.

### For a guaranteed-identical demo

```bash
MARKETDIFF_PROVIDER=demo uvicorn app.main:app --port 8000
```

On demo data, "Start with a sample watchlist" also backfills eight sessions of history and
sets the checkpoint five days back, so the first feed has an honest comparison and a compact
movement trace. Without that, a fresh install correctly has nothing to diff.

### Environment variables

All optional — see `backend/.env.example`.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./marketdiff.db` | `postgresql+psycopg://…` for Postgres |
| `MARKETDIFF_PROVIDER` | `auto` | `auto` \| `yahoo` \| `demo` |
| `MARKETDIFF_PROVIDER_TIMEOUT` | `4.0` | Seconds |
| `MARKETDIFF_QUOTE_TTL` | `45` | Seconds before a quote is refetched |
| `MARKETDIFF_CORS_ORIGINS` | `http://localhost:3000,…` | Comma-separated |

### PostgreSQL

```bash
export DATABASE_URL="postgresql+psycopg://marketdiff:marketdiff@localhost:5432/marketdiff"
```

No code changes. The model uses no dialect-specific types, and `psycopg` is already in
`requirements.txt`.

---

## Judge walkthrough

1. Press **Mark as seen** to create the watchlist checkpoint.
2. Return later and refresh: MarketDiff compares the stored checkpoint against the newest observations and shows only meaningful changes.
3. Choose **Demo · All states** for a deterministic, immediately populated walkthrough of important states. Demo prices are generated for demonstration and are not real market prices.
4. Click a change row to inspect the **Before → Now** comparison and its score breakdown.
5. Use the filters to focus on needs-attention, worth-watching, normal, and stale rows.
6. **While You Were Away** groups historical changes by trading session.
7. **Yahoo Finance · Live** remains the live market-data provider.

## Testing

```bash
cd backend && python -m pytest -q     # 65 tests
cd frontend && npm run typecheck && npm run build
```

The suite covers what would actually break in front of a judge:

- **`test_scoring.py`** — band thresholds; relative-vs-absolute sizing; direction symmetry;
  monotonicity in move size; score bounds; volume-only flags; missing volume ignored rather
  than assumed zero; stale, conflicting, and history-less data lowering confidence; low
  confidence demoting but not hiding; determinism; every reason carrying a number.
- **`test_state.py`** — duplicate ingestion producing one row; out-of-order arrivals stored
  but never becoming current; version increments only on real advances; two sources at the
  same instant coexisting; checkpoint monotonicity, future-clamping, concurrent writes, and
  idempotency; the four freshness states.
- **`test_api.py`** — missing and malformed identity; cross-user isolation; duplicate names
  and duplicate stocks; unknown and injection-shaped symbols; empty watchlist; first visit
  with no checkpoint; reading not advancing the checkpoint; provider timeout falling back
  with a price still on screen; the circuit breaker limiting upstream calls; rate limits
  reported as such; stale and conflicting data surfaced; long-absence timelines.

---

## Security

- Identity is an opaque browser-generated key. **No email, no password, no PII** — the
  strongest guarantee about personal data is not collecting it.
- Every watchlist lookup is scoped by owner. A watchlist belonging to someone else returns
  **404, not 403**, so the API does not confirm that an id exists to someone who cannot see it.
- All input is validated by Pydantic; symbols must match a strict character pattern.
- All database access goes through the ORM with bound parameters. There is no string-built
  SQL anywhere in the project.
- CORS is an explicit allow-list. In development Next proxies `/api`, so the browser never
  makes a cross-origin request at all.
- No secrets are required, so none can leak.

---

## Accessibility

- Colour is **never** the only channel. Severity is carried simultaneously by a written band
  label, a diff-gutter glyph (`!` `~` `·` `+` `?`), the numeric score, and a bar. Direction
  is an arrow *and* a sign *and* a screen-reader word.
- Semantic HTML: real `<ul>`/`<li>` feeds, `<button>` for actions, a labelled `<select>`,
  and an ARIA combobox with full arrow-key navigation for search.
- Visible focus rings on everything, a skip link to the feed, and `aria-live` regions for
  updates that arrive without a click.
- Keyboard shortcuts (`r` refresh, `m` mark as seen, `s` simple mode), announced in the
  footer and declared with `aria-keyshortcuts`.
- `prefers-reduced-motion` disables the one entrance animation.
- **Simple Mode** strips scores, multiples, and jargon down to one plain sentence per stock.
- Responsive from 320px up; dark mode via `prefers-color-scheme`.

---

## Trade-offs

**SQLite by default, Postgres by configuration.** A judge should be running this in 60
seconds. The data layer is dialect-neutral and `DATABASE_URL` is the only difference. The
concurrency guarantees are enforced by conditional UPDATEs and unique constraints, which
behave the same on both.

**No Docker.** The brief said to use it only if it genuinely simplifies setup. With SQLite
the setup is two `pip`/`npm` commands, and a compose file would add a moving part without
removing one.

**No background scheduler.** Snapshots are written on request. A worker would add a process
to run and supervise, and the checkpoint model does not need one — the diff is computed
against whatever we have, and freshness is always visible. This is the honest limitation:
history accumulates only while someone is using the app. Named here because it is the first
thing I would change with more time.

**A 45-second quote cache instead of websockets.** This is a "what did I miss" product, not
a trading screen. Sub-second prices would be engineering effort spent against the product's
own thesis.

**Weights were chosen, not fitted.** 45/25/15/15 reflects a judgement that price movement
matters most and confirmation by volume matters next. Fitting them would require labelled
data about which changes users *actually* wanted flagged, which does not exist. They live as
named constants at the top of one file and are trivial to tune.

**Exchange holidays are not modelled.** The session calendar treats every weekday as a
trading day. A holiday makes σ scaling slightly conservative — it never produces a wrong
price. A real deployment needs the NSE holiday list.

---

## What I deliberately did not build

- **Price predictions or buy/sell signals.** The product's credibility rests on every claim
  being checkable against a number in the same sentence. A forecast cannot be checked.
- **An LLM in the explanation path.** Explanations are generated from the signals that fired.
  This makes them deterministic, testable, instant, and impossible to hallucinate.
- **Charts.** A sparkline would answer "what is the shape" — a question nobody asked. The
  brief warned against dozens of charts; the honest response is zero.
- **News and sentiment.** The brief permits a news signal "only if reliable data exists."
  Free news APIs give inconsistent coverage of Indian mid-caps, and an unreliable signal in a
  scoring engine is worse than an absent one because it silently distorts every score.
- **Accounts, OAuth, sessions, password reset.** A personal watchlist does not need an
  identity system, and building one would mean storing data worth stealing.
- **Portfolio tracking, alerts, social feeds, gamification.** Each is a different product.
- **Microservices, Redis, Kafka, a queue.** One API process and one database is the correct
  size for this problem. Adding infrastructure would be a claim about scale I cannot justify.

---

## If I kept going

1. **A scheduled snapshot worker.** The single change that most improves the product — it
   would make the timeline dense and the diff meaningful even for users who visit rarely.
2. **A real second source** for the conflict-detection path, which currently proves itself
   against a deliberately disagreeing demo feed rather than two live providers.
3. **Per-user threshold tuning.** The scoring bands are global; some people want a quieter
   feed than others. `user_preferences.attention_threshold` already exists for this.
4. **Sector and index context** — "INFY moved 3.7%, but so did the whole IT index" is a
   materially different message from "INFY moved alone," and it is the most valuable signal
   the engine currently lacks.
5. **NSE holiday calendar** and a proper corporate-actions guard, so a stock split does not
   read as a 50% move.
