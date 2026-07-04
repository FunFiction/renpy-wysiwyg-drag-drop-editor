# Editor test suite

Every directory under `cases/` is a tiny, self-contained Ren'Py project.
Each one plays a scene, drives the editor from a self-test function
(import, edit, save, close) and writes what happened to
`game/selftest_result.txt`. The runner copies each case to a scratch
build directory, drops the current `game/wysiwyg_editor.rpy` in, runs
the game unattended (a real Ren'Py window opens for a few seconds per
case) and checks the output against the case's `expect.txt`.

Run everything:

    python run_tests.py

The runner needs a Ren'Py 8.5.x SDK: set the `RENPY_SDK` environment
variable to the SDK directory (the one containing `renpy.exe`) or pass
`--sdk PATH`. Other flags: `--case NAME` runs a single case, `--keep`
leaves the build directory of passing cases on disk.

## Cases

| Case | Covers |
|------|--------|
| 01_save_layer | multi-line statements, static ATL blocks with comments, `with`, `show expression ... as`, `behind`, exact round-trip |
| 02_locked_matrix | which statements are editable vs locked: custom/animated transforms, non-round-trip kwargs and values, inherited live transforms, engine property registry |
| 03_line_loop | picking the truly last-executed source line in a looping label |
| 04_snapshot_restore | closing without saving restores the exact scene; closing after a save shows the saved state |
| 05_backups | one backup per save, rotation, tree mirroring game/, locked characters never written |
| 06_add_sprite | browser limited to game/images/, bad names blocked, prefix grouping and name/path filtering, discard leaves no trace, inserts in add order above the earliest tracked show (uncertain source lines fall back to the paused statement), `with` presets insert at the paused statement, source carryover across a wiped line log, uncertain-save confirmation gate, standalone scene `with` detection and rewrite (trailing comment stripped from the expression and preserved by the rewrite) |
| 07_menu_insert | inserting a new sprite while the game waits on a `menu:` |
| 08_clean_save | a save with no edits leaves files byte-for-byte identical |
| 09_remove_char | removing characters: `hide` inserted above the paused statement, original show untouched, pending sprites discarded traceless, undo remove, close-without-save leaves the file identical, close gate (unsaved edits open a confirmation; a second request discards and closes) |
| 10_hostile_source | non-ASCII (Polish/Japanese) content around every edited line, explicit `zorder`/`onlayer` clauses and a unicode trailing comment surviving a rewrite, inserts into a file with no trailing newline, and a second run that re-parses the rewritten file from scratch and asserts its runtime effect |
| 11_first_click | the first five minutes of a new user: Import before any scene exists, Save with nothing imported, F5 spam, and a save into a read-only script file (error surfaced, file untouched, no false restore warnings, file disabled until restart) |
| 12_dziwna ścieżka | the whole project under a directory with a space and Polish diacritics: source rewrite, backup tree, and the motion fx companion file all land correctly |
| 13_monkey | seeded-random monkey test: 41 random operations per run (moves, transforms, motion fx, add, remove, undo, reset, re-import, save) with invariants checked after every save - the file must parse, the locked character's line must stay byte-identical, structural markers must survive. Run 2 replays a different seeded sequence on the file run 1 rewrote |
| 14_undo | undo semantics through the real UI functions: one op of every undoable class (nudge, rotate, scale, alpha, filter, motion fx, position preset) undone step by step back to the baseline with exact-value checks, the 50-entry cap, the empty-stack hint message, and the designed rule that undo reverts an added sprite's edits but never removes the sprite |
| 15_filters_flip | color filters and flip round-trip: brightness/saturation/hue/contrast/blur/sepia applied through the real setters plus a horizontal flip, saved, then re-imported in a second run - the values parsed back from the rewritten line must match, the line must stay editable, and the flip must survive as a negative xzoom |
| 16_ui_actions | the UI actions a coverage audit found untested: inline edit fields (position "x, y" parsing, comma decimals, validation errors, cancel), the four reset flavors (script originals vs neutral defaults, per-filter vs all-filters, motion), scale lock/unlock semantics, slider grab = exactly one undo entry, Clear Editor (state wiped, file untouched), and the close-gate buttons (save-from-gate, clean close, discard) |
| 17_remaining_ui | the last audit stragglers, completing 42/42 screen-action coverage: status display, selection cancelling a half-typed edit field, code panel toggle, the uncertain-gate Show Code shortcut, image browser open/close and group folding, sepia and preview-hide toggles, slider clamp callbacks, the reset-position wrapper, and hide_master_chars |

`python coverage_audit.py` (in this directory) lists any screen-action callable no test touches; it currently reports 0 of 42.

Ren'Py opens a real window for a few seconds per case; the whole suite
takes about a minute.
