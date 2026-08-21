# solpulse

An auto-updating report on the state of the Solana ecosystem. Pure Python
standard library — **no pip installs, no API keys, no accounts**. Clone it and
run it.

```bash
python3 -m solpulse.cli collect                      # human digest
python3 -m solpulse.cli collect --json               # machine-readable snapshot

# all three output formats from one collection run
python3 -m solpulse.cli collect \
    --out snapshot.json --markdown report.md --html dashboard.html

# re-render saved JSON into any format, without touching the network
python3 -m solpulse.cli render snapshot.json --html dashboard.html

# keep a history and flag anomalies against it
python3 -m solpulse.cli collect --history history.jsonl --html dashboard.html
python3 -m solpulse.cli history history.jsonl --metric tps_nonvote_mean
```

## What it reports

| Area | Metrics | Source |
|---|---|---|
| Network | health, epoch + progress + ETA, block height, lifetime tx count, TPS (all and non-vote), observed slot time | Solana JSON-RPC |
| Validators | active vs delinquent count, delinquent stake share, superminority size, top-10 stake concentration, top validators, commission distribution | Solana JSON-RPC |
| Economics | total / circulating / non-circulating SOL supply, inflation rate | Solana JSON-RPC |
| Price | SOL spot, 24h change, market cap | CoinGecko (keyless) |
| DeFi | Solana TVL and 1d / 7d / 30d change | DefiLlama (keyless) |

RPC methods used: `getHealth`, `getEpochInfo`, `getRecentPerformanceSamples`,
`getVoteAccounts`, `getSupply`, `getInflationRate`.

## Output formats

| Format | Flag | What it is |
|---|---|---|
| JSON | `--out PATH` / `--json` | The `solpulse/snapshot/v1` document — the source of truth every other format renders from |
| Markdown | `--markdown PATH` | Plain CommonMark report: at-a-glance summary, then network / validator / economics / source tables |
| HTML | `--html PATH` | Self-contained interactive dark-theme dashboard — one file, no CDN, no fonts, no JS framework |
| Text | `--digest PATH` | One-screen terminal digest — the fast "is Solana OK right now" view |

All three come out of a **single collection run**, so the JSON, Markdown and
HTML carrying the same timestamp can never disagree with each other. Renderers
are pure functions of the snapshot dict — no network, no clock — which is why
`render` can rebuild any past snapshot offline.

Real captured output lives in `samples/`: `snapshot.json`, `report.md`,
`dashboard.html`, `digest.txt` — all four re-renderable offline from the JSON.

### The dashboard

Open `samples/dashboard.html` in any browser — from `file://` is fine, it
requests nothing external. It is dark by default with a light toggle that
persists, and the design follows a few deliberate rules:

- **The form follows the data's job.** A single current value is a stat tile,
  not a one-bar chart. Epoch progress is one ratio against a limit, so it is a
  meter, not a two-slice pie. Stake by validator is a magnitude comparison, so
  it is a single-hue sequential bar chart rather than ten cycled colors. Supply
  is part-to-whole, so it is a stacked bar.
- **Nothing is signalled by color alone.** Deltas carry an arrow and a signed
  number; statuses carry a glyph and a word; the validator chart has a table
  view; charts carry text labels and `aria-label`s.
- **Colors are a validated palette** used unmodified — categorical slots 1–2
  for part-to-whole, and a bounded single-hue blue ramp for magnitude, stepped
  for the dark surface rather than flipped from the light one.
- **Chain data is treated as untrusted input.** Every string from the network
  is HTML-escaped, and the embedded raw JSON escapes `<`, `>`, `&` and the
  U+2028/2029 line terminators so a value can never break out of its
  `<script>` element.

## Design decisions worth knowing

**An error is never recorded as a value.** Every source lands in the snapshot
as either `{"status": "ok", "data": {...}}` or
`{"status": "error", "error": "..."}`. A rate-limited price lookup shows up as
`unknown`, not as `0`. This is not a hypothetical: an earlier project by the
same author shipped a bug where an RPC timeout was recorded as "the value
disappeared", and the shape here exists to make that class of bug impossible.

**Sources are isolated.** One failing source degrades the report rather than
killing it. `collect` exits non-zero when any source failed, so a cron job or
CI run notices a degraded report without having to parse the output.

**TPS is reported twice.** Vote transactions dominate raw Solana TPS. The
report gives both `tps_mean` and `tps_nonvote_mean`, because "TPS" in an
ecosystem report almost always means the non-vote number.

**Derived figures use observed data, not constants.** Slot time comes from
`getRecentPerformanceSamples`, not from the 400 ms target; the target is
carried alongside as a label only. Epoch ETA is computed from the observed
slot time.

**Batched RPC.** Each source issues one HTTP round-trip regardless of how many
methods it needs, which keeps the free public endpoint's rate limit comfortable.
Responses are matched by request `id`, not by position, because a node is
allowed to answer a batch out of order.

