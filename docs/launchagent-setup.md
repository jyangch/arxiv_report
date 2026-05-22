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
