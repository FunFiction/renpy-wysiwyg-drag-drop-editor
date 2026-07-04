# Undo semantics, tested through the real UI functions (direct dict
# mutation bypasses the undo stack, so every edit here goes through the
# same code path the buttons and sliders use): one op of every undoable
# class, then step-by-step undo back to the baseline; the 50-entry cap;
# and the designed behavior around an added sprite (undo reverts its
# edits but never removes it - that is Del's job).

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image extra one = Solid("#ff0", xsize=200, ysize=400)
image bg room = Solid("#00f")

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback
        out = []
        try:
            KEYS = ("x", "rotate", "xzoom", "yzoom", "alpha", "filter_brightness", "motion_fx")

            def snap(c):
                return dict((k, c.get(k)) for k in KEYS)

            def same(a, b):
                for k in KEYS:
                    va, vb = a.get(k), b.get(k)
                    if isinstance(va, float) or isinstance(vb, float):
                        if abs(wysiwyg_float(va, 0.0) - wysiwyg_float(vb, 0.0)) > 0.001:
                            return "key " + k + ": " + repr(va) + " != " + repr(vb)
                    elif va != vb:
                        return "key " + k + ": " + repr(va) + " != " + repr(vb)
                return None

            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            store.wysiwyg_selected_tag = "eileen"
            c = wysiwyg_find_char("eileen")

            # one op of every undoable class, snapshotting before each
            history = [snap(c)]
            ops = [
                ("nudge", lambda: wysiwyg_nudge_selected(50, 0)),
                ("rotate", lambda: wysiwyg_set_char_transform("eileen", "rotate", 45)),
                ("scale", lambda: wysiwyg_set_char_transform("eileen", "xzoom", 0.8)),
                ("alpha", lambda: wysiwyg_set_char_transform("eileen", "alpha", 0.5)),
                ("filter", lambda: wysiwyg_set_char_transform("eileen", "filter_brightness", 0.3)),
                ("motion", lambda: wysiwyg_set_motion_fx("eileen", "float")),
                ("preset", lambda: wysiwyg_place_selected_on_x_target("right")),
            ]
            for name, op in ops:
                op()
                if same(snap(c), history[-1]) is None:
                    out.append("OP-NOCHANGE " + name)
                history.append(snap(c))

            # walk back: every undo must restore the exact pre-op snapshot
            problems = []
            for i in range(len(ops), 0, -1):
                wysiwyg_undo_move()
                diff = same(snap(c), history[i - 1])
                if diff:
                    problems.append("undo of " + ops[i - 1][0] + ": " + diff)
            out.append("UNDO-CHAIN: " + ("; ".join(problems) if problems else "all restored"))

            # the 50-entry cap: 60 nudges, 50 undos, one more says so
            x0 = wysiwyg_float(c.get("x"), 0.0)
            for _i in range(60):
                wysiwyg_nudge_selected(1, 0)
            for _i in range(50):
                wysiwyg_undo_move()
            out.append("CAP-LEFTOVER: " + str(int(round(wysiwyg_float(c.get("x"), 0.0) - x0))))
            wysiwyg_undo_move()
            out.append("CAP-STATUS: " + str(store.wysiwyg_status))

            # an added sprite: undo reverts its edits, never removes it
            wysiwyg_add_character("extra one")
            store.wysiwyg_selected_tag = "extra"
            e = wysiwyg_find_char("extra")
            ex0 = wysiwyg_float(e.get("x"), 0.0)
            wysiwyg_nudge_selected(30, 0)
            wysiwyg_undo_move()
            out.append("ADD-X-RESTORED: " + str(abs(wysiwyg_float(e.get("x"), 0.0) - ex0) < 0.001))
            out.append("ADD-STILL-THERE: " + str(wysiwyg_find_char("extra") is not None))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy at Transform(xpos=200, ypos=300)
    pause 0.3
    $ selftest()
