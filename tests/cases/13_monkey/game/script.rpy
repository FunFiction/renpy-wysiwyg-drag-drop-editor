# Monkey test: a long, seeded-random sequence of editor operations with
# hard invariants checked after every save. This is the closest thing to
# "test everything": it exercises combinations no hand-written scenario
# would think of, deterministically (the seed derives from the file
# content, so each of the two runs replays a different but reproducible
# sequence - run 2 monkeys around on the file run 1 already rewrote).

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=280, ysize=560)
image ben calm = Solid("#0ff", xsize=260, ysize=520)
image extra one = Solid("#ff0", xsize=200, ysize=400)
image extra two = Solid("#f0f", xsize=200, ysize=400)
image bg room = Solid("#00f")

transform wobble:
    xpos 900
    ypos 150
    linear 0.5 xpos 940
    repeat

label main_menu:
    return

init python:
    def selftest():
        import io, os, traceback, hashlib, random
        out = []
        parse_failures = []
        ops_done = 0
        script_path = os.path.join(config.gamedir, "script.rpy")
        try:
            with io.open(script_path, "r", encoding="utf-8") as f:
                body = f.read()
            seed = int(hashlib.md5(body.encode("utf-8")).hexdigest()[:8], 16)
            rng = random.Random(seed)
            out.append("SEED: " + str(seed))

            def locked_line():
                with io.open(script_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "ben calm at wobble" in line:
                            return line.rstrip("\n")
                return None
            locked_before = locked_line()

            def editable():
                return [c for c in store.wysiwyg_chars
                        if not c.get("locked") and not c.get("pending_hide")]

            def op_move():
                chars = editable()
                if chars:
                    c = rng.choice(chars)
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + rng.randint(-120, 120)
                    c["y"] = wysiwyg_float(c.get("y", 0), 0) + rng.randint(-80, 80)
                    store.wysiwyg_saved_runtime = False

            def op_transform():
                chars = editable()
                if chars:
                    c = rng.choice(chars)
                    c["rotate"] = rng.choice([0, 0, 15, -30, 90])
                    z = rng.choice([0.5, 0.8, 1.0, 1.25])
                    c["xzoom"] = z
                    c["yzoom"] = z
                    c["alpha"] = rng.choice([0.4, 0.7, 1.0])
                    store.wysiwyg_saved_runtime = False

            def op_motion():
                chars = editable()
                if chars:
                    c = rng.choice(chars)
                    c["motion_fx"] = rng.choice(["none", "float", "shake", "breathe"])
                    store.wysiwyg_saved_runtime = False

            def op_add():
                wysiwyg_add_character(rng.choice(["extra one", "extra two"]))

            def op_remove():
                chars = editable()
                if chars:
                    wysiwyg_remove_character(rng.choice(chars).get("tag"))

            def op_undo():
                wysiwyg_undo_move()

            def op_reset():
                chars = editable()
                if chars:
                    wysiwyg_reset_position_for(rng.choice(chars).get("tag"))

            def op_reimport():
                wysiwyg_import_scene()

            def op_save():
                wysiwyg_save_changes()
                status = str(store.wysiwyg_status)
                problem = wysiwyg_verify_file_parses("game/script.rpy")
                if problem:
                    parse_failures.append("after save: " + str(problem) + " status=" + status)
                cur = locked_line()
                if locked_before and cur != locked_before:
                    parse_failures.append("locked line changed: " + repr(cur))

            ops = ([op_move] * 8 + [op_transform] * 4 + [op_motion] * 3 +
                   [op_add] * 2 + [op_remove] * 2 + [op_undo] * 2 +
                   [op_reset] * 2 + [op_reimport] * 2 + [op_save] * 5)

            wysiwyg_toggle()
            wysiwyg_import_scene()
            out.append("IMPORT: " + str(store.wysiwyg_status))
            for _i in range(40):
                rng.choice(ops)()
                ops_done += 1
            op_save()
            ops_done += 1
            wysiwyg_toggle()
            out.append("OPS: " + str(ops_done))
            if parse_failures:
                out.append("PARSE-CHECKS: " + " | ".join(parse_failures[:5]))
            else:
                out.append("PARSE-CHECKS: all clean")
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    # MARKER-A: ta linia musi przetrwac kazda operacje edytora.
    scene bg room
    show eileen happy:
        xpos 100
        ypos 200
    show lucy mad at Transform(xpos=700, ypos=250)
    show ben calm at wobble
    # MARKER-B: i ta rowniez.
    pause 0.3
    $ selftest()
