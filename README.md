# Gabbee

Gabbee is a floating push-to-talk dictation bar for Linux desktops.

It is designed for real text entry workflows on terminals, editors, chat apps, browsers, and other foreground windows. On this machine, the delivery path prefers active-window typing first, then IBus when available, and mirrors successful dictation to the clipboard so text is still recoverable if the foreground app does not accept direct input.

The main target is KDE Plasma on Wayland, but the project is intentionally pragmatic: it should help you speak text into the app you are actually using instead of behaving like a fragile keyboard macro toy.

## Highlights

- Floating voice bar with global push-to-talk.
- Active-window typing fallback for terminals and editors.
- IBus integration for IME-style text commit when available.
- Clipboard mirroring for recovery and paste-based workflows.
- ElevenLabs STT and local Whisper support.
- PipeWire recording on modern Linux desktops.

## Principles

- English-first UI and docs.
- IME-first text delivery through IBus.
- No raw `/dev/input` grabs.
- No synthetic keypress loop as the primary path.
- Floating control bar instead of shortcut-only control.
- Provider secrets stay in `.env`, outside the codebase.

## Current status

- Floating Qt bar with `Start`, `Stop`, and `Cancel`.
- PipeWire recording via `pw-record`.
- STT provider abstraction.
- ElevenLabs STT provider using `ELEVENLABS_API_KEY` from `.env`.
- IBus engine with a local commit bridge.
- `gabbee-install-ibus` helper for writing the user-local IBus component.
- Active-window typing fallback for terminals and editors.
- Clipboard mirroring for recovery and paste-based workflows.

## Environment

By default Gabbee reads:

`~/.opencasenv/.env`

On this machine that resolves to:

`/home/cabewse/.opencasenv/.env`

Supported keys today:

- `ELEVENLABS_API_KEY`
- `GABBEE_STT_PROVIDER`
- `GABBEE_LANGUAGE_CODE`
- `GABBEE_ELEVENLABS_MODEL_ID`
- `GABBEE_ELEVENLABS_BASE_URL`
- `GABBEE_AUDIO_SOURCE`
- `GABBEE_ENV_FILE`
- `GABBEE_TOGGLE_SHORTCUT`
- `GABBEE_WHISPER_LOCAL_MODEL`
- `GABBEE_WHISPER_LOCAL_DEVICE`
- `GABBEE_WHISPER_LOCAL_COMPUTE_TYPE`

To use local Whisper transcription, first install the optional dependency group:

```bash
python3 -m pip install --user -e .[whisper_local]
```

Then set:

```bash
export GABBEE_STT_PROVIDER=whisper_local
```

Optional tuning keys:

- `GABBEE_TOGGLE_SHORTCUT` (default: `F5`)
- `GABBEE_WHISPER_LOCAL_MODEL` (default: `tiny`)
- `GABBEE_WHISPER_LOCAL_DEVICE` (default: `cpu`)
- `GABBEE_WHISPER_LOCAL_COMPUTE_TYPE` (default: `default`)

## Architecture

Gabbee is split into three layers:

1. `gabbee-bar`
   A floating controller UI for recording and status.
2. `gabbee-engine`
   An IBus engine process that owns focused text commit.
3. `gabbee.stt`
   Provider adapters for transcription.

The bar records audio, asks the configured STT backend for text, and then tries to deliver the transcript in a practical order: active-window typing first, IBus when it is the better path, and clipboard mirroring as a safety net.

## Local install

### 1. Install system packages

On Arch or Manjaro, install the runtime pieces first:

```bash
sudo pacman -S --needed python python-pip ibus pipewire wl-clipboard gobject-introspection
```

Notes:

- The IBus engine needs `gi`, so use the system Python at `/usr/bin/python3`.
- `pw-record` comes from PipeWire.
- `wl-clipboard` is used for the clipboard fallback on Wayland.

On Ubuntu or Debian-based systems, the equivalent packages are:

```bash
sudo apt update
sudo apt install -y python3-venv python3-gi gir1.2-ibus-1.0 ibus pipewire-bin wl-clipboard
```

### 2. Create a virtual environment and install Gabbee

For a development install from this tree:

```bash
cd /home/cabewse/gabbee
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

If you want local Whisper instead of ElevenLabs:

```bash
python -m pip install -e .[whisper_local]
```

### 3. Create or update the environment file

Gabbee reads `~/.opencasenv/.env` by default.

For ElevenLabs:

```bash
mkdir -p ~/.opencasenv
cat > ~/.opencasenv/.env <<'EOF'
ELEVENLABS_API_KEY=your-key-here
GABBEE_STT_PROVIDER=elevenlabs
GABBEE_LANGUAGE_CODE=en
EOF
```

For local Whisper:

```bash
mkdir -p ~/.opencasenv
cat > ~/.opencasenv/.env <<'EOF'
GABBEE_STT_PROVIDER=whisper_local
GABBEE_LANGUAGE_CODE=en
GABBEE_WHISPER_LOCAL_MODEL=tiny
GABBEE_WHISPER_LOCAL_DEVICE=cpu
GABBEE_WHISPER_LOCAL_COMPUTE_TYPE=default
EOF
```

### 4. Install the IBus component and launcher

Run the one-shot setup command:

```bash
source /home/cabewse/gabbee/.venv/bin/activate
gabbee-install-ibus --setup --icon /home/cabewse/gabbee/gabbee.png
```

`--setup` writes the IBus component, installs the icon, writes the launcher, and attempts an IBus restart.  
It also auto-detects the icon from `GABBEE_ICON_PATH` and common project locations, so this repo icon path is the recommended default here.

### 5. Enable the input method in IBus

Open IBus preferences:

```bash
ibus-setup
```

Then:

1. Open the `Input Method` tab.
2. Click `Add`.
3. Search for `Gabbee`.
4. Add `Gabbee Voice Input`.
5. Make sure that input method is selected when you want direct text commit into focused fields.
6. If the bar says it copied to the clipboard instead of typing, Gabbee is not the active IBus engine for that field.

### 6. Run Gabbee

Start the floating bar:

```bash
source /home/cabewse/gabbee/.venv/bin/activate
gabbee-bar
```

Usage:

1. Focus a text field.
2. If you want explicit IBus commit behavior, switch your current input method to `Gabbee Voice Input`.
3. Click `Start`.
4. Speak.
5. Click `Stop`.
6. Gabbee will transcribe and try to type into the active window, use IBus when available, and mirror successful output to the clipboard.

On KDE Plasma Wayland, the first run may show a desktop portal prompt to approve the global `F5` push-to-talk shortcut. If you do not approve it, `F5` still works while the Gabbee window is focused.

### 7. Useful checks

Write the component file without starting the engine:

```bash
source /home/cabewse/gabbee/.venv/bin/activate
python -m gabbee.main_engine --write-component
```

If you only want to install the engine component first (for debugging), run:

```bash
gabbee-install-ibus
```

To install the launcher and icon only (skip re-writing component):

```bash
gabbee-install-ibus --skip-engine-install --all --icon /home/cabewse/gabbee/gabbee.png
```

## Why IBus

The point of the IBus boundary is to make voice input behave like text input, not like a keyboard macro recorder. That is the path that avoids repeat-key bugs, device grabs, and compositor-dependent hacks.

## Next steps

- Add persistent settings UI.
- Add preedit and streaming partial transcript support.
- Add a guided first-run setup flow for IBus and desktop integration.

## License

Gabbee is licensed under the GNU General Public License v3.0. See `LICENSE`.
