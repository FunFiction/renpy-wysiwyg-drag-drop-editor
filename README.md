# Ren'Py Drag-and-Drop WYSIWYG Editor (v1.0.0)

A single-file overlay tool for Ren'Py games. You arrange, rotate, scale, animate and filter sprites visually while the game runs, and the editor writes the result straight back into your `.rpy` source files.

Instead of editing coordinates in a text editor and relaunching the game to check them, press **F5**, drag the characters where you want them, and save.

**Requires:** Ren'Py 8.5.x (developed and tested on 8.5.3; older versions untested, 7.x will not work). **Quick start:** copy `game/wysiwyg_editor.rpy` into your project's `game/` folder, run the game in developer mode, press **F5**.

---

## Screenshots

### 1. Initial Launch Overlay (F5)
![Initial Launch](assets/editor_menu_01.jpg)
*The editor overlay right after opening it with F5.*

### 2. Main Interface (After Scene Import)
![Main Interface](assets/editor_menu_02.jpg)
*The main panel displaying imported sprites, character lists, and basic layout buttons.*

### 3. Color Filters Menu in Action
![Color Filters Menu](assets/editor_menu_03.jpg)
*Real-time rendering of filters including blur, brightness, contrast, saturation, hue shift, inversion, and sepia.*

### 4. Motion FX Animations
![Motion FX Menu](assets/editor_menu_04.jpg)
*Configuring dynamic animations like breathe, shake, float, sway, bounce, sink, and blink with custom strengths.*

### 5. Advanced Transformations (Rotation & Scaling)
![Transformations](assets/editor_menu_05.jpg)
*Demonstration of precise rotation adjustments, linked/unlinked scaling, and image flipping.*

### 6. Code Compare Panel (Show Code)
![Code Compare](assets/editor_menu_06.jpg)
*Side-by-side comparison of the original script lines vs. the newly generated code before writing to disk.*

---

## Key Features

