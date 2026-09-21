# Command Studio Design

## Goal

Add a powerful advanced configuration interface for Gabbee that lets users manage custom vocabulary and secondary-binding commands without changing the safe behavior of primary dictation.

## Scope

Command Studio is a separate window from the existing basic Configuration dialog. The basic dialog remains focused on provider, shortcut, and transcription settings. Command Studio owns advanced spoken behavior:

- custom vocabulary and preferred spellings
- custom spoken commands
- preview/testing of transcript behavior
- future macro recording and profiles

Primary dictation stays text-only. Advanced commands, key actions, CLI command text, and future macros only run from the secondary command binding.

## User-facing behavior

### Vocabulary

Users can add enabled vocabulary entries with:

- spoken form
- preferred written form
- enabled state

Examples:

- `gabby` -> `Gabbee`
- `pie torch` -> `PyTorch`
- `react query` -> `TanStack Query`
- project, library, name, and command-line terms

Vocabulary applies to both normal dictation and command mode before output delivery.

### Commands

Users can add enabled command entries with:

- spoken phrase
- action type
- action payload
- enabled state

Version 1 action types:

- Type text
- Type CLI command text
- Press key or key combo

Commands only apply when Gabbee is recording from the secondary command shortcut. They do not apply from the primary dictation shortcut or the Start button.

CLI command actions are type-only by default. Gabbee types the command into the focused terminal but does not press Enter automatically.

### Built-in command interaction

Existing built-in commands such as delete, undo, copy that, paste that, and navigation commands remain available in command mode.

Custom commands take priority over built-in commands on exact spoken phrase matches. The UI warns about conflicts with built-ins but still allows intentional overrides.

## Data model

Store advanced behavior in a user-editable JSON file separate from the `.env` settings.

Initial schema:

```json
{
  "metadata": {
    "version": 1
  },
  "vocabulary": [
    {
      "spoken": "gabby",
      "written": "Gabbee",
      "enabled": true
    }
  ],
  "commands": [
    {
      "spoken": "run tests",
      "action": {
        "type": "type_text",
        "text": "pytest"
      },
      "enabled": true
    }
  ]
}
```

Action schema:

```json
{ "type": "type_text", "text": "pytest" }
{ "type": "type_cli", "text": "git status" }
{ "type": "press_key", "key": "Control+c" }
```

`type_cli` behaves like `type_text` in version 1, but it is distinct in the model so the UI can label and later constrain shell-oriented commands.

## UI layout

Command Studio opens from the tray menu and from the basic Configuration dialog.

Recommended layout:

- left sidebar navigation
  - Vocabulary
  - Commands
  - Test / Preview
  - Macros, disabled until implemented
  - Profiles, disabled until implemented
- main editor area for the selected page
- bottom actions: Save, Revert, Close

### Vocabulary page

The Vocabulary page shows a table with:

- Enabled
- Spoken
- Written

Actions:

- Add
- Edit
- Delete
- Save

The page should make common examples visible so users understand the feature quickly.

### Commands page

The Commands page shows a table with:

- Enabled
- Spoken phrase
- Action type
- Output/action preview

The editor supports:

- Type text
- Type CLI command
- Press key combo

The page explains that these commands only run through the secondary command shortcut.

### Test / Preview page

The Test / Preview page includes:

- transcript input
- mode toggle: Dictation or Command
- preview output
- warnings for vocabulary replacements, command matches, built-in conflicts, and disabled entries

Preview does not deliver text or press keys. A future explicit test button may type into the focused window, but preview-only is the default for version 1.

## Processing architecture

Introduce a small advanced configuration layer that loads and saves the JSON file. The controller should build `TextProcessor` with the advanced configuration, similar to how it already passes keyword maps and vocabulary paths.

`TextProcessor` remains the boundary that converts transcript text into delivery actions:

1. apply enabled vocabulary replacements
2. normalize dots and numbers
3. if command mode is off, return text only
4. if command mode is on, check enabled custom command exact matches first
5. fall back to built-in keyword command parsing

The controller continues to decide whether command mode is active based on which shortcut started recording.

## Safety rules

- Primary dictation never runs commands or macros.
- CLI commands are type-only by default and do not press Enter in version 1.
- Preview is non-destructive.
- Conflicts with built-in commands are warnings, not hard errors.
- Disabled commands and vocabulary entries remain stored but inactive.
- Invalid JSON should not erase the existing advanced config; the UI should report the load error and preserve the file.

## Future phases

### Macro recording

Add a Macro page that records a sequence of actions, lets users edit each step, and replays only from the secondary command binding.

Macro action types can include typed text, key combos, delays, and command-mode-only action chains.

### Profiles

Add profiles for context-specific dictionaries and command sets, such as Terminal, Editor, Browser, or per-project profiles.

### Import/export

Add import/export for vocabulary and commands so users can share or back up configuration.

## Testing strategy

Unit tests should cover:

- loading and saving the advanced config JSON
- invalid config handling without data loss
- vocabulary replacements in dictation and command mode
- custom command exact-match priority over built-ins
- command actions active only when command mode is enabled
- CLI command actions producing typed text without Enter
- Command Studio table add/edit/delete behavior
- preview output for dictation and command modes

UI tests should use the existing offscreen Qt pattern used by current bar and tray tests.
