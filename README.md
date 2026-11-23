# calbase

**Like `git rebase -i`, for your Google Calendar.**

Open your day or week in `$EDITOR`, rearrange lines, edit times, drop meetings, and save to apply the diff — plus quoteless quick capture from your terminal.

![calbase demo](assets/demo.gif)

## Install

**Homebrew (macOS / Linux):**

```bash
brew install omarish/tap/calbase
```

**uv:**

```bash
uv tool install git+https://github.com/omarish/calbase
```

**pipx:**

```bash
pipx install git+https://github.com/omarish/calbase
```

This installs both `calbase` and the short alias `cb` on your `PATH`.

## Rebase your calendar: `cb edit <when>`

Opens `$EDITOR` with your day or week as plain text, like an interactive `git rebase`. Edit, reorder, add, or delete lines — save to preview the diff and apply.

```bash
cb edit today
cb edit yesterday
cb edit tomorrow
cb edit fri                     # upcoming Friday (or today if it is Friday)
cb edit week                    # current week, grouped by day
cb edit next week
cb edit 2026-05-10..2026-05-14
```

Your editor opens with a dayfile:

```text
# calbase edit — Mon May 11 – Sun May 17, 2026 (calendar: you@example.com)
# Edit, add, delete, reorder lines. Save to apply, quit-without-saving to abort.
# Modifiers: @location (or @"multi word"), +meet, +invite:email (repeatable)

# Monday May 11
09:00-09:30  Standup                                    # id:abc123
09:30-11:00  Deep work                                  # id:def456
11:00-12:00  Design review @office +meet                # id:ghi789
14:00-15:00  Status sync                                # id:jkl012

# Tuesday May 12
09:00-09:30  Standup                                    # id:mno345
```

How it diffs:

- **Edit a line** (title, time, `@location`, `+meet`, `+invite:`) → `~ update`
- **Move a line to another day header** (keeping its `# id:`) → `~ update` with the new date
- **Delete a line** → `- delete`
- **Add a line** (no `# id:` suffix) → `+ create`

Save and quit to see the diff plan before anything touches Google Calendar:

```text
  ~ update  2026-05-11 09:30-12:00  Deep work      (end: 11:00 → 12:00)
  ~ update  2026-05-13 11:00-12:00  Design review  (date: May 11 → May 13)
  - delete  2026-05-11 14:00-15:00  Status sync
  + create  2026-05-11 17:30-18:30  Gym

Apply? [Y/n/e] [Y]: y
Applied: 1 create(s), 2 update(s), 1 delete(s).
```

- `Y` — apply all changes to Google Calendar.
- `n` — abort safely; your edits are stashed at `~/.calbase/edits/<timestamp>-aborted.txt`.
- `e` — re-open `$EDITOR` with your last buffer.

If a line has a syntax error, `$EDITOR` reopens with `# !! ERROR line N: …` comments right at the top.

## Quick capture: `cb <when> <title…>`

Time spec first, title is everything after — no quotes or flags needed.

```bash
# Retrospective (log what just happened)
cb 4h Home office cleanup and reorganization
cb 30m Code review with Sara
cb 9a-12p Deep work on calbase

# Forward-looking (schedule ahead)
cb sat 8p-11p Server maintenance
cb tomorrow 9-10a 1:1 with Alex
cb fri 3p-4p Dentist
cb 2026-05-15 9a-5p Offsite

# Today, shorthand
cb 6p-7p Dinner
cb 11p-1a Late-night debug   # auto-overnight
```

Recognized time specs:

| Shape | Examples |
|---|---|
| Duration (retrospective) | `4h`, `30m`, `1h30m` |
| Today range | `9a-12p`, `9-12`, `14:00-16:00` |
| Day + range | `sat 8p-11p`, `tomorrow 9-10a`, `yesterday 6p-8p` |
| ISO + range | `2026-05-15 9a-5p` |

### Inline modifiers

Drop modifiers anywhere in the title (or use the equivalent CLI flags):

```bash
cb fri 3p-4p Sync with Alice @office +meet +invite:alice@example.com
cb 1h Coffee @"Blue Bottle"
```

| Inline | Flag | What it does |
|---|---|---|
| `@location` / `@"multi word"` | `--location, -l` | Set event location |
| `+meet` | `--meet, -m` | Attach a Google Meet link |
| `+invite:email` | `--invite, -i` (repeatable) | Invite an attendee |
| — | `--description` / `-e` | Add description (or open `$EDITOR`) |
| — | `--calendar <id-or-name>` | Target a specific calendar |

## Calendars

```bash
cb cals                          # list available calendars (marks default)
cb set-default "Personal"        # change default calendar by name or id
```

On first run, `calbase` lets you pick a default writable calendar and saves it in `~/.calbase/config.json`.

## First-time setup (Google Calendar API)

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/) and enable the **Google Calendar API**.
2. Configure the OAuth consent screen (External → add your email as a test user).
3. Create **OAuth client ID** credentials of type **Desktop app** and download the JSON file.
4. Save it to `~/.calbase/credentials.json` (or point `CALBASE_CREDENTIALS` to its path).
5. Run any command (e.g. `cb cals`) to complete the one-time browser OAuth flow. Tokens are cached at `~/.calbase/token.json`.

## License

MIT
