# Classification matrix: what may be edited (and must round-trip exactly)
# versus what must be locked so the game's animation/props cannot be lost.

define config.developer = True
define my_rot = 30

image a1 = Solid("#111", xsize=80, ysize=160)
image a2 = Solid("#222", xsize=80, ysize=160)
image a3 = Solid("#333", xsize=80, ysize=160)
image a4 = Solid("#444", xsize=80, ysize=160)
image a5 = Solid("#555", xsize=80, ysize=160)
image a6 = Solid("#666", xsize=80, ysize=160)
image a7 = Solid("#777", xsize=80, ysize=160)
image a8 = Solid("#888", xsize=80, ysize=160)
image a9 = Solid("#999", xsize=80, ysize=160)
image b1 = Solid("#aa1", xsize=80, ysize=160)
image b2 = Solid("#aa2", xsize=80, ysize=160)
image dana calm = Solid("#c11", xsize=80, ysize=160)
image dana happy = Solid("#c22", xsize=80, ysize=160)
image ewa calm = Solid("#c33", xsize=80, ysize=160)
image ewa happy = Solid("#c44", xsize=80, ysize=160)
image greg calm = Solid("#c55", xsize=80, ysize=160)
image greg happy = Solid("#c66", xsize=80, ysize=160)
image fred calm = Solid("#c77", xsize=80, ysize=160)
image fred happy = Solid("#c88", xsize=80, ysize=160)
image iga calm = Solid("#c99", xsize=80, ysize=160)
image iga happy = Solid("#caa", xsize=80, ysize=160)
image pysprite = Solid("#f0f", xsize=80, ysize=160)
image bg room = Solid("#00f")

transform wobble:
    xpos 300
    ypos 200
    linear 0.5 xpos 340
    repeat

init python:
    def slow_fn(trans, st, at):
        return 0.016

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
            for c in sorted(store.wysiwyg_chars, key=lambda c: str(c.get("tag"))):
                out.append("TAG {0}: locked={1!r}".format(c.get("tag"), c.get("locked")))
            for c in store.wysiwyg_chars:
                if not c.get("locked"):
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 9
            wysiwyg_adjust_zorder("b1", 1)
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            wysiwyg_save_changes()
            out.append("RESAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    # locked: custom animated transform
    show a1 at wobble
    # locked: Transform with a function callback
    show a2 at Transform(xpos=100, ypos=100, function=slow_fn)
    # locked: placement mixed with a custom transform
    show a3 at center, wobble
    # editable: one-line static positions in an ATL block
    show a4:
        xpos 300 ypos 100
    # locked: ATL event block
    show a5:
        xpos 400
        on show:
            linear 0.3 alpha 1.0
    # editable: engine placement + plain positional Transform
    show a6 at left, Transform(ypos=50)
    # locked: zoom is not round-tripped
    show a7 at Transform(xpos=500, ypos=100, zoom=1.5)
    # locked: value the import parser cannot read back
    show a8 at Transform(xpos=600, ypos=100, alpha=.5)
    # locked: value is a variable
    show a9 at Transform(xpos=700, ypos=100, rotate=my_rot)
    # editable: proper literal, alpha must survive the rewrite
    show b1 at Transform(xpos=800, ypos=100, alpha=0.5)
    # editable: static Transform kwargs within the round-trip set
    show b2 at Transform(xpos=900, ypos=100, rotate=15, xzoom=0.8, yzoom=0.8)
    # locked: inherits a live animation through an attribute change
    show dana calm at wobble
    pause 0.05
    show dana happy
    # editable: inherits a static engine placement
    show ewa calm at left
    pause 0.05
    show ewa happy
    # locked: inherits zoom (invisible to the statement text)
    show greg calm at Transform(xpos=1000, ypos=100, zoom=1.4)
    pause 0.05
    show greg happy
    # locked: inherits a blend mode (engine registry check)
    show iga calm at Transform(xpos=1100, ypos=100, blend="multiply")
    pause 0.05
    show iga happy
    # editable: inherits a purely positional Transform
    show fred calm at Transform(align=(0.5, 1.0))
    pause 0.05
    show fred happy
    # locked: shown from code, no source line
    $ renpy.show("pysprite", layer="master")
    pause 0.3
    $ selftest()
    return
