# Floating Bar Visibility, Live Hotkey Rebinding, and Feature Backlog Design

## Summary

Gabbee will get a focused usability pass that fixes two immediate issues and records the next useful feature directions.

This implementation will:

- Add a visible way to hide the floating bar without quitting Gabbee.
- Add tray show/hide controls so the bar can be restored after hiding.
- Make shortcut changes saved from the running Configuration window take effect immediately.
- Fix global shortcut identity so dictation and command bindings are distinct.
- Keep the larger feature ideas as a prioritized backlog, not as scope for this patch.

## Goals

- The floating bar can be hidden while Gabbee keeps running in the tray.
- A user can change `GABBEE_COMMAND_SHORTCUT` to a key such as `F23` in the Configuration window and use it globally without restarting Gabbee, assuming the desktop portal accepts the binding.
- Dictation and command shortcuts are independently registered and refreshed.
- Tests cover visibility behavior and live shortcut refresh behavior.
- The README documents the new hide/show behavior and the highest-value future features.

## Non-goals

- Do not implement auto-hide in this patch.
- Do not implement custom dictionaries in this patch.
- Do not implement the spoken CLI command hotkey in this patch.
- Do not redesign the whole configuration window.
- Do not change transcription provider behavior except where config reload already requires it.

## Current behavior

`gabbee-bar` starts a `FloatingBar`, a `GabbeeController`, and a tray icon. The tray currently exposes `Show Bar`, `Configuration...`, and `Quit`. Closing the last window does not quit the application, but the bar itself has no explicit hide button.

The floating bar receives `toggle_shortcut` and `command_shortcut` values at construction time. It creates local `QKeySequence` fallbacks and portal-backed global shortcut bindings. The Configuration window saves updates to `.env` and reloads the controller transcriber, but it does not tell the running `FloatingBar` to replace its shortcut sequences or portal bindings. This means changes such as setting command mode to `F23` can require a restart.

Both portal bindings currently use the same internal shortcut id, `push_to_talk`, even though Gabbee now has dictation and command modes. Each binding lives in its own session, but distinct IDs and descriptions make the intent explicit and avoid ambiguity.

## Design 1: hideable floating bar

The floating bar will gain a small `Hide` button in its header. Clicking it hides the widget with `hide()` and leaves the application, tray icon, controller, and global shortcuts running.

The tray menu will expose visibility controls:

- `Show Bar` restores and raises the floating bar.
- `Hide Bar` hides it without quitting.
- A tray left-click continues to trigger `Show Bar` so hiding is easy to reverse.
- `Quit` remains the only action that exits Gabbee.

Pinned behavior must respect intentional hiding. Today the pin refresh timer calls `show()` while pinned, which would immediately resurrect a hidden bar. The refresh path will only raise the window when it is visible. Pin toggling can still show the bar because the user is interacting with the bar when toggling pin state.

## Design 2: live shortcut rebinding

`FloatingBar` will expose a method for applying new shortcut texts at runtime. After the Configuration window saves updates, `main_bar.py` will call this method with the saved dictation and command shortcuts.

The refresh method will:

1. Close any existing dictation and command portal bindings.
2. Update `toggle_shortcut_text`, `command_shortcut_text`, `shortcut_sequence`, and `command_shortcut_sequence`.
3. Reset pressed-state flags so a stale press cannot carry across the rebind.
4. Reset local fallback state for both shortcuts.
5. Recreate portal bindings for the new shortcut values.
6. Re-render the hint text so it reports whether each shortcut works globally or only while Gabbee is focused.

This method will be used for both dictation and command shortcuts, so `GABBEE_TOGGLE_SHORTCUT` and `GABBEE_COMMAND_SHORTCUT` behave consistently.

## Design 3: distinct portal shortcut identities

`PortalPushToTalkBinding` will accept a shortcut id and description from the caller. `FloatingBar` will create bindings with separate identities:

- Dictation: `dictation_push_to_talk`, description `Gabbee dictation push to talk`.
- Command mode: `command_push_to_talk`, description `Gabbee command push to talk`.

Portal activation and deactivation handlers will compare against the instance-specific shortcut id instead of a shared module constant.

## Data flow

Configuration save flow:

1. User opens Configuration from the tray or bar settings button.
2. User edits shortcut text, for example setting command mode to `F23`.
3. User clicks Save.
4. `main_bar.py` saves the updates through `AppConfig.save()`.
5. `GabbeeController.reload_transcriber()` keeps existing provider reload behavior.
6. `FloatingBar.apply_shortcuts(config.toggle_shortcut, config.command_shortcut)` refreshes the running UI and global bindings.
7. The hint label updates when each portal binding reports success or fallback.

Hide/show flow:

1. User clicks the bar `Hide` button or tray `Hide Bar`.
2. The window hides, but Gabbee remains running.
3. User left-clicks the tray icon or selects `Show Bar`.
4. The window shows, raises, and keeps the existing pinned behavior.

## Error handling

If portal registration fails after a shortcut refresh, Gabbee will keep the same fallback behavior it has today: the shortcut remains available while the Gabbee window is focused, and the hint text says so.

If the command shortcut field is blank, command shortcut registration is skipped and only dictation remains active.

Invalid or unsupported shortcut strings are outside this patch's scope. The future shortcut capture and validation feature will address that directly.

## Testing

Unit tests will cover:

- Clicking the new Hide button hides the bar without closing it.
- Pinned refresh does not show a deliberately hidden bar.
- Tray `Show Bar` and `Hide Bar` actions call the expected window behavior.
- Applying new shortcuts closes old bindings and creates new bindings.
- Applying `F23` to command mode updates `command_shortcut_sequence` and local fallback matching.
- Dictation and command portal bindings receive distinct IDs and descriptions.

Manual validation will cover:

- Launch `gabbee-bar`.
- Hide the bar from the bar button and restore it from the tray.
- Hide and restore from the tray menu.
- Change the command shortcut to `F23` in Configuration while Gabbee is running.
- Approve the new global shortcut prompt if the desktop portal asks.
- Confirm `F23` starts command-mode recording globally and release stops it.
- Confirm dictation still works with its configured shortcut.

## Future feature backlog

These features are valuable but are not part of this implementation patch.

1. **Shortcut capture and validation**
   Replace free-text shortcut fields with a press-to-capture flow. Show whether the shortcut is valid locally and whether the desktop portal accepted it globally.

2. **Custom words and dictionaries**
   Let users define names, project names, libraries, commands, and domain vocabulary. Gabbee should prefer entries such as `Gabbee`, the user's name spelling, and active project/library terms when cleaning up transcripts or preparing command text.

3. **Spoken CLI command hotkey**
   Add another bindable hotkey for terminal command dictation. Pressing it would record speech, transform the transcript into a shell command, and type it into the active terminal without requiring terminal-specific integration. This mode should be explicit and separate from normal dictation and command-keyword mode because it has higher safety risk.

4. **Auto-hide / low-clutter mode**
   Add an optional mode that hides the bar until recording, transcribing, error, or tray activation.

5. **Recording reliability indicators**
   Show microphone/source state, active provider, and output delivery path so users can diagnose whether Gabbee heard them and where the text went.

6. **Command-mode expansion**
   Add richer editing and navigation phrases, plus a UI for custom commands, while keeping command mode opt-in so normal dictation remains text-only.

7. **Startup and install polish**
   Add autostart, portal permission diagnostics, and a test-shortcuts button.
