# Editor test suite

Every directory under `cases/` is a tiny, self-contained Ren'Py project.
Each one plays a scene, drives the editor from a self-test function
(import, edit, save, close) and writes what happened to
`game/selftest_result.txt`. The runner copies each case to `build/`,
drops the current `game/wysiwyg_editor.rpy` in, runs the game headlessly
and checks the output against the case's `expect.txt`.

Run everything:

    python run_tests.py

Useful flags: `--case NAME` runs a single case, `--keep` leaves the build
directory of passing cases on disk, `--sdk PATH` points at a different
Ren'Py SDK (default: the 8.5.3 SDK path, or the RENPY_SDK env var).

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

Ren'Py opens a real window for a few seconds per case; the whole suite
takes about a minute.