## Snapshot schema

`solpulse/snapshot/v1`:

```json
{
  "schema": "solpulse/snapshot/v1",
  "generator": "solpulse 0.1.0",
  "generated_at": "2026-08-21T00:55:48Z",
  "sources": {
    "network":    {"status": "ok",    "data": {...}, "elapsed_ms": 412.3},
    "price":      {"status": "error", "error": "HttpError: HTTP 429 ...", "elapsed_ms": 88.1}
  },
  "summary": {"sources_total": 5, "sources_ok": 4, "sources_failed": 1,
              "failed_sources": ["price"], "complete": false}
}
```

Read it in code with the `get` helper, which returns your default for both a
missing key and a failed source:

```python
from solpulse.snapshot import get, snapshot

snap = snapshot()
tps = get(snap, "network", "throughput", "tps_nonvote_mean", default="unknown")
```

Everything in `samples/` is real captured output from a live mainnet run, not
hand-written examples.

## History and anomaly detection

Pass `--history PATH` to `collect` and each run appends one flat record to a
JSONL log, then gets compared against the records already in it. The anomaly
report is embedded in the snapshot JSON and rendered into the Markdown report
and the HTML dashboard alongside everything else.

Two independent families of rule:

**Thresholds** need no history and fire on the very first run — node health not
`ok`, delinquent stake above 5% (warn) or 15% (critical, against the 33.3% share
at which finality stops), measured slot time above 600 ms / 800 ms against the
~400 ms target, a superminority of 20 or fewer validators, and any data source
that failed.

**Deviations** ask "is this unlike this chain's own recent behaviour", using the
**median and median absolute deviation** of the last 30 records rather than mean
and standard deviation. That choice is deliberate: one 3,000-TPS spike would
inflate a standard deviation enough to hide the *next* spike, whereas the median
barely moves. Each rule also has to clear a minimum relative change, so a very
steady metric does not alert every time its last decimal twitches.

What it will not do:

- **Invent a baseline.** Under five prior records the deviation rules do not run
  at all, and every output says so in as many words instead of quietly printing
  a clean bill of health.
- **Read a gap as a zero.** A metric its source failed to deliver is skipped and
  listed in `skipped` with the reason — it never drags a median down.
- **Report an infinity.** If the baseline has no spread, the sigma is `null` and
  the finding rests on the relative change alone.
- **Judge a snapshot against itself.** Detection runs against the log *before*
  the current record is appended.

The record is a flat `{metric: value}` map, so the log stays small enough to keep
indefinitely and greppable by hand. It is JSONL for one specific failure mode: a
process killed mid-append corrupts at most the last line, and the reader skips
unparseable lines rather than losing the history in front of them.

```bash
python3 -m solpulse.cli history history.jsonl                     # recent records
python3 -m solpulse.cli history history.jsonl --metric sol_usd    # one series
python3 -m solpulse.cli render snapshot.json --history history.jsonl  # re-judge offline
```

## Automation

The collector is stateless and side-effect free, so scheduling is whatever you
already use:

```cron
# refresh the live dashboard every 15 minutes, keeping a timestamped JSON archive
*/15 * * * * cd /path/to/solpulse && python3 -m solpulse.cli collect \
  --out snapshots/$(date -u +\%Y\%m\%dT\%H\%M).json \
  --history /var/lib/solpulse/history.jsonl \
  --markdown /var/www/solpulse/report.md \
  --html /var/www/solpulse/index.html
```

`collect` writes each file only after the fetch succeeds and exits non-zero on a
degraded report, so a failed run leaves the last good dashboard in place and is
visible to any cron mailer or monitoring wrapper.

Every output file is written **atomically** — to a temporary file in the target's
own directory, then `os.replace`d into position. A browser reloading a hosted
dashboard while the timer regenerates it gets either the whole old file or the
whole new one, never a truncated one.

## Tests

124 tests, standard library `unittest`, no network access:

```bash
python3 -m unittest discover -s tests -t . -v
```

Fixtures in `tests/fakes.py` are captured from real mainnet responses; the RPC
transport is injected, so the suite runs offline and deterministically.

## Status

Work in progress.

**Landed:** batched RPC client · five data sources · snapshot assembly with
per-source error isolation · JSON, text digest, Markdown and interactive
dark-theme HTML output · offline `render` command · append-only history store ·
anomaly detection over that history (threshold and robust-deviation rules) ·
`history` inspection command · atomic output writes · 124 tests.

**Planned:** wider metric coverage (stablecoin supply, DEX volume, REV, median
fees, daily active addresses, tokenized-asset volumes) · sparklines in the
dashboard once the history log is deep enough to draw honestly.

## Write-up

[`SUBMISSION.md`](SUBMISSION.md) covers the data sources, the automation
strategy and the anomaly-detection design in depth — including a table of what
this does **not** yet cover, and why.

## Licence

MIT.
