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
| 06_add_sprite | browser limited to game/images/, bad names blocked, discard leaves no trace, inserts in add order |
| 07_menu_insert | inserting a new sprite while the game waits on a `menu:` |
| 08_clean_save | a save with no edits leaves files byte-for-byte identical |

Ren'Py opens a real window for a few seconds per case; the whole suite
takes about a minute.
