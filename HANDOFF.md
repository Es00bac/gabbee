# Gabbee Handoff

## Project summary

Gabbee is a fresh project, not a Dictee fork in practice.

The design target is:

- English-first UX
- KDE Plasma on Wayland
- IBus/input-method based text commit
- no raw `/dev/input` grabs
- no key replay as the primary typing path
- floating control UI instead of grab-based push-to-talk

The intent is to build a voice input tool that behaves like an input method, not like a keyboard macro daemon.

## Current state

The repository currently contains a working MVP scaffold:

- floating Qt bar UI
- controller state machine
- PipeWire recording via `pw-record`
- STT provider abstraction
- ElevenLabs batch STT provider
- IBus engine skeleton with local Unix socket bridge
- clipboard fallback when no active Gabbee IBus engine is focused
- helper to write the user-local IBus component XML

Core files:

- `src/gabbee/main_bar.py`
- `src/gabbee/ui/bar.py`
- `src/gabbee/controller.py`
- `src/gabbee/audio.py`
- `src/gabbee/config.py`
- `src/gabbee/stt/elevenlabs.py`
- `src/gabbee/ibus_engine.py`
- `src/gabbee/install.py`

## What is verified

The following currently pass:

- `python3 -m py_compile $(rg --files src tests -g '*.py')`
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`

Current tests:

- `tests/test_config.py`
- `tests/test_controller.py`

The generated IBus component XML was also smoke-tested with:

- `PYTHONPATH=src python3 -m gabbee.install --output <tmpfile>`

## What is not done yet

This is the most important section.

Gabbee is not yet a complete end-user product.

### Not implemented

- no global hotkey support
- no KDE `KGlobalAccel` integration
- no local Whisper provider yet
- no streaming partial transcript path
- no preedit support in the IBus engine
- no packaged install flow
- no settings UI
- no first-run wizard

### Not activated in the live desktop session

The code has not been fully installed and activated in the actual user session yet.

That means:

- Gabbee is not currently registered as a live input method in the user desktop session
- the floating bar is not installed as a desktop app
- text commit through IBus is only scaffolded, not fully integrated end-to-end for real user workflows

### Important limitation: no hotkey path

At this point, Gabbee does **not** detect a hotkey.

The only control path implemented in the repo is the floating bar:

- `Start`
- `Stop`
- `Cancel`

If hotkeys are added later, they should be implemented through desktop/session APIs, not raw input grabs.

## Current runtime behavior

### Recording

Audio capture is handled by `pw-record` in:

- `src/gabbee/audio.py`

Current format:

- mono
- 16 kHz
- signed 16-bit PCM in WAV container

### STT providers

The provider abstraction lives under:

- `src/gabbee/stt/`

Implemented providers:

- `mock`
- `elevenlabs`
- `whisper_local`

The ElevenLabs provider currently uses the batch STT HTTP endpoint, not realtime WebSocket streaming.
The local Whisper provider is implemented with `faster-whisper`.

### Delivery path

Text delivery is:

1. try Gabbee IBus engine via Unix socket bridge
2. fall back to clipboard if no active Gabbee engine is focused

This behavior is implemented in:

- `src/gabbee/output.py`
- `src/gabbee/ibus_client.py`
- `src/gabbee/ibus_engine.py`

Clipboard fallback is intentional. It avoids fake key injection when IBus is not active.

## Secrets and configuration

Gabbee is explicitly designed to keep secrets out of the codebase.

Default env file:

- `~/.opencasenv/.env`

On the intended target machine, that is:

- `/home/cabewse/.opencasenv/.env`

Current config behavior:

- `.env` is loaded if present
- process environment overrides `.env`
- the merged values are captured into a stable config snapshot at startup

Relevant config file:

- `src/gabbee/config.py`

Important keys already supported:

- `ELEVENLABS_API_KEY`
- `GABBEE_STT_PROVIDER`
- `GABBEE_LANGUAGE_CODE`
- `GABBEE_ELEVENLABS_MODEL_ID`
- `GABBEE_ELEVENLABS_BASE_URL`
- `GABBEE_AUDIO_SOURCE`
- `GABBEE_SAMPLE_RATE`
- `GABBEE_FALLBACK_SINK`
- `GABBEE_UI_TITLE`
- `GABBEE_ENV_FILE`
- `GABBEE_WHISPER_LOCAL_MODEL`
- `GABBEE_WHISPER_LOCAL_DEVICE`
- `GABBEE_WHISPER_LOCAL_COMPUTE_TYPE`

## Important environment note

One major source of confusion during development:

this Codex session was running as `root`, not as the desktop user.

That means the default env resolution from this shell was:

- `/root/.opencasenv/.env`

instead of:

- `/home/cabewse/.opencasenv/.env`

So from the root shell, Gabbee currently resolves to `mock` unless `GABBEE_ENV_FILE` is explicitly pointed at the real user env file.

Any real user-session install should be done as the actual desktop user, not from a root session.

## Recommended next steps

The best next work items are:

1. Finish the real user-session run path.
2. Add safe desktop hotkey integration.
3. Make the IBus engine install/activation flow smoother.
4. Add preedit support and improve partial transcript UX in IBus.

### 1. Finish the real user-session run path

Do this first.

Target:

- editable install or package install as the actual user
- write user-local IBus component with `gabbee-install-ibus`
- restart IBus or session
- enable `Gabbee Voice Input` in IBus preferences
- verify end-to-end text commit into a focused app

### 2. Add safe hotkey integration

If a hotkey path is added, it should be one of:

- KDE global shortcut integration
- D-Bus exposed controller methods plus desktop shortcut binding
- QML/Qt frontend that uses session APIs

It should **not** use:

- `evdev` grabs
- `/dev/input` reads
- key replay daemons

### 3. Improve IBus engine install/activation flow

The IBus engine is currently a useful skeleton, but not a polished input method yet.
Key focus is making install/activation reliable and discoverable.

Likely next work:

- better engine lifecycle handling
- real install/remove tooling

### 4. Add preedit support and improve partial transcript UX in IBus

Likely next work:

- preedit text
- partial transcript display
- focused-context robustness

## Suggested run commands for development

From the project root:

```bash
cd /home/cabewse/gabbee
python3 -m pip install --user -e .
```

If running against the real user env file from a non-user shell:

```bash
export GABBEE_ENV_FILE=/home/cabewse/.opencasenv/.env
```

Write the IBus component XML:

```bash
gabbee-install-ibus
```

Run the floating bar:

```bash
gabbee-bar
```

Run the IBus engine directly:

```bash
gabbee-engine
```

## Architectural guardrails

These should remain explicit project constraints:

- no raw keyboard grabbing
- no “fixing” keyboards by intercepting and replaying device events
- no dependence on compositor-hostile injection hacks as the main path
- no hardcoded API keys
- no French-first UI or inherited Dictee UX assumptions

If a fallback typing path is ever added beyond clipboard, it should be clearly marked as fallback-only and disabled by default.

## Useful references already consulted

IBus reference implementation consulted during scaffolding:

- `https://github.com/PhilippeRo/IBus-Speech-To-Text`

ElevenLabs STT docs consulted:

- `https://elevenlabs.io/docs/overview/capabilities/speech-to-text`
- `https://elevenlabs.io/docs/api-reference/speech-to-text/convert`
- `https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime`

## Handoff assessment

This repo is in a good scaffold state, not a release state.

The architecture is pointed in the right direction:

- safer than Dictee-style raw input handling
- cleaner UX direction
- cleaner secret handling
- better Wayland/KDE fit

But the next person should assume they are taking over an MVP foundation, not a finished desktop app.