- **Live Drag-and-Drop Layout**: Click and drag any sprite on the screen to reposition it instantly.
- **Direct Source Code Rewriting**: When you click **Save Changes**, the editor locates the exact `show` statements that rendered the active sprites (using Ren'Py's execution line log `config.line_log`) and rewrites the parameters in-place inside your `.rpy` files using `renpy.scriptedit`. The scene's standalone `with` reveal line can be rewritten too; the `scene` statement itself is never touched.
- **Automatic Backups + Write Verification**: Before every save, each touched file is backed up into `game/wysiwyg_backups/` (rotated, 10 newest per file plus the session baseline). After every save the whole file is re-parsed with the engine parser. If anything is wrong, the backup comes back automatically on the spot.
- **Only Changed Lines Are Written**: Characters you did not modify are never rewritten, so untouched statements keep their original transitions, at-lists and formatting.
- **Animated Characters Are Locked, Not Broken**: A character shown with an animated ATL block or a custom transform is imported as *locked*: it stays live and animated on screen, cannot be dragged, and its source line is never rewritten. Placements the editor can fully round-trip (`at left/center/right`, `Transform(...)` calls and ATL blocks using only position properties) stay editable; anything else is locked rather than risked.
- **Motion FX are self-contained**: the first save that uses a Motion FX also writes `game/wysiwyg_motion_fx.rpy` with standalone transform definitions, so saved lines keep working even if you remove the editor before release.
- **Add sprites from `game/images/`**: the "+ Add" button opens a file browser limited to the images folder, the one Ren'Py auto-defines images from and the only one the browser can see. Files are grouped by name prefix (`chloe_*`, `bg_*`), a search box filters by name or path, and hovering an entry shows a floating image preview. Pick a file, drag the sprite into place, and Save Changes inserts a proper `show` line right above the scene's earliest tracked `show`, so the new sprite is revealed by the same transition as the rest of the scene instead of popping in after it (falls back to the paused statement when there is no such anchor). Close without saving and the sprite vanishes without a trace.
- **Show transition presets (`with`)**: each character has an "Appear (with)" row (None / 0.25s / 0.5s / 1s dissolve / fade) that edits the `with` clause on its show line - both for existing characters and freshly added sprites. Equivalent spellings light up the matching preset (a game's bare `dissolve` selects "0.5s") and clicking an equivalent preset never rewrites the author's text. Any value the preset buttons cannot represent (your own transition, or a custom time like 0.75s) stays visible as highlighted text under the row, so the panel never looks like "None" when a transition is set. A "Restore original" button brings the game's own value back after any change, and an "s..." field takes a custom dissolve time in seconds.
- **Scene reveal transition**: the common script style puts the reveal on a standalone `with dissolve` line below the shows. The editor detects that statement (first `with` between the scene line and the paused statement) and exposes it as one shared scene-level value with the same presets, custom seconds, and a Restore original button. Save rewrites the `with` line in place after re-checking its text; transitions the editor does not model (e.g. a custom `flash`) are displayed verbatim and round-trip untouched unless explicitly replaced. A trailing `# comment` on the line is ignored when reading the transition and kept when the line is rewritten. Because the value belongs to the scene rather than to a character, the section also appears when nothing is selected, so removing the last sprite does not hide it. A sprite with its own transition is inserted at the paused statement (its transition should fire there, not mid scene build-up). A standalone `with` statement on its own script line is a different statement and is never touched.
- **Remove characters from the scene**: the "Del" button next to a character marks it for removal; Save Changes then inserts a `hide TAG` line before the statement the game is paused on. The original `show` line and the image definitions are never touched, so earlier parts of the scene play exactly as before. Removing a sprite that was added but never saved simply discards it. "Undo remove" un-marks a character before saving.
- **Closing asks before discarding**: closing the editor (the Close button or F5) while there is unsaved work opens a confirmation that lists exactly what would be lost, e.g. `chloe (moved/edited)`, `buddy (added)`, `scene with`. The rest of the editor is frozen while the box is open, so the list cannot go stale under it. Esc goes back, a second press of F5 discards and closes, and the box's own Save Changes button saves instead of closing. With nothing unsaved, Close and F5 exit immediately, as before.
- **Save errors are logged**: every failed part of a save is appended to `game/wysiwyg_debug.txt` as a `[SAVE-ERROR]` line, next to the existing `[INSERT]`/`[HIDE]`/`[SAVE]` entries. The status bar only has room for the first two errors; the log keeps all of them.
- **Uncertain sources are gated, not silently saved**: when the engine's line log is empty (after an autoreload or loading a save), characters import as "uncertain" - matched by scanning the script instead of watching it run. Saving a moved uncertain character now opens a confirmation box (with a Show Code shortcut) instead of writing right away. And when the game is still paused on the exact statement of the last save, re-import carries the previous known-good source lines over, so the editor's own save-autoreload cycle no longer degrades anything to uncertain.
- **Bypasses Default Anchors**: Grabs live bounds via `renpy.get_image_bounds`. The parsed source line is only trusted if it matches the live render within 2 pixels, so the editor also works in games with custom anchors or menu branching.
- **Center-Based Anchoring**: Saves lines in the format `show TAG at Transform(xpos=CX, ypos=CY, xanchor=0.5, yanchor=0.5, ...)`. Center anchors are invariant under rotation and scaling, and explicit anchors prevent issues with default game configurations.
- **Rotated Bounding Box Match**: The drag container matches the renderer's exact rotated bounding box (incorporating `rotate_pad=True` calculations and integer clipping) to avoid 1px shifting bugs when saving.
- **Virtual Resolution Scaling**: The UI is designed at 1080p and scales by `config.screen_height / 1080.0`, so the panel stays proportional at 720p or 4K.
- **Core Transform Controls**:
  - Horizontal/Vertical flipping (`xzoom`/`yzoom`).
  - Linked/Unlinked scale locking.
  - Rotation slider & manual degree input.
  - Opacity/Alpha slider.
  - Layout presets (At Left, At Center, At Right).
- **Color Filters**: Apply live filters including Blur (px), Brightness, Contrast, Saturation, Hue rotation, Inversion, and Sepia matrices using `matrixcolor`.
- **Motion FX Animations**: Toggle built-in animated effects (breathe, shake, float, sway, bounce, sink, blink) with adjustable strength.
- **Grid Overlay**:
  - Toggleable background grid overlay with 100px steps to help with visual alignment.
- **Undo Stack**: Maintains a history of up to 50 operations.
- **Code Compare Panel**: View your original source lines side-by-side with the generated code before writing to disk.

---

## Interface & Controls Reference

### 1. Global Toolbar (Top)
- **Characters** tab: Selects the character editing panel.
- **Import Scene**: Scans the active Ren'Py master layer, finds the exact source files and lines, and loads the sprites into the editor.
- **Save Changes**: Rewrites the mapped lines in your `.rpy` files in-place with the updated `Transform` code. A `●` dot on the button means there are unsaved changes.
- **Undo**: Undoes the last modification (holds up to 50 steps).
- **Show Code**: Opens the side-by-side code comparison panel to preview modifications before writing to disk.
- **Grid**: Toggles a background grid with 100px steps.
- **Clear Editor**: Resets the editor state, discarding all unsaved transformations.
- **Close**: Exits the editor and restores the scene to its last saved/imported state. With unsaved work it first opens the confirmation described above.

> [!NOTE]
> The `●` dot is the editor's "something here" marker: on **Save Changes** it means unsaved changes exist, next to the selected character's name it means that character has unsaved edits, and on the **Color Filters** / **Motion FX** section buttons it means the selected character has an active filter or motion.

### 2. Base Controls Panel
- **Reset Pos**: Restores the character's X and Y coordinates to their last imported/saved state (a save becomes the new baseline).
- **Reset Transform**: Restores the character's scale, rotation, and opacity back to the values originally defined in the script.
- **Defaults**: Resets the character to default parameters (rotation `0`, scale `1.0`, opacity `1.0`).
- **At Left / At Center / At Right**: Snaps the character's center to the quarter, half, or three-quarter point of the screen width (note: this is not the same spot as Ren'Py's `left`/`right` transforms, which hug the screen edges).
- **Flip H / Flip V**: Mirrors the character horizontally or vertically (toggles negative `xzoom` or `yzoom`).
- **Rotation Slider**: Rotates the character between `-180°` and `180°`.
- **Linked / Unlinked**: Locks or unlocks the scale aspect ratio. When locked, scaling the X axis scales the Y axis proportionally.
- **Scale Sliders**: Changes the character's zoom level.
- **Opacity Slider**: Changes the character's transparency (`alpha`) from `0.0` (invisible) to `1.0` (opaque).
- **Nudge Buttons (`◄`, `►`, `▲`, `▼`)**: Move the selected character in 1px or 10px increments.
- **Nudge Step Toggle**: Click the **Step 1px / Step 10px** button to toggle the movement resolution for both the screen buttons and your keyboard's arrow keys.

> [!TIP]
> **In-place Value Editing**: You can click directly on the **Coordinates label** (`Center: X, Y`), the **Rotation angle**, or the **Scale values** in the panel to turn them into manual input fields, and type exact numbers (e.g. `960, 540` for the center position, `45.5` for rotation, `0.85` for scale). **Enter** or the **OK** button commits the value; **Esc**, the **✕**/**Cancel** button, or clicking the same field button again discards it. The custom-seconds fields (`s...`) next to the `with` presets work the same way, and clicking any preset closes a half-typed field.

### 3. Color Filters Panel
- **Defaults**: Resets all color filters to their neutral values (not to the values from your script - use Undo or re-import for those).
- **Reset (next to sliders)**: Resets only that specific filter back to its default state.
- **Blur**: Gaussian blur in pixels (`0px` to `20px`).
- **Brightness**: Adjusts image brightness matrix.
- **Contrast / Saturation / Hue / Invert**: Adjusts respective color filters dynamically.
- **Sepia ON/OFF**: Toggles a custom sepia matrix.

### 4. Motion FX Panel
- **Defaults**: Disables active motion animations.
- **Breathe / Shake / Float / Sway / Bounce / Sink / Blink**: Selects and runs the corresponding animation loop on the active character.
- **Strength**: Adjusts the speed, range, or amplitude of the selected animation.

---

## Installation

1. Copy the file `wysiwyg_editor.rpy` into the `game/` directory of your Ren'Py project.
2. Launch the game.
3. Press **F5** during play to open the editor.

To uninstall, delete `wysiwyg_editor.rpy` (and its `.rpyc`). If you used Motion FX, keep the generated `wysiwyg_motion_fx.rpy` so saved lines keep working; `wysiwyg_backups/` and `wysiwyg_debug.txt` can go whenever you like.

---

## Usage Guide

1. Play your game until you reach a scene containing sprites you want to edit.
2. Press **F5** on your keyboard to open the editor overlay.
3. Click **Import Scene** to read the characters currently shown on screen.
4. Select a character from the **On Scene** list or click them directly.
5. Drag the sprite to your desired position. You can also:
   - Use the **arrow keys** on your keyboard to nudge the sprite by the current step (1px by default; hold **Shift** for 10px).
   - Go to **Base Controls** to adjust rotation, scale, opacity, or flip the character.
   - Go to **Color Filters** to add blurs, brightness/contrast adjustments, or hue shifts.
   - Go to **Motion FX** to apply animations.
6. Click **Show Code** to compare your changes with the original code.
7. Click **Save Changes** to write the updated coordinates directly to your `.rpy` files.
8. Click **Close** (or press **F5**) to exit the editor. If anything is still unsaved, a confirmation lists it first; discarding restores the scene to its last saved/imported state and writes nothing to any file.

---

## Technical Notes

### Viewport Vertical Scrollbar Workaround
Ren'Py 8.5.x has a known bug where `scrollbars "vertical"` inside a `viewport` silently breaks child rendering or shows an empty viewport. This editor bypasses the issue entirely by using a separate `vbar` component mapped to `YScrollValue`, combined with `mousewheel True` and `draggable True` on the viewport.

### File Backups & Write Verification
Every click of **Save Changes** first copies each file it is about to touch into `game/wysiwyg_backups/<file>.<timestamp>.bak`, so every save has its own restore point, not just the first one of the session.

After writing, the editor immediately re-parses the whole file with Ren'Py's own parser:

- if the file parses, the save is confirmed in the status bar ("verified");
- if it does not, the pre-save backup is **restored automatically**, the save is reported as failed, and saving to that file is disabled until the game restarts (so a desynced session cannot keep writing).

> [!NOTE]
> The backups folder is rotated automatically: the 10 newest backups per file are kept, plus the first backup of the current session (your pre-editor baseline). Delete `game/wysiwyg_backups/` whenever you want a clean slate.

To restore an original file manually, replace the modified `.rpy` file with the corresponding `.bak` from `game/wysiwyg_backups/`.

### Debug log
The editor appends a line to `game/wysiwyg_debug.txt` for every import, insert, rewrite and save error (`[IMPORT]`, `[INSERT]`, `[HIDE]`, `[SAVE]`, `[SCENE-WITH]`, `[SAVE-ERROR]`). When a save did not do what you expected, this file answers "what did the editor actually write, and where" better than memory does. It is written as UTF-8 and starts over automatically past 2 MB; delete it whenever you like. Both this file and `game/wysiwyg_backups/` are excluded from built distributions.

### Locked (Read-Only) Characters
Characters whose `show` statement uses an **animated or otherwise unsupported ATL block** (e.g. `linear`, `ease`, `repeat`, or properties the editor cannot round-trip, like `zoom` inside a block), a **custom named transform**, or that were shown **from Python code**, are imported as *locked*:

- they stay live on the master layer, so their animation keeps playing while you edit others;
- they cannot be selected, dragged or reset, and their source line is never rewritten;
- the On Scene list and Show Code panel state the reason (e.g. `uses transform 'wobble'`).

Simple placements are not affected: `at left`, `at center`, `at right`, `Transform(...)` calls and ATL blocks that use only the position properties the editor round-trips (`xpos`, `ypos`, `xanchor`, `xalign`, offsets, …) remain fully editable. Anything beyond that set (a `zoom` or `alpha` inside an ATL block, a variable as a property value) locks the character instead of risking a lossy rewrite.

### The game menu and quit prompt while the editor is open
While the editor is open, it deliberately swallows the inputs that would advance or leave the game: dismiss clicks, rollback, skipping, and the game menu (Esc / right-click show a status hint instead). If you click the OS window's close **"X"** button, Ren'Py's quit prompt appears underneath the editor's input traps and cannot be clicked. In all cases: close the editor first (**F5** or **Close**), then use the menu or quit.

---

## Known limitations

Read this section before trusting the tool with a project that has no version control.

- **This is a scene editor, nothing more.** It edits sprite placement, transforms, filters, Motion FX and `with` transitions. It does not edit screens or UI, dialogue text, or animations; statements with animated ATL or custom transforms are imported as locked and left alone.
- **Developer builds only.** The editor activates only while `config.developer` is True. In a shipped build F5 does nothing, and saved lines keep working through `game/wysiwyg_motion_fx.rpy` even after you delete the editor file.
- **Saves are not transactional.** A save is a sequence of single-line writes, not one atomic operation. The protection is layered around that fact: a backup of every touched file before the write, a full engine re-parse after it, automatic restore from the backup when the re-parse fails, and a block on further saves to that file until the game restarts. If the game process dies in the middle of a write, put the `.bak` from `game/wysiwyg_backups/` back yourself.
- **The preview can be a pixel or two off.** For sprites positioned through one of the game's own transforms that includes zoom, the editor's drag preview is an approximation. The line that gets saved is computed from the live render bounds, so the file is right even when the preview is slightly off.
- **Do not save/load the game mid-edit.** The snapshot used to restore the scene when you close without saving does not survive a save/load cycle inside the editor session. Close the editor first, then use the game's save/load.
- **Translations are only partly guarded.** Inserting new lines while a `game/tl/` translation is playing is refused. Editing an existing `show` statement whose source already sits inside `game/tl/` is not blocked; do your editing in the base language.
- **The game menu and quit prompt are blocked while the editor is open.** Esc and right-click show a hint instead of the menu (Load/Main Menu would silently discard unsaved editor work), and the window's X button shows a quit prompt that cannot be clicked. Close the editor first (F5), then use the menu or quit. Details in Technical Notes.

---

## License

MIT - see [LICENSE](LICENSE). In practice:

- The editor is free, for commercial and non-commercial projects alike.
- Everything it writes into your `.rpy` files is yours, with no strings attached.
- The generated `game/wysiwyg_motion_fx.rpy` may ship inside any game, commercial or not, with no attribution required (the file says so in its own header).
- Ren'Py itself is licensed separately (MIT plus LGPL components); see the [Ren'Py license](https://www.renpy.org/doc/html/license.html) when distributing a built game.

---

## Changelog

### 1.0.0 (2026-07-04)

Feature-complete release; the save machinery is unchanged since 0.3.0.

- MIT license added, with an explicit no-attribution grant on the generated `wysiwyg_motion_fx.rpy`.
- The Show Code panel no longer previews lines the save would not write: unchanged characters read "unchanged - will not be written", the scene line is marked as never rewritten, and files whose saving is disabled after a failed write say so. What the panel displays branches on the same predicate the save loop uses.
- Release-hardening pass (two thorough review rounds over the UI, docs and hostile environments; ~30 findings fixed). Highlights: `game/wysiwyg_backups/` and `wysiwyg_debug.txt` are excluded from built distributions; a forgotten editor file no longer swallows F5 in shipped builds; Esc/right-click no longer open the game menu over unsaved editor work; tooltips are actually rendered now (hover help in the status corner); the debug log is UTF-8 and capped at 2 MB; the Code Compare panel scales below 1080p and stops re-reading files from disk on every hover; sprites whose tag the importer skips (`ui`, `black`, ...) are refused at add time instead of being orphaned after a save; every misleading UI string and README claim found by the audit was reworded to match what the code does.
- A seeded-random monkey test (case 13): 41 random editor operations per run, saves included, with invariants checked after every save (the file must still parse, locked lines must stay byte-identical, structural markers must survive); each run replays a different reproducible sequence.
- Three hostile-scenario test cases added: non-ASCII scripts with explicit `zorder`/`onlayer` clauses and no trailing newline, a brand-new user's first clicks including a save into a read-only file, and a project under a path with spaces and diacritics. They caught two real issues, both fixed: importing a scene with no editable characters said "Imported 0 character(s)" instead of something helpful, and a save that failed before writing anything warned "AUTO-RESTORE FAILED" about a file that was never touched.
- Version bumped to 1.0.0 to mark the feature set as final: fixes will keep coming, new features are not planned.

### 0.3.0 (2026-07-03)

This release replaces the 0.2.1 line published in June. The safety work from 0.2.1 (developer gate, pre-save line verification, restore after autoreload) is included here in a rebuilt and extended form; everything below is new since then. The whole release went through a internal review (21 findings) and four multi-agent review cycles, all findings fixed.

Added:

- Add sprites straight from `game/images/`: a browser with prefix grouping, name/path search and hover preview. New `show` lines are inserted above the scene's earliest tracked show (so the scene's own transition reveals them), with a fallback to the paused statement. Names are gated the way the engine defines them, and inserting into `game/tl/` is refused.
- Remove characters: Del marks a tracked character and Save Changes inserts a `hide TAG` line above the paused statement, leaving the original `show` untouched. Undo remove un-marks it; deleting a never-saved sprite leaves no trace in any file.
- `with` transition editing: per-character "Appear (with)" presets plus a custom-seconds field, and detection and rewriting of the scene's standalone `with` reveal line. Trailing comments on the `with` line survive the rewrite.
- Close confirmation: closing with unsaved work lists what would be discarded and freezes the editor until you answer. Esc backs out, a second F5 discards and closes.
- Per-save backups with write verification: every save re-parses the touched file with the engine parser and restores the backup automatically when parsing fails.
- Locked characters: animated ATL blocks and custom transforms stay live on screen and are never rewritten.
- Uncertain-source gate: characters matched by scanning the script (empty line log after autoreload or loading) need an extra confirmation before their lines are rewritten, and known-good sources are carried over across the editor's own save-autoreload cycle.
- A permanent automated test suite (`python tests/run_tests.py`; each case drives a real Ren'Py window unattended) covering round-trips, backups, inserts, removal, the close gate and the confirmation gates.
- `[SAVE-ERROR]` entries in `game/wysiwyg_debug.txt`, so failed saves can be diagnosed after the fact.

Changed:

- Inline numeric fields commit on Enter and cancel on Esc, a cancel button, or a second click on the field's own button; the OK button is no longer the only way out.
- Values read from the script (current `with` transition, source lines) are highlighted and separated from help text; long paths and image names wrap inside the panel instead of poking out of it.
- Save internals were deduplicated: one shared definition of "what needs saving" drives the save loop, the uncertain-save gate and the close gate.

Fixed:

- Orphaned trailing ATL comments after edits, stale line offsets after multi-line rewrites, and the remaining findings from the review cycles (overlapping confirmation dialogs, a live input keeping keyboard focus behind a dialog, a mouse drag surviving into a frozen dialog, comments leaking into the parsed scene transition, and others).

### 0.2.1 (2026-06-12)

Safety hardening: developer-build gate, pre-save line verification, scene restore after autoreload.

### 0.2.0

First public version: drag-and-drop placement, rotation and scale, color filters, Motion FX, grid, undo, Code Compare, in-place source rewriting.
