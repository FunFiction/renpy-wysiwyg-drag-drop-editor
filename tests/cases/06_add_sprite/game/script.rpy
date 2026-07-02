# Add Sprite: the browser sees game/images/ and nothing else, bad names
# are blocked, collisions refused, closing without saving leaves no trace,
# and saving inserts show lines in add order before the paused statement.

define config.developer = True

image bg room = Solid("#00f")
image existing = Solid("#0ff", xsize=100, ysize=200)

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        try:
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            rows = wysiwyg_list_image_files()
            out.append("BROWSER: " + "; ".join(
                r["name"] + ("(BLOCKED)" if r["problem"] else "") for r in rows))
            leaks = [r["file"] for r in rows if not r["file"].lower().startswith("images/")]
            out.append("OUTSIDE-IMAGES-LEAK: " + repr(leaks))
            wysiwyg_add_character("existing")
            out.append("COLLISION: " + str(store.wysiwyg_status))
            # add, then close WITHOUT saving: no trace may land in the file
            wysiwyg_add_character("hero happy")
            wysiwyg_toggle()
            with io.open(os.path.join(config.gamedir, "script.rpy"), "r", encoding="utf-8") as f:
                stray = [l for l in f.read().splitlines() if l.strip().startswith("show hero")]
            out.append("DISCARD-LEAK: " + repr(stray))
            # for real now: two sprites, positioned, saved
            wysiwyg_toggle()
            wysiwyg_import_scene()
            wysiwyg_add_character("hero happy")
            wysiwyg_add_character("buddy")
            for c in store.wysiwyg_chars:
                if c.get("tag") == "hero":
                    c["x"] = 200.0
                if c.get("tag") == "buddy":
                    c["x"] = 900.0
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show existing at Transform(xpos=600, ypos=300)
    pause 0.3
    $ selftest()
    return
