# Scene integrity: closing without saving restores the exact scene-list
# entries (same displayable objects, so running ATL animations survive);
# closing after a save shows the saved state, not a stale snapshot, even
# when an unsaved tweak happened after the save.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=300, ysize=600)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        try:
            sls = renpy.game.context().scene_lists
            before = [(e.tag, id(e.displayable)) for e in sls.layers["master"]]
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            # close WITHOUT saving: identical objects must come back
            wysiwyg_toggle()
            restored = [(e.tag, id(e.displayable)) for e in sls.layers["master"]]
            out.append("RESTORE-MATCH: " + repr(restored == before))
            # now edit + save + stray tweak + close: saved state must show
            wysiwyg_toggle()
            wysiwyg_import_scene()
            target = None
            for c in store.wysiwyg_chars:
                if c.get("tag") == "eileen":
                    target = c
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 50
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            out.append("SNAPSHOT-AFTER-SAVE: " + repr(WYSIWYG_RUNTIME.master_snapshot))
            target["x"] = wysiwyg_float(target.get("x", 0), 0) + 30
            store.wysiwyg_saved_runtime = False
            wysiwyg_toggle()
            at = sls.at_list["master"].get("eileen")
            shown_xpos = None
            try:
                shown_xpos = at[0].kwargs.get("xpos")
            except Exception:
                shown_xpos = getattr(at[0], "xpos", None)
            out.append("SHOWN-XPOS: " + repr(shown_xpos))
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy at Transform(xpos=400, ypos=300)
    show lucy mad:
        xpos 800
        ypos 300
        linear 0.5 ypos 280
        linear 0.5 ypos 300
        repeat
    pause 0.3
    $ selftest()
    return
