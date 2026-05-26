# Running the web UI as a macOS LaunchAgent

A LaunchAgent keeps `uvicorn server:app` alive in the background. It starts at
login and respawns if the process dies. The plist lives at
`~/Library/LaunchAgents/com.junyang.arxiv-report.plist` (rename to your own
reverse-DNS label if you prefer).

This file is the canonical record of the setup -- the plist itself is outside
the repo, so this document tells you what it should contain and how to manage it.

## Plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.junyang.arxiv-report</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/junyang/miniforge3/bin/uvicorn</string>
        <string>server:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>8080</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/junyang/Documents/python_works/arxiv_report</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/junyang/Documents/python_works/arxiv_report/.uvicorn.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/junyang/Documents/python_works/arxiv_report/.uvicorn.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/junyang/.local/bin:/Users/junyang/miniforge3/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/junyang</string>
    </dict>
</dict>
</plist>
```

### Key points

- `ProgramArguments`: absolute path to the `uvicorn` binary, then arguments.
  No `--reload` -- that flag is for development; in a daemon it just wastes CPU.
- `--host 0.0.0.0` makes the UI reachable from the LAN. Use `127.0.0.1` to
  restrict to loopback if you only want local browser access.
- `WorkingDirectory` must be the repo root so `server.py`, `core/`, `templates/`,
  `static/`, and `reports/` resolve via the relative paths the app uses.
- `RunAtLoad=true` starts the agent when you log in. `KeepAlive=true` respawns
  it if the process exits for any reason.
- `StandardOutPath` and `StandardErrorPath` both point at `.uvicorn.log` in the
  repo root -- a single combined log is easier to grep. The generic `*.log`
  rule in `.gitignore` keeps it out of version control.
- `EnvironmentVariables.PATH` ensures the `claude` CLI (under `~/.local/bin`)
  is on PATH when the default `CLAUDE_BACKEND=cli` invokes it. Without this,
  cli backend will fail with a `FileNotFoundError`.

## Lifecycle commands

```bash
# Load (start) the agent. Use after creating or editing the plist.
launchctl load ~/Library/LaunchAgents/com.junyang.arxiv-report.plist

# Unload (stop) the agent. Use before editing the plist.
launchctl unload ~/Library/LaunchAgents/com.junyang.arxiv-report.plist

# Reload (apply plist changes).
launchctl unload ~/Library/LaunchAgents/com.junyang.arxiv-report.plist && \
launchctl load   ~/Library/LaunchAgents/com.junyang.arxiv-report.plist

# Status -- PID and last exit code.
launchctl list | grep arxiv-report
```

A running agent looks like:

```
82647   0       com.junyang.arxiv-report
^pid    ^exit   ^label
```

`exit=0` means the last spawn started cleanly; a non-zero number (especially
negative, indicating a signal) means the process is crash-looping. Tail
`.uvicorn.log` to debug.

## Verifying

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
# Expect: 307 (redirect to /r/<latest-date>) or 200 (placeholder, no reports yet)

tail -f /Users/junyang/Documents/python_works/arxiv_report/.uvicorn.log
```

Then open <http://localhost:8080> in a browser.

## Editing the plist

`launchctl load` does not pick up changes to an already-loaded plist. The
correct dance is:

```bash
launchctl unload ~/Library/LaunchAgents/com.junyang.arxiv-report.plist
# edit the file ...
launchctl load   ~/Library/LaunchAgents/com.junyang.arxiv-report.plist
```

If `load` fails with `Load failed: 5: Input/output error`, the plist has a
syntax error -- check it with `plutil ~/Library/LaunchAgents/com.junyang.arxiv-report.plist`.

## Notes

- The agent does not inherit your shell environment. Anything in `~/.zshrc` or
  similar (API keys, custom PATH entries) must be set under
  `EnvironmentVariables` in the plist or sourced inside a wrapper script.
- The default `CLAUDE_BACKEND=cli` mode requires the Claude Code CLI to be
  authenticated (`claude /login` from a normal terminal once). The OAuth token
  is stored in `~/.claude/`; the daemon picks it up automatically since `HOME`
  is set above.
- If you change the listening port, also update `README.md` and any cron jobs
  or local bookmarks that hit `http://localhost:8080`.
