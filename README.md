# Gabbee

Gabbee is an ElevenLabs-first push-to-talk dictation and deterministic desktop-control bar for Linux Wayland desktops. KDE Plasma 6 and LXQt/Labwc have first-class shortcut paths. Normal dictation can only produce text. A separate command shortcut can run saved, schema-validated desktop actions.

The project is experimental, but its core paths are implemented and covered by automated tests.

## What works

- ElevenLabs `scribe_v2_realtime` over WebSocket with manual commit on key release.
- Live partial text in the bar and IBus preedit; partials are never typed as final text.
- Simultaneous mono 16 kHz PCM streaming and a temporary recovery WAV.
- Automatic Scribe v2 batch fallback after connection, stream, commit, authentication, quota, or protocol failure.
- Exactly one final delivery, with failed audio retained for Retry and successful audio removed.
- Semantic formatting for prose-smart numbers, phones, ZIP/PIN/OTP values, versions, IPs, ports, dates, times, money, percentages, measurements, identifiers, fractions, ordinals, signs, decimals, and magnitudes.
- Spoken punctuation, paragraphs, quotes, parentheses, dashes, slashes, and `literal …` escaping.
- Focus-captured delivery in this order: verified IBus commit, AT-SPI `EditableText`, clipboard paste with restoration, then `dotool`.
- Last Dictation actions: Undo, Edit & Replace, Retry, Copy Raw, and Copy Formatted, with an option to save explicit corrections.
- Schema-v3 command patterns with typed slots: `text`, `integer`, `digits`, `grouped_digits`, `phone`, `choice`, `app`, `window`, and `ui_target`.
- Desktop-only macros for text, keys, waits, app/window activation, installed desktop entries, accessible actions, pointer actions, scrolling, bounded repeats, and target-exists branches.
- Automatic app profiles by desktop-file ID and optional title regex, plus a persistent manual override.
- KWin window discovery/activation on Plasma through short-lived generated scripts and a private D-Bus callback. On other compositors, IBus delivery and AT-SPI control remain available while compositor-level window switching is disabled.
- AT-SPI target discovery and action invocation, non-focus-stealing numbered overlays, and a recursive screen-grid fallback.
- Provider-specific settings, first-run setup, Secret Service/KDE Wallet credentials, redacted diagnostics, and distinct dictation/command feedback.

There is no shell/process/eval action and no free-form spoken program execution. Launch actions resolve only installed `.desktop` entries. Screenshot/vision targeting is intentionally deferred behind a future resolver adapter.

## Shortcuts and safety model

- Dictation shortcut: `F5` by default. Its transcript is always treated as text, including words such as “delete,” “click,” or “undo.”
- Command shortcut: `F6` by default. Only built-in deterministic commands and enabled saved patterns can act on the desktop.
- Pressing the command shortcut again while a macro runs cancels its remaining steps. The bar also exposes Cancel Macro.
- Ambiguous accessible controls or app windows are numbered instead of guessed.

Examples:

```text
I have two dogs and twelve cats
→ I have two dogs and 12 cats

phone number three oh three five five five one two one two
→ (303) 555-1212

write as digits one two three
→ 123

version one point two point three
→ version 1.2.3
```

Desktop commands include “switch to Firefox,” “next Firefox window,” “previous app,” “open Kate,” “click Save,” “focus Search,” “scroll down three,” and “show numbers.”

## Metered ElevenLabs options

Realtime dictation and batch recovery use the configured ElevenLabs account. Two optional profile features are off by default:

- Keyterm prompting sends at most the 50 highest-priority enabled profile terms. ElevenLabs currently documents a 20% realtime transcription premium for keyterms.
- Entity detection sends only selected hints. Gabbee maps friendly names such as `zip`, `id`, and `ip` to ElevenLabs labels such as `location_zip`, `generic_id`, and `ip_address`.

See the official [Realtime API reference](https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime), [commit strategy guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/transcripts-and-commit-strategies), [keyterm guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/batch/keyterm-prompting), and [entity-detection guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/batch/entity-detection).

## Install for development

Gabbee needs Python 3.11+, PyQt 6, PipeWire, IBus introspection, and Wayland clipboard tools. `dotool` is the final text/pointer fallback; `qdbus6` enables KWin integration on Plasma.

On Gentoo:

