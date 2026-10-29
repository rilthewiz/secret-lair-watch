# secret-lair-watch

[![tests](https://github.com/rilthewiz/secret-lair-watch/actions/workflows/tests.yml/badge.svg)](https://github.com/rilthewiz/secret-lair-watch/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

A small, deliberately boring notifier for **official Magic: The Gathering Secret
Lair announcements**. It reads public Wizards of the Coast sources on a schedule,
stores what it has already seen, and notifies you (console, desktop toast,
email, or Discord webhook) when something new appears or a published drop status
changes.

It does **not** buy, hold, queue, refresh, or log in to anything. You open the
store and buy manually, yourself.

```
python -m lairwatch --once --dry-run     # look, do not touch
python -m lairwatch --once               # one check
python -m lairwatch                      # watch forever
python -m lairwatch --calendar drops.ics # reminders for configured drop times
python -m lairwatch --remind             # wait for T-15m / T-0 alarms
```

---

## Why these sources

Verified on 2026-10-28 with single polite requests:

| Source | What it gives us | Why it is allowed |
| --- | --- | --- |
| `https://secretlair.wizards.com/us` | The public storefront page, server-rendered, embeds an `upcoming_event_slides` JSON array with drop `title`, `subtitle` (e.g. `Coming Soon`), `anchor_link`, `visible` | `robots.txt` allows `/us`; only `/search`, `/customer`, `/orders`, payment and data paths are disallowed |
| `https://magic.wizards.com/en/sitemap.xml` | Official sitemap with `<loc>` + `<lastmod>`; currently ~4,595 URLs, ~97 matching Secret Lair; supports `ETag` → `304` | Advertised in `magic.wizards.com/robots.txt` |
| `https://secretlair.wizards.com/us/chaosvault` | The Chaos Vault page, whose "notify me" form disappears when the vault opens. **Experimental and disabled by default** — see below | Same `robots.txt` as the storefront |

Things that were checked and **rejected**:

- `magic.wizards.com/en/news` has **no RSS/Atom feed** (zero `application/rss+xml`
  links). No feed is invented.
- `secretlair.wizards.com/products.json` **does not exist**. It returns the
  storefront shell containing a **Queue-it** waiting-room script. The storefront
  is not standard Shopify JSON, so there is no product API to consume.
- No third-party trackers, social accounts, resellers, or Discord feeds.

## Requirements

- **Python 3.12+** (tested on 3.14.7, Windows 11; Python 3.9 and 3.10 are
  end-of-life and not supported)
- **No runtime dependencies** — standard library only (see `requirements.txt`)
- No Node.js, no browser automation, no Docker, no database

## Install

```bash
git clone <your-repo-url> secret-lair-watch
cd secret-lair-watch
py -3.14 -m venv .venv          # Windows (or: python3.14 -m venv .venv)
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt   # nothing to install; keeps the habit
cp config.example.json config.json
python -m lairwatch --once --dry-run
```

`config.json` is git-ignored on purpose: it holds your email address.

## Configuration

Copy `config.example.json` to `config.json`. The example contains **placeholders
only** — no credentials, no tokens.

| Key | Default | Meaning |
| --- | --- | --- |
| `user_agent` | `secret-lair-watch/0.2 (+...)` | Sent on every request. Put a real contact in it. Browser user agents are rejected. |
| `interval_seconds` | `3600` | Seconds between checks. Values below `900` are rejected. |
| `min_interval_seconds` | `900` | Hard floor, also enforced by validation. |
| `jitter_ratio` | `0.1` | ±10% random jitter on the sleep, so checks do not land on exact minutes. |
| `request_timeout_seconds` | `30` | Per-request timeout. |
| `retry_delay_seconds` | `5` | Backoff for the single retry on 5xx/timeouts. |
| `state_path` | `state/state.json` | Where seen items, ETags, and robots cache live. |
| `respect_robots_txt` | `true` | Fetch and obey `robots.txt` (cached 24h). |
| `prune_after_days` | `365` | Drop state entries not seen for this long. |
| `sources[]` | see example | `name`, `type` (`secret_lair_store`, `wotc_sitemap`, or `secret_lair_chaos_vault`), `url`, `enabled`, `url_pattern`, `notify_on_status_change`, `alert_on_first_run`. |
| `notifiers.console` | on | Writes the digest to stdout and `logs/lairwatch.log`. |
| `notifiers.desktop` | on | Windows toast via PowerShell WinRT; `osascript` on macOS; `notify-send` on Linux. Best effort. |
| `notifiers.email` | off | SMTP digest. See below. |
| `notifiers.discord` | off | Webhook digest. See below. Stores only the **name** of the env var holding the URL. |
| `drop_schedule[]` | `[]` | `label`, `starts_at` (ISO 8601 **with offset**), `url`, `reminders_minutes` (default `[15, 0]`). |
| `remind_open_browser` | `false` | At a reminder, open the official URL in your default browser. |
| `remind_sound` | `true` | Audible alarm (`winsound` beep on Windows, `afplay`/`paplay` if available). |
| `remind_max_wait_hours` | `72` | `--remind` exits instead of sleeping longer than this. |

Environment overrides (handy for Task Scheduler / cron):

```
LAIRWATCH_CONFIG, LAIRWATCH_INTERVAL_SECONDS, LAIRWATCH_STATE_PATH,
LAIRWATCH_LOG_LEVEL, LAIRWATCH_LOG_FILE, LAIRWATCH_USER_AGENT,
LAIRWATCH_DESKTOP_ENABLED, LAIRWATCH_EMAIL_ENABLED, LAIRWATCH_DISCORD_ENABLED,
LAIRWATCH_SMTP_HOST, LAIRWATCH_SMTP_PORT, LAIRWATCH_SMTP_FROM,
LAIRWATCH_SMTP_TO, LAIRWATCH_SMTP_STARTTLS, LAIRWATCH_OPEN_BROWSER
```

Config files saved by Notepad or Windows PowerShell may start with a UTF-8
byte-order mark; it is stripped before parsing, so both editors are safe.

### Email setup

Credentials are read from environment variables at send time and never written to
config, state, or logs.

```jsonc
"email": {
  "enabled": true,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "starttls": true,
  "from_addr": "you@gmail.com",
  "to_addrs": ["you@gmail.com"],
  "username_env": "LAIRWATCH_SMTP_USER",
  "password_env": "LAIRWATCH_SMTP_PASSWORD"
}
```

```powershell
# Windows, current session
$env:LAIRWATCH_SMTP_USER = "you@gmail.com"
$env:LAIRWATCH_SMTP_PASSWORD = "<16-char Gmail app password>"
```

Gmail requires an **App Password** (Google Account → Security → 2-Step
Verification → App passwords), not your normal password. Other providers: use
their SMTP host and an app-specific password. To persist on Windows:

```powershell
setx LAIRWATCH_SMTP_USER "you@gmail.com"
setx LAIRWATCH_SMTP_PASSWORD "<app-password>"
```

Open a new terminal afterwards. Never commit these values.

### Discord setup

A Discord **webhook** needs no bot account, no OAuth, and no login. In your own
server: *Server Settings → Integrations → Webhooks → New Webhook*, pick a
channel, then copy the webhook URL.

```jsonc
"discord": {
  "enabled": true,
  "webhook_env": "LAIRWATCH_DISCORD_WEBHOOK",
  "timeout_seconds": 10
}
```

```powershell
$env:LAIRWATCH_DISCORD_WEBHOOK = "https://discord.com/api/webhooks/..."
```

Only `https` URLs on Discord-owned hosts (`discord.com`, `discordapp.com`,
`canary.discord.com`, `ptb.discord.com`) are accepted, and the URL is never
logged — an error message reports the host or the HTTP status, not the token.
`allowed_mentions` is forced off, so a drop title containing `@everyone` cannot
ping your server. Digests longer than Discord's 2,000-character message limit are
split on line boundaries, and a `429` is retried once when Discord's
`retry_after` hint is short; a long rate-limit sends a warning instead of hanging.

### Experimental: Chaos Vault monitor

The Chaos Vault (`secretlair.wizards.com/us/chaosvault`) publishes no
machine-readable "vault is open" flag. The only observable difference is that
the page shows a **"get notified" signup form while the vault is closed**, and
lists products when it opens.

That inference is shipped **disabled by default**:

```jsonc
{ "name": "chaos_vault", "type": "secret_lair_chaos_vault", "enabled": false, ... }
```

Enable it deliberately. Rules it follows:

- signup form present → `Closed`
- no signup form, two or more product links → `Open`
- anything else → `ParseError`, logged, **no alert** (never a guess)

While it was written the vault was closed, so the open state could not be
observed on the real page — treat this source as a convenience, not a promise. A
wrong "vault open" alert is worse than a missed one, hence the default. It never
submits the signup form, never stores an email address, and never touches a cart
or queue.

### Drop reminders

```jsonc
"drop_schedule": [
  {
    "label": "Doomsdrop",
    "starts_at": "2026-11-02T09:00:00-08:00",
    "url": "https://secretlair.wizards.com/us",
    "reminders_minutes": [15, 0]
  }
]
```

```bash
python -m lairwatch --calendar drops.ics   # import into phone/Outlook
python -m lairwatch --remind               # T-15m and T-0 toast + beep (+ browser if enabled)
```

The calendar event and the alarm contain the official URL. **You** open it and
join the queue as a normal customer, once.

## Running

```bash
python -m lairwatch --once                 # one cycle (cron/Task Scheduler friendly)
python -m lairwatch --once --dry-run       # real fetch, no sends, no state writes
python -m lairwatch                        # loop with jitter
python -m lairwatch --verbose              # DEBUG logging
python -m lairwatch --calendar drops.ics
python -m lairwatch --remind --dry-run     # print the reminder plan and exit
```

Exit codes: `0` success, `1` partial failure (one source/notifier failed),
`2` configuration error, `3` all sources blocked.

Windows Task Scheduler (every hour, starting at logon):

```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
           -Argument "-m lairwatch --once" `
           -WorkingDirectory "C:\path\to\secret-lair-watch"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
           -RepetitionInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "SecretLairWatch" -Action $action -Trigger $trigger
```

Linux/macOS cron (hourly at :17 to avoid thundering herds):

```
17 * * * * cd /path/to/secret-lair-watch && /path/to/.venv/bin/python -m lairwatch --once >> logs/cron.log 2>&1
```

## Tests

```bash
python -m unittest discover -s tests -v
```

134 tests, no network. They cover:

- storefront JSON extraction (brackets/quotes inside strings, trailing commas,
  hidden slides, duplicates, HTML fallback, stable keys)
- sitemap parsing, namespace handling, `lastmod`, URL filtering, malformed XML
- duplicate suppression across runs, status-change alerts once, opt-out,
  first-run baseline, `alert_on_first_run`
- alias-based identity: a renamed drop keeps its record (and `first_seen`)
  instead of alerting as new; a genuinely different drop still alerts
- dry-run sends nothing and writes no state
- conditional requests (`304`), 5xx/network retry-once, `403` block, `429`
  backoff with `Retry-After`
- robots.txt groups, `*` and `$` rules, longest-match and Allow tie-break
- interstitial handling: a healthy storefront page that embeds Queue-it assets is
  **not** treated as a queue, while a queue-only page still is
- repeated `upcoming_event_slides` assignments (the real page initialises it empty
  before populating it)
- config validation: interval floor, browser UA rejection, duplicate sources,
  unknown types, email placeholders, naive timestamps, env overrides, UTF-8 BOM
  tolerance, Discord webhook never read from the file
- Discord notifier: host allowlist, mention suppression, message splitting,
  single `429` retry, no webhook token in error text
- Chaos Vault heuristics: closed/open/ambiguous, `ParseError` instead of a guess,
  ships disabled
- `.ics` output: UTC times, two alarms, escaping, line folding
- notifier formatting and failure isolation

Add `--verbose` or point `log_file` at a file when debugging.

## How duplicate detection works

`state/state.json` is written atomically (temp file + `os.replace`) and contains
per-source `etag`, `last_modified`, `baseline_done`, and one record per item:

```json
{
  "version": 1,
  "sources": {
    "secret_lair_store": {
      "etag": "\"abc\"",
      "baseline_done": true,
      "items": {
        "doomsdrop": {
          "title": "Doomsdrop",
          "status": "Coming Soon",
          "first_seen": 1793300000.0,
          "last_seen": 1793303600.0,
          "last_alerted_status": "Coming Soon",
          "aliases": ["doomsdrop"]
        }
      }
    }
  }
}
```

- Stable keys: the absolute URL for sitemap entries, the slugged `anchor_link`
  (or title) for storefront entries.
- **Aliases instead of product ids.** The storefront publishes no product id we
  can key on: a full scan of the live page found **zero** `data-product-id`
  attributes and no ids in the `upcoming_event_slides` payload, so keying on one
  would mean inventing it. Instead each drop records its anchor slug, its title
  slug, and its `event_name` slug as aliases. If Wizards renames a drop or changes
  its anchor, the run still matches the existing record: no new-drop alert, and
  `first_seen` is preserved. Changing *both* title and anchor yields a new item,
  which is the honest reading. Pre-existing state files without `aliases` load
  unchanged.
- First run per source is a **silent baseline** (avoids emailing ~97 historical
  announcements). Set `alert_on_first_run: true` to opt out of that.
- A changed `subtitle` produces one status-change alert when
  `notify_on_status_change` is on, and nothing on reruns.
- A corrupt state file is renamed to `state.json.corrupt` and rebuilt, so a bad
  write can never wedge the app.

## Prior art

Other public Secret Lair watchers were read before writing this one (checked
2026-10). Credit where it is due — the comparison shaped the feature set:

| Project | What it does well | What this project did differently |
| --- | --- | --- |
| [`epiphanyplx/Secret-Lair-Monitor`](https://github.com/epiphanyplx/Secret-Lair-Monitor) | Broad coverage: storefront **and** Chaos Vault, Discord announcements, push to a Discord channel | Same broad-coverage idea, but with `robots.txt`, `ETag`/`If-Modified-Since`, a 15-minute floor with jitter (no cache-busting query strings), JSON state, a test suite, and an MIT license |
| [`Kevin-Wilkes/SecretLairBot`](https://github.com/Kevin-Wilkes/SecretLairBot) | Shows how far automation can go | Out of scope on purpose: it automates the purchase path. This project never adds to a cart, joins a queue, or checks out |
| [`product17/secretlair-worth-it`](https://github.com/product17/secretlair-worth-it) | Useful price/value analysis of past drops | Declined its data source: an undocumented `StoreSearch` endpoint with a hard-coded user id and a session `preference` token. Undocumented, credential-shaped, and unstable — not used |

Concretely, the comparison is why this release adds a Discord notifier, a Chaos
Vault monitor, and rename-tolerant identity. It is also why two tempting ideas
were **dropped**:

1. **Keying drops on a product id.** There is no product id in the public
   payload; inventing one would mean scraping an internal API. Aliases achieve the
   same "rename is not a new drop" outcome using only published fields.
2. **A storefront search/API endpoint.** Undocumented, session-tokened, and
   exactly the kind of thing that breaks (or gets you blocked) without warning.

## Limitations of the official sources

- **No official Secret Lair API and no RSS feed.** The sitemap and the storefront
  HTML are the only public machine-readable sources.
- **Storefront HTML is the contract.** The page embeds hand-maintained AngularJS
  JSON. If Wizards restructures it, parsing fails loudly (`parse error` in the
  log, no state change) rather than silently reporting "no drops". The HTML
  fallback (`notify-me-title` / `notify-me-subtitle`) covers simple reflows only.
- **Status text is marketing copy**, not an inventory feed. `Coming Soon` /
  `On Sale Now` reflects what the page says; it does not guarantee stock.
- **Queue-it waiting rooms appear during live drops.** If the storefront answers
  with a queue/CAPTCHA/Cloudflare interstitial, the app logs `blocked`, skips the
  cycle, and does not retry aggressively. It never attempts to pass the queue.
- **Sitemap discovery can lag.** `<lastmod>` is not a publish timestamp and can be
  edited after the fact; an article may appear in the sitemap minutes after the
  newsroom posts. Announcements can also be revised (delays, corrections).
- **Drop times in `drop_schedule` are your input.** Published times change; the
  alarm is best effort and makes no claim about when the store opens.
- **The Chaos Vault state is inferred, not published.** No machine-readable flag
  exists; the monitor reads the presence of the notify-me form. It is off by
  default and refuses to guess (`ParseError`) when the page looks neither open
  nor closed.
- **No real-time guarantee.** An hourly check can be up to an hour late, by
  design. Lowering `interval_seconds` below 900 is refused.
- **Commercial limits.** The app is not affiliated with Wizards of the Coast.
  Respect their terms; do not run it at scale.

## Ethics & compliance

- Public, unauthenticated pages only: one request per source per cycle, robots.txt
  honoured, ETag/`304` caching, descriptive User-Agent, 60-minute default.
- No login, no passwords, no payment data, no cart, no checkout, no order
  placement, no queue participation, no CAPTCHA or anti-bot circumvention, no
  proxies, no account rotation, no parallel requests. The Chaos Vault signup form
  is never submitted and no email address is ever collected.
- Buying is manual: the app tells you *that* a drop exists and *when* it is
  announced to open; you open the official store and buy like everyone else.

## License

MIT — see `LICENSE`.
