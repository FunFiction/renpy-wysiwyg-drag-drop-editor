# The first five minutes of a hostile new user: F5 and Import before any
# scene exists, F5 spammed, and a save into a read-only script file (a
# file locked by Dropbox/Perforce/antivirus looks the same).

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest_empty():
        # Runs before any scene/show statement was executed.
        out = store._selftest_out
        wysiwyg_toggle()
        wysiwyg_import_scene()
        out.append("EMPTY-IMPORT: " + str(store.wysiwyg_status))
        out.append("EMPTY-CHARS: " + str(len(store.wysiwyg_chars)))
        # a save with nothing imported must refuse politely
        wysiwyg_save_changes()
        out.append("EMPTY-SAVE: " + str(store.wysiwyg_status))
        wysiwyg_toggle()
        # F5 spam: five toggles end with the editor open, sixth closes it
        for _i in range(5):
            wysiwyg_toggle()
        out.append("SPAM-ACTIVE: " + str(store.wysiwyg_active))
        wysiwyg_toggle()
        out.append("SPAM-CLOSED: " + str(store.wysiwyg_active))

    def selftest_readonly():
        import io, os, stat, traceback
        out = store._selftest_out
        path = os.path.join(config.gamedir, "script.rpy")
        try:
            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            for c in store.wysiwyg_chars:
                if c.get("tag") == "eileen":
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 25
            store.wysiwyg_saved_runtime = False
            with io.open(path, "r", encoding="utf-8") as f:
                body_before = f.read()
            os.chmod(path, stat.S_IREAD)
            try:
                wysiwyg_save_changes()
                out.append("RO-SAVE: " + str(store.wysiwyg_status))
            finally:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            with io.open(path, "r", encoding="utf-8") as f:
                body_after = f.read()
            out.append("RO-UNCHANGED: " + str(body_before == body_after))
            # after unlocking, saving to this file may stay disabled for the
            # session (by design after a failed write) - record what happens
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("RETRY-SAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    $ store._selftest_out = []
    $ selftest_empty()
    scene bg room
    show eileen happy:
        xpos 100
        ypos 200
    pause 0.3
    $ selftest_readonly()