```bash
sudo emerge -av app-i18n/ibus gui-apps/wl-clipboard
```

On Arch/Manjaro:

```bash
sudo pacman -S --needed python python-pip python-pyqt6 python-dotenv python-requests \
  python-websockets python-secretstorage ibus pipewire wl-clipboard \
  gobject-introspection qt6-tools dotool libcanberra
```

Then install and set up the IBus component:

```bash
cd /path/to/gabbee
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e .
gabbee-install-ibus --setup --icon "$PWD/gabbee.png"
gabbee-bar
```

On LXQt with Labwc, install compositor-native press/release shortcuts and an autostart entry after the commands above:

```bash
gabbee-install-labwc
```

This adds idempotent `F5` dictation and `F6` command bindings to `~/.config/labwc/rc.xml`, saves the original as `rc.xml.pre-gabbee`, installs Gabbee and IBus LXQt autostart entries, and reloads Labwc. The bindings call the running bar through `gabbee-control`; they do not require KDE or the GlobalShortcuts portal. `QT_IM_MODULE=ibus`, `GTK_IM_MODULE=ibus`, and `XMODIFIERS=@im=ibus` should be set in the session environment before launching target applications.

The first-run wizard configures the provider credential, microphone, two shortcuts, output test, and IBus/AT-SPI/compositor status. New API keys go to Secret Service or the desktop keyring when available. Existing `.env` credentials continue to work and are not deleted automatically.

## Configuration

The legacy environment file defaults to `~/.opencasenv/.env`; override it with `GABBEE_ENV_FILE`.

Important keys:

- `ELEVENLABS_API_KEY`
- `GABBEE_STT_PROVIDER` (`elevenlabs`, `gemini`, `whisper_local`, or `mock`)
- `GABBEE_LANGUAGE_CODE` (default `en`)
- `GABBEE_ELEVENLABS_MODEL_ID` (default `scribe_v2`)
- `GABBEE_ELEVENLABS_REALTIME_MODEL_ID` (default `scribe_v2_realtime`)
- `GABBEE_ELEVENLABS_REALTIME_ENABLED` (default `true`)
- `GABBEE_ELEVENLABS_KEYTERMS_ENABLED` (default `false`)
- `GABBEE_ELEVENLABS_ENTITY_DETECTION` (comma-separated friendly hints)
- `GABBEE_ELEVENLABS_BASE_URL`
- `GABBEE_AUDIO_SOURCE`
- `GABBEE_SAMPLE_RATE` (the realtime path is mono 16 kHz)
- `GABBEE_TOGGLE_SHORTCUT` (default `F5`)
- `GABBEE_COMMAND_SHORTCUT` (default `F6`)
- `GABBEE_WHISPER_LOCAL_MODEL`, `GABBEE_WHISPER_LOCAL_DEVICE`, and related local-Whisper settings

Advanced vocabulary, commands, macros, reusable templates, behavior, and profiles live in the XDG config directory as schema-v3 JSON and are managed through Command Studio.

## Architecture

- `gabbee.controller`: capture, realtime/batch recovery, semantic processing, queueing, exactly-once delivery, and Last Dictation state.
- `gabbee.stt`: batch and streaming provider contracts.
- `gabbee.number_normalizer` / `gabbee.text_processor`: semantic parsing and layout.
- `gabbee.output`: focus-aware text delivery router.
- `gabbee.desktop` / `gabbee.desktop_actions`: replaceable window, accessibility, and pointer backends.
- `gabbee.macro_runtime` / `gabbee.command_patterns`: typed patterns and cancellable desktop-only actions.
- `gabbee.ui`: floating bar, numbered overlay, first-run wizard, settings, diagnostics, and Command Studio.
- `gabbee.ibus_engine`: focused commit and preedit bridge.

KWin integration follows the [KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/). Accessible target names, actions, and screen extents come from [AT-SPI](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/).

## Verification

Run:

```bash
PYTHONPATH=src pytest -q
python -m py_compile $(rg --files src tests -g '*.py')
```

Packaged integration should additionally require the IBus namespace and manually qualify terminals, editors, Firefox, Chromium/Electron, and representative chat fields on Plasma or LXQt/Labwc Wayland.

Diagnostics record categories and timings, not audio or transcript contents. Exported reports redact credential-like values.

## License

Gabbee is licensed under GPL-3.0. See [LICENSE](LICENSE).
