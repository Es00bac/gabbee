# Codex handoff — Gabbee install state

Written 2026-08-30. Machine: Gentoo Linux x86_64, KDE Plasma 6 / Wayland.
Read this before acting on "install gabbee" — **it is already installed.**

## TL;DR

Gabbee is installed editable into `/home/cabewse/gabbee/.venv` and all 163 tests
pass. What remains is **not** Python work: three system packages are missing and
need root. Do not re-run the install; see "Remaining work".

## Repo state — read before editing

- Path: `/home/cabewse/gabbee` (this repo). Branch `main`, HEAD `ec5d7af`.
- **76 uncommitted entries**: 25 modified tracked files (most of `src/gabbee/`,
  plus `README.md`, `HANDOFF.md`, `pyproject.toml`, 4 test files) and ~50
  untracked. This is live work in progress, not stale cruft.
- **Do not `git checkout`, `stash`, `clean`, or `reset`.** Nothing here has been
  committed on the user's behalf and it should stay that way unless asked.
- `HANDOFF.md` is the project's own implementation handoff — a different
  document from this one. Don't merge or overwrite it.

## What was installed (done, do not repeat)

Environment repairs, because the box had no Node at all:

- `codex` 0.151.0 → `~/.local/bin/codex` (musl static binary from the
  `openai/codex` GitHub release; there is no npm install here).
- `codex-code-mode-host` → `~/.local/bin/`. **This was the cause of the earlier
  "failed to spawn code-mode host" error.** The release ships it as a separate
  asset from `codex`; installing only `codex` leaves code mode broken. The
  `code_mode_host` feature flag was already `stable/true` — no config change was
  needed, only the binary.
- Node v24.20.0 LTS → `~/.local/node`, with `node`/`npm`/`npx` symlinked into
  `~/.local/bin`. **This was the cause of the `dataAnalyticsWidgets` MCP
  failure** — that plugin runs `node ./mcp/server.cjs`. Other curated plugins
  (github, gmail, figma, stripe) need Node too.
- `kimi` 0.39.1 → `~/.kimi-code/bin/kimi` (unrelated to Gabbee).
- `~/.local/bin` and `~/.kimi-code/bin` were appended to PATH in `~/.bashrc`.
  Both lines sit **after** the `[[ $- != *i* ]] && return` interactive guard, so
  non-interactive shells and cron do not see them. Use absolute paths in scripts.

Gabbee itself:

- `.venv` rebuilt with `/usr/bin/python3 -m venv --system-site-packages .venv`
  (Python 3.14.6; system site packages are needed for `gi`).
- `pip install -e .` → PyQt6 6.11.0, PyQt6-Qt6 6.11.2, websockets 17.1,
  python-dotenv 1.2.3, SecretStorage 3.5.0, cryptography 50.0.1, jeepney 0.9.0.
- Entry points live: `gabbee-bar`, `gabbee-engine`, `gabbee-commit`,
  `gabbee-install-ibus`, `gabbee-dbus`.
- `pytest` and `dbus-python` 1.4.0 installed into the venv (see gotchas).

The old `.venv-vertex-stt/` is a separate, older environment. Left untouched.

## Two gotchas that will bite you

**1. `dbus-python` is missing from `pyproject.toml`.**
`src/gabbee/ui/global_shortcuts.py` (uncommitted) does `import dbus`, but
`dbus-python` is not in `[project] dependencies`. A clean install therefore
fails at import — `ModuleNotFoundError: No module named 'dbus'` — and collection
of `tests/test_ui_bar.py` dies with it. It is installed in `.venv` as a
stopgap. **The real fix is to add it to `dependencies`**, which is a source
change the user has not yet approved. Ask first.

**2. The test suite hangs unless you kill the D-Bus session address.**
`tests/test_config.py` reaches for the Secret Service / KWallet over D-Bus. In a
headless or agent shell there is nothing listening, and it **blocks
indefinitely** rather than failing — it will silently eat your timeout. It is
not a bug and not a regression.

Run tests like this:

```bash
cd /home/cabewse/gabbee
DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent QT_QPA_PLATFORM=offscreen \
  ./.venv/bin/python -m pytest -q
```

Result as of this handoff: **163 passed in 0.72s.** Anything less is a real
regression you introduced.

## Remaining work — needs root, ask the user

Missing system tools. The user has not run these; do not run them yourself
without asking, and do not attempt to work around them with pip.

```bash
sudo emerge -av app-i18n/ibus gui-apps/wl-clipboard
```

- **`ibus`** — the primary text-delivery path. Currently
  `gi.require_version('IBus','1.0')` raises `Namespace IBus not available`.
  Highest priority; Gabbee's main output route is degraded without it.
- **`wl-clipboard`** — the clipboard-paste fallback (`wl-copy`/`wl-paste`).
- **`dotool`** — last-resort pointer/text fallback. **Not in the Gentoo tree and
  not in the `qindaqt` overlay.** Needs the GURU overlay or a manual build.
  Lowest priority — three delivery paths sit ahead of it.
- Already present: `pipewire` 1.6.7, `at-spi2-core`, `qdbus6`.

**Not run: `gabbee-install-ibus --setup`.** It is an interactive GUI wizard, it
requires IBus to be installed first, and it rewrites the user's KDE global
shortcuts (`~/.config/kglobalshortcutsrc` — note the existing `.bak-gabbee` in
`~/restore-staging/`). This must be run by the user, in their own Plasma
session. Do not invoke it from an agent shell.

## Suggested order

1. User runs the `emerge` above.
2. Verify: `./.venv/bin/python -c "import gi; gi.require_version('IBus','1.0')"`.
3. User runs `gabbee-install-ibus --setup --icon "$PWD/gabbee.png"`, then
   `gabbee-bar`, in their Plasma session.
4. Resolve the `dbus-python` declaration in `pyproject.toml` (ask first).
5. The 76 uncommitted changes still need review and a commit message — the
   user's call, not yours.
