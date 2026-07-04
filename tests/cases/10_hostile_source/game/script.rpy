# Hostile source file: non-ASCII content around every edited line, an
# explicit zorder, an explicit onlayer, a Polish trailing comment on a
# rewritten line, and NO newline at the end of the file (the paused
# statement is the last physical line).

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=300, ysize=600)
image nowy sprite = Solid("#ff0", xsize=200, ysize=400)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        second = False
        try:
            with io.open(os.path.join(config.gamedir, "script.rpy"), "r", encoding="utf-8") as f:
                body_before = f.read()
            # assembled so this source line cannot match itself
            second = ("hide" + " lucy") in body_before
            if second:
                # Second run: the rewritten file parsed from scratch and
                # the game reached this point - assert it BEHAVES right.
                out.append("SECOND-RUN: rewritten file parsed and executed")
                wysiwyg_toggle()
                wysiwyg_import_scene()
                out.append("IMPORT-2: " + str(store.wysiwyg_status))
                for c in store.wysiwyg_chars:
                    out.append("TAG2 " + str(c.get("tag")) + ": zorder=" + repr(c.get("zorder")) + " onlayer=" + repr(c.get("onlayer")))
                wysiwyg_toggle()
            else:
                wysiwyg_toggle()
                wysiwyg_import_scene()
                out.append("IMPORT: " + str(store.wysiwyg_status))
                for c in store.wysiwyg_chars:
                    out.append("TAG " + str(c.get("tag")) + ": zorder=" + repr(c.get("zorder")) + " onlayer=" + repr(c.get("onlayer")) + " locked=" + repr(c.get("locked")))
                def move(tag, dx):
                    for c in store.wysiwyg_chars:
                        if c.get("tag") == tag and not c.get("locked"):
                            c["x"] = wysiwyg_float(c.get("x", 0), 0) + dx
                move("eileen", 15)
                move("lucy", 15)
                wysiwyg_add_character("nowy sprite")
                out.append("ADD: " + str(store.wysiwyg_status))
                store.wysiwyg_saved_runtime = False
                wysiwyg_save_changes()
                out.append("SAVE-1: " + str(store.wysiwyg_status))
                # remove lucy: the hide is inserted before the LAST line of
                # a file that has no trailing newline
                wysiwyg_remove_character("lucy")
                out.append("REMOVE: " + str(store.wysiwyg_status))
                store.wysiwyg_saved_runtime = False
                wysiwyg_save_changes()
                out.append("SAVE-2: " + str(store.wysiwyg_status))
                wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        target = "selftest_result.txt" if second else "selftest_run1.txt"
        with io.open(os.path.join(config.gamedir, target), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        if not second:
            # The runner requires selftest_result.txt after every run.
            with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
                f.write("RUN1-DONE (details in selftest_run1.txt)")
        renpy.quit()

label start:
    # Zażółć gęślą jaźń, ąćęłńóśźż.
    $ tekst_pl = "Zażółć gęślą jaźń, ąćęłńóśźż."
    scene bg room
    $ tekst_jp = "日本語のテキストもここにあります。"
    show eileen happy:
        xpos 100
        ypos 200
    show lucy mad at Transform(xpos=800, ypos=300) zorder 5 onlayer master  # komentarz ąę
    $ tekst_pl2 = "Więcej polskich znaków tuż pod edytowanymi liniami."
    pause 0.3
    $ selftest()