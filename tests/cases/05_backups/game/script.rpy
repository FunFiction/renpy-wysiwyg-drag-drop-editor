# Backups and write verification: one restore point per save, rotation in
# a tree that mirrors game/ (so files with colliding flattened names keep
# separate pools), and a forced-dirty locked character that must never be
# written.

define config.developer = True

image eileen happy = Solid("#f00", xsize=300, ysize=600)
image lucy mad = Solid("#0f0", xsize=300, ysize=600)
image bg room = Solid("#00f")

transform wobble:
    xpos 300
    ypos 200
    linear 0.5 xpos 340
    repeat

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
            def move(tag, dx):
                for c in store.wysiwyg_chars:
                    if c.get("tag") == tag and not c.get("locked"):
                        c["x"] = wysiwyg_float(c.get("x", 0), 0) + dx
            move("lucy", 10)
            move("g1", 10)
            move("g2", 10)
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE-1: " + str(store.wysiwyg_status))
            move("lucy", 10)
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE-2: " + str(store.wysiwyg_status))
            # force-dirty the locked eileen: save must not touch her
            for c in store.wysiwyg_chars:
                if c.get("tag") == "eileen":
                    c["x"] = wysiwyg_float(c.get("x", 0), 0) + 99
            store.wysiwyg_saved_runtime = False
            wysiwyg_save_changes()
            out.append("SAVE-3: " + str(store.wysiwyg_status))
            bdir = os.path.join(config.gamedir, "wysiwyg_backups")
            tree = []
            for root, dirs, files in os.walk(bdir):
                for fn in files:
                    tree.append(os.path.relpath(os.path.join(root, fn), bdir).replace("\\", "/"))
            out.append("BACKUP-TREE: " + "; ".join(sorted(tree)))
            wysiwyg_toggle()
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        with io.open(os.path.join(config.gamedir, "selftest_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        renpy.quit()

label start:
    scene bg room
    show eileen happy at wobble
    show lucy mad:
        xpos 800
        ypos 300
    call sub_g1
    call sub_g2
    pause 0.3
    $ selftest()
    return
