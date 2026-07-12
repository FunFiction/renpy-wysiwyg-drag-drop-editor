# Removing characters: a pending (unsaved) sprite is discarded with no
# trace, a tracked character's removal inserts `hide TAG` right above the
# paused statement while its original show line stays untouched, undo
# remove saves nothing, and closing without saving leaves the file
# byte-for-byte identical.

define config.developer = True

image bg room = Solid("#00f")
image alpha = Solid("#0f0", xsize=100, ysize=200)
image beta = Solid("#f00", xsize=100, ysize=200)

label main_menu:
    return

init python:
    def selftest():
        import io, os, shutil, filecmp, traceback
        out = []
        script = os.path.join(config.gamedir, "script.rpy")
        pristine = os.path.join(config.gamedir, "script_pristine.copy")
        try:
            shutil.copyfile(script, pristine)

            # mark for removal, close WITHOUT saving: no trace in the file
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            wysiwyg_remove_character("alpha")
            out.append("MARK: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
            out.append("CLOSE-NO-SAVE-IDENTICAL: " + str(filecmp.cmp(script, pristine, shallow=False)))

            # discarding a pending sprite touches nothing
            wysiwyg_toggle()
            wysiwyg_import_scene()
            wysiwyg_add_character("hero happy")
            wysiwyg_remove_character("hero")
            out.append("DISCARD: " + str(store.wysiwyg_status))
            wysiwyg_save_changes()
            out.append("SAVE-AFTER-DISCARD: " + str(store.wysiwyg_status))
            with io.open(script, "r", encoding="utf-8") as f:
                stray = [l for l in f.read().splitlines() if l.strip().startswith("show hero")]
            out.append("DISCARD-LEAK: " + repr(stray))

            # undo remove: the next save writes nothing
            wysiwyg_remove_character("alpha")
            wysiwyg_unremove_character("alpha")
            wysiwyg_save_changes()
            out.append("UNDO-REMOVE-SAVE: " + str(store.wysiwyg_status))

            # for real now: hide line written, character leaves the editor
            wysiwyg_remove_character("alpha")
            # adding the same tag while it is marked for removal is refused
            # with a message that names the actual way forward
            wysiwyg_add_character("alpha")
            out.append("ADD-WHILE-REMOVED: " + str(store.wysiwyg_status))
            wysiwyg_save_changes()
            out.append("REMOVE-SAVE: " + str(store.wysiwyg_status))
            out.append("STILL-TRACKED: " + repr(sorted([str(c.get("tag")) for c in store.wysiwyg_chars])))
            # re-adding a removed tag must land BELOW its hide line (at the
            # paused statement), or the sprite would never appear on replay
            wysiwyg_add_character("alpha")
            wysiwyg_save_changes()
            out.append("READD-SAVE: " + str(store.wysiwyg_status))
            # close gate: unsaved work opens the confirmation instead of
            # closing; the second request (F5) discards and closes
            alpha = [c for c in store.wysiwyg_chars if c.get("tag") == "alpha"][0]
            alpha["x"] = wysiwyg_float(alpha.get("x"), 0.0) + 25.0
            wysiwyg_request_close()
            out.append("CLOSE-GATE: active=" + str(store.wysiwyg_active)
                       + " " + repr([str(i) for i in (store.wysiwyg_confirm_close or [])]))
            wysiwyg_request_close()
            out.append("CLOSE-GATE-DISCARD: active=" + str(store.wysiwyg_active)
                       + " box=" + repr(store.wysiwyg_confirm_close))
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show alpha at Transform(xpos=300, ypos=300)
    show beta at Transform(xpos=700, ypos=300)
    pause 0.3
    $ selftest()
    return
