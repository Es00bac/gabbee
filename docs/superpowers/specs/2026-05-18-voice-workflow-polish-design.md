# Voice Workflow Polish Design

## Goal

Ship one bundled Gabbee release that makes voice input reliable for real work: queued utterances, smarter number handling, working Gemini STT configuration, safer broader macros, import/export, setup guidance, and visible polish for unfinished UI paths.

## Scope

This release includes:

- queued push-to-talk utterances for accidental releases and slow transcription
- context-aware number normalization
- Gemini STT configuration and error handling fixes
- command-mode macro expansion with terminal text insertion but no auto-submit
- Command Studio macro/profile UI replacing placeholder pages
- import/export for advanced voice behavior
- guided setup checks for desktop integration
- cleanup of rough debug output and reload gaps
- tests and README updates for the new behavior

This release does not include arbitrary shell execution, automatic terminal command submission, or fake streaming/preedit behavior when providers do not expose partial transcripts.

## Design principles

Normal dictation remains prose-first and text-only. Command behavior remains opt-in through the command shortcut. Macro actions may type terminal command text, but must never press Enter automatically. Recording must stay responsive even when transcription is slow.

## Utterance queue

Replace the current single recording/transcription flow with a FIFO utterance queue.

Each stopped recording becomes an utterance job containing:

- sequence number
- audio path
- mode: dictation or command
- created timestamp
- processing status

Gabbee should allow a new recording while prior utterances are transcribing or delivering. Jobs are delivered in sequence order even if later transcription work finishes earlier.

Controller-level behavior:

- `Start` or dictation shortcut starts a new recording when no recording is active, even if queued jobs are processing.
- `Stop` queues the recording and returns the app to a state where recording can start again.
- A background worker drains queued jobs in order.
- `Cancel` cancels the active recording. If queued jobs exist, the UI exposes a clear-pending action rather than silently discarding work.
- State snapshots include queue depth and active processing status.

## Dictation chaining

Dictation jobs that arrive close together are treated as one chain before text processing. This supports accidental button release and slow local Whisper.

Example:

```text
Utterance 1: The coordinates are nine point two
Utterance 2: seven one eight three north by four point
Utterance 3: one two three four five six seven east
```

Final delivery:

```text
The coordinates are 9.27183 north by 4.1234567 east
```

The continuation window should be short and configurable later; for this release it can be a fixed internal value around a few seconds. Command-mode jobs do not merge by default. Each command utterance remains a separate action unit.

## Number normalization

Move number handling out of the current broad `_convert_numbers` routine into a dedicated number normalizer used by `TextProcessor` after vocabulary replacement and dot conversion.

The normalizer should classify surrounding context before deciding whether to emit words or digits.

Required behavior:

- Casual prose ranges stay natural: `I gave Bill one or two dollars.`
- Decimal and coordinate contexts collapse digit sequences: `9.27183 north by 4.1234567 east`.
- Scientific and measurement contexts prefer normalized digits: `186,000 miles per second`.
- Mixed STT artifacts like `100 eighty-six 1000` can repair to one number when unit/science context makes that more plausible than a list.
- Technical contexts such as versions, coordinates, IDs, addresses, code, measurements, and explicit `digit/number` cues prefer digits.
- Normal prose without technical cues should avoid aggressively digitizing small numbers.

The implementation should be deterministic and test-driven from examples rather than probabilistic.

## Gemini STT

Fix Gemini so a correctly configured API key and model produce a clear request and understandable failures.

Changes:

- Align dataclass and config defaults to one valid Gemini model.
- Keep `GEMINI_API_KEY` in the env file model; do not store secrets elsewhere.
- Validate missing key with a clear runtime error.
- Improve non-200 API error messages so invalid key/model/endpoint failures are visible in the bar.
- Keep request construction isolated enough for tests to assert payload shape without live API calls.
- Document Gemini setup keys in the README and config UI.

## Macro system

Command Studio should grow from simple commands into a broader command-mode macro system.

Supported action types:

- type text
- type terminal command text without pressing Enter
- press key combo
- wait
- multi-step macro sequence

Macros are only activated in command mode. Dictation mode never interprets ordinary words as macros.

Profiles group vocabulary, commands, and macros into reusable sets. Profiles may be enabled/disabled from Command Studio. The default profile preserves current behavior.

Macro safety boundaries:

- no arbitrary shell execution
- no automatic terminal submit
- no hidden destructive actions
- preview shows the exact action sequence before saving or testing

## Import/export

Add import/export for advanced configuration, including:

- vocabulary
- commands
- macros
- profiles
- metadata schema version

Import should validate shape and show clear errors for invalid files. Export should write a portable JSON file without provider secrets or env values.

## First-run and setup checks

Add a guided setup/checks flow reachable from the configuration window or tray.

Checks include:

- env file exists
- selected STT provider has required config
- `pw-record` exists
- clipboard command exists (`wl-copy` or `xclip`)
- IBus component status is understandable
- desktop portal shortcut status is understandable
- optional Plasma service/plasmoid status is understandable

Checks are informational by default. Any install, restart, or system modification remains explicit user action.

## UI/status updates

The floating bar should show queue-aware status. Minimal status text is enough for this release:

- recording
- transcribing with queue depth
- delivering with queue depth
- idle
- error

The bar should continue to expose Start, Stop, Cancel, Hide, Pin, and Settings. Button enabling must account for active recording and queued processing separately.

Tray and Plasma/DBus state should expose queue-aware state without forcing the plasmoid to understand internal implementation details.

## Internal architecture

Add focused internal units instead of growing the controller indefinitely:

- utterance job model
- queue/coordinator owned by `GabbeeController`
- number normalizer used by `TextProcessor`
- macro action/schema types in advanced config
- import/export helpers for advanced config
- setup check service used by UI/tests

The controller remains responsible for orchestration, but parsing, macro schema handling, setup checks, and number normalization should be separately testable.

## Testing

Add or update tests for:

- queued recordings process in FIFO order
- new recordings can be queued while transcription is active
- dictation utterances join inside the continuation window
- command utterances remain separate
- cancel behavior for active recording and pending queued work
- number examples from this spec
- money/prose ranges, coordinates, decimals, measurements, IDs, versions, and mixed STT artifacts
- Gemini missing key, configured key, payload shape, API error, malformed response, and empty response
- advanced config schema loading/saving for macros and profiles
- import/export validation
- Command Studio macro/profile UI behavior
- setup checks without mutating the system

## Documentation

Update README to document:

- queued utterance behavior
- Gemini setup
- number handling expectations
- macro safety model
- import/export
- setup checks

Remove or revise README next-step entries that this release completes. Keep any genuinely deferred streaming/preedit work listed as future work.

## Acceptance criteria

- Pressing the push-to-talk shortcut again while previous audio is processing queues another utterance instead of being ignored.
- Queued utterances deliver in spoken order.
- Short dictation continuation chains can produce one normalized final sentence.
- Command-mode utterances queue but remain separate commands.
- The three motivating number examples produce the expected outputs in tests.
- Gemini has clear config defaults and actionable error messages.
- Command Studio has real macro/profile pages rather than placeholders.
- Terminal macros type text only and never auto-submit.
- Import/export works without including API keys or env secrets.
- Setup checks report status without making system changes unless explicitly invoked.
- README and tests reflect the shipped behavior.