- `reports/.cache/` and `reports/*.html` are gitignored; they accumulate in
  the working directory as you generate.

## Daily auto-generation

A second LaunchAgent at
`~/Library/LaunchAgents/com.junyang.arxiv-report.daily.plist` fires on weekdays
(Mon-Fri): every 30 minutes from 11:00 to 13:00, then hourly from 14:00 to
18:00 local. Each firing first checks whether today's report already exists
(HTTP GET `/r/$DATE/raw`) and skips `/generate` if it does, so once any attempt
succeeds the remaining slots become no-ops and no Claude tokens are wasted
(an empty/failed run saves no file, returns 404, and is correctly retried).

The two bands serve different failure modes:

- **Morning 30-min cadence (11:00-13:00)** rides out arXiv 429 rate limits: if
  the 11:00 attempt is throttled, the 30-minute cadence gives the cooldown
  (typically ~30 min) time to expire before the next retry.
- **Afternoon hourly catch-up (14:00-18:00)** covers *holiday-delayed*
  announcements. Local time is GMT+8, ~12 h ahead of US Eastern. arXiv normally
  announces at 20:00 ET (≈08:00 local, so the morning band catches it), but a
  US-holiday deferral can push the announcement into the early ET morning =
  local afternoon. Observed on 2026-05-26 after Memorial Day: the listing
  published ~02:54 ET ≈ 14:54 local, which only the afternoon slots catch. The
  18:00 slot reaches ~05:00 ET even in winter (EST = GMT-5, local−13 h).

arXiv announces no papers on Saturday or Sunday, so weekends are skipped
entirely.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.junyang.arxiv-report.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>D=$(date +%F); S=$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080/r/$D/raw" 2>/dev/null || echo 000); if [ "$S" = 200 ]; then echo "[$(date)] $D exists, skip"; else echo "[$(date)] $D missing (status=$S), triggering /generate"; curl -fsS -X POST http://127.0.0.1:8080/generate --data-urlencode "date=$D" >/dev/null; fi</string>
    </array>

    <key>StartCalendarInterval</key>
    <array>
        <!-- Mon-Fri (Weekday 1-5): 11:00, 11:30, 12:00, 12:30, 13:00 (429 retry)
             then 14:00, 15:00, 16:00, 17:00, 18:00 (holiday-delay catch-up). -->
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    </array>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>/Users/junyang/Documents/python_works/arxiv_report/.daily-generate.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/junyang/Documents/python_works/arxiv_report/.daily-generate.log</string>
</dict>
</plist>
```

### Behaviour notes

- The trigger relies on the main `com.junyang.arxiv-report` agent already
  serving the UI on `127.0.0.1:8080`. If the service is down, the existence
  check returns `status=000`, the `POST /generate` also fails, and curl's
  error lands in `.daily-generate.log`; launchd just waits for the next
  calendar slot to try again.
- If the laptop is asleep at a scheduled slot, launchd fires the missed job
  once when the machine wakes -- a late-morning wake still kicks off the
  day's report.
- The trigger is inlined in `ProgramArguments` via `sh -c "..."` rather than
  pointing at a script file in the repo. macOS TCC blocks launchd from
  reading executables under `~/Documents`, so a `scripts/daily-trigger.sh`
  in the project tree fails with `Operation not permitted`. Inline shell
  sidesteps the sandbox entirely.
- `/generate` enforces the arXiv 429 cooldown server-side, so a scheduled
  trigger that lands during a cooldown returns the error partial (logged, no
  report) instead of queuing a doomed task. The next scheduled slot retries.
- If two slots overlap because a Claude generation runs past the next slot
  (rare), both workers will run; the arXiv fetch is cached after the first,
  but the second will still spend Claude tokens. Not handled today -- add
  in-flight dedupe to `/generate` if it ever becomes a real problem.
- To test without waiting for the schedule:
  `launchctl start com.junyang.arxiv-report.daily`.
- To change the schedule, edit the plist, then
  `launchctl unload ~/Library/LaunchAgents/com.junyang.arxiv-report.daily.plist && launchctl load ~/Library/LaunchAgents/com.junyang.arxiv-report.daily.plist`.
