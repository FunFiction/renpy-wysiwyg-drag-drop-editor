# Insertion while the game is paused on a menu: the new show line must
# land right above the earliest tracked show at the same indent (joining
# the scene's reveal), and the file must still parse. The self-test fires
# from a timer screen because a python statement cannot run while a menu
# is waiting for input.

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
            filename, line = wysiwyg_get_current_position()
            out.append("PAUSED-AT: " + repr(wysiwyg_source_line_text(filename, line)))
            wysiwyg_add_character("hero happy")
            out.append("ADD: " + str(store.wysiwyg_status))
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

screen autotest():
    timer 0.6 action Function(selftest)

label start:
    scene bg room
    show existing at Transform(xpos=300, ypos=300)
    show screen autotest
    menu:
        "First choice":
            "picked first"
        "Second choice":
            "picked second"
    return
