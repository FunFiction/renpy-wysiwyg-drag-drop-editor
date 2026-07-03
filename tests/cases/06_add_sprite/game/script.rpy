# Add Sprite: the browser sees game/images/ and nothing else, bad names
# are blocked, collisions refused, closing without saving leaves no trace,
# and saving inserts show lines in add order above the earliest tracked
# show, so added sprites join the scene's own reveal transition.

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
            # browser grouping: unique prefixes pool into the "*" group,
            # shared prefixes get their own; a filter bypasses grouping
            # and matches name or path, case-insensitively
            groups = wysiwyg_browser_groups(rows, "")
            out.append("GROUPS: " + "; ".join(g[0] + ":" + str(len(g[1])) for g in groups))
            fake = [{"name": "chloe_a", "file": "images/chloe_a.png", "problem": None},
                    {"name": "chloe_b", "file": "images/chloe_b.png", "problem": None},
                    {"name": "solo", "file": "images/solo.png", "problem": None}]
            groups2 = wysiwyg_browser_groups(fake, "")
            out.append("GROUPS2: " + "; ".join(
                g[0] + ":" + ",".join(r["name"] for r in g[1]) for g in groups2))
            filtered = wysiwyg_browser_groups(rows, "BUD")
            out.append("FILTER: " + "; ".join(r["name"] for r in filtered[0][1]))
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
            # heuristic guesses are NOT trusted as insert anchors (they can
            # point into untaken menu branches) - only linelog/carryover
            # confidence anchors the with-scene insert
            saved_conf = [(c, c.get("source_confidence")) for c in store.wysiwyg_chars]
            for c in store.wysiwyg_chars:
                c["source_confidence"] = "heuristic"
            pos_f, pos_l = wysiwyg_get_current_position()
            tgt = wysiwyg_added_sprite_target(str(pos_f).replace("\\", "/"), int(pos_l))
            out.append("ANCHOR-HEURISTIC: " + repr(str(wysiwyg_source_line_text(tgt[0], tgt[1])).strip()[:13]))
            for c in store.wysiwyg_chars:
                c["source_confidence"] = "carryover"
            tgt = wysiwyg_added_sprite_target(str(pos_f).replace("\\", "/"), int(pos_l))
            out.append("ANCHOR-CARRYOVER: " + repr(str(wysiwyg_source_line_text(tgt[0], tgt[1])).strip()[:13]))
            for c, conf in saved_conf:
                c["source_confidence"] = conf
            wysiwyg_add_character("hero happy")
            wysiwyg_add_character("buddy")
            for c in store.wysiwyg_chars:
                if c.get("tag") == "hero":
                    c["x"] = 200.0
                if c.get("tag") == "buddy":
                    c["x"] = 900.0
            wysiwyg_save_changes()
            out.append("SAVE: " + str(store.wysiwyg_status))
            # tl/ guard: a position inside game/tl/ must refuse the insert
            orig_pos = wysiwyg_get_current_position
            store.wysiwyg_get_current_position = lambda: ("game/tl/polish/fake.rpy", 5)
            wysiwyg_add_character("bigguy")
            out.append("TL-GUARD: " + str(store.wysiwyg_status))
            store.wysiwyg_get_current_position = orig_pos
            # a sprite with its own `with` preset appears AT the paused
            # statement instead of being spliced into the scene reveal
            wysiwyg_add_character("bigguy")
            wysiwyg_set_with_expr("bigguy", "Dissolve(0.5)")
            # equivalent spellings key the same (bare dissolve IS
            # Dissolve(0.5)); setting an equivalent value is a no-op that
            # keeps the original text; custom transitions key as raw
            kd = wysiwyg_with_preset_key
            big = [c for c in store.wysiwyg_chars if c.get("tag") == "bigguy"][0]
            wysiwyg_set_with_expr("bigguy", "dissolve")
            out.append("WITHKEY: " + str(kd("dissolve") == kd("Dissolve(0.5)"))
                       + str(kd("Dissolve(.25)") == kd("Dissolve(0.25)"))
                       + str(kd("myflash")[0] == "raw")
                       + str(big.get("with_expr") == "Dissolve(0.5)"))
            wysiwyg_save_changes()
            out.append("WITH-SAVE: " + str(store.wysiwyg_status))
            # carryover: with the engine line log wiped (what an autoreload
            # does) but the game still paused on the statement recorded at
            # the last save, re-import keeps the known-good source lines
            # instead of degrading everything to uncertain guesses
            orig_get_line_log = renpy.get_line_log
            renpy.get_line_log = lambda: []
            wysiwyg_import_scene()
            renpy.get_line_log = orig_get_line_log
            out.append("CARRYOVER: " + ";".join(sorted(
                str(c.get("tag")) + "=" + str(c.get("source_confidence")) for c in store.wysiwyg_chars)))
            out.append("CARRYOVER-STATUS: " + str(store.wysiwyg_status))
            # the save gate: a dirty character with an uncertain source
            # line is intercepted before anything is written
            for c in store.wysiwyg_chars:
                if c.get("tag") == "buddy":
                    c["source_confidence"] = "heuristic"
                    c["x"] = wysiwyg_float(c["x"], 0.0) + 50.0
            wysiwyg_request_save()
            out.append("CONFIRM-GATE: " + repr(store.wysiwyg_confirm_save))
            wysiwyg_confirm_save_proceed()
            out.append("CONFIRM-SAVE: " + str(store.wysiwyg_status))
            # the scene-level standalone `with` statement: detected at
            # import, preset-equivalent set is a no-op, a custom value is
            # written back into the with line itself
            out.append("SCENE-WITH: " + repr((store.wysiwyg_scene_with or {}).get("expr")))
            wysiwyg_set_scene_with("Dissolve(0.5)")
            out.append("SCENE-WITH-NOOP: " + repr((store.wysiwyg_scene_with or {}).get("expr")))
            wysiwyg_set_scene_with("Dissolve(0.75)")
            wysiwyg_save_changes()
            out.append("SCENE-WITH-SAVE: " + str(store.wysiwyg_status))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show existing at Transform(xpos=600, ypos=300)
    with dissolve  # reveal
    pause 0.3
    $ selftest()
    return
