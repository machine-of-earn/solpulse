# solpulse — submission write-up

**Data sources · automation strategy · anomaly detection**

This is the companion write-up to the code. Setup and usage live in
[`README.md`](README.md); this document explains *why* each piece is built the
way it is, and — just as importantly — what it deliberately refuses to do.

The whole system is **Python standard library only**. No `pip install`, no API
keys, no accounts, no config file. `git clone` and run. Every number in this
document came from a real run against Solana mainnet-beta; nothing here is
illustrative.

---

## 1. Data sources

Five independent sources, all keyless. Each is fetched separately and each can
fail on its own without taking the run down with it.

| Source | Endpoint | What it provides | Auth |
|---|---|---|---|
| Solana JSON-RPC — network | `api.mainnet-beta.solana.com` | `getHealth`, `getEpochInfo`, `getRecentPerformanceSamples` | none |
| Solana JSON-RPC — validators | same | `getVoteAccounts` | none |
| Solana JSON-RPC — economics | same | `getSupply`, `getInflationRate` | none |
| CoinGecko | `api.coingecko.com/api/v3/simple/price` | SOL spot, 24h change, market cap | none (free tier, keyless) |
| DefiLlama | `api.llama.fi/v2/historicalChainTvl/Solana` | Solana DeFi TVL + 1d/7d/30d change | none |

The RPC endpoint is `--endpoint`-configurable, so anyone with a private or
self-hosted validator RPC can point at it without touching code. The default is
the public endpoint, which is rate-limited but sufficient at a per-hour cadence.

### Derived, not just relayed

Several of the most useful numbers are computed here rather than read off an
API, because no keyless API publishes them:

- **Slot time and TPS** — `getRecentPerformanceSamples` returns 30 raw windows
  of `numSlots` / `numTransactions` / `samplePeriodSecs`. The collector derives
  mean and latest TPS, **separates vote from non-vote transactions** (the
  headline "Solana does 4,000 TPS" number is mostly consensus votes; the
  non-vote figure is the one that reflects user activity), and derives observed
  slot time in ms against the ~400 ms target.
- **Superminority size** — the smallest number of validators whose combined
  stake exceeds 33.3%, computed by sorting `getVoteAccounts` by stake and
  accumulating. This is the single best one-number answer to "how centralised
  is Solana right now", and it is not exposed by any endpoint.
- **Delinquent stake share** — the count of delinquent validators is nearly
  meaningless on its own (ten tiny validators dropping off is noise). The
  *stake* they control is what matters, so that is what the threshold rules read.
- **Top-10 concentration**, **median commission**, **zero-commission count**,
  **circulating supply %**, **epoch ETA in seconds**.

### Failure isolation

Each source records its own `status`, `elapsed_ms`, and on failure an error
string. A failed source means its metrics are **absent** from the snapshot — the
renderers print the literal string `unknown`, never a stand-in `0`, and the
history log omits the key entirely rather than writing a zero that would drag a
future baseline down. A run with 3/5 sources up still produces a complete,
honest report about the 3.

Every run reports its own source health (`Sources: 5/5 ok`), and **any failed
source raises a `warn` anomaly**, so a silently half-empty report is not a
failure mode this system has.

---

## 2. Automation strategy

The listing asks for automatic updates at configurable intervals, and for
low-maintenance operation. The strategy here is to make the tool **a pure
function of the chain plus a clock**, and then let the operating system schedule
it. That is a deliberate architectural choice, not a shortcut.

### One collection, every format

```bash
python3 -m solpulse.cli collect \
    --out snapshot.json --markdown report.md --html dashboard.html \
    --digest digest.txt --history history.jsonl
```

A single network pass produces JSON, Markdown, HTML, a terminal digest, and one
appended history record. Because they all render from the same in-memory
snapshot, **three artifacts carrying the same timestamp can never disagree**.
Contrast the common alternative — three scripts hitting the API separately —
where the Markdown and the dashboard routinely describe slightly different
moments and nobody notices.

### Renderers are pure

`render/markdown.py` and `render/html.py` take the snapshot dict and return a
string. No network, no clock, no filesystem. Two consequences:

- **`render` works offline.** `python3 -m solpulse.cli render snapshot.json
  --html dashboard.html` rebuilds any past snapshot with no connectivity — and
  it can rebuild *every* format the tool emits, the text digest included, so
  there is no output that only exists if you happened to ask for it at fetch time — good
  for backfilling a format you didn't ask for at the time, and good for
  debugging a bad report without waiting for the condition to recur.
- **They are trivially testable.** Every rendering test is a pure input/output
  assertion against a fixture, so the test suite needs no network and no mocked
  sockets. All 124 tests run offline in about a tenth of a second.

### Scheduling

There is no daemon, no supervisor, no scheduler baked into the tool — because a
long-lived Python process is a thing that can wedge, leak, or drift, and it
would be one more thing to maintain. Instead the tool exits cleanly every run
and the OS schedules it. Interval is therefore *entirely* configurable and needs
no code change.

`cron`, hourly:

```cron
0 * * * * cd /srv/solpulse && /usr/bin/python3 -m solpulse.cli collect \
    --out /var/www/solpulse/snapshot.json \
    --markdown /var/www/solpulse/report.md \
    --html /var/www/solpulse/index.html \
    --history /var/www/solpulse/history.jsonl >> /var/log/solpulse.log 2>&1
```

`systemd`, with the same effect plus jitter and journald logging:

```ini
# solpulse.timer
[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true
```

`RandomizedDelaySec` keeps many deployments from hitting the public RPC on the
same second. `Persistent=true` catches up a run missed while the box was down.

### Why this is low-maintenance

- **Nothing to rotate.** No API key can expire, no free tier can be revoked, no
  account can be suspended. The two off-chain sources are keyless public
  endpoints; the primary source is the chain itself.
- **Nothing to upgrade.** Zero dependencies means zero dependency CVEs, zero
  lockfile churn, zero "works on Python 3.11, breaks on 3.13 because of a
  transitive pin". The only requirement is a Python 3 interpreter.
- **Writes are atomic.** Output files are written to a temporary file and
  renamed into place, so a browser reloading the dashboard mid-run gets either
  the old file or the new one, never a half-written one.
- **Append-only history.** JSONL, one flat record per line — chosen for one
  specific failure mode: a process killed mid-append corrupts at most the final
  line, and the reader skips unparseable lines rather than losing the history
  in front of them. That behaviour is asserted by a test, not assumed.
- **Degradation is visible, not silent.** A failed source is a `warn` finding on
  the dashboard, in the Markdown, and in the digest.

### Hosting

The HTML dashboard is **a single self-contained file with zero external
requests** — no CDN, no webfont, no framework, no analytics (asserted by a
test). Hosting is therefore just serving a static file: point nginx at the
output directory, or `python3 -m http.server`, or drop it on any static host.
It also works from `file://`, which is genuinely useful — you can email the
dashboard to someone and it renders.

---

## 3. Anomaly detection

Two independent rule families in `anomaly.py`. `detect()` is a pure function —
no clock, no network, no filesystem — which is why it can be tested exhaustively
against synthetic histories.

### Family 1 — thresholds (need no history, fire on run #1)

These are conditions that are alarming in absolute terms, so they don't wait for
a baseline:

| Rule | Trigger | Severity | Why this number |
|---|---|---|---|
| `network_unhealthy` | `getHealth` ≠ `ok` | critical | The node itself says it is behind. |
| `source_failure` | any source failed | warn | Metrics are unknown, not zero — say so loudly. |
| `delinquent_stake_high` | ≥ 15% of stake delinquent | critical | Read against the **33.3%** share at which the cluster stops finalising blocks. Half-way there is an emergency. |
| `delinquent_stake_elevated` | ≥ 5% | warn | Normal is well under 1%. |
| `slot_time_high` | ≥ 800 ms | critical | Roughly double the ~400 ms target. |
| `slot_time_elevated` | ≥ 600 ms | warn | Meaningfully above target. |
| `superminority_concentrated` | ≤ 20 validators | info | That many colluding or failing together could halt finality. |

Thresholds are read against *what the number means for the network*, not against
a round figure that looked reasonable. The delinquent-stake ladder exists
because 33.3% is where finality stops; the slot-time ladder exists because
400 ms is the protocol target.

### Family 2 — deviations (this is unlike this chain's own recent behaviour)

Ten metrics are tracked against a rolling baseline of the last 30 records — at a
4-hourly cadence, a bit over four days of context.

**Median and median absolute deviation, not mean and standard deviation.** This
is the single most important decision in the module. Consider the sequence of
TPS readings around a spike: one past 3,000-TPS outlier inflates a standard
deviation enough that the *next* spike falls inside 3σ and goes unreported —
the anomaly detector is blinded precisely by the events it exists to catch. A
median barely moves, and the MAD (rescaled by 1.4826 so thresholds still read in
familiar σ units) stays honest. There is a test that asserts exactly this:
a contaminated baseline that would hide a real spike under stddev still reports
it under MAD.

Flag at **3.5σ**, critical at **6σ**. Each metric additionally has a **minimum
relative change gate** — 25% for throughput, 15% for slot time, 5% for SOL price
and TVL, 1% for circulating supply — so a metric that is merely very steady does
not emit an alert every time its last decimal place twitches. A statistically
"impossible" 0.02% move in circulating supply is still not news.

### Four things it refuses to do

Each of these is a test, not an intention:

1. **Never invent a baseline.** Under 5 prior records the deviation rules do not
   run *and every output says so* — "baseline too short for deviation rules
   (need 5); threshold rules still applied". The alternative, printing a quiet
   all-clear, is worse than printing nothing, because it reads as "checked, fine".
2. **Never read a gap as a zero.** A metric missing from a snapshot is skipped
   and named in `skipped`, never treated as a drop to zero. Otherwise one failed
   CoinGecko call would report SOL going to $0 — and then poison the baseline.
3. **Never report an infinity.** A baseline with no spread at all gives a `null`
   sigma rather than a divide-by-zero ∞, and the finding rests on relative
   change alone.
