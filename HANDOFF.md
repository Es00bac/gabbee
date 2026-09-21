# Gabbee implementation handoff

## Current implementation

Gabbee now has the ElevenLabs-first semantic dictation and deterministic desktop-control layer described in the project roadmap.

The primary runtime is:

1. Capture the active non-Gabbee KWin window and focused AT-SPI control when push-to-talk begins.
2. Open an ElevenLabs `scribe_v2_realtime` session with manual commit, the previous Gabbee dictation tail, and any opted-in profile hints.
3. Tee mono 16 kHz PCM to the WebSocket and a recovery WAV while showing partial text only as bar/preedit state.
4. Commit once on release; use Scribe v2 batch recovery if realtime fails.
5. Parse semantic numbers/entities and spoken layout using captured surrounding text and the selected schema-v3 app profile.
6. Deliver once through verified IBus, AT-SPI, restored clipboard paste, or `dotool`, retaining failed audio and deleting successful audio.

Normal dictation never enters command dispatch. The separate command shortcut resolves only exact compiled patterns or the deterministic built-in grammar.

## Main modules

- `src/gabbee/models.py`: public context, transcript, target, and typed action models.
- `src/gabbee/stt/base.py`, `stt/elevenlabs.py`: batch/streaming contracts and Scribe realtime implementation.
- `src/gabbee/audio.py`: PCM streaming plus recovery WAV.
- `src/gabbee/number_normalizer.py`, `text_processor.py`: semantic and punctuation pipeline.
- `src/gabbee/output.py`: focus-aware delivery router and clipboard restoration.
- `src/gabbee/advanced_config.py`: schema-v3 migration, preservation, and security validation.
- `src/gabbee/command_patterns.py`, `macro_runtime.py`: typed slots and cancellable actions.
- `src/gabbee/profiles.py`: automatic and manual profile selection.
- `src/gabbee/desktop.py`, `desktop_actions.py`: KWin, AT-SPI, and pointer backends.
- `src/gabbee/ui/target_overlay.py`: multi-monitor numbered overlay and recursive grid.
- `src/gabbee/ui/command_studio.py`: command, macro, profile, template, import/export, and dry-run UI.
- `src/gabbee/ui/first_run.py`, `config_window.py`, `bar.py`: setup, provider pages, diagnostics, state, and Last Dictation actions.

## Security and privacy invariants

- There is no shell, process, eval, script, or arbitrary executable action in the schema or typed action union.
- App launches resolve only installed `.desktop` entries.
- KWin executes generated templates containing only JSON-quoted internal window IDs and private callback names.
- Ambiguous windows/controls are numbered and never probabilistically selected.
- Plain dictation cannot trigger actions.
- Previous provider context is only Gabbee’s own successful dictation tail, capped to 50 characters at the realtime boundary.
- Diagnostics reject transcript/audio categories and redact credential-like detail.
- New credentials prefer Secret Service/KDE Wallet; legacy `.env` files are still read and never automatically removed.

## Verification state

The repository’s complete pytest suite covers legacy behavior plus semantic formatting, realtime events and recovery, entity offsets, typed patterns, v1/v2-to-v3 migration, forbidden actions, profile selection, delivery order, focus changes, clipboard restoration, AT-SPI/pointer preference, KWin data, recursive grids, macro failure/cancellation, WAV recovery, and diagnostics privacy.

Use:

```bash
PYTHONPATH=src pytest -q
python -m py_compile $(rg --files src tests -g '*.py')
```

The live KWin bridge has been smoke-tested against Plasma for enumeration, geometry, and activation of the already-active window. Full release qualification still requires non-destructive manual passes through Konsole, Kate, Firefox, Chromium/Electron, and representative chat fields, with the packaged IBus namespace installed.

## Deliberate boundary

Inaccessible canvases and games use a deterministic recursive screen grid. Screenshot/vision-based targeting is not implemented; a future `TargetResolver` adapter can add it without changing the command or action contracts.
