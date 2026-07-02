# Source-line resolution in a looping label: the engine's own line log
# deduplicates entries (first-execution order), so only the editor's
# execution-ordered log can tell that the day_loop show ran most recently.

define config.developer = True
default day = 1

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image bg room = Solid("#00f")

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
            for c in store.wysiwyg_chars:
                out.append("SRC tag={0} conf={1} line-text={2!r}".format(
                    c.get("tag"), c.get("source_confidence"),
                    wysiwyg_source_line_text(c.get("source_file", ""), c.get("source_line", 0))))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label day_loop:
    show eileen happy at Transform(xpos=400, ypos=300)  # day-loop-show
    pause 0.05
    if day == 1:
        # runs once, on day 1 only; sits later in the deduped engine log
        show eileen happy at Transform(xpos=700, ypos=300)  # day-one-show
        pause 0.05
        $ day += 1
        hide eileen
        jump day_loop
    $ selftest()
    return

label start:
    scene bg room
    jump day_loop