4. **Never judge a snapshot against itself.** Detection runs against the log
   *before* the current record is appended, so the reading can't dampen the
   baseline it is being measured against.

A snapshot collected **without** a history log renders **no anomaly section at
all** — an empty card would read as "checked, all clear" when nothing was checked.

### It has already found something real

The first live run with detection enabled produced a true finding off mainnet:
**only 18 validators hold the superminority.** That is not a staged demo — it is
what the chain actually looked like, and it is arguably the most interesting
single fact in the whole report.

---

## 4. Coverage against the brief — including what is *not* covered

| Asked for | Status |
|---|---|
| Network performance — TPS, slot time, block height, epoch progress | ✅ plus vote/non-vote split, epoch ETA |
| Validator status — active/delinquent, stake distribution, top validators, commission | ✅ plus superminority, top-10 share, delinquent *stake* share |
| Economic indicators — SOL price | ✅ spot, 24h change, market cap |
| Economic indicators — DeFi TVL | ✅ plus 1d/7d/30d change |
| Supply / inflation | ✅ total, circulating, non-circulating, inflation rate |
| Anomaly detection *(optional, "highly valued")* | ✅ two rule families, 124 tests |
| Output: interactive HTML dashboard, dark theme | ✅ single file, dark by default, persisted light toggle |
| Output: human-readable Markdown | ✅ |
| Output: machine-readable JSON | ✅ the schema everything else renders from |
| Automation, configurable interval, low-maintenance | ✅ cron/systemd, zero deps, zero keys |
| No API keys / no dependencies *(stated preference)* | ✅ stdlib only |
| Stablecoin supply, DEX volume, REV, median fees | ❌ **not yet** |
| Daily active addresses, tokenized asset volumes | ❌ **not yet** |
| Ecosystem news, upcoming upgrades (Alpenglow, SIMD-525) | ❌ **not yet** |

The bottom three rows are stated plainly rather than glossed. They are the
metrics that have no keyless primary source — they generally require a Dune API
key or a paid analytics provider, which is in direct tension with the brief's
own "no API keys" preference. The honest position is: these are the next thing
to build, the collector's source interface is designed to take them (each source
is an independent module returning a `{status, data, elapsed_ms}` envelope), and
adding one is a ~50-line module plus tests. Claiming them as done would be the
kind of thing this codebase is specifically built not to do.

---

## 5. Presentation

The dashboard's design follows a small number of rules, applied consistently:

- **Form follows the data's job.** A single current value is a stat tile, not a
  one-bar chart. Epoch progress is one ratio against a limit, so it is a meter.
  Stake by validator is a magnitude comparison, so it is a **single-hue
  sequential** bar chart — not ten cycled colors, which would imply ten
  unrelated categories.
- **Nothing is signalled by color alone.** Deltas carry an arrow *and* a signed
  number; statuses carry a glyph *and* a word; the validator chart has a table
  view behind it; both charts carry `aria-label`s. This matters for
  accessibility and it matters when the report is printed or screenshotted.
- **Chain-supplied strings are untrusted.** Validator pubkeys are HTML-escaped
  everywhere, and the embedded raw JSON escapes `<`, `>`, `&`, U+2028 and U+2029
  so a value cannot break out of its `<script>` tag. There is a test that feeds
  a hostile pubkey through and asserts it cannot.

---

## 6. Testing

**124 tests, all offline, all in about a tenth of a second.** No network, no mocked
sockets, no fixtures scraped from a live run and quietly edited.

The suite's centre of gravity is deliberately on the failure paths, because the
easy path — five sources up, everything renders — is the one that gets exercised
by every real run anyway. What gets tested is: an all-sources-failed snapshot
rendering without inventing a single number; unknown-vs-real-zero for every
formatter (a real `0` must render as `0` and a missing value as `unknown`, and
`False` must not render as `1`); a truncated final line in the history log;
meter clamping above 100% and below 0%; a hostile validator pubkey; a JSON
script-tag breakout attempt; an interrupted output write leaving the previous
file intact; and the `render` round trip.

```bash
python3 -m unittest discover -s tests -t . -v
```

---

## 7. Honest limitations

- **Coverage gaps** are listed in §4 above and not glossed anywhere else.
- **The public RPC endpoint is rate-limited.** At an hourly cadence this is a
  non-issue; at a 30-second cadence you want `--endpoint` pointed at your own.
- **CoinGecko and DefiLlama are third parties.** They are the two non-chain
  sources, and if either is down that source reports `failed`, the metrics read
  `unknown`, and a `warn` anomaly fires. The chain data is unaffected.
- **The history log starts empty.** Deviation rules need 5 records before they
  run, and until then every output says so. This is the correct behaviour but it
  does mean a fresh clone's first few runs report thresholds only.
- **The design system's palette validator is a Node script** and was not run in
  the environment this was built in. Mitigation: the reference palette's
  documented, already-validated dark steps are used unmodified rather than a
  hand-tuned variant. That ships a validated palette, but it is not the same as
  having re-run the check, and it is recorded here as such rather than left
  unsaid.
