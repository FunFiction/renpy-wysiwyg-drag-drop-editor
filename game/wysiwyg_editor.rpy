# =============================================================================
# WYSIWYG Scene Editor for Ren'Py
# =============================================================================
# Created by: FunFiction
# GitHub: https://github.com/FunFiction/renpy-wysiwyg-drag-drop-editor
# =============================================================================
# Drop this single file into game/. Press F5 while playing to open the editor.
#
# What it does:
#   - "Import Scene" reads the characters currently shown on the master layer,
#     finds the exact `show` statement in the .rpy source that displayed each
#     of them, and lets you drag / rotate / scale / filter them live.
#   - "Save Changes" rewrites those exact source lines in place (every save
#     first copies each touched file into game/wysiwyg_backups/).
#   - Closing the editor restores the scene to its last saved/imported state,
#     so no unsaved preview changes ever leak into the running game.
#
# Key design decisions (do not break these when editing):
#   - Positions are stored and saved CENTER-BASED with explicit
#     xanchor=0.5 / yanchor=0.5. The center is the only point invariant
#     under rotation and scaling, and an explicit anchor makes the saved
#     line independent of whatever default anchors a given game uses.
#   - On import the live render bounds (renpy.get_image_bounds) are the
#     source of truth for position; the parsed source line is trusted only
#     when it agrees with the live render within 2 px. This makes the editor
#     work in any game regardless of its transforms or menu branching.
#   - The statement to overwrite is chosen via the line log of actually
#     executed lines (config.line_log), not just "closest line above".
#   - The selected character's drag container is sized and positioned with
#     the same truncation arithmetic the renderer uses, so the editor
#     preview is pixel-identical to the live scene (incl. rotate_pad).
#
# File layout:
#   1. default state variables
#   2. init python: helpers, PNG alpha analysis, source location, import,
#      transforms/preview, save, undo, UI callbacks
#   3. styles (scaled to the game's virtual resolution)
#   4. motion FX transforms
#   5. screens: hotkey, main overlay, characters panel, code panel
# =============================================================================

default wysiwyg_active = False
default wysiwyg_panel = "characters"
default wysiwyg_bg = None
default wysiwyg_bg_source = None
default wysiwyg_chars = []
default wysiwyg_status = ""
default wysiwyg_saved_runtime = False
default wysiwyg_selected_tag = None
default wysiwyg_undo_stack = []
default wysiwyg_transform_memory = {}
default wysiwyg_scale_locked = True
default wysiwyg_grid = False
default wysiwyg_char_page = "main"
default wysiwyg_nudge_step = 1
default wysiwyg_edit_field = None
default wysiwyg_edit_buffer = ""
default wysiwyg_browser_filter = ""
default wysiwyg_browser_hover = None
default wysiwyg_browser_open_groups = set()
default wysiwyg_saved_position = None
default wysiwyg_confirm_save = None
default wysiwyg_confirm_close = None
default wysiwyg_scene_with = None

init -2 python:
    import os
    import re
    import io
    import math
    import time

    WYSIWYG_VERSION = "1.0.0"
    WYSIWYG_BLACKLIST = set(["black", "white", "text", "vtext", "side", "icon", "ui", "button"])

    class _WysiwygRuntime:
        # Session-only state. Held on a single object that is assigned once at
        # init and never rebound, so none of it is written into player save
        # files or participates in rollback (unlike the default screen vars).
        def __init__(self):
            # path -> first backup made this session (never pruned).
            self.first_backup = {}
            # Elided filenames whose post-save verification failed; saving to
            # them stays disabled until the game restarts.
            self.failed_files = set()
            self.master_snapshot = None
            self.prev_allow_skipping = None
            # Rows of the add-sprite file browser, refreshed on open.
            self.image_browser = None
            # True execution-ordered line log (newest last, duplicates kept).
            # The engine's own line log deduplicates entries, so its order is
            # first-execution order - wrong for "which show ran most
            # recently" in games that loop labels.
            self.exec_log = []
            self.exec_log_registered = False
            # (file, line) -> line text, for the code panel only: screens
            # re-evaluate on every interaction restart, and re-reading big
            # .rpy files from disk each time makes the whole UI stutter.
            self.source_text_cache = {}

    WYSIWYG_RUNTIME = _WysiwygRuntime()

    def wysiwyg_enabled():
        # The editor is a development tool: keep it (and its line log) out of
        # shipped builds, where config.developer resolves to False.
        return bool(getattr(config, "developer", False))

    def wysiwyg_init():
        # line_log is what lets Import Scene find the exact executed source
        # line. It is only enabled for developer builds (see wysiwyg_enabled),
        # from start/after-load callbacks because config.developer is still
        # "auto" at init time.
        def _enable():
            if wysiwyg_enabled():
                config.line_log = True
                config.clear_lines = False
                if not WYSIWYG_RUNTIME.exec_log_registered:
                    config.line_log_callbacks.append(wysiwyg_line_log_callback)
                    WYSIWYG_RUNTIME.exec_log_registered = True
        def _after_load():
            _enable()
            # The newest exec_log entries describe the timeline abandoned by
            # this load; keeping them would resolve tags to statements the
            # loaded game never executed, with full "linelog" confidence.
            del WYSIWYG_RUNTIME.exec_log[:]
        config.start_callbacks.append(_enable)
        config.after_load_callbacks.append(_after_load)

    def wysiwyg_line_log_callback(entry):
        # Fires from LineLogEntry.__init__ on EVERY executed statement,
        # before the engine deduplicates - this is the true most-recent
        # execution order the engine log cannot provide.
        log = WYSIWYG_RUNTIME.exec_log
        try:
            log.append((str(entry.filename), int(entry.line)))
        except Exception:
            return
        if len(log) > 4000:
            del log[:2000]
        # The engine's own line log is unbounded and pays a linear dedup
        # scan per executed statement (execution.py); a long ctrl-skip
        # session would get progressively slower. Keep it windowed the
        # same way as exec_log - imports only need the recent scene.
        try:
            engine_log = renpy.game.context().line_log
            if len(engine_log) > 4000:
                del engine_log[:2000]
        except Exception:
            pass

    def wysiwyg_game_dir():
        return getattr(config, "gamedir", renpy.config.gamedir)

    def wysiwyg_log_debug(msg):
        try:
            path = os.path.join(wysiwyg_game_dir(), "wysiwyg_debug.txt")
            # Cap the log so weeks of sessions cannot grow it without
            # bound; utf-8 with errors="replace" so non-ASCII source lines
            # (the games that need diagnostics most) never lose an entry.
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    os.remove(path)
            except OSError:
                pass
            with io.open(path, "a", encoding="utf-8", errors="replace") as f:
                f.write(str(msg) + "\n")
        except Exception:
            pass

    def wysiwyg_screen_w():
        return int(getattr(config, "screen_width", 1280) or 1280)

    def wysiwyg_screen_h():
        return int(getattr(config, "screen_height", 720) or 720)

    def wysiwyg_ui_scale():
        # Editor UI is designed at 1080p; scale it to the game's virtual
        # resolution so a 720p or 4K project gets a proportional panel.
        return wysiwyg_screen_h() / 1080.0

    def wysiwyg_ui_text(value):
        return str(value or "").replace("[", "[[")

    def wysiwyg_norm_path(value):
        # Forward-slash form of a source path, as the engine reports them.
        return str(value or "").replace("\\", "/")

    def wysiwyg_set_status(text):
        store.wysiwyg_status = text
        renpy.restart_interaction()

    def wysiwyg_mark_runtime_dirty():
        # The standard tail of every mutation handler: the scene now
        # differs from the last save, and the UI must reflect it.
        store.wysiwyg_saved_runtime = False
        renpy.restart_interaction()

    def wysiwyg_source_path(filename):
        if not filename or not filename.startswith("game/"):
            return None
        return os.path.join(wysiwyg_game_dir(), filename[5:].replace("/", os.sep))

    def wysiwyg_get_image_size(image_name, tag=None):
        img_name = image_name
        try:
            if isinstance(img_name, str) and " " in img_name:
                img_name = tuple(img_name.split())
            
            size = renpy.image_size(img_name)
            if size:
                return float(size[0]), float(size[1])
        except Exception:
            pass

        try:
            first = img_name[0] if isinstance(img_name, tuple) else img_name
            size = renpy.image_size(first)
            if size:
                return float(size[0]), float(size[1])
        except Exception:
            pass

        # Fallback: measure by rendering the displayable. Fast (uses the normal
        # texture pipeline) and works for any image type (layeredimage,
        # Composite, attribute images...). The previous fallback decoded the
        # PNG in pure Python and made the first import take seconds.
        try:
            name_str = image_name if isinstance(image_name, str) else " ".join([str(i) for i in image_name])
            d = renpy.displayable(name_str)
            rend = renpy.render(d, wysiwyg_screen_w(), wysiwyg_screen_h(), 0, 0)
            w, h = rend.get_size()
            if w and h:
                return float(w), float(h)
        except Exception:
            pass

        return 0.0, 0.0

    def wysiwyg_backup_dir():
        return os.path.join(wysiwyg_game_dir(), "wysiwyg_backups")

    def wysiwyg_backup_source(filename):
        # One backup per touched file per SAVE, not per session: every Save
        # click gets its own restore point in game/wysiwyg_backups/. Kept to
        # the 10 newest per file, plus this session's first backup (the
        # pre-editor baseline), so the folder cannot grow without bound.
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            return None
        # The backup tree mirrors the game/ tree, so two different source
        # files can never share a backup name (game/sub/extra.rpy and
        # game/sub_extra.rpy stay apart) and rotation never touches another
        # file's restore points.
        if filename.startswith("game/"):
            rel = wysiwyg_norm_path(filename[5:])
        else:
            rel = os.path.basename(path)
        base = os.path.basename(rel)
        backup_dir = os.path.join(wysiwyg_backup_dir(), os.path.dirname(rel).replace("/", os.sep))
        if not os.path.isdir(backup_dir):
            os.makedirs(backup_dir)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(backup_dir, base + "." + stamp + ".bak")
        counter = 1
        while os.path.exists(backup):
            backup = os.path.join(backup_dir, base + "." + stamp + "-" + str(counter) + ".bak")
            counter += 1
        with io.open(path, "r", encoding="utf-8") as handle:
            data = handle.read()
        with io.open(backup, "w", encoding="utf-8") as handle:
            handle.write(data)
        if path not in WYSIWYG_RUNTIME.first_backup:
            WYSIWYG_RUNTIME.first_backup[path] = backup
        try:
            keep = WYSIWYG_RUNTIME.first_backup.get(path)
            # Sort by mtime: lexical order puts "...-1.bak" counter suffixes
            # BEFORE their base stamp, which would prune the newest backups
            # first in a same-second burst of saves.
            mine = [f for f in os.listdir(backup_dir) if f.startswith(base + ".") and f.endswith(".bak")]
            mine.sort(key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)))
            for old in mine[:-10]:
                full = os.path.join(backup_dir, old)
                if full != keep:
                    os.remove(full)
        except Exception:
            pass
        return backup

    def wysiwyg_restore_backup(filename, backup):
        # Puts a backup's content back into the live source file.
        try:
            path = wysiwyg_source_path(filename)
            if not path or not backup or not os.path.exists(backup):
                return False
            with io.open(backup, "r", encoding="utf-8") as handle:
                data = handle.read()
            with renpy.loader.auto_lock:
                with io.open(path, "w", encoding="utf-8") as handle:
                    handle.write(data)
                renpy.loader.add_auto(path, force=True)
            return True
        except Exception:
            return False

    def wysiwyg_file_matches_backup(filename, backup):
        # True when the source file already equals its pre-save backup -
        # i.e. the failed write never changed anything on disk. Restoring
        # would be pointless, and a restore FAILURE (read-only file, a sync
        # lock) would tell the user to repair a file that is intact.
        try:
            path = wysiwyg_source_path(filename)
            if not path or not backup or not os.path.exists(path) or not os.path.exists(backup):
                return False
            with io.open(path, "r", encoding="utf-8") as handle:
                current = handle.read()
            with io.open(backup, "r", encoding="utf-8") as handle:
                saved = handle.read()
            return current == saved
        except Exception:
            return False

    def wysiwyg_verify_file_parses(filename):
        # Re-parses the whole just-saved file with the engine parser. Any
        # error means the save damaged it and the pre-save backup must come
        # back - the damage is caught NOW, not at the next game launch.
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            return "file missing"
        try:
            with io.open(path, "r", encoding="utf-8") as handle:
                data = handle.read()
        except Exception as exc:
            return "unreadable: " + str(exc)
        import collections
        old_errors = renpy.parser.parse_errors
        old_deferred = renpy.parser.deferred_parse_errors
        renpy.parser.parse_errors = []
        # Deferred diagnostics (duplicate_id etc.) queued by this throwaway
        # parse must not leak into the engine's global queue, or the next
        # Shift+R reload reports parse errors the on-disk file doesn't have.
        renpy.parser.deferred_parse_errors = collections.defaultdict(list)
        try:
            result = renpy.parser.parse(path, filedata=data)
            messages = list(renpy.parser.parse_errors)
        except Exception as exc:
            result = None
            messages = [str(exc)]
        finally:
            renpy.parser.parse_errors = old_errors
            renpy.parser.deferred_parse_errors = old_deferred
        if result is None or messages:
            for message in messages:
                lines = str(message).strip().splitlines()
                if lines:
                    return lines[0]
            return "parse failed"
        return None

    # --- AST / source statement helpers -------------------------------------
    # Functions below unpack Ren'Py Show/Scene AST nodes and locate the
    # exact source file and line that displayed each on-screen image.
    def wysiwyg_imspec_parts(imspec):
        if not imspec:
            return None, None, None, [], None

        if len(imspec) == 7:
            name, expression, tag, at_list, layer, zorder, behind = imspec
        elif len(imspec) == 6:
            name, expression, tag, at_list, layer, zorder = imspec
        else:
            name, at_list, layer = imspec
            expression = None
            tag = None

        if isinstance(name, tuple):
            name_text = " ".join([str(i) for i in name])
            default_tag = name[0] if name else None
        else:
            name_text = str(name)
            default_tag = name_text.split(" ", 1)[0] if name_text else None

        if tag is None:
            tag = default_tag

        if layer is None and tag:
            try:
                layer = renpy.default_layer(None, tag)
            except Exception:
                layer = "master"

        return name_text, expression, tag, list(at_list or []), layer or "master"

    def wysiwyg_imspec_explicit_tag(imspec):
        # The raw `as alias` tag, or None when the statement had no as-clause.
        # wysiwyg_imspec_parts defaults the tag from the image name, which is
        # right for lookups but must not leak into the rewritten line.
        if imspec and len(imspec) >= 6:
            return imspec[2]
        return None

    def wysiwyg_node_image(node):
        name, expression, tag, at_list, layer = wysiwyg_imspec_parts(getattr(node, "imspec", None))
        if expression:
            return None, expression, tag, at_list, layer
        return name, None, tag, at_list, layer

    def wysiwyg_is_background(tag, image_name):
        value = (image_name or tag or "").lower()
        tag = (tag or "").lower()
        return tag.startswith("bg") or tag.startswith("background") or "background" in value or "/bg" in value or "\\bg" in value

    def wysiwyg_char_label(tag):
        obj = getattr(store, tag, None)
        name = getattr(obj, "name", None) or getattr(obj, "who", None)
        return name or tag

    def wysiwyg_ellipsize(text, limit):
        # Single home of the truncation math, so labels and expressions
        # can never ellipsize differently.
        text = str(text)
        if len(text) > limit:
            text = text[:max(1, limit - 1)] + "…"
        return text

    def wysiwyg_short_label(tag, limit=16):
        # Row labels live in a fixed-width button; anything longer than the
        # button gets ellipsized here (the full name stays in the tooltip
        # and in the selected-character header).
        return wysiwyg_ellipsize(wysiwyg_char_label(tag), limit)

    def wysiwyg_short_expr(expr, limit=24):
        # Long author-side expressions (custom transitions and the like)
        # would stretch a button past the panel edge; the full text goes
        # into the tooltip instead.
        return wysiwyg_ellipsize(expr or "", limit)

    def wysiwyg_comment_index(text):
        # Index of the trailing comment's '#' in a logical line, or None.
        # Quote-aware: a '#' inside a string literal is not a comment.
        # Single home of the scan - the stripper below and the comment
        # extractor both derive their result from this one loop.
        quote = None
        i = 0
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
            elif ch == "#":
                return i
            i += 1
        return None

    def wysiwyg_strip_line_comment(text):
        # The logical line without its trailing "# comment". Used wherever
        # a source line is parsed as an EXPRESSION - the comment must not
        # leak into the parsed value.
        text = str(text or "")
        idx = wysiwyg_comment_index(text)
        return text if idx is None else text[:idx]

    def wysiwyg_wrap_path(value):
        # Paths, image names and code lines are long single "words" - with
        # no spaces the text system cannot wrap them and they poke out of
        # the panel. A zero-width space (U+200B, rendered at zero width by
        # Ren'Py with any font) after each separator gives every one of
        # them a legal break point without changing what the user sees.
        s = wysiwyg_ui_text(value)
        zwsp = chr(0x200B)
        return re.sub("([/\\\\_.,()\\-])", "\\1" + zwsp, s)

    def wysiwyg_char_color(tag):
        obj = getattr(store, tag, None)
        who_args = getattr(obj, "who_args", None)
        if isinstance(who_args, dict):
            return who_args.get("color", "#ffffff")
        return "#ffffff"

    def wysiwyg_find_char(tag):
        for char in store.wysiwyg_chars:
            if char.get("tag") == tag:
                return char
        return None

    def wysiwyg_float(value, fallback=0.0):
        try:
            return float(value)
        except Exception:
            return fallback

    def wysiwyg_fmt_float(value, digits=3):
        text = "%.*f" % (digits, float(value))
        text = text.rstrip("0").rstrip(".")
        return text if text else "0"

    def wysiwyg_transform_defaults():
        return {
            "rotate": 0.0,
            "xzoom": 1.0,
            "yzoom": 1.0,
            "alpha": 1.0,
            # xanchor/yanchor intentionally have no defaults here: their
            # presence in the parsed dict means the source line pins them
            # explicitly. Without them the game's own default transform
            # decides the anchor, so only the rendered bounds are reliable.
            "blur": 0.0,
            "filter_brightness": 0.0,
            "filter_contrast": 1.0,
            "filter_saturation": 1.0,
            "filter_hue": 0.0,
            "filter_invert": 0.0,
            "filter_sepia": False,
            "motion_fx": "none",
            "motion_fx_strength": 1.0,
        }

    def wysiwyg_source_line_text(filename, line):
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            return ""
        try:
            with io.open(path, "r", encoding="utf-8") as handle:
                for index, text in enumerate(handle, 1):
                    if index == int(line):
                        return text.strip()
        except Exception:
            pass
        return ""

    def wysiwyg_source_line_text_cached(filename, line):
        # Code-panel path only: screens re-evaluate on every interaction
        # restart, and re-reading a large .rpy from disk per hover makes
        # the whole UI stutter. The cache is dropped on import and after
        # every save (wysiwyg_clear_editor_state clears it too), so it can
        # never outlive the lines it mirrors. Import/save paths must keep
        # calling the uncached read.
        key = (wysiwyg_norm_path(filename or ""), int(line or 0))
        cache = WYSIWYG_RUNTIME.source_text_cache
        if key not in cache:
            cache[key] = wysiwyg_source_line_text(filename, line)
        return cache[key]

    # Parses an editor-style `show ... at Transform(...)` source line back
    # into a dict of transform values. Lines not written by this editor
    # simply yield defaults; the live render bounds then take over.
    def wysiwyg_parse_transform_from_line(filename, line):
        text = wysiwyg_source_line_text(filename, line)
        result = wysiwyg_transform_defaults()
        if "Transform(" not in text:
            return result

        for key in ("xpos", "ypos", "xanchor", "yanchor", "rotate", "xzoom", "yzoom", "alpha"):
            match = re.search(r"\b" + key + r"\s*=\s*(-?\d+(?:\.\d+)?)", text)
            if match:
                result[key] = wysiwyg_float(match.group(1), result.get(key, 0.0))

        blur_match = re.search(r"\bblur\s*=\s*(-?\d+(?:\.\d+)?)", text)
        if blur_match:
            result["blur"] = wysiwyg_float(blur_match.group(1), 0.0)

        matrix_parsers = {
            "filter_brightness": r"BrightnessMatrix\(\s*(-?\d+(?:\.\d+)?)\s*\)",
            "filter_contrast": r"ContrastMatrix\(\s*(-?\d+(?:\.\d+)?)\s*\)",
            "filter_saturation": r"SaturationMatrix\(\s*(-?\d+(?:\.\d+)?)\s*\)",
            "filter_hue": r"HueMatrix\(\s*(-?\d+(?:\.\d+)?)\s*\)",
            "filter_invert": r"InvertMatrix\(\s*(-?\d+(?:\.\d+)?)\s*\)",
        }

        for key, pattern in matrix_parsers.items():
            match = re.search(pattern, text)
            if match:
                result[key] = wysiwyg_float(match.group(1), result[key])

        result["filter_sepia"] = ("SepiaMatrix()" in text)

        motion_match = re.search(r"\bwysiwyg_(float|shake|bounce|sink|breathe|sway|blink)_motion\s*\(\s*(-?\d+(?:\.\d+)?)?\s*\)", text)
        if motion_match:
            result["motion_fx"] = motion_match.group(1)
            if motion_match.group(2):
                result["motion_fx_strength"] = wysiwyg_float(motion_match.group(2), 1.0)

        return result

    def wysiwyg_hide_master_chars():
        for char in store.wysiwyg_chars:
            if char.get("locked"):
                continue
            tag = char.get("tag")
            try:
                renpy.hide(tag, layer="master")
            except Exception:
                pass

    def wysiwyg_transform_for_char(char, use_original=False):
        x_key = "original_x" if use_original else "x"
        y_key = "original_y" if use_original else "y"

        xzoom_val = wysiwyg_float(char.get("original_xzoom" if use_original else "xzoom", 1.0), 1.0)
        yzoom_val = wysiwyg_float(char.get("original_yzoom" if use_original else "yzoom", 1.0), 1.0)

        img_w = wysiwyg_float(char.get("img_w", char.get("w", 0.0)), 0.0)
        img_h = wysiwyg_float(char.get("img_h", char.get("h", 0.0)), 0.0)
        if img_w <= 0:
            img_w = 400.0
        if img_h <= 0:
            img_h = 800.0
        xpos = int(round(wysiwyg_float(char.get(x_key, 0.0), 0.0) + (img_w * abs(xzoom_val)) / 2.0))
        ypos = int(round(wysiwyg_float(char.get(y_key, 0.0), 0.0) + (img_h * abs(yzoom_val)) / 2.0))
        return Transform(
            xpos=xpos,
            ypos=ypos,
            xanchor=0.5,
            yanchor=0.5,
            **wysiwyg_transform_effect_kwargs(char, use_original=use_original)
        )

    def wysiwyg_render_box(char):
        # Size of the rendered (rotated, zoomed) bounding box, rounded up to
        # an even integer. The selected-character drag container must use this
        # size: with an even box and an integer center, the inner blit offset
        # has exactly the same fractional part as the live master-layer blit,
        # so both round identically and the preview is pixel-equal to the
        # game render (a fractional rotated width otherwise causes a 1px jump
        # because negative inner offsets round differently than positive ones).
        bw, bh = wysiwyg_render_size(char)
        bw = int(math.ceil(bw))
        bh = int(math.ceil(bh))
        if bw % 2:
            bw += 1
        if bh % 2:
            bh += 1
        return bw, bh

    def wysiwyg_render_size(char):
        # Exact float size of the surface Ren'Py renders for this character
        # (with rotate_pad=True a rotated render is a hypot(w,h) square).
        img_w = wysiwyg_float(char.get("img_w", char.get("original_w", 400.0)), 400.0)
        img_h = wysiwyg_float(char.get("img_h", char.get("original_h", 800.0)), 800.0)
        w = img_w * abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0))
        h = img_h * abs(wysiwyg_float(char.get("yzoom", 1.0), 1.0))
        if abs(wysiwyg_float(char.get("rotate", 0.0), 0.0)) > 0.01:
            s = math.hypot(w, h)
            return s, s
        return w, h

    def wysiwyg_drag_pos(char, cx, cy, box_w, box_h):
        # Position the drag container so that the preview lands on exactly the
        # same screen pixel as the live master-layer render. Ren'Py truncates
        # blit offsets toward zero (int cast), so a live offset of -73.999
        # draws at -73 while a positive 68.97 draws at 68; replicate that
        # truncation here instead of plain int division.
        sw, sh = wysiwyg_render_size(char)
        drag_x = int(cx - sw / 2.0) - int((box_w - sw) / 2.0)
        drag_y = int(cy - sh / 2.0) - int((box_h - sh) / 2.0)
        return drag_x, drag_y

    def wysiwyg_push_undo(char):
        store.wysiwyg_undo_stack.append({
            "tag": char.get("tag"),
            "x": wysiwyg_float(char.get("x", 0.0), 0.0),
            "y": wysiwyg_float(char.get("y", 0.0), 0.0),
            "w": wysiwyg_float(char.get("w", 0), 0.0),
            "h": wysiwyg_float(char.get("h", 0), 0.0),
            "anchor_x": wysiwyg_float(char.get("anchor_x", char.get("x", 0.0)), 0.0),
            "anchor_y": wysiwyg_float(char.get("anchor_y", char.get("y", 0.0)), 0.0),
            "rotate": wysiwyg_float(char.get("rotate", 0.0), 0.0),
            "xzoom": wysiwyg_float(char.get("xzoom", 1.0), 1.0),
            "yzoom": wysiwyg_float(char.get("yzoom", 1.0), 1.0),
            "alpha": wysiwyg_float(char.get("alpha", 1.0), 1.0),
            "filter_blur": wysiwyg_float(char.get("filter_blur", 0.0), 0.0),
            "filter_brightness": wysiwyg_float(char.get("filter_brightness", 0.0), 0.0),
            "filter_contrast": wysiwyg_float(char.get("filter_contrast", 1.0), 1.0),
            "filter_saturation": wysiwyg_float(char.get("filter_saturation", 1.0), 1.0),
            "filter_hue": wysiwyg_float(char.get("filter_hue", 0.0), 0.0),
            "filter_invert": wysiwyg_float(char.get("filter_invert", 0.0), 0.0),
            "filter_sepia": bool(char.get("filter_sepia", False)),
            "motion_fx": str(char.get("motion_fx", "none") or "none"),
            "motion_fx_strength": wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0),
            "parsed_x": char.get("parsed_x", False),
            "parsed_y": char.get("parsed_y", False),
            "parsed_center_x": char.get("parsed_center_x", char.get("x", 0.0) + wysiwyg_float(char.get("w", 0.0), 0.0) / 2.0),
            "parsed_center_y": char.get("parsed_center_y", char.get("y", 0.0) + wysiwyg_float(char.get("h", 0.0), 0.0) / 2.0),
        })
        store.wysiwyg_undo_stack = store.wysiwyg_undo_stack[-50:]

    def wysiwyg_update_char_size(char):
        if not char:
            return
        orig_w = wysiwyg_float(char.get("original_w", char.get("w", 0.0)), 0.0)
        orig_h = wysiwyg_float(char.get("original_h", char.get("h", 0.0)), 0.0)
        if orig_w <= 0.01:
            orig_w = wysiwyg_float(char.get("w", 0.0), 0.0)
        if orig_h <= 0.01:
            orig_h = wysiwyg_float(char.get("h", 0.0), 0.0)

        xzoom_val = abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0))
        yzoom_val = abs(wysiwyg_float(char.get("yzoom", 1.0), 1.0))

        old_w = wysiwyg_float(char.get("w", 0.0), 0.0)
        old_h = wysiwyg_float(char.get("h", 0.0), 0.0)

        new_w = orig_w * xzoom_val
        new_h = orig_h * yzoom_val

        if old_w > 0.01 and abs(old_w - new_w) > 0.01:
            char["x"] = wysiwyg_float(char.get("x", 0.0), 0.0) + (old_w - new_w) / 2.0
        if old_h > 0.01 and abs(old_h - new_h) > 0.01:
            char["y"] = wysiwyg_float(char.get("y", 0.0), 0.0) + (old_h - new_h) / 2.0

        char["w"] = new_w
        char["h"] = new_h
        char["anchor_x"] = wysiwyg_float(char.get("x", 0.0), 0.0)
        char["anchor_y"] = wysiwyg_float(char.get("y", 0.0), 0.0)
        char["parsed_center_x"] = wysiwyg_float(char.get("x", 0.0), 0.0) + new_w / 2.0
        char["parsed_center_y"] = wysiwyg_float(char.get("y", 0.0), 0.0) + new_h / 2.0

    def wysiwyg_clamp(value, low, high):
        return max(low, min(high, value))

    def wysiwyg_set_char_transform(tag, key, value):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        old_value = wysiwyg_float(char.get(key, 0.0), 0.0)
        value = wysiwyg_float(value, old_value)
        if key == "rotate":
            value = round(wysiwyg_clamp(value, -180.0, 180.0), 1)
        elif key in ("xzoom", "yzoom"):
            value = round(wysiwyg_clamp(abs(value), 0.01, 2.0), 3)
        elif key == "alpha":
            value = round(wysiwyg_clamp(value, 0.0, 1.0), 3)
        if abs(old_value - value) < 0.0001:
            return
        wysiwyg_push_undo(char)
        char[key] = value

        if key == "xzoom" and store.wysiwyg_scale_locked:
            char["yzoom"] = value
            store.wysiwyg_transform_memory[tag + ":yzoom"] = value
        elif key == "yzoom" and store.wysiwyg_scale_locked:
            char["xzoom"] = value
            store.wysiwyg_transform_memory[tag + ":xzoom"] = value
        if key in ("xzoom", "yzoom"):
            wysiwyg_update_char_size(char)
        store.wysiwyg_transform_memory[tag + ":" + key] = value
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_flip_char(tag, key):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        current = wysiwyg_float(char.get(key, 1.0), 1.0)
        if abs(current) < 0.001:
            current = 1.0
        wysiwyg_push_undo(char)
        char[key] = -current
        wysiwyg_update_char_size(char)
        store.wysiwyg_transform_memory[tag + ":" + key] = char[key]
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_ensure_color_filter_state(char):
        if not char:
            return None
        defaults = wysiwyg_default_color_filter_values()
        for key, value in defaults.items():
            if key not in char:
                char[key] = value
        return char

    def wysiwyg_default_color_filter_values():
        return {
            "filter_blur": 0.0,
            "filter_brightness": 0.0,
            "filter_contrast": 1.0,
            "filter_saturation": 1.0,
            "filter_hue": 0.0,
            "filter_invert": 0.0,
            "filter_sepia": False,
        }

    def wysiwyg_default_motion_fx_values():
        return {
            "motion_fx": "none",
            "motion_fx_strength": 1.0,
        }

    def wysiwyg_toggle_char_bool(tag, key):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        char[key] = not bool(char.get(key, False))
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_char_filter_value(char, key, default, use_original=False):
        if use_original:
            return char.get("original_" + key, char.get(key, default))
        return char.get(key, default)

    def wysiwyg_ensure_motion_fx_state(char):
        if not char:
            return None
        defaults = wysiwyg_default_motion_fx_values()
        for key, value in defaults.items():
            if key not in char:
                char[key] = value
        return char

    def wysiwyg_motion_fx_uses_placement(char):
        if not char:
            return False
        return str(char.get("motion_fx", "none") or "none").strip().lower() in ("float", "shake", "bounce", "sink")

    def wysiwyg_motion_named(effect, strength):
        fn = {
            "float": wysiwyg_float_motion, "shake": wysiwyg_shake_motion,
            "bounce": wysiwyg_bounce_motion, "sink": wysiwyg_sink_motion,
            "breathe": wysiwyg_breathe_motion, "sway": wysiwyg_sway_motion,
            "blink": wysiwyg_blink_motion,
        }.get(effect)
        return fn(strength) if fn else None

    def wysiwyg_motion_fx_placement_transform(char):
        # Callers gate on wysiwyg_motion_fx_uses_placement, so only the
        # placement effects (float/shake/bounce/sink) ever reach the lookup.
        effect = str(char.get("motion_fx", "none") or "none").strip().lower()
        strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
        t = wysiwyg_motion_named(effect, strength)
        return t if t is not None else Transform()

    def wysiwyg_motion_fx_transform_for_char(char, use_original=False):
        if use_original:
            effect = str(char.get("original_motion_fx", char.get("motion_fx", "none")) or "none").strip().lower()
            strength = wysiwyg_clamp(wysiwyg_float(char.get("original_motion_fx_strength", char.get("motion_fx_strength", 1.0)), 1.0), 0.0, 2.0)
        else:
            effect = str(char.get("motion_fx", "none") or "none").strip().lower()
            strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)

        return wysiwyg_motion_named(effect, strength)



    def wysiwyg_matrixcolor_for_char(char, use_original=False):
        char = wysiwyg_ensure_color_filter_state(char)
        if not char:
            return None

        matrix = IdentityMatrix()

        brightness = wysiwyg_float(wysiwyg_char_filter_value(char, "filter_brightness", 0.0, use_original=use_original), 0.0)
        if abs(brightness) > 0.0001:
            matrix = matrix * BrightnessMatrix(brightness)

        contrast = wysiwyg_float(wysiwyg_char_filter_value(char, "filter_contrast", 1.0, use_original=use_original), 1.0)
        if abs(contrast - 1.0) > 0.0001:
            matrix = matrix * ContrastMatrix(contrast)

        saturation = wysiwyg_float(wysiwyg_char_filter_value(char, "filter_saturation", 1.0, use_original=use_original), 1.0)
        if abs(saturation - 1.0) > 0.0001:
            matrix = matrix * SaturationMatrix(saturation)

        hue = wysiwyg_float(wysiwyg_char_filter_value(char, "filter_hue", 0.0, use_original=use_original), 0.0)
        if abs(hue) > 0.0001:
            matrix = matrix * HueMatrix(hue)

        invert = wysiwyg_float(wysiwyg_char_filter_value(char, "filter_invert", 0.0, use_original=use_original), 0.0)
        if abs(invert) > 0.0001:
            matrix = matrix * InvertMatrix(invert)

        if bool(wysiwyg_char_filter_value(char, "filter_sepia", False, use_original=use_original)):
            matrix = matrix * SepiaMatrix()

        return matrix

    def wysiwyg_transform_effect_kwargs(char, use_original=False):
        blur_value = wysiwyg_clamp(
            wysiwyg_float(wysiwyg_char_filter_value(char, "filter_blur", 0.0, use_original=use_original), 0.0),
            0.0,
            20.0,
        )

        rotate_val = wysiwyg_float(char.get("original_rotate", char.get("rotate", 0.0)), 0.0) if use_original else wysiwyg_float(char.get("rotate", 0.0), 0.0)

        kwargs = dict(
            xzoom=wysiwyg_float(char.get("original_xzoom", char.get("xzoom", 1.0)), 1.0) if use_original else wysiwyg_float(char.get("xzoom", 1.0), 1.0),
            yzoom=wysiwyg_float(char.get("original_yzoom", char.get("yzoom", 1.0)), 1.0) if use_original else wysiwyg_float(char.get("yzoom", 1.0), 1.0),
            alpha=wysiwyg_float(char.get("original_alpha", char.get("alpha", 1.0)), 1.0) if use_original else wysiwyg_float(char.get("alpha", 1.0), 1.0),
            blur=blur_value,
            matrixcolor=wysiwyg_matrixcolor_for_char(char, use_original=use_original),
        )

        kwargs["rotate"] = rotate_val if abs(rotate_val) > 0.01 else None

        return kwargs

    def wysiwyg_preview_displayable(char, xpos=None, ypos=None):
        char = wysiwyg_ensure_color_filter_state(char)
        char = wysiwyg_ensure_motion_fx_state(char)
        if not char:
            return Null()

        img = char.get("runtime_image") or char.get("image") or char.get("tag")
        if isinstance(img, (list, tuple)):
            img_str = " ".join(img)
        else:
            img_str = str(img)

        child = None
        if char.get("expression"):
            # `show expression ...`: the tag is an alias, not an image name,
            # so evaluate the original expression for the preview.
            try:
                child = renpy.displayable(renpy.python.py_eval(str(char.get("expression"))))
            except Exception:
                child = None
        if child is None:
            try:
                child = renpy.displayable(img_str)
            except Exception:
                child = Null()

        kwargs = wysiwyg_transform_effect_kwargs(char)
        if xpos is not None:
            kwargs["xpos"] = int(round(xpos))
        if ypos is not None:
            kwargs["ypos"] = int(round(ypos))

        return Transform(
            child,
            xanchor=0.5,
            yanchor=0.5,
            function=wysiwyg_motion_fx_function(char),
            **kwargs
        )

    def wysiwyg_motion_fx_function(char):
        char = wysiwyg_ensure_motion_fx_state(char)
        effect = str(char.get("motion_fx", "none") or "none").strip().lower()
        base_alpha = wysiwyg_clamp(wysiwyg_float(char.get("alpha", 1.0), 1.0), 0.0, 1.0)
        base_rotate = wysiwyg_float(char.get("rotate", 0.0), 0.0)
        base_xzoom = wysiwyg_float(char.get("xzoom", 1.0), 1.0)
        base_yzoom = wysiwyg_float(char.get("yzoom", 1.0), 1.0)
        strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)

        if effect == "none":
            return None

        if effect == "float":
            return None

        if effect == "breathe":
            def _breathe_fx(trans, st, at):
                trans.xoffset = 0
                trans.yoffset = -int(round(math.sin(st * 1.6) * 5.0 * strength))
                trans.rotate = base_rotate
                trans.xzoom = base_xzoom * (1.0 + (0.025 * strength * math.sin(st * 1.6)))
                trans.yzoom = base_yzoom * (1.0 + (0.025 * strength * math.sin(st * 1.6)))
                trans.alpha = base_alpha
                return 0.016
            return _breathe_fx

        if effect == "shake":
            return None

        if effect == "sway":
            def _sway_fx(trans, st, at):
                trans.xoffset = int(round(math.sin(st * 1.8) * 6.0 * strength))
                trans.yoffset = 0
                trans.rotate = base_rotate + (math.sin(st * 1.8) * 3.0 * strength)
                trans.xzoom = base_xzoom
                trans.yzoom = base_yzoom
                trans.alpha = base_alpha
                return 0.016
            return _sway_fx

        if effect == "bounce":
            return None

        if effect == "sink":
            return None

        if effect == "blink":
            def _blink_fx(trans, st, at):
                trans.xoffset = 0
                trans.yoffset = 0
                trans.rotate = base_rotate
                trans.xzoom = base_xzoom
                trans.yzoom = base_yzoom
                trans.alpha = 0.0 if (st % 1.2) < 0.16 else base_alpha
                return 0.016
            return _blink_fx

        return None

    def wysiwyg_color_matrix_expression_for_char(char):
        parts = []

        brightness = wysiwyg_float(char.get("filter_brightness", 0.0), 0.0)
        if abs(brightness) > 0.0001:
            parts.append("BrightnessMatrix(" + wysiwyg_fmt_float(brightness) + ")")

        contrast = wysiwyg_float(char.get("filter_contrast", 1.0), 1.0)
        if abs(contrast - 1.0) > 0.0001:
            parts.append("ContrastMatrix(" + wysiwyg_fmt_float(contrast) + ")")

        saturation = wysiwyg_float(char.get("filter_saturation", 1.0), 1.0)
        if abs(saturation - 1.0) > 0.0001:
            parts.append("SaturationMatrix(" + wysiwyg_fmt_float(saturation) + ")")

        hue = wysiwyg_float(char.get("filter_hue", 0.0), 0.0)
        if abs(hue) > 0.0001:
            parts.append("HueMatrix(" + wysiwyg_fmt_float(hue) + ")")

        invert = wysiwyg_float(char.get("filter_invert", 0.0), 0.0)
        if abs(invert) > 0.0001:
            parts.append("InvertMatrix(" + wysiwyg_fmt_float(invert) + ")")

        if char.get("filter_sepia"):
            parts.append("SepiaMatrix()")

        if not parts:
            return None

        return " * ".join(parts)

    def wysiwyg_motion_fx_at_expression_for_char(char):
        effect = str(char.get("motion_fx", "none") or "none").strip().lower()
        if effect not in ("float", "shake", "bounce", "sink", "breathe", "sway", "blink"):
            return None
        strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
        return "wysiwyg_" + effect + "_motion(" + wysiwyg_fmt_float(strength) + ")"

    def wysiwyg_on_color_filter_change(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        wysiwyg_ensure_color_filter_state(char)
        char["filter_blur"] = wysiwyg_clamp(wysiwyg_float(char.get("filter_blur", 0.0), 0.0), 0.0, 20.0)
        char["filter_brightness"] = wysiwyg_clamp(wysiwyg_float(char.get("filter_brightness", 0.0), 0.0), -1.0, 1.0)
        char["filter_contrast"] = wysiwyg_clamp(wysiwyg_float(char.get("filter_contrast", 1.0), 1.0), 0.0, 2.0)
        char["filter_saturation"] = wysiwyg_clamp(wysiwyg_float(char.get("filter_saturation", 1.0), 1.0), 0.0, 2.0)
        char["filter_hue"] = wysiwyg_clamp(wysiwyg_float(char.get("filter_hue", 0.0), 0.0), -180.0, 180.0)
        char["filter_invert"] = wysiwyg_clamp(wysiwyg_float(char.get("filter_invert", 0.0), 0.0), 0.0, 1.0)
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_on_motion_fx_change(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        wysiwyg_ensure_motion_fx_state(char)
        char["motion_fx"] = str(char.get("motion_fx", "none") or "none").strip().lower()
        char["motion_fx_strength"] = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_reset_selected_color_filters_to_defaults():
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        wysiwyg_push_undo(char)
        wysiwyg_ensure_color_filter_state(char)
        for key, value in wysiwyg_default_color_filter_values().items():
            char[key] = value
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status("Reset selected color filters to defaults.")

    def wysiwyg_reset_selected_color_filter_key(tag, key):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        defaults = wysiwyg_default_color_filter_values()
        if key not in defaults:
            return
        wysiwyg_push_undo(char)
        char[key] = defaults[key]
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_set_motion_fx(tag, effect):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        wysiwyg_ensure_motion_fx_state(char)
        effect = str(effect or "none").strip().lower()
        active = str(char.get("motion_fx", "none") or "none").strip().lower()
        if active == effect:
            effect = "none"
        wysiwyg_push_undo(char)
        char["motion_fx"] = effect
        wysiwyg_on_motion_fx_change(tag)
        if effect == "none":
            wysiwyg_set_status("Motion FX disabled.")
        else:
            wysiwyg_set_status("Motion FX: " + effect + ".")

    def wysiwyg_reset_selected_motion_fx_to_defaults():
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        wysiwyg_ensure_motion_fx_state(char)
        if str(char.get("motion_fx", "none") or "none").strip().lower() == "none" and abs(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0) - 1.0) < 0.0001:
            return
        wysiwyg_push_undo(char)
        char["motion_fx"] = "none"
        char["motion_fx_strength"] = 1.0
        wysiwyg_mark_runtime_dirty()
        wysiwyg_set_status("Motion FX reset to defaults.")

    def wysiwyg_drag_transform_slider(tag, key):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        current = wysiwyg_float(char.get(key, 0.0), 0.0)

        if key in ("xzoom", "yzoom"):
            mem_key = tag + ":" + key
            previous = wysiwyg_float(store.wysiwyg_transform_memory.get(mem_key, 1.0), 1.0)
            sign = -1.0 if previous < 0.0 else 1.0
            current = round(wysiwyg_clamp(abs(current), 0.01, 2.0) * sign, 3)
            char[key] = current
            if store.wysiwyg_scale_locked:
                other_key = "yzoom" if key == "xzoom" else "xzoom"
                other_mem_key = tag + ":" + other_key
                other_previous = wysiwyg_float(store.wysiwyg_transform_memory.get(other_mem_key, 1.0), 1.0)
                other_sign = -1.0 if other_previous < 0.0 else 1.0
                char[other_key] = round(abs(current) * other_sign, 3)
            wysiwyg_update_char_size(char)
        elif key == "rotate":
            char[key] = round(wysiwyg_clamp(current, -180.0, 180.0), 1)
        elif key == "alpha":
            char[key] = round(wysiwyg_clamp(current, 0.0, 1.0), 3)

        
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_snap_char_transform(char):
        # Rounds the transform values to what the writer would emit and
        # recenters x/y on the rounded pixel center, so the preview and
        # the written line agree exactly.
        if "xzoom" in char:
            char["xzoom"] = round(wysiwyg_float(char["xzoom"], 1.0), 3)
        if "yzoom" in char:
            char["yzoom"] = round(wysiwyg_float(char["yzoom"], 1.0), 3)
        if "rotate" in char:
            char["rotate"] = round(wysiwyg_float(char["rotate"], 0.0), 1)
        if "alpha" in char:
            char["alpha"] = round(wysiwyg_float(char["alpha"], 1.0), 3)
        wysiwyg_update_char_size(char)
        w = wysiwyg_float(char.get("w", 0.0), 0.0)
        h = wysiwyg_float(char.get("h", 0.0), 0.0)
        cx = round(wysiwyg_float(char.get("x", 0.0), 0.0) + w / 2.0)
        cy = round(wysiwyg_float(char.get("y", 0.0), 0.0) + h / 2.0)
        char["x"] = cx - w / 2.0
        char["y"] = cy - h / 2.0
        char["parsed_center_x"] = cx
        char["parsed_center_y"] = cy
        char["anchor_x"] = char["x"]
        char["anchor_y"] = char["y"]

    def wysiwyg_release_transform_slider(tag, key):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        wysiwyg_snap_char_transform(char)

        mem_key = tag + ":" + key
        current = wysiwyg_float(char.get(key, 0.0), 0.0)
        previous = wysiwyg_float(store.wysiwyg_transform_memory.get(mem_key, current), current)
        if abs(previous - current) > 0.0001:
            snapshot = {
                "tag": char.get("tag"),
                "x": wysiwyg_float(char.get("x", 0.0), 0.0),
                "y": wysiwyg_float(char.get("y", 0.0), 0.0),
                "w": wysiwyg_float(char.get("w", 0), 0.0),
                "h": wysiwyg_float(char.get("h", 0), 0.0),
                "anchor_x": wysiwyg_float(char.get("anchor_x", char.get("x", 0.0)), 0.0),
                "anchor_y": wysiwyg_float(char.get("anchor_y", char.get("y", 0.0)), 0.0),
                "rotate": wysiwyg_float(char.get("rotate", 0.0), 0.0),
                "xzoom": wysiwyg_float(char.get("xzoom", 1.0), 1.0),
                "yzoom": wysiwyg_float(char.get("yzoom", 1.0), 1.0),
                "alpha": wysiwyg_float(char.get("alpha", 1.0), 1.0),
                "parsed_x": char.get("parsed_x", False),
                "parsed_y": char.get("parsed_y", False),
                "parsed_center_x": char.get("parsed_center_x", char.get("x", 0.0) + wysiwyg_float(char.get("w", 0.0), 0.0) / 2.0),
                "parsed_center_y": char.get("parsed_center_y", char.get("y", 0.0) + wysiwyg_float(char.get("h", 0.0), 0.0) / 2.0),
            }
            snapshot[key] = previous
            if key in ("xzoom", "yzoom") and store.wysiwyg_scale_locked:
                other_key = "yzoom" if key == "xzoom" else "xzoom"
                other_mem_key = tag + ":" + other_key
                other_previous = wysiwyg_float(store.wysiwyg_transform_memory.get(other_mem_key, current), current)
                snapshot[other_key] = other_previous
                store.wysiwyg_transform_memory[mem_key] = current
                store.wysiwyg_transform_memory[other_mem_key] = current
            else:
                store.wysiwyg_transform_memory[mem_key] = current

            store.wysiwyg_undo_stack.append(snapshot)
            store.wysiwyg_undo_stack = store.wysiwyg_undo_stack[-50:]

        store.wysiwyg_selected_tag = tag
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            renpy.restart_interaction()

    def wysiwyg_toggle_scale_lock():
        store.wysiwyg_scale_locked = not store.wysiwyg_scale_locked
        if store.wysiwyg_scale_locked:
            char = wysiwyg_find_char(store.wysiwyg_selected_tag)
            if char:
                value = round(wysiwyg_clamp(abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0)), 0.01, 2.0), 3)
                char["xzoom"] = value
                char["yzoom"] = value
                wysiwyg_update_char_size(char)
                store.wysiwyg_transform_memory[char.get("tag") + ":xzoom"] = value
                store.wysiwyg_transform_memory[char.get("tag") + ":yzoom"] = value
                store.wysiwyg_saved_runtime = False
        renpy.restart_interaction()

    def wysiwyg_apply_selected_transform(rotate, xzoom, yzoom, alpha, status):
        # Shared body of the two transform resets: the callers differ only
        # in the values they assign and the status they report.
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character to reset.")
            return
        wysiwyg_push_undo(char)
        char["rotate"] = rotate
        char["xzoom"] = xzoom
        char["yzoom"] = yzoom
        char["alpha"] = alpha
        wysiwyg_update_char_size(char)
        for transform_key in ("rotate", "xzoom", "yzoom", "alpha"):
            store.wysiwyg_transform_memory[char.get("tag") + ":" + transform_key] = char[transform_key]
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status(status)

    def wysiwyg_reset_selected_transform():
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character to reset.")
            return
        wysiwyg_apply_selected_transform(
            wysiwyg_float(char.get("original_rotate", 0.0), 0.0),
            wysiwyg_float(char.get("original_xzoom", 1.0), 1.0),
            wysiwyg_float(char.get("original_yzoom", 1.0), 1.0),
            wysiwyg_float(char.get("original_alpha", 1.0), 1.0),
            "Reset selected character transform.")

    def wysiwyg_reset_selected_transform_to_defaults():
        wysiwyg_apply_selected_transform(0.0, 1.0, 1.0, 1.0,
            "Reset selected character transform to defaults.")

    def wysiwyg_x_position_targets_for_char(char):
        screen_w = wysiwyg_screen_w()
        sprite_w = int(char.get("w", 0) or 0)

        if sprite_w > 0:
            return [
                ("left_edge", 0),
                ("left", int(screen_w * 0.25 - sprite_w / 2)),
                ("center", int((screen_w - sprite_w) / 2)),
                ("right", int(screen_w * 0.75 - sprite_w / 2)),
                ("right_edge", int(screen_w - sprite_w)),
            ]

        return [
            ("left_edge", 0),
            ("left", int(screen_w * 0.25)),
            ("center", int(screen_w / 2)),
            ("right", int(screen_w * 0.75)),
            ("right_edge", screen_w),
        ]

    def wysiwyg_place_selected_on_x_target(target_name):
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return

        targets = dict(wysiwyg_x_position_targets_for_char(char))
        if target_name not in targets:
            return

        new_x = int(targets[target_name])
        w = wysiwyg_float(char.get("w", 0.0), 0.0)
        cx = round(new_x + w / 2.0)
        adjusted_x = cx - w / 2.0

        if abs(wysiwyg_float(char.get("x", 0.0), 0.0) - adjusted_x) < 0.01:
            wysiwyg_set_status("Already at the '" + str(target_name) + "' position.")
            return

        wysiwyg_push_undo(char)
        char["x"] = adjusted_x
        char["anchor_x"] = adjusted_x
        char["parsed_center_x"] = cx
        char["parsed_x"] = True
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status("Placed at the '" + str(target_name) + "' position.")

    def wysiwyg_current_image_name(tag):
        try:
            attrs = list(renpy.game.context().images.get_attributes("master", tag, ()))
        except Exception:
            attrs = []
        if attrs:
            return " ".join([tag] + [str(i) for i in attrs])
        return tag
    def wysiwyg_get_current_position():
        try:
            ctx = renpy.game.context()
            if ctx and hasattr(ctx, "current") and getattr(ctx, "current", None):
                filename = getattr(ctx.current, "filename", None)
                linenumber = getattr(ctx.current, "linenumber", None)
                if filename and not filename.endswith("wysiwyg_editor.rpy"):
                    return filename, linenumber
        except Exception:
            pass
        try:
            filename, linenumber = renpy.get_filename_line()
            if filename and not filename.endswith("wysiwyg_editor.rpy"):
                return filename, linenumber
        except Exception:
            pass
        return None, None

    def wysiwyg_executed_lines():
        # Set of (filename, line) pairs that actually executed, newest last.
        # Requires config.line_log = True (enabled in wysiwyg_init).
        try:
            log = renpy.get_line_log()
        except Exception:
            log = []
        result = []
        for entry in log or []:
            try:
                if hasattr(entry, "filename"):
                    result.append((str(entry.filename), int(entry.line)))
                else:
                    result.append((str(entry[0]), int(entry[1])))
            except Exception:
                pass
        return result

    def wysiwyg_find_source_safely(tag, image_name=None, is_bg=False):
        filename, current_line = wysiwyg_get_current_position()
        if not filename or not current_line:
            return None

        best_node = None
        best_node_image = None
        best_line = -1

        # All candidate statements for this tag, keyed by source location, so
        # the line log can tell us which one really executed. The simple
        # "last line number before the current one" heuristic fails with menu
        # branches: a show in an untaken branch can sit closer to the current
        # line than the one that actually ran.
        candidates = {}
        # With nodes indexed by location in the same pass: a `with fade` on
        # a show line becomes separate With statements at the same
        # filename:line, and the rewritten line must carry them over.
        with_by_loc = {}

        for node in getattr(renpy.game.script, "all_stmts", []):
            if isinstance(node, renpy.ast.With):
                expr_text = str(getattr(node, "expr", "") or "").strip()
                if expr_text and expr_text != "None":
                    with_by_loc[(str(getattr(node, "filename", "")), int(getattr(node, "linenumber", 0) or 0))] = expr_text
                continue
            if is_bg:
                if not isinstance(node, renpy.ast.Scene):
                    continue
            else:
                if not isinstance(node, renpy.ast.Show):
                    continue

            node_image, expression, node_tag, at_list, layer = wysiwyg_node_image(node)
            if layer != "master":
                continue

            if not is_bg and node_tag != tag:
                continue

            node_filename = getattr(node, "filename", "")
            line = getattr(node, "linenumber", 0)
            candidates[(str(node_filename), int(line))] = (node, node_image)

            if node_filename != filename:
                continue
            if line > current_line:
                continue

            if line > best_line:
                best_line = line
                best_node = node
                best_node_image = node_image

        # Prefer the statement that actually executed most recently. The
        # session exec_log (line_log_callbacks) keeps true execution order;
        # the engine line log deduplicates (first-execution order), so a hit
        # found only there is downgraded to "linelog-dedup" and the import
        # status tells the user to verify. Chars resolved by neither are
        # "heuristic". Every save re-validates the target line regardless.
        source_confidence = "heuristic"
        matched = None
        for key in reversed(list(WYSIWYG_RUNTIME.exec_log)):
            if key in candidates:
                matched = key
                source_confidence = "linelog"
                break
        if matched is None:
            for key in reversed(wysiwyg_executed_lines()):
                if key in candidates:
                    matched = key
                    source_confidence = "linelog-dedup"
                    break
        if matched is not None:
            best_node, best_node_image = candidates[matched]

        if not best_node:
            return None

        zorder_val = None
        zorder_raw = None
        behind = []
        expression = None
        as_tag = None
        at_list_exprs = []
        onlayer = None
        imspec = getattr(best_node, "imspec", None)
        if imspec:
            _, expression, _, node_at_list, _ = wysiwyg_imspec_parts(imspec)
            at_list_exprs = [str(a) for a in (node_at_list or [])]
            as_tag = wysiwyg_imspec_explicit_tag(imspec)
            # An EXPLICIT `onlayer` clause (raw imspec layer is None when
            # absent) must survive the rewrite: dropping it would move the
            # sprite to its default layer on the next execution.
            if len(imspec) >= 6:
                onlayer = imspec[4]
            elif len(imspec) == 3:
                onlayer = imspec[2]
            onlayer = str(onlayer) if onlayer else None
            if len(imspec) >= 6 and imspec[5] is not None:
                try:
                    zorder_val = int(str(imspec[5]))
                except Exception:
                    # zorder given as an expression (e.g. `zorder z + 1`):
                    # keep the raw text so the rewritten line preserves it.
                    zorder_val = None
                    zorder_raw = str(imspec[5])
            if len(imspec) == 7:
                behind = [str(i) for i in (imspec[6] or [])]

        with_expr = with_by_loc.get((str(getattr(best_node, "filename", "")), int(getattr(best_node, "linenumber", 0) or 0)))

        return {
            "key": tag,
            "tag": tag,
            "image": best_node_image or image_name or tag,
            "runtime_image": best_node_image or image_name or tag,
            "expression": str(expression) if expression else None,
            "as_tag": str(as_tag) if as_tag else None,
            "with_expr": with_expr,
            "original_with_expr": with_expr,
            "onlayer": onlayer,
            "at_list_exprs": at_list_exprs,
            "has_atl": getattr(best_node, "atl", None) is not None,
            "source_confidence": source_confidence,
            "source_file": best_node.filename,
            "source_line": best_node.linenumber,
            "zorder": zorder_val,
            "zorder_raw": zorder_raw,
            "original_zorder": zorder_val,
            "behind": behind,
            "unsaved": True,
        }

    def wysiwyg_char_center_from_transform(transform_data, img_w, img_h, bounds=None):
        if bounds:
            center_x = float(bounds[0]) + float(bounds[2]) / 2.0
            center_y = float(bounds[1]) + float(bounds[3]) / 2.0
        else:
            center_x = float(wysiwyg_screen_w() * 0.5)
            center_y = float(wysiwyg_screen_h() * 0.5)

        xzoom_val = abs(wysiwyg_float(transform_data.get("xzoom", 1.0), 1.0))
        yzoom_val = abs(wysiwyg_float(transform_data.get("yzoom", 1.0), 1.0))

        # The live render (bounds) is the ground truth: its center equals the
        # sprite center for any anchor, zoom and rotation, in any game.
        # The parsed source line can be a *different* statement than the one
        # that actually placed the sprite (menu branches, at-transforms, game
        # default anchors), so it is only trusted when it agrees with the live
        # render within a couple of pixels - in that case the parsed integers
        # win, giving an exact, drift-free save/import round trip.
        def _parsed_center(pos_key, anchor_key, img_size, zoom_val, screen_size):
            if pos_key not in transform_data or anchor_key not in transform_data:
                return None
            pos = transform_data[pos_key]
            pos_px = pos * float(screen_size) if abs(pos) <= 1.0001 else pos
            anchor = transform_data[anchor_key]
            anchor_px = anchor * img_size * zoom_val if abs(anchor) <= 1.0001 else anchor
            return pos_px - anchor_px + (img_size * zoom_val) / 2.0

        parsed_cx = _parsed_center("xpos", "xanchor", img_w, xzoom_val, wysiwyg_screen_w())
        parsed_cy = _parsed_center("ypos", "yanchor", img_h, yzoom_val, wysiwyg_screen_h())

        has_x = bool(bounds)
        if parsed_cx is not None:
            if bounds is None or abs(parsed_cx - center_x) <= 2.0:
                center_x = parsed_cx
                has_x = True

        has_y = bool(bounds)
        if parsed_cy is not None:
            if bounds is None or abs(parsed_cy - center_y) <= 2.0:
                center_y = parsed_cy
                has_y = True

        return center_x, center_y, has_x, has_y

    # --- Scene import --------------------------------------------------------
    # Reads the master layer, resolves each tag to its source statement,
    # measures live render bounds, and builds the editable character dicts
    # (current values + original_* copies used by the reset buttons).
    def wysiwyg_import_scene():
        WYSIWYG_RUNTIME.source_text_cache = {}
        had_existing_import = bool(store.wysiwyg_chars or store.wysiwyg_bg)
        dropped_pending = len([c for c in store.wysiwyg_chars if c.get("pending_insert")])
        dropped_hides = len([c for c in store.wysiwyg_chars if c.get("pending_hide")])

        # Carryover: an autoreload (triggered by our own save) wipes the
        # engine's line log, so the NEXT import would degrade every
        # character to an uncertain AST guess. But when the game is still
        # paused on the exact statement recorded at the last successful
        # save, the previous entries' source lines are known-good - reuse
        # them instead of guessing. Only same-position imports qualify:
        # after the player moves on, old lines may describe other scenes.
        prev_by_tag = {}
        saved_pos = store.wysiwyg_saved_position
        if saved_pos and store.wysiwyg_chars:
            saved_lines = saved_pos[1] if isinstance(saved_pos[1], (list, tuple, set)) else [saved_pos[1]]
            cur_f, cur_l = wysiwyg_get_current_position()
            if cur_f and wysiwyg_norm_path(cur_f) == str(saved_pos[0]) and int(cur_l or 0) in [int(l) for l in saved_lines]:
                for c in store.wysiwyg_chars:
                    if c.get("pending_insert") or c.get("locked"):
                        continue
                    if c.get("source_confidence") not in ("linelog", "linelog-dedup", "carryover"):
                        # Never promote a heuristic guess to trusted: the
                        # previous entry itself was never validated by
                        # execution, so carrying it forward would disarm
                        # the uncertain-save confirmation for a line that
                        # may belong to an untaken branch.
                        continue
                    if c.get("source_file") and c.get("source_line"):
                        prev_by_tag[c.get("tag")] = c

        if store.wysiwyg_chars or store.wysiwyg_bg:
            wysiwyg_restore_imported_preview()

        store.wysiwyg_saved_runtime = False
        store.wysiwyg_bg = None
        store.wysiwyg_bg_source = None
        store.wysiwyg_scene_with = None
        store.wysiwyg_chars = []
        store.wysiwyg_transform_memory = {}

        try:
            showing_tags = set(renpy.get_showing_tags("master"))
        except Exception:
            showing_tags = set()

        bg_seen = False
        by_tag = {}
        imported = 0

        bg_node = wysiwyg_find_source_safely(None, is_bg=True)
        if bg_node:
            image_name = bg_node["image"]
            store.wysiwyg_bg = image_name
            store.wysiwyg_bg_source = {"file": bg_node["source_file"], "line": bg_node["source_line"], "image": image_name}
            bg_seen = True

        for tag in sorted(showing_tags):
            if tag.startswith("_") or tag in WYSIWYG_BLACKLIST:
                continue

            image_name = wysiwyg_current_image_name(tag)
            if wysiwyg_is_background(tag, image_name):
                if not bg_seen:
                    bg_seen = True
                    store.wysiwyg_bg = image_name
                    bg_ast = wysiwyg_find_source_safely(tag, image_name, is_bg=False)
                    if bg_ast:
                        store.wysiwyg_bg_source = {"file": bg_ast["source_file"], "line": bg_ast["source_line"], "image": image_name}
                continue

            ast_found = wysiwyg_find_source_safely(tag, image_name, is_bg=False)
            if ast_found:
                by_tag[tag] = ast_found
            else:
                by_tag[tag] = {
                    "key": tag,
                    "tag": tag,
                    "image": image_name,
                    "runtime_image": image_name,
                    "expression": None,
                    "as_tag": None,
                    "with_expr": None,
                    "at_list_exprs": [],
                    "has_atl": False,
                    "source_confidence": "none",
                    "source_file": "",
                    "source_line": 0,
                    "zorder": None,
                    "zorder_raw": None,
                    "original_zorder": None,
                    "behind": [],
                    "unsaved": True,
                }
            data = by_tag[tag]
            prev = prev_by_tag.get(tag)
            if (prev is not None
                    and data.get("source_confidence") != "linelog"
                    and str(prev.get("image")) == str(data.get("image") or image_name)):
                prev_file = wysiwyg_norm_path(prev.get("source_file", ""))
                prev_line = int(prev.get("source_line") or 0)
                if re.match(r"show(\s|:|$)", str(wysiwyg_source_line_text(prev_file, prev_line) or "").strip()):
                    # The previous entry described exactly this statement, so
                    # its statement-level fields are more trustworthy than
                    # whatever the AST guess dug up. Unsaved tweaks were
                    # discarded above, so originals are the file truth.
                    data["source_file"] = prev_file
                    data["source_line"] = prev_line
                    data["source_confidence"] = "carryover"
                    carried_with = prev.get("original_with_expr", prev.get("with_expr"))
                    data["with_expr"] = carried_with
                    data["original_with_expr"] = carried_with
                    for key in ("expression", "as_tag", "at_list_exprs", "has_atl",
                                "zorder", "zorder_raw", "behind", "onlayer"):
                        if key in prev:
                            data[key] = prev[key]
                    data["original_zorder"] = prev.get("original_zorder", prev.get("zorder"))
                    data["zorder"] = data["original_zorder"]

        chars = []
        for tag, data in sorted(by_tag.items()):
            try:
                bounds = renpy.get_image_bounds(tag, width=wysiwyg_screen_w(), height=wysiwyg_screen_h(), layer="master")
            except Exception:
                bounds = None

            transform_data = wysiwyg_parse_transform_from_line(data.get("source_file", ""), data.get("source_line", 0))
            original_xzoom = abs(wysiwyg_float(transform_data.get("xzoom", 1.0), 1.0))
            original_yzoom = abs(wysiwyg_float(transform_data.get("yzoom", 1.0), 1.0))
            is_rotated = (abs(wysiwyg_float(transform_data.get("rotate", 0.0), 0.0)) > 0.01)

            img_w, img_h = wysiwyg_get_image_size(data.get("image", tag), tag)

            if img_w <= 0.01 or img_h <= 0.01:
                if bounds:
                    if is_rotated:
                        img_w = 400.0
                        img_h = 800.0
                    else:
                        img_w = float(bounds[2]) / (original_xzoom if original_xzoom > 0.001 else 1.0)
                        img_h = float(bounds[3]) / (original_yzoom if original_yzoom > 0.001 else 1.0)
                else:
                    img_w = 400.0
                    img_h = 800.0

            if img_w <= 0.01:
                img_w = 400.0
            if img_h <= 0.01:
                img_h = 800.0

            data["img_w"] = img_w
            data["img_h"] = img_h

            w_unrotated = img_w * original_xzoom
            h_unrotated = img_h * original_yzoom

            center_x, center_y, has_x, has_y = wysiwyg_char_center_from_transform(transform_data, img_w, img_h, bounds)
            center_x = round(center_x)
            center_y = round(center_y)
            data["parsed_x"] = has_x
            data["parsed_y"] = has_y
            data["parsed_center_x"] = center_x
            data["parsed_center_y"] = center_y
            data["original_parsed_center_x"] = center_x
            data["original_parsed_center_y"] = center_y

            if bounds:
                x = center_x - w_unrotated / 2.0
                y = center_y - h_unrotated / 2.0
                w = w_unrotated
                h = h_unrotated
            else:
                w = w_unrotated
                h = h_unrotated
                x_fallback = float(wysiwyg_screen_w() * (0.35 + 0.12 * imported))
                y_fallback = float(wysiwyg_screen_h() * 0.15)
                x = x_fallback - w_unrotated / 2.0
                y = y_fallback - h_unrotated / 2.0

            if not data.get("runtime_image"):
                data["runtime_image"] = data.get("image", tag)
            data["x"] = float(x)
            data["y"] = float(y)
            data["original_x"] = data["x"]
            data["original_y"] = data["y"]
            data["anchor_x"] = data["x"]
            data["anchor_y"] = data["y"]
            data["original_anchor_x"] = data["x"]
            data["original_anchor_y"] = data["y"]
            data["w"] = w
            data["h"] = h
            data["original_w"] = img_w
            data["original_h"] = img_h
            data["rotate"] = wysiwyg_float(transform_data.get("rotate", 0.0), 0.0)
            data["xzoom"] = wysiwyg_float(transform_data.get("xzoom", 1.0), 1.0)
            data["yzoom"] = wysiwyg_float(transform_data.get("yzoom", 1.0), 1.0)
            data["alpha"] = wysiwyg_float(transform_data.get("alpha", 1.0), 1.0)
            data["filter_blur"] = wysiwyg_float(transform_data.get("blur", 0.0), 0.0)
            data["filter_brightness"] = wysiwyg_float(transform_data.get("filter_brightness", 0.0), 0.0)
            data["filter_contrast"] = wysiwyg_float(transform_data.get("filter_contrast", 1.0), 1.0)
            data["filter_saturation"] = wysiwyg_float(transform_data.get("filter_saturation", 1.0), 1.0)
            data["filter_hue"] = wysiwyg_float(transform_data.get("filter_hue", 0.0), 0.0)
            data["filter_invert"] = wysiwyg_float(transform_data.get("filter_invert", 0.0), 0.0)
            data["filter_sepia"] = bool(transform_data.get("filter_sepia", False))
            data["motion_fx"] = str(transform_data.get("motion_fx", "none") or "none").strip().lower()
            data["motion_fx_strength"] = wysiwyg_clamp(wysiwyg_float(transform_data.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
            data["original_rotate"] = data["rotate"]
            data["original_xzoom"] = data["xzoom"]
            data["original_yzoom"] = data["yzoom"]
            data["original_alpha"] = data["alpha"]
            data["original_filter_blur"] = data["filter_blur"]
            data["original_filter_brightness"] = data["filter_brightness"]
            data["original_filter_contrast"] = data["filter_contrast"]
            data["original_filter_saturation"] = data["filter_saturation"]
            data["original_filter_hue"] = data["filter_hue"]
            data["original_filter_invert"] = data["filter_invert"]
            data["original_filter_sepia"] = data["filter_sepia"]
            data["original_motion_fx"] = data["motion_fx"]
            data["original_motion_fx_strength"] = data["motion_fx_strength"]
            for transform_key in ("rotate", "xzoom", "yzoom", "alpha"):
                store.wysiwyg_transform_memory[tag + ":" + transform_key] = data[transform_key]
            # Alpha bounds (pure-Python PNG decode) used to be computed here for
            # the removed guides/snapping feature. It walked every .rpy file and
            # decoded full sprites pixel by pixel, making the first import take
            # seconds in games where the sprite file was found. Left at 0 until
            # a feature actually needs them (then compute lazily, per sprite).
            data["alpha_top"] = 0
            data["alpha_bottom"] = 0
            wysiwyg_log_debug("[IMPORT] tag={0} img_w={1} img_h={2} x={3} y={4} rotate={5} xzoom={6} yzoom={7}".format(
                tag, img_w, img_h, data["x"], data["y"], data["rotate"], data["xzoom"], data["yzoom"]
            ))
            data["locked"] = wysiwyg_lock_reason(data)
            if not data["locked"] and not (data.get("at_list_exprs") or []) and not data.get("has_atl"):
                # Statement has neither an at-clause nor its own ATL block:
                # the tag may be inheriting a live transform from an earlier
                # show (keep_running_transform) that the statement text
                # cannot reveal. A statement with its own ATL block defines
                # its transform in text, which is already validated above.
                if not wysiwyg_tag_runtime_at_safe(tag):
                    data["locked"] = "inherits a transform from an earlier show"
            wysiwyg_log_debug("[IMPORT-SRC] tag={0} bounds={1} center=({2},{3}) locked={4!r} source={5}:{6} line={7!r}".format(
                tag, bounds, center_x, center_y, data.get("locked"), data.get("source_file"), data.get("source_line"),
                wysiwyg_source_line_text(data.get("source_file", ""), data.get("source_line", 0))
            ))
            chars.append(data)
            imported += 1

        editable_chars = [c for c in chars if not c.get("locked")]

        if chars and not had_existing_import:
            store.wysiwyg_chars = chars
            store.wysiwyg_selected_tag = editable_chars[0].get("tag") if editable_chars else None
            wysiwyg_refresh_char_bounds(chars, write_original=True)

        # Snapshot the untouched master layer (entries, at-lists, attributes)
        # before anything is hidden. Closing the editor without saving puts
        # this exact state back, so the game's own transforms/ATL animations
        # survive an edit session untouched.
        WYSIWYG_RUNTIME.master_snapshot = wysiwyg_capture_master_snapshot()

        # Locked characters are never hidden: they stay live on the master
        # layer (their animation keeps playing) and the editor never touches
        # them.
        for char in editable_chars:
            try:
                renpy.hide(char.get("tag"), layer="master")
            except Exception:
                pass

        store.wysiwyg_chars = chars
        store.wysiwyg_selected_tag = editable_chars[0].get("tag") if editable_chars else None
        store.wysiwyg_scene_with = wysiwyg_detect_scene_with()

        locked_count = len(chars) - len(editable_chars)
        uncertain = len([c for c in editable_chars if c.get("source_confidence") not in ("linelog", "carryover")])
        if imported or bg_seen:
            if locked_count:
                message = "Imported " + str(len(editable_chars)) + " editable + " + str(locked_count) + " locked character(s)."
            elif imported:
                message = "Imported " + str(imported) + " character(s)."
            else:
                # A background was matched but no characters: "Imported 0
                # character(s)" would read like a silent failure.
                message = "Imported the scene background only - no editable characters found."
            if uncertain:
                message += " " + str(uncertain) + " with uncertain source line - verify in Show Code before saving."
            if dropped_pending:
                message += " Discarded " + str(dropped_pending) + " added-but-unsaved sprite(s)."
            if dropped_hides:
                message += " Discarded " + str(dropped_hides) + " unsaved removal mark(s)."
            wysiwyg_set_status(message)
        else:
            wysiwyg_set_status("No editable scene/show lines found. Advance the scene, then press Import Scene.")

    # --- Adding new sprites ---------------------------------------------------
    # The file browser lists game/images/ ONLY - the directory Ren'Py
    # auto-defines images from. Selection is the sole input (there is no
    # free path field), so files outside game/images/ can never be added.
    WYSIWYG_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".avif")

    def wysiwyg_image_name_problem(name):
        parts = str(name or "").split()
        if not parts:
            return "empty name"
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", parts[0]):
            return "tag must start with a letter or underscore"
        if parts[0] in WYSIWYG_BLACKLIST:
            # The importer skips these tags, so a sprite added under one
            # could never be re-imported for editing later.
            return "tag '" + parts[0] + "' is reserved by the editor - rename the file"
        for part in parts[1:]:
            if not re.match(r"^[A-Za-z0-9_]+$", part):
                return "bad attribute '" + part + "'"
        return None

    def wysiwyg_image_name_for_file(fn):
        # Mirrors the engine's auto-image naming (00images.rpy): lowercased
        # basename without the extension and without an @oversampled suffix.
        base = os.path.splitext(os.path.basename(str(fn)))[0]
        base = base.lower()
        base = base.partition("@")[0]
        return base

    def wysiwyg_list_image_files():
        rows = []
        try:
            files = renpy.list_files()
        except Exception:
            files = []
        try:
            extensions = tuple(config.image_extensions)
        except Exception:
            extensions = WYSIWYG_IMAGE_EXTS
        for fn in sorted(files):
            low = wysiwyg_norm_path(fn).lower()
            if not low.startswith("images/"):
                continue
            if not low.endswith(extensions):
                continue
            name = wysiwyg_image_name_for_file(fn)
            problem = wysiwyg_image_name_problem(name)
            if not problem:
                # The engine registers auto-images at startup; a name it
                # cannot resolve would save a show line that renders the
                # "Image not found" error text instead of the sprite.
                try:
                    resolvable = renpy.has_image(name, exact=True)
                except Exception:
                    resolvable = False
                if not resolvable:
                    problem = "image not defined - restart the game if the file is new"
            rows.append({
                "file": str(fn),
                "name": name,
                "problem": problem,
                # Display strings are precomputed here: the row screens
                # re-render on every hover/keystroke, and re-wrapping
                # hundreds of identical paths there is wasted work.
                "name_wrapped": wysiwyg_wrap_path(name),
                "file_wrapped": wysiwyg_wrap_path(fn),
                "problem_line": (wysiwyg_wrap_path("cannot add: " + str(problem) + "  [" + str(fn) + "]") if problem else ""),
            })
        return rows

    # Group key for browser entries whose name prefix is unique. "*" can
    # never collide with a real prefix: image tags must match
    # [A-Za-z_][A-Za-z0-9_]*, so no file groups under a literal "*".
    WYSIWYG_BROWSER_UNGROUPED = "*"

    def wysiwyg_browser_groups(rows, filter_text):
        # Rows for the add-sprite browser, as a list of (prefix, rows)
        # pairs. With a filter active grouping is bypassed: one flat
        # pseudo-group holding every match, so the user never has to
        # expand groups while searching. Without a filter, rows group by
        # the name's first "_"-separated word; prefixes owning a single
        # file all pool into a trailing "*" group, otherwise the list
        # would be mostly one-entry headers.
        rows = rows or []
        filt = str(filter_text or "").strip().lower()
        if filt:
            matched = [r for r in rows
                       if filt in str(r.get("name", "")).lower()
                       or filt in str(r.get("file", "")).lower()]
            return [(WYSIWYG_BROWSER_UNGROUPED, matched)]
        buckets = {}
        for r in rows:
            prefix = str(r.get("name", "")).partition("_")[0]
            buckets.setdefault(prefix, []).append(r)
        groups = []
        singles = []
        for prefix in sorted(buckets):
            if len(buckets[prefix]) > 1:
                groups.append((prefix, buckets[prefix]))
            else:
                singles.extend(buckets[prefix])
        if singles:
            groups.append((WYSIWYG_BROWSER_UNGROUPED, singles))
        return groups

    def wysiwyg_toggle_browser_group(prefix):
        open_groups = store.wysiwyg_browser_open_groups
        if prefix in open_groups:
            open_groups.discard(prefix)
        else:
            open_groups.add(prefix)
        renpy.restart_interaction()

    def wysiwyg_select_char(tag):
        # Selecting another character cancels any half-typed edit field:
        # committing a buffer typed for one character against a freshly
        # selected one would teleport the wrong sprite.
        if store.wysiwyg_selected_tag != tag:
            wysiwyg_clear_edit_field()
        store.wysiwyg_selected_tag = tag
        renpy.restart_interaction()

    def wysiwyg_toggle_image_browser():
        store.wysiwyg_browser_hover = None
        # The browser's search input must be the only live input on the
        # screen - a lingering edit field would fight it for keystrokes.
        wysiwyg_clear_edit_field()
        if store.wysiwyg_char_page == "add":
            store.wysiwyg_char_page = "main"
        else:
            store.wysiwyg_browser_filter = ""
            WYSIWYG_RUNTIME.image_browser = wysiwyg_list_image_files()
            store.wysiwyg_char_page = "add"
        renpy.restart_interaction()

    def wysiwyg_add_character(image_name):
        # Creates a pending character: fully editable in the preview, but no
        # source line yet - Save Changes INSERTS a new `show` statement
        # before the statement the game is paused on.
        image_name = str(image_name or "").strip()
        problem = wysiwyg_image_name_problem(image_name)
        if problem:
            wysiwyg_set_status("Cannot add: " + problem)
            return
        try:
            resolvable = renpy.has_image(image_name, exact=True)
        except Exception:
            resolvable = False
        if not resolvable:
            wysiwyg_set_status("Cannot add: image '" + image_name + "' is not defined - restart the game if the file is new.")
            return
        tag = image_name.split()[0]
        if wysiwyg_find_char(tag):
            wysiwyg_set_status("Tag '" + tag + "' is already tracked - edit it in On Scene.")
            return
        try:
            showing = set(renpy.get_showing_tags("master"))
        except Exception:
            showing = set()
        if tag in showing:
            wysiwyg_set_status("Tag '" + tag + "' is already on the scene - use Import Scene first.")
            return
        filename, line = wysiwyg_get_current_position()
        if not filename or not line:
            wysiwyg_set_status("Cannot add: current script position is unknown.")
            return
        if wysiwyg_norm_path(filename).startswith("game/tl/"):
            # In a non-default language ctx.current sits inside game/tl/ -
            # inserting there would put the show inside a translate block:
            # it runs only in that language and is wiped when translations
            # regenerate.
            wysiwyg_set_status("Cannot add while playing a translation - switch to the base language first.")
            return
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            wysiwyg_set_status("Cannot add: the current statement is not in an editable .rpy file.")
            return

        img_w, img_h = wysiwyg_get_image_size(image_name, tag)
        if img_w <= 0.01:
            img_w = 400.0
        if img_h <= 0.01:
            img_h = 800.0
        cx = int(round(wysiwyg_screen_w() / 2.0))
        cy = int(round(wysiwyg_screen_h() / 2.0))

        char = {
            "key": tag,
            "tag": tag,
            "image": image_name,
            "runtime_image": image_name,
            "expression": None,
            "as_tag": None,
            "with_expr": None,
            "at_list_exprs": [],
            "has_atl": False,
            "source_confidence": "new",
            "source_file": "",
            "source_line": 0,
            "zorder": None,
            "zorder_raw": None,
            "original_zorder": None,
            "behind": [],
            "unsaved": True,
            "pending_insert": True,
            "locked": None,
            "img_w": img_w,
            "img_h": img_h,
            "w": img_w,
            "h": img_h,
            "x": float(cx - img_w / 2.0),
            "y": float(cy - img_h / 2.0),
            "parsed_x": True,
            "parsed_y": True,
            "parsed_center_x": cx,
            "parsed_center_y": cy,
            "rotate": 0.0,
            "xzoom": 1.0,
            "yzoom": 1.0,
            "alpha": 1.0,
            "motion_fx": "none",
            "motion_fx_strength": 1.0,
        }
        char["anchor_x"] = char["x"]
        char["anchor_y"] = char["y"]
        char.update(wysiwyg_default_color_filter_values())
        for key in ("x", "y", "w", "h", "anchor_x", "anchor_y", "rotate",
                    "xzoom", "yzoom", "alpha", "motion_fx", "motion_fx_strength",
                    "parsed_center_x", "parsed_center_y"):
            char["original_" + str(key)] = char[key]
        for key, value in wysiwyg_default_color_filter_values().items():
            char["original_" + key] = value
        for transform_key in ("rotate", "xzoom", "yzoom", "alpha"):
            store.wysiwyg_transform_memory[tag + ":" + transform_key] = char[transform_key]

        store.wysiwyg_chars.append(char)
        store.wysiwyg_selected_tag = tag
        store.wysiwyg_saved_runtime = False
        store.wysiwyg_char_page = "main"
        wysiwyg_set_status("Added '" + image_name + "' - drag it into place; Save Changes writes the new show line.")

    # --- Removing characters ---------------------------------------------------
    # A pending (added but unsaved) sprite is discarded on the spot - it
    # never touched any file. A tracked character is only MARKED: Save
    # Changes then inserts a `hide TAG` line before the statement the game
    # is paused on. The original show line and the image definitions are
    # never touched, so the character still appears as before and simply
    # leaves the scene from this point of the script on.
    def wysiwyg_remove_character(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        if char.get("locked"):
            wysiwyg_set_status("Locked characters cannot be removed.")
            return
        if char.get("pending_insert"):
            store.wysiwyg_chars.remove(char)
            for key in list(store.wysiwyg_transform_memory):
                if str(key).startswith(str(tag) + ":"):
                    del store.wysiwyg_transform_memory[key]
            if store.wysiwyg_selected_tag == tag:
                store.wysiwyg_selected_tag = None
            wysiwyg_set_status("Discarded unsaved sprite '" + str(tag) + "' - no file was touched.")
        else:
            char["pending_hide"] = True
            store.wysiwyg_saved_runtime = False
            if store.wysiwyg_selected_tag == tag:
                store.wysiwyg_selected_tag = None
            wysiwyg_set_status("'" + str(tag) + "' marked for removal - Save Changes writes a hide line.")
        renpy.restart_interaction()

    def wysiwyg_unremove_character(tag):
        char = wysiwyg_find_char(tag)
        if char and char.get("pending_hide"):
            char["pending_hide"] = False
            wysiwyg_set_status("Removal of '" + str(tag) + "' cancelled.")
            renpy.restart_interaction()

    # Presets for the `with` clause written onto the character's show line.
    # Editing it only reaches a `with` that sits ON the show line itself; a
    # standalone `with dissolve` statement on its own line is a different
    # statement the editor never rewrites (adding a preset next to one
    # stacks a second transition).
    WYSIWYG_WITH_PRESETS = [
        ("None", None),
        ("0.25s", "Dissolve(0.25)"),
        ("0.5s", "Dissolve(0.5)"),
        ("1s", "Dissolve(1.0)"),
        ("fade", "fade"),
    ]

    def wysiwyg_with_preset_key(expr):
        # Canonical form of a `with` expression, so equivalent spellings
        # light up the same preset button: the engine's bare `dissolve` IS
        # Dissolve(0.5), and numeric literals may vary (.25 vs 0.25).
        # Anything else - the author's own transitions - keys as raw text.
        s = str(expr or "").strip()
        if not s or s == "None":
            return None
        if s == "dissolve":
            return ("dissolve", 0.5)
        m = re.match(r"^Dissolve\(\s*([0-9]*\.?[0-9]+)\s*\)$", s)
        if m:
            return ("dissolve", float(m.group(1)))
        if s == "fade":
            return ("fade",)
        return ("raw", s)

    def wysiwyg_detect_scene_with():
        # The standalone `with` STATEMENT that reveals the current scene:
        # the first With node after the scene/earliest-show line and at or
        # before the paused statement, in the paused file. It is shared by
        # everything shown with it - the editor exposes it as one
        # scene-level value, not per character. A `with` living on a show
        # line is that line's own clause and is excluded here (its line
        # text starts with `show`, not `with`).
        cur_f, cur_l = wysiwyg_get_current_position()
        if not cur_f or not cur_l:
            return None
        cur_f = wysiwyg_norm_path(cur_f)
        cur_l = int(cur_l)
        lower = None
        bg_src = store.wysiwyg_bg_source or {}
        if wysiwyg_norm_path(bg_src.get("file", "")) == cur_f:
            lower = int(bg_src.get("line") or 0) or None
        if lower is None:
            show_lines = [int(c.get("source_line") or 0) for c in store.wysiwyg_chars
                          if not c.get("pending_insert")
                          and wysiwyg_norm_path(c.get("source_file", "")) == cur_f
                          and 0 < int(c.get("source_line") or 0) < cur_l]
            lower = min(show_lines) if show_lines else None
        if lower is None:
            return None
        # Control flow after `lower` ends the straight-line preamble the
        # editor is willing to reason about. A `with` past a menu/if/jump
        # may sit inside a branch the player never takes, and rewriting it
        # would corrupt an unrelated transition - past the barrier nothing
        # is offered. Within the preamble the LAST with wins: with e.g.
        # `scene bg` / `with fade` / shows / `with dissolve`, the dissolve
        # is the one that reveals the sprites, which is what the UI says.
        barrier_types = tuple(t for t in (
            getattr(renpy.ast, n, None)
            for n in ("Menu", "If", "Jump", "Call", "Label", "While", "Return")
        ) if t is not None)
        barrier = cur_l + 1
        for node in getattr(renpy.game.script, "all_stmts", []):
            if not isinstance(node, barrier_types):
                continue
            node_file = wysiwyg_norm_path(getattr(node, "filename", ""))
            node_line = int(getattr(node, "linenumber", 0) or 0)
            if node_file == cur_f and lower < node_line <= cur_l and node_line < barrier:
                barrier = node_line
        best = None
        for node in getattr(renpy.game.script, "all_stmts", []):
            if not isinstance(node, renpy.ast.With):
                continue
            node_file = wysiwyg_norm_path(getattr(node, "filename", ""))
            node_line = int(getattr(node, "linenumber", 0) or 0)
            if node_file != cur_f or not (lower < node_line <= cur_l) or node_line >= barrier:
                continue
            # Comments must not leak into the expression: `with dissolve
            # # note` would otherwise key as a "custom transition" and the
            # equivalent preset would not light up.
            text = wysiwyg_strip_line_comment(str(wysiwyg_source_line_text(node_file, node_line) or "")).strip()
            match = re.match(r"^with\s+(.+?)\s*$", text)
            if not match:
                continue
            if best is None or node_line > best[0]:
                best = (node_line, match.group(1))
        if best is None:
            return None
        return {"file": cur_f, "line": best[0], "expr": best[1], "original": best[1]}

    def wysiwyg_set_scene_with(expr):
        # Picking a preset supersedes a half-typed custom time: close the
        # inline input instead of leaving it blinking next to the presets.
        if store.wysiwyg_edit_field == "scenewithsec":
            wysiwyg_clear_edit_field()
        scene_with = store.wysiwyg_scene_with
        if not scene_with:
            return
        expr = "None" if expr is None else str(expr)
        if wysiwyg_with_preset_key(scene_with.get("expr")) == wysiwyg_with_preset_key(expr):
            return
        scene_with["expr"] = expr
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status("Scene reveal transition set to 'with " + expr + "' - Save Changes rewrites the with line (no live preview).")
        renpy.restart_interaction()

    def wysiwyg_set_with_expr(tag, expr):
        if store.wysiwyg_edit_field == "withsec":
            wysiwyg_clear_edit_field()
        char = wysiwyg_find_char(tag)
        if not char or char.get("locked"):
            return
        if wysiwyg_with_preset_key(char.get("with_expr")) == wysiwyg_with_preset_key(expr):
            # Same transition, differently spelled (author's `dissolve` vs
            # our Dissolve(0.5)): keep the original text so the line is not
            # rewritten just to normalize it.
            return
        char["with_expr"] = expr
        store.wysiwyg_saved_runtime = False
        if expr:
            wysiwyg_set_status("'" + str(tag) + "' will be shown with " + str(expr) + " - Save Changes writes it into the show line.")
        else:
            wysiwyg_set_status("'" + str(tag) + "' will be shown without a with transition.")
        renpy.restart_interaction()

    def wysiwyg_scriptedit_insert(filename, line, code):
        # Inserts a brand-new statement before filename:line. Returns the
        # physical line delta (+1) for the caller's bookkeeping.
        filename = wysiwyg_norm_path(filename)
        line = int(line)
        renpy.scriptedit.ensure_loaded(filename)
        if renpy.scriptedit.lines.get((filename, line)) is None:
            raise Exception("insert target " + filename + ":" + str(line) + " is not editable")

        surgery = wysiwyg_begin_ast_surgery()
        try:
            renpy.scriptedit.add_to_ast_before(code, filename, line)
            renpy.scriptedit.insert_line_before(code, filename, line)
            written_entry = renpy.scriptedit.lines.get((filename, line))
            if written_entry is None or written_entry.text.strip() != code.strip():
                raise Exception("post-insert line check failed at " + filename + ":" + str(line))
        finally:
            wysiwyg_end_ast_surgery(surgery)
        return 1

    def wysiwyg_added_sprite_target(base_file, base_line):
        # Where a brand-new show line goes. Preferred: right above the
        # earliest tracked show in the file the game is paused in, so the
        # added sprite is revealed by the same `with` transition as the
        # rest of the scene instead of popping in after it. Anything
        # ambiguous falls back to the paused statement itself, which is
        # always safe (that was the only behaviour before this helper).
        candidate = None
        for char in store.wysiwyg_chars:
            if char.get("pending_insert") or char.get("locked"):
                continue
            if char.get("source_confidence") not in ("linelog", "linelog-dedup", "carryover"):
                # A heuristic guess may point into an untaken menu branch;
                # anchoring there would splice the new sprite into code the
                # player never runs. Post-autoreload imports keep trust via
                # carryover, so this rarely costs the with-scene insert.
                continue
            src_file = wysiwyg_norm_path(char.get("source_file", ""))
            try:
                src_line = int(char.get("source_line") or 0)
            except Exception:
                continue
            if src_file != base_file or not (0 < src_line < base_line):
                continue
            # Belt and braces: the remembered line must still literally
            # hold a show statement.
            if not re.match(r"show(\s|:|$)", str(wysiwyg_source_line_text(src_file, src_line) or "").strip()):
                continue
            if candidate is None or src_line < candidate:
                candidate = src_line
        if candidate is not None:
            # Never land above the scene statement: the background must be
            # up before the sprite shows.
            bg_src = store.wysiwyg_bg_source or {}
            bg_file = wysiwyg_norm_path(bg_src.get("file", ""))
            try:
                bg_line = int(bg_src.get("line") or 0)
            except Exception:
                bg_line = 0
            if bg_file == base_file and bg_line >= candidate:
                candidate = None
        if candidate is not None:
            try:
                renpy.scriptedit.ensure_loaded(base_file)
                if renpy.scriptedit.lines.get((base_file, candidate)) is None:
                    candidate = None
            except Exception:
                candidate = None
        if candidate is not None:
            return (base_file, candidate)
        return (base_file, base_line)

    def wysiwyg_hide_between(target_file, start_line, end_line, tag):
        # True when a `hide TAG` statement sits in (start_line, end_line].
        # Inserting a new show for that tag above it would create a sprite
        # its own later hide immediately cancels on replay.
        if end_line <= start_line:
            return False
        try:
            renpy.scriptedit.ensure_loaded(target_file)
        except Exception:
            return False
        pattern = re.compile(r"^hide\s+" + re.escape(str(tag)) + r"(\s|$)")
        for line in range(start_line + 1, end_line + 1):
            entry = renpy.scriptedit.lines.get((target_file, line))
            if entry is not None and pattern.match(entry.text.strip()):
                return True
        return False

    def wysiwyg_at_parts_for_char(char):
        # The at-list the writer puts on the line, as a list of expression
        # strings. Split out of the line builder so a save can refresh the
        # character's `at_list_exprs` to match what was actually written -
        # stale metadata would mis-classify the statement on re-import.
        rotate_val = wysiwyg_float(char.get("rotate", 0.0), 0.0)
        xzoom_val = wysiwyg_float(char.get("xzoom", 1.0), 1.0)
        yzoom_val = wysiwyg_float(char.get("yzoom", 1.0), 1.0)

        img_w = wysiwyg_float(char.get("img_w", char.get("w", 0.0)), 0.0)
        img_h = wysiwyg_float(char.get("img_h", char.get("h", 0.0)), 0.0)
        if img_w <= 0:
            img_w = 400.0
        if img_h <= 0:
            img_h = 800.0
        xpos = int(round(wysiwyg_float(char.get("x", 0), 0.0) + (img_w * abs(xzoom_val)) / 2.0))
        ypos = int(round(wysiwyg_float(char.get("y", 0), 0.0) + (img_h * abs(yzoom_val)) / 2.0))
        parts = [
            "xpos=" + str(xpos),
            "ypos=" + str(ypos),
            "xanchor=0.5",
            "yanchor=0.5",
        ]

        if abs(rotate_val) > 0.01:
            parts.append("rotate=" + wysiwyg_fmt_float(rotate_val))
        parts.extend([
            "xzoom=" + wysiwyg_fmt_float(char.get("xzoom", 1.0)),
            "yzoom=" + wysiwyg_fmt_float(char.get("yzoom", 1.0)),
            "alpha=" + wysiwyg_fmt_float(char.get("alpha", 1.0)),
        ])
        blur_value = wysiwyg_float(char.get("filter_blur", 0.0), 0.0)
        if abs(blur_value) > 0.0001:
            parts.append("blur=" + wysiwyg_fmt_float(blur_value))
        matrix_expr = wysiwyg_color_matrix_expression_for_char(char)
        if matrix_expr:
            parts.append("matrixcolor=" + matrix_expr)
        at_parts = ["Transform(" + ", ".join(parts) + ")"]
        motion_expr = wysiwyg_motion_fx_at_expression_for_char(char)
        if motion_expr:
            at_parts.append(motion_expr)
        return at_parts

    def wysiwyg_position_line_for_char(char):
        at_parts = wysiwyg_at_parts_for_char(char)
        # `show expression`/`as alias` statements must round-trip: writing the
        # alias as if it were an image name produces a line that crashes the
        # game on its next execution ("Image '<alias>' not found").
        if char.get("expression"):
            target = "expression " + str(char.get("expression"))
        else:
            target = char.get("image", char.get("tag", ""))
        line = "show " + target + " at " + ", ".join(at_parts)
        if char.get("as_tag"):
            line += " as " + str(char.get("as_tag"))
        behind = char.get("behind") or []
        if behind:
            line += " behind " + ", ".join([str(i) for i in behind])
        if char.get("onlayer"):
            line += " onlayer " + str(char.get("onlayer"))
        zorder_val = char.get("zorder")
        if zorder_val is not None:
            line += " zorder " + str(int(zorder_val))
        elif char.get("zorder_raw"):
            line += " zorder " + str(char.get("zorder_raw"))
        if char.get("with_expr"):
            line += " with " + str(char.get("with_expr"))
        return line

    def wysiwyg_scene_line():
        if not store.wysiwyg_bg:
            return None
        return "scene " + store.wysiwyg_bg

    def wysiwyg_line_physical_span(entry):
        # True number of physical file lines a logical line occupies. When
        # the line carries a trailing comment, scriptedit tracks it only up
        # to the '#' (full_text has no final newline), so the last physical
        # line is not counted by the newlines alone.
        span = entry.full_text.count("\n")
        if not entry.full_text.endswith("\n"):
            span += 1
        return span or 1

    def wysiwyg_line_comment_text(entry):
        # The trailing "# comment" of a logical line, or "" when there is
        # none. Lines the engine loaded from disk track the comment via
        # full_text stopping at the '#'; lines the editor wrote itself
        # (insert_line_before builds synthetic entries whose full_text is
        # the whole line, newline included) carry the comment inside text,
        # so a second save of the same line would silently drop it without
        # the quote-aware scan below.
        if entry.full_text.endswith("\n"):
            idx = wysiwyg_comment_index(entry.text)
            if idx is None:
                return ""
            return entry.text[idx:].rstrip()
        try:
            with io.open(entry.filename, "r", encoding="utf-8") as handle:
                data = handle.read()
        except Exception:
            return ""
        start = entry.start + len(entry.full_text)
        end = data.find("\n", start)
        if end < 0:
            end = len(data)
        return "#" + data[start:end]

    WYSIWYG_PLACEMENT_TRANSFORMS = set([
        "left", "right", "center", "truecenter", "top", "topleft",
        "topright", "offscreenleft", "offscreenright", "default", "reset",
    ])

    # Position-type properties are safe even when the editor cannot parse
    # their values: the live render bounds capture the resulting position,
    # and the saved line pins it with explicit xpos/ypos.
    WYSIWYG_SAFE_POSITION_KWARGS = set([
        "xpos", "ypos", "xanchor", "yanchor", "xalign", "yalign",
        "xcenter", "ycenter", "xoffset", "yoffset",
        "pos", "anchor", "align", "offset",
    ])

    # Everything the editor actually round-trips: parsed back from the line
    # text on import AND written out on save. A Transform(...) using any
    # other keyword (zoom, xsize, crop, function, ...) would be silently
    # altered by a rewrite, so such statements are locked instead.
    WYSIWYG_ROUNDTRIP_KWARGS = WYSIWYG_SAFE_POSITION_KWARGS | set([
        "rotate", "xzoom", "yzoom", "alpha", "blur", "matrixcolor",
    ])

    WYSIWYG_NUM_LITERAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
    WYSIWYG_MATRIX_TERM = r"(?:(?:Identity|Sepia)Matrix\(\s*\)|(?:Brightness|Contrast|Saturation|Hue|Invert)Matrix\(\s*-?\d+(?:\.\d+)?\s*\))"
    WYSIWYG_MATRIX_EXPR_RE = re.compile(r"^" + WYSIWYG_MATRIX_TERM + r"(?:\s*\*\s*" + WYSIWYG_MATRIX_TERM + r")*$")

    def wysiwyg_transform_call_safe(text):
        # A textual Transform(...) is replace-safe only when every keyword is
        # one the editor round-trips AND its value is something the import
        # parser can read back. Checking names alone is not enough:
        # `alpha=.5` or `rotate=my_var` pass a name check but the import
        # regex only reads plain numeric literals, so a rewrite would
        # silently reset them.
        text = str(text).strip()
        m = re.match(r"^Transform\s*\(", text)
        if not m:
            return False
        inner = text[m.end():]
        depth = 1
        end = None
        for i, ch in enumerate(inner):
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None or inner[end + 1:].strip():
            return False
        inner = inner[:end]

        parts = []
        depth = 0
        current = ""
        for ch in inner:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current)
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current)

        for part in parts:
            part = part.strip()
            if not part:
                continue
            m2 = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", part, re.S)
            if not m2:
                # positional argument (a child displayable etc.)
                return False
            name = m2.group(1)
            value = m2.group(2).strip()
            if name not in WYSIWYG_ROUNDTRIP_KWARGS:
                return False
            if name == "matrixcolor":
                if not WYSIWYG_MATRIX_EXPR_RE.match(value):
                    return False
            elif name not in WYSIWYG_SAFE_POSITION_KWARGS:
                # rotate/xzoom/yzoom/alpha/blur must be literals the import
                # parser reads back; positions may be any static expression
                # because the live bounds re-capture them.
                if not WYSIWYG_NUM_LITERAL_RE.match(value):
                    return False
        return True

    def wysiwyg_at_expr_safe(text):
        # Can this at-list element be replaced by the editor's Transform
        # without changing behavior? Only static placement expressions
        # qualify: the engine placement transforms, the editor's own output
        # and its motion transforms. Anything else (custom/animated
        # transforms) makes the character read-only.
        text = str(text or "").strip()
        if text in WYSIWYG_PLACEMENT_TRANSFORMS:
            return True
        if re.match(r"^wysiwyg_(float|shake|bounce|sink|breathe|sway|blink)_motion\s*\(", text):
            return True
        if re.match(r"^Transform\s*\(", text):
            return wysiwyg_transform_call_safe(text)
        return False

    def wysiwyg_atl_line_is_static(text):
        # Validates a whole simple ATL line as `prop value [prop value ...]`
        # where every prop is a position-type property. A single unknown
        # token (zoom, a warper, repeat, on, ...) fails the line - matching
        # only the first word would let `xpos 500 zoom 0.9` slip through and
        # lose the zoom on rewrite.
        s = text.strip()
        while s:
            m = re.match(r"([A-Za-z_]\w*)\s+", s)
            if not m:
                return False
            if m.group(1) not in WYSIWYG_SAFE_POSITION_KWARGS:
                return False
            s = s[m.end():].lstrip()
            if s.startswith("("):
                depth = 0
                end = None
                for i, ch in enumerate(s):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end is None:
                    return False
                s = s[end + 1:].lstrip()
            else:
                m2 = re.match(r"\S+", s)
                if not m2:
                    return False
                s = s[m2.end():].lstrip()
        return True

    def wysiwyg_collect_atl_block(filename, line):
        # The logical lines of the ATL block under the header at `line`,
        # including blank and comment-only lines inside the block (trailing
        # ones stay). Returns [] when the header opens no block.
        # Entries: (Line, physical_span).
        header = renpy.scriptedit.lines.get((filename, line))
        if header is None or not header.text.rstrip().endswith(":"):
            return []
        header_indent = len(header.text) - len(header.text.lstrip())
        entries = []
        pending = []
        scan = line + wysiwyg_line_physical_span(header)
        while True:
            entry = renpy.scriptedit.lines.get((filename, scan))
            if entry is None:
                break
            span = wysiwyg_line_physical_span(entry)
            if not entry.text.strip():
                pending.append((entry, span))
                scan += span
                continue
            indent = len(entry.text) - len(entry.text.lstrip())
            if indent <= header_indent:
                break
            entries.extend(pending)
            pending = []
            entries.append((entry, span))
            scan += span
        # Trailing comment lines indented deeper than the header belong to
        # the block and are deleted with it; a blank line or a comment at
        # header level (or shallower) separates the NEXT statement and
        # everything from it on stays.
        for entry, span in pending:
            body = entry.full_text
            if body.endswith("\n"):
                break
            stripped = body.rstrip("#")
            indent = len(stripped) - len(stripped.lstrip())
            if indent <= header_indent:
                break
            entries.append((entry, span))
        return entries

    def wysiwyg_atl_block_is_static(filename, line):
        # True when every statement in the block is a position-type property
        # line - such a block can become Transform(...) without losing
        # behavior (the position is re-captured from the live render). Any
        # other property, warper, repeat, parallel or event line means the
        # statement must never be rewritten.
        for entry, _span in wysiwyg_collect_atl_block(filename, line):
            text = entry.text.strip()
            if not text:
                continue
            if not wysiwyg_atl_line_is_static(text):
                return False
        return True

    def wysiwyg_lock_reason(data):
        # Decides whether a character may be edited at all. A locked
        # character stays live on the master layer during editing and is
        # never hidden, previewed, modified or saved - so its animation or
        # custom transform cannot be damaged.
        filename = data.get("source_file")
        line = data.get("source_line")
        if not filename or not line:
            return "shown from code (no source line)"
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            return "source .rpy not on disk"
        elided = wysiwyg_norm_path(filename)
        if data.get("has_atl"):
            try:
                renpy.scriptedit.ensure_loaded(elided)
                if not wysiwyg_atl_block_is_static(elided, int(line)):
                    return "animated or unsupported ATL block"
            except Exception:
                return "unreadable ATL block"
        for expr in (data.get("at_list_exprs") or []):
            if not wysiwyg_at_expr_safe(expr):
                return "uses transform '" + str(expr).strip() + "'"
        return None

    def wysiwyg_runtime_transform_safe(t):
        # Judges a LIVE at-list entry (a transform object, not source text).
        # Needed for `show tag attribute` statements with no at-clause: with
        # config.keep_running_transform the tag inherits the transform of an
        # earlier show, so the statement text says nothing about it.
        try:
            atl = getattr(t, "atl", None)
            if atl is not None:
                # ATL-defined transform: safe only if it IS one of the
                # engine's static placement transforms.
                for name in WYSIWYG_PLACEMENT_TRANSFORMS:
                    ref = getattr(renpy.store, name, None)
                    if ref is not None and getattr(ref, "atl", None) is atl:
                        return True
                return False
            if isinstance(t, Transform):
                if getattr(t, "function", None) is not None:
                    return False
                # Inherited Transform copies lose their kwargs (and in .rpy
                # python `dict` is RevertableDict, so isinstance checks on
                # the engine's plain dict fail) - the live TransformState is
                # the reliable source. Safe when every non-position property
                # is still at its engine default; position itself is
                # re-measured from the render bounds.
                return wysiwyg_transform_state_safe(t)
            return False
        except Exception:
            return False

    def wysiwyg_transform_state_safe(t):
        # Structural check: every ATL property REGISTERED BY THE ENGINE
        # (renpy.display.transform.all_properties) must still equal its
        # class-level default on the live TransformState. Only position-type
        # properties (re-derived from the render bounds) and pure render
        # hints may differ. Tracking the engine registry instead of a
        # hand-maintained list means new/exotic properties (blend, 3D
        # rotations, uniforms registered as properties, ...) are
        # unsafe-by-default rather than silently droppable.
        state = getattr(t, "state", None)
        if state is None:
            return False
        try:
            transform_module = renpy.display.transform
            properties = getattr(transform_module, "all_properties", None)
            if not properties:
                return False
            ignore = WYSIWYG_SAFE_POSITION_KWARGS | set(["subpixel", "nearest"])
            sentinel = object()
            for name in properties:
                if name in ignore:
                    continue
                live = getattr(state, name, sentinel)
                default = getattr(transform_module.TransformState, name, sentinel)
                if live is sentinel or default is sentinel:
                    continue
                if live is default:
                    continue
                try:
                    if live == default:
                        continue
                except Exception:
                    return False
                return False
            return True
        except Exception:
            return False

    def wysiwyg_tag_runtime_at_safe(tag):
        try:
            sls = renpy.game.context().scene_lists
        except Exception:
            return True
        try:
            entries = list(sls.at_list.get("master", {}).get(tag) or [])
        except Exception:
            entries = []
        for entry in entries:
            if not wysiwyg_runtime_transform_safe(entry):
                return False
        # With keep_running_transform the inherited transform often lives
        # only in the displayable itself: `show tag attr` leaves the at-list
        # empty and swaps the child of the OLD transform instance
        # (_change_transform_child). Walk the wrapper chain on the live
        # scene-list entry as well.
        try:
            for sle in sls.layers.get("master", []):
                if getattr(sle, "tag", None) != tag:
                    continue
                d = getattr(sle, "displayable", None)
                depth = 0
                while isinstance(d, Transform) and depth < 10:
                    if not wysiwyg_runtime_transform_safe(d):
                        return False
                    d = getattr(d, "child", None)
                    depth += 1
        except Exception:
            return False
        return True

    def wysiwyg_begin_ast_surgery():
        # Shared setup for AST-mutating operations (replace and insert).
        # replace_node is patched for the duration: a context's return stack
        # can hold names no longer in the namemap, and the stock lookup
        # raises instead of skipping them. Autoreload is paused so our own
        # writes don't trigger a reload mid-surgery.
        try:
            prev_autoreload = renpy.get_autoreload()
        except Exception:
            prev_autoreload = False
        try:
            renpy.set_autoreload(False)
        except Exception:
            pass

        orig_replace_node = renpy.execution.Context.replace_node

        def patched_replace_node(self, old, new):
            def replace_one(name):
                try:
                    if renpy.game.script.lookup(name) is old:
                        return new.name
                except Exception:
                    pass
                return name
            self.current = replace_one(self.current)
            self.return_stack = [replace_one(i) for i in self.return_stack]

        renpy.execution.Context.replace_node = patched_replace_node
        return orig_replace_node, prev_autoreload

    def wysiwyg_end_ast_surgery(state):
        orig_replace_node, prev_autoreload = state
        renpy.execution.Context.replace_node = orig_replace_node
        if prev_autoreload:
            try:
                renpy.set_autoreload(prev_autoreload)
            except Exception:
                pass

    def wysiwyg_remove_logical_line(filename, line):
        # renpy.scriptedit.remove_line tracks a commented line only up to the
        # '#': it removes the code plus the '#' itself and leaves the comment
        # body behind as a bare line of text ("Indentation mismatch" on the
        # next launch). Remove the logical line completely, comment included,
        # and keep scriptedit's offsets consistent.
        entry = renpy.scriptedit.lines.get((filename, line))
        if entry is None:
            raise Exception("source line " + filename + ":" + str(line) + " is not tracked")
        has_comment = not entry.full_text.endswith("\n")
        start = entry.start
        real_path = entry.filename
        renpy.scriptedit.remove_line(filename, line)
        if not has_comment:
            return
        with io.open(real_path, "r", encoding="utf-8") as handle:
            data = handle.read()
        end = data.find("\n", start)
        end = (end + 1) if end >= 0 else len(data)
        leftover = data[start:end]
        data = data[:start] + data[end:]
        renpy.scriptedit.adjust_line_locations(filename, line, -len(leftover), -leftover.count("\n"))
        with renpy.loader.auto_lock:
            with io.open(real_path, "w", encoding="utf-8") as handle:
                handle.write(data)
            renpy.loader.add_auto(real_path, force=True)

    def wysiwyg_scriptedit_replace(filename, line, code):
        # Replaces one logical source line (plus its ATL block, if any) with
        # `code`, keeping AST line numbers in sync with the file. Returns the
        # net change in physical line count so the caller can shift the
        # remembered source_line of every statement below the edit.
        #
        # The renpy.scriptedit primitives are asymmetric: remove_line deletes
        # the whole logical line (N physical lines when the statement wraps
        # inside parentheses), while remove_from_ast always shifts AST line
        # numbers by exactly -1. Without the compensation below, one save of
        # a wrapped statement desyncs every later edit in the same file and
        # deletes innocent lines.
        filename = wysiwyg_norm_path(filename)
        line = int(line)

        renpy.scriptedit.ensure_loaded(filename)
        header = renpy.scriptedit.lines.get((filename, line))
        if header is None:
            raise Exception("source line " + filename + ":" + str(line) + " is not editable")

        physical = wysiwyg_line_physical_span(header)

        # A trailing comment on the statement is the user's - carry it over
        # onto the rewritten line.
        header_comment = wysiwyg_line_comment_text(header)
        if header_comment:
            code = code + "  " + header_comment

        # An ATL block under `show x:` consists of separate logical lines
        # that would be orphaned by replacing the header alone - a parse
        # error on the next launch. Collect the block (including blank and
        # comment-only lines inside it) for deletion. The caller (save loop)
        # is responsible for the pre-save backup.
        block_lines = [span for _entry, span in wysiwyg_collect_atl_block(filename, line)]

        surgery = wysiwyg_begin_ast_surgery()
        block_physical = 0
        try:
            renpy.scriptedit.add_to_ast_before(code, filename, line)
            renpy.scriptedit.insert_line_before(code, filename, line)
            renpy.scriptedit.remove_from_ast(filename, line + 1)
            wysiwyg_remove_logical_line(filename, line + 1)
            if physical > 1:
                # The removal dropped `physical` file lines but remove_from_ast
                # only shifted the AST by -1; re-align the AST with the file.
                renpy.scriptedit.adjust_ast_linenumbers(filename, line + 1, -(physical - 1))

            for entry_physical in block_lines:
                wysiwyg_remove_logical_line(filename, line + 1)
                block_physical += entry_physical
            if block_physical:
                renpy.scriptedit.adjust_ast_linenumbers(filename, line + 1, -block_physical)

            # Echo check: the tracked line must now be exactly what was
            # written. A mismatch means the surgery landed in the wrong
            # place; failing loudly here makes the save loop restore the
            # pre-save backup instead of leaving silent damage.
            written_entry = renpy.scriptedit.lines.get((filename, line))
            if written_entry is None or written_entry.text.strip() != code.strip():
                raise Exception("post-write line check failed at " + filename + ":" + str(line))
        finally:
            wysiwyg_end_ast_surgery(surgery)

        return 1 - physical - block_physical

    def wysiwyg_capture_master_snapshot():
        # Captures the master layer exactly as the game built it: scene-list
        # entries (with their displayables and running transforms), per-tag
        # at-lists, shown-attribute state and sticky tags.
        try:
            sls = renpy.game.context().scene_lists
            entries = [entry.copy() for entry in sls.layers.get("master", [])]
            at_list = dict(sls.at_list.get("master", {}))
            try:
                showing = set(renpy.get_showing_tags("master"))
            except Exception:
                showing = set()
            shown = []
            for entry in entries:
                if not entry.tag:
                    continue
                try:
                    attrs = tuple(sls.shown.get_attributes("master", entry.tag))
                except Exception:
                    attrs = ()
                shown.append(((entry.tag,) + attrs, entry.tag in showing))
            sticky = {}
            for tag, layer in getattr(sls, "sticky_tags", {}).items():
                if layer == "master":
                    sticky[tag] = layer
            return {"entries": entries, "at_list": at_list, "shown": shown, "sticky": sticky}
        except Exception:
            return None

    def wysiwyg_restore_master_snapshot():
        snapshot = WYSIWYG_RUNTIME.master_snapshot
        if not snapshot:
            return False
        try:
            sls = renpy.game.context().scene_lists
            sls.layers["master"][:] = [entry.copy() for entry in snapshot["entries"]]
            sls.at_list["master"].clear()
            sls.at_list["master"].update(snapshot["at_list"])
            for name, was_showing in snapshot["shown"]:
                try:
                    sls.shown.predict_show("master", name, show=was_showing)
                except Exception:
                    pass
            for tag, layer in snapshot["sticky"].items():
                sls.sticky_tags[tag] = layer
            return True
        except Exception:
            return False

    def wysiwyg_restore_imported_preview():
        # Closing without saving: put back the exact scene-list entries that
        # were captured at import, so the game's own transforms and running
        # ATL animations are untouched and nothing from the preview leaks
        # into the running game. Tags the editor never imported (incl. the
        # background and blacklisted overlays) are never touched.
        if not store.wysiwyg_saved_runtime:
            if wysiwyg_restore_master_snapshot():
                wysiwyg_log_debug("[RESTORE] master snapshot restored")
                return

        # After a save the source file is the new truth: re-show each edited
        # tag with its saved values. This is an approximation for tags whose
        # original statement had game-side at-transforms. Locked characters
        # were never hidden, so they are never re-shown either; pending
        # (added but not yet saved) characters must not leak onto the scene.
        for char in store.wysiwyg_chars:
            if char.get("locked") or char.get("pending_insert"):
                continue
            tag = char.get("tag")
            image_name = char.get("runtime_image") or char.get("image", tag)
            try:
                renpy.hide(tag, layer="master")
            except Exception:
                pass
            try:
                restore_char = dict(char)
                restore_char["rotate"] = wysiwyg_float(char.get("original_rotate", 0.0), 0.0)
                restore_char["xzoom"] = wysiwyg_float(char.get("original_xzoom", 1.0), 1.0)
                restore_char["yzoom"] = wysiwyg_float(char.get("original_yzoom", 1.0), 1.0)
                restore_char["alpha"] = wysiwyg_float(char.get("original_alpha", 1.0), 1.0)
                at_list = [wysiwyg_transform_for_char(restore_char, use_original=True)]
                motion_transform = wysiwyg_motion_fx_transform_for_char(restore_char, use_original=True)
                if motion_transform:
                    at_list.append(motion_transform)
                wysiwyg_log_debug("[RESTORE] tag={0} image={1} orig_x={2} orig_y={3} rotate={4} xzoom={5} yzoom={6}".format(
                    tag, image_name, restore_char.get("original_x"), restore_char.get("original_y"), restore_char.get("rotate"), restore_char.get("xzoom"), restore_char.get("yzoom")
                ))
                # Explicit tag: an `as`-aliased sprite (image "hero sad",
                # tag "h2") must come back under its alias, not replace the
                # base "hero" instance.
                renpy.show(image_name, at_list=at_list, layer="master", zorder=int(char.get("zorder") or 0), tag=tag)
            except Exception:
                try:
                    renpy.show(image_name, layer="master", tag=tag)
                except Exception:
                    pass

    def wysiwyg_refresh_char_bounds(chars, write_original=False):
        for char in chars:
            tag = char.get("tag")
            if not tag:
                continue
            try:
                bounds = renpy.get_image_bounds(tag, width=wysiwyg_screen_w(), height=wysiwyg_screen_h(), layer="master")
            except Exception:
                bounds = None
            if not bounds:
                continue
            xzoom_val = abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0))
            yzoom_val = abs(wysiwyg_float(char.get("yzoom", 1.0), 1.0))
            is_rotated = (abs(wysiwyg_float(char.get("rotate", 0.0), 0.0)) > 0.01)

            img_w = wysiwyg_float(char.get("img_w", 0.0), 0.0)
            img_h = wysiwyg_float(char.get("img_h", 0.0), 0.0)
            if img_w <= 0.01 or img_h <= 0.01:
                img_w, img_h = wysiwyg_get_image_size(char.get("image", tag), tag)

            if img_w <= 0.01 or img_h <= 0.01:
                if is_rotated:
                    img_w = 400.0
                    img_h = 800.0
                else:
                    img_w = float(bounds[2]) / (xzoom_val if xzoom_val > 0.001 else 1.0)
                    img_h = float(bounds[3]) / (yzoom_val if yzoom_val > 0.001 else 1.0)
                    if img_w <= 0.01:
                        img_w = 400.0
                    if img_h <= 0.01:
                        img_h = 800.0

            if char.get("parsed_x") and "parsed_center_x" in char:
                center_x = round(char["parsed_center_x"])
            else:
                center_x = round(float(bounds[0]) + float(bounds[2]) / 2.0)

            if char.get("parsed_y") and "parsed_center_y" in char:
                center_y = round(char["parsed_center_y"])
            else:
                center_y = round(float(bounds[1]) + float(bounds[3]) / 2.0)
            w_unrotated = img_w * xzoom_val
            h_unrotated = img_h * yzoom_val

            char["x"] = float(center_x - w_unrotated / 2.0)
            char["y"] = float(center_y - h_unrotated / 2.0)
            char["w"] = w_unrotated
            char["h"] = h_unrotated
            char["img_w"] = img_w
            char["img_h"] = img_h
            char["parsed_center_x"] = center_x
            char["parsed_center_y"] = center_y

            wysiwyg_log_debug("[REFRESH] tag={0} bounds={1} img_w={2} img_h={3} x={4} y={5} parsed_cx={6}".format(
                tag, bounds, img_w, img_h, char["x"], char["y"], char.get("parsed_center_x")
            ))

            if write_original:
                char["original_x"] = char["x"]
                char["original_y"] = char["y"]
                char["original_parsed_center_x"] = center_x
                char["original_parsed_center_y"] = center_y
                if "original_anchor_x" not in char or "original_anchor_y" not in char:
                    char["original_anchor_x"] = center_x
                    char["original_anchor_y"] = center_y
                char["original_w"] = img_w
                char["original_h"] = img_h


    # --- Saving --------------------------------------------------------------
    # Rounds the edited values, rewrites each character's source line via
    # renpy.scriptedit (AST + file in one step), then refreshes original_*
    # so closing the editor keeps the just-saved state on screen.
    # Every numeric property the editor edits and saves, with its default
    # and the dirty-check tolerance. wysiwyg_char_dirty and
    # wysiwyg_write_originals both iterate THIS table, so an editable
    # property is added in one place. Keeping four hand-written lists in
    # sync already failed once: original_zorder was missing from the
    # post-save refresh, which left zorder edits permanently dirty.
    WYSIWYG_NUMERIC_PROPS = (
        ("rotate", 0.0, 0.05),
        ("xzoom", 1.0, 0.0005),
        ("yzoom", 1.0, 0.0005),
        ("alpha", 1.0, 0.0005),
        ("filter_blur", 0.0, 0.0005),
        ("filter_brightness", 0.0, 0.0005),
        ("filter_contrast", 1.0, 0.0005),
        ("filter_saturation", 1.0, 0.0005),
        ("filter_hue", 0.0, 0.0005),
        ("filter_invert", 0.0, 0.0005),
        ("motion_fx_strength", 1.0, 0.0005),
    )

    def wysiwyg_write_originals(char):
        # Snapshots the current state as the new baseline (after a save, or
        # when creating a character), so the dirty check compares against it.
        char["original_x"] = wysiwyg_float(char.get("x", 0.0), 0.0)
        char["original_y"] = wysiwyg_float(char.get("y", 0.0), 0.0)
        char["original_anchor_x"] = wysiwyg_float(char.get("anchor_x", char.get("x", 0.0)), 0.0)
        char["original_anchor_y"] = wysiwyg_float(char.get("anchor_y", char.get("y", 0.0)), 0.0)
        for key, default, _tolerance in WYSIWYG_NUMERIC_PROPS:
            char["original_" + key] = wysiwyg_float(char.get(key, default), default)
        char["original_filter_sepia"] = bool(char.get("filter_sepia", False))
        char["original_motion_fx"] = str(char.get("motion_fx", "none") or "none").strip().lower()
        char["original_zorder"] = char.get("zorder")
        char["original_with_expr"] = char.get("with_expr")
        char["original_parsed_center_x"] = char.get("parsed_center_x", char.get("x", 0.0) + wysiwyg_float(char.get("w", 0.0), 0.0) / 2.0)
        char["original_parsed_center_y"] = char.get("parsed_center_y", char.get("y", 0.0) + wysiwyg_float(char.get("h", 0.0), 0.0) / 2.0)

    def wysiwyg_shift_source_lines(edited_file, from_line, delta, inclusive=False, skip_char=None, journal=None):
        # An edit changed the physical line count of `edited_file`: shift
        # every remembered location at/below it - character records, the
        # background pointer, and the session execution log (a stale log
        # line would otherwise match the wrong statement with full
        # confidence after a multi-line edit). When `journal` is given,
        # every mutation is recorded so wysiwyg_unshift_source_lines can
        # undo it exactly if the save later fails verification.
        def hit(line):
            line = int(line or 0)
            return line >= from_line if inclusive else line > from_line

        for other in store.wysiwyg_chars:
            if other is skip_char:
                continue
            if wysiwyg_norm_path(other.get("source_file", "")) != edited_file:
                continue
            if hit(other.get("source_line", 0)):
                old = int(other["source_line"])
                if journal is not None:
                    journal.append(("char", other, old))
                other["source_line"] = old + delta

        for ref_kind, ref in (("bg", store.wysiwyg_bg_source), ("scenewith", store.wysiwyg_scene_with)):
            if ref and wysiwyg_norm_path(ref.get("file", "")) == edited_file:
                if hit(ref.get("line", 0)):
                    old = int(ref["line"])
                    if journal is not None:
                        journal.append((ref_kind, ref, old))
                    ref["line"] = old + delta

        log = WYSIWYG_RUNTIME.exec_log
        for index, entry in enumerate(log):
            filename, line = entry
            if filename == edited_file and hit(line):
                if journal is not None:
                    journal.append(("log", index, entry))
                log[index] = (filename, int(line) + delta)

    def wysiwyg_unshift_source_lines(journal):
        for kind, ref, old in reversed(journal):
            try:
                if kind == "char":
                    ref["source_line"] = old
                elif kind in ("bg", "scenewith"):
                    ref["line"] = old
                elif kind == "log":
                    WYSIWYG_RUNTIME.exec_log[ref] = old
            except Exception:
                pass

    def wysiwyg_char_dirty(char):
        # True when the user actually changed something versus the imported
        # (or last saved) state. Clean statements are never rewritten: a
        # rewrite normalizes the line and would silently drop game-side
        # at-transforms even when the user touched nothing.
        w = wysiwyg_float(char.get("w", 0.0), 0.0)
        h = wysiwyg_float(char.get("h", 0.0), 0.0)
        cx = int(round(wysiwyg_float(char.get("x", 0.0), 0.0) + w / 2.0))
        cy = int(round(wysiwyg_float(char.get("y", 0.0), 0.0) + h / 2.0))
        ocx = char.get("original_parsed_center_x")
        ocy = char.get("original_parsed_center_y")
        if ocx is None or int(round(wysiwyg_float(ocx, cx))) != cx:
            return True
        if ocy is None or int(round(wysiwyg_float(ocy, cy))) != cy:
            return True

        for key, default, tolerance in WYSIWYG_NUMERIC_PROPS:
            current = wysiwyg_float(char.get(key, default), default)
            original = wysiwyg_float(char.get("original_" + key, default), default)
            if abs(current - original) > tolerance:
                return True

        if bool(char.get("filter_sepia", False)) != bool(char.get("original_filter_sepia", False)):
            return True
        current_fx = str(char.get("motion_fx", "none") or "none").strip().lower()
        original_fx = str(char.get("original_motion_fx", "none") or "none").strip().lower()
        if current_fx != original_fx:
            return True
        if char.get("zorder") != char.get("original_zorder"):
            return True
        if wysiwyg_char_with_dirty(char):
            return True
        return False

    def wysiwyg_char_with_dirty(char):
        # Shared by wysiwyg_char_dirty and the "Restore original" gate in
        # the panel, so the button can never disagree with what a save
        # counts as changed.
        return str(char.get("with_expr") or "") != str(char.get("original_with_expr") or "")

    def wysiwyg_char_save_kind(char):
        # THE definition of "this character needs saving": None (nothing
        # to write), "added", "removed" or "edited". The save loop, the
        # uncertain-save gate and the close gate all branch on this one
        # function, so they cannot drift apart when a new savable state
        # is introduced.
        if char.get("locked"):
            return None
        if char.get("pending_insert"):
            return "added"
        if char.get("pending_hide"):
            return "removed"
        if wysiwyg_char_dirty(char):
            return "edited"
        return None

    def wysiwyg_scene_with_dirty(scene_with=None):
        # THE definition of "the scene reveal needs saving" - shared by
        # the save path, the gates and the code panel.
        sw = store.wysiwyg_scene_with if scene_with is None else scene_with
        return bool(sw) and str(sw.get("expr") or "") != str(sw.get("original") or "")

    def wysiwyg_validate_save_target(char):
        # Re-checks, immediately before writing, that the remembered source
        # location still holds the statement we imported. This is the last
        # line of defense when the line-log/heuristic match was wrong or the
        # file changed since import.
        filename = char.get("source_file")
        line = char.get("source_line")
        if not filename or not line:
            return "no source line"
        if wysiwyg_norm_path(filename) in WYSIWYG_RUNTIME.failed_files:
            return "saving to this file is disabled after a failed write - restart the game"
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            return "source .rpy not on disk (archived or .rpyc-only build?)"
        text = wysiwyg_source_line_text(filename, line)
        if not re.match(r"show(\s|:|$)", text.strip()):
            return "line " + str(line) + " is no longer a show statement - re-import"
        try:
            nodes = renpy.scriptedit.nodes_on_line(wysiwyg_norm_path(filename), int(line))
        except Exception:
            nodes = []
        for node in nodes:
            if not isinstance(node, renpy.ast.Show):
                continue
            _, _, node_tag, _, _ = wysiwyg_imspec_parts(getattr(node, "imspec", None))
            if node_tag == char.get("tag"):
                return None
        return "statement at line " + str(line) + " no longer matches this tag - re-import"

    WYSIWYG_MOTION_FX_FILE = "wysiwyg_motion_fx.rpy"
    WYSIWYG_MOTION_FX_HEADER = (
        "# Generated by WYSIWYG Scene Editor " + WYSIWYG_VERSION + ".\n"
        "# Standalone definitions of the wysiwyg_*_motion transforms used by\n"
        "# saved show statements. Ship this file with the game (or keep the\n"
        "# editor installed) so those statements keep working.\n"
        "#\n"
        "# This generated file may be used, modified and distributed without\n"
        "# restriction and without attribution, in any project, commercial or\n"
        "# not. (The editor itself is MIT-licensed; this grant is broader.)\n"
    )

    # Keep in sync with the `transform wysiwyg_*_motion` section further
    # down. Embedded here (instead of regex-extracted from the editor's own
    # source at save time) so the companion file can be written even when
    # the editor runs from a .rpyc or the file was renamed.
    WYSIWYG_MOTION_FX_SOURCE = """\
transform wysiwyg_float_motion(strength=1.0):
    yoffset 0
    ease 0.75 yoffset int(round(-18 * strength))
    ease 0.75 yoffset 0
    repeat

transform wysiwyg_shake_motion(strength=1.0):
    xoffset 0
    yoffset 0
    pause 0.04
    xoffset int(round(-8 * strength))
    yoffset int(round(4 * strength))
    pause 0.04
    xoffset int(round(7 * strength))
    yoffset int(round(-5 * strength))
    pause 0.04
    xoffset int(round(-6 * strength))
    yoffset int(round(3 * strength))
    pause 0.04
    xoffset int(round(5 * strength))
    yoffset int(round(-4 * strength))
    pause 0.04
    repeat

transform wysiwyg_bounce_motion(strength=1.0):
    yoffset 0
    ease 0.18 yoffset int(round(-24 * strength))
    ease 0.22 yoffset 0
    pause 0.08
    repeat

transform wysiwyg_sink_motion(strength=1.0):
    yoffset 0
    ease 0.75 yoffset int(round(18 * strength))
    ease 0.75 yoffset 0
    repeat

transform wysiwyg_breathe_motion(strength=1.0):
    yoffset 0
    zoom 1.0
    ease 0.75 yoffset int(round(-5 * strength)) zoom (1.0 + (0.025 * strength))
    ease 0.75 yoffset 0 zoom 1.0
    repeat

transform wysiwyg_sway_motion(strength=1.0):
    xoffset 0
    rotate 0
    ease 0.75 xoffset int(round(6 * strength)) rotate (3 * strength)
    ease 0.75 xoffset int(round(-6 * strength)) rotate (-3 * strength)
    ease 0.75 xoffset 0 rotate 0
    repeat

transform wysiwyg_blink_motion(strength=1.0):
    alpha 1.0
    pause 1.0
    alpha 0.0
    pause 0.12
    alpha 1.0
    pause 0.2
    repeat
"""

    def wysiwyg_ensure_motion_fx_file():
        # Saved lines can reference wysiwyg_*_motion transforms. Those must
        # not depend on the editor file staying installed, so the transforms
        # are materialized once into a small standalone .rpy. Raises on
        # failure - the save loop reports it instead of letting a "verified"
        # save quietly depend on the editor staying installed.
        path = os.path.join(wysiwyg_game_dir(), WYSIWYG_MOTION_FX_FILE)
        if os.path.exists(path):
            return
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(WYSIWYG_MOTION_FX_HEADER + "\n" + WYSIWYG_MOTION_FX_SOURCE)

    def wysiwyg_save_changes():
        changed = 0
        skipped_clean = 0
        errors = []
        written = []

        for char in store.wysiwyg_chars:
            wysiwyg_snap_char_transform(char)

        # The background is never editable in the editor, so its scene line
        # is never rewritten (rewriting it would strip the game's at-list).

        needs_motion_file = False
        batch_backups = {}

        def _backup_once(filename):
            if filename not in batch_backups:
                batch_backups[filename] = wysiwyg_backup_source(filename)
            return filename

        shift_journals = {}
        pending_written = set()
        hidden_written = []
        failed_now = set()
        # Insert targets, fetched ONCE per save: add_to_ast_before repoints
        # ctx.current at the inserted node, so asking for the current
        # position again after the first insert would reverse the order of
        # subsequently inserted lines. Two independent targets:
        #   "add"  - new show lines; preferably right above the earliest
        #            tracked show in the paused file, so the added sprite
        #            joins the same `with` transition that reveals the rest
        #            of the scene (fallback: the paused statement itself),
        #   "hide" - hide lines; always the paused statement (a hide above
        #            the character's own show would be a no-op).
        # Each insert bumps BOTH targets when it lands at or above them, and
        # every line-count-changing edit above them bumps them by its delta,
        # so they stay valid as the file grows during the batch. Fetched
        # lazily (not before the loop): an edit with delta != 0 processed
        # before the first insert would leave eagerly-fetched targets stale,
        # while ctx.current tracks such shifts on its own.
        insert_targets = {"add": None, "hide": None, "base": None}

        def _ensure_insert_targets():
            if insert_targets["hide"] is not None:
                return True
            target_file, target_line = wysiwyg_get_current_position()
            if not target_file or not target_line:
                return False
            target_file = wysiwyg_norm_path(target_file)
            target_line = int(target_line)
            # Within a session the paused node keeps its pre-insert line
            # number, so after an earlier save inserted lines here the
            # fetched position points at OUR first inserted line (e.g. a
            # freshly written `hide`) instead of the pause. The recorded
            # save position knows every alias of this statement - the
            # highest one is its real physical line.
            saved_pos = store.wysiwyg_saved_position
            if saved_pos and str(saved_pos[0]) == target_file:
                try:
                    known = saved_pos[1] if isinstance(saved_pos[1], (list, tuple, set)) else [saved_pos[1]]
                    known = [int(l) for l in known]
                    if target_line in known:
                        target_line = max(known)
                except Exception:
                    pass
            insert_targets["hide"] = (target_file, target_line)
            insert_targets["base"] = (target_file, target_line)
            insert_targets["add"] = wysiwyg_added_sprite_target(target_file, target_line)
            return True

        def _after_insert(kind, target_file, target_line):
            insert_targets[kind] = (target_file, target_line + 1)
            other = "hide" if kind == "add" else "add"
            other_target = insert_targets[other]
            if other_target and other_target[0] == target_file and other_target[1] >= target_line:
                insert_targets[other] = (target_file, other_target[1] + 1)

        def _insert_target_problem(target_file, tag):
            if target_file.startswith("game/tl/"):
                return tag + ": cannot insert while playing a translation - switch to the base language"
            if target_file in WYSIWYG_RUNTIME.failed_files or target_file in failed_now:
                return tag + ": saving to this file is disabled after a failed write - restart the game"
            target_path = wysiwyg_source_path(target_file)
            if not target_path or not os.path.exists(target_path):
                return tag + ": current statement is not in an editable .rpy file"
            return None

        for char in store.wysiwyg_chars:
            touched_file = None
            try:
                if char.get("locked"):
                    continue
                save_kind = wysiwyg_char_save_kind(char)
                if save_kind is None:
                    # A clean tracked character: counted for the status line.
                    skipped_clean += 1
                    continue
                if save_kind == "added":
                    # A newly added sprite: INSERT a fresh show line instead
                    # of replacing an existing one.
                    if not _ensure_insert_targets():
                        errors.append(char.get("tag", "?") + ": current script position unknown")
                        continue
                    # A sprite with its own `with` transition is meant to
                    # appear at THIS point of the script - splicing it above
                    # the scene's first show would fire the transition in
                    # the middle of the scene build-up instead.
                    target_kind = "hide" if char.get("with_expr") else "add"
                    if target_kind == "add" and wysiwyg_hide_between(
                            insert_targets["add"][0], insert_targets["add"][1],
                            insert_targets["hide"][1], char.get("tag")):
                        # A `hide` of this very tag sits below the anchor
                        # (the character was removed here earlier): the new
                        # show must land after it, at the paused statement.
                        target_kind = "hide"
                    target_file, target_line = insert_targets[target_kind]
                    problem = _insert_target_problem(target_file, char.get("tag", "?"))
                    if problem:
                        errors.append(problem)
                        continue
                    line_to_write = wysiwyg_position_line_for_char(char)
                    touched_file = _backup_once(target_file)
                    wysiwyg_log_debug("[INSERT] tag={0} target={1}:{2} mode={3} code={4}".format(
                        char.get("tag"), target_file, target_line,
                        "at-pause" if target_kind == "hide" else "with-scene",
                        line_to_write
                    ))
                    delta = wysiwyg_scriptedit_insert(target_file, target_line, line_to_write)
                    char["source_file"] = target_file
                    char["source_line"] = target_line
                    char["pending_insert"] = False
                    char["source_confidence"] = "linelog"
                    # The dict must describe the line that now exists on
                    # disk, or the next import will mis-classify it (an
                    # empty at_list_exprs reads as "inherits a transform").
                    char["at_list_exprs"] = wysiwyg_at_parts_for_char(char)
                    char["has_atl"] = False
                    pending_written.add(id(char))
                    # The next added sprite goes right below this one.
                    _after_insert(target_kind, target_file, target_line)
                    wysiwyg_shift_source_lines(target_file, target_line, delta, inclusive=True,
                                               skip_char=char,
                                               journal=shift_journals.setdefault(target_file, []))
                    if "wysiwyg_" in line_to_write and "_motion(" in line_to_write:
                        needs_motion_file = True
                    changed += 1
                    written.append(char)
                    continue
                if save_kind == "removed":
                    # Removal of a tracked character: INSERT a hide line
                    # before the paused statement. The original show line
                    # stays untouched, so earlier parts of the scene play
                    # exactly as before.
                    if not _ensure_insert_targets():
                        errors.append(char.get("tag", "?") + ": current script position unknown")
                        continue
                    target_file, target_line = insert_targets["hide"]
                    problem = _insert_target_problem(target_file, char.get("tag", "?"))
                    if problem:
                        errors.append(problem)
                        continue
                    line_to_write = "hide " + str(char.get("tag"))
                    touched_file = _backup_once(target_file)
                    wysiwyg_log_debug("[HIDE] tag={0} target={1}:{2} code={3}".format(
                        char.get("tag"), target_file, target_line, line_to_write
                    ))
                    delta = wysiwyg_scriptedit_insert(target_file, target_line, line_to_write)
                    char["pending_hide"] = False
                    # Resolved after batch verification: the char leaves the
                    # editor only when the write survives the reparse check.
                    hidden_written.append((char, target_file))
                    _after_insert("hide", target_file, target_line)
                    wysiwyg_shift_source_lines(target_file, target_line, delta, inclusive=True,
                                               skip_char=char,
                                               journal=shift_journals.setdefault(target_file, []))
                    changed += 1
                    continue
                # save_kind == "edited": rewrite the existing show line.
                edited_file = wysiwyg_norm_path(char["source_file"])
                if edited_file in failed_now:
                    errors.append(char.get("tag", "?") + ": saving to this file is disabled after a failed write - restart the game")
                    continue
                problem = wysiwyg_validate_save_target(char)
                if problem:
                    errors.append(char.get("tag", "?") + ": " + problem)
                    continue
                line_to_write = wysiwyg_position_line_for_char(char)
                edited_line = int(char["source_line"])
                touched_file = _backup_once(edited_file)
                wysiwyg_log_debug("[SAVE] tag={0} source={1}:{2} code={3}".format(
                    char.get("tag"), edited_file, edited_line, line_to_write
                ))
                delta = wysiwyg_scriptedit_replace(edited_file, edited_line, line_to_write)
                char["has_atl"] = False
                char["at_list_exprs"] = wysiwyg_at_parts_for_char(char)
                if "wysiwyg_" in line_to_write and "_motion(" in line_to_write:
                    needs_motion_file = True
                if delta:
                    # The edit changed the physical line count: shift every
                    # remembered location below it in the same file,
                    # including the already-captured insert targets.
                    wysiwyg_shift_source_lines(edited_file, edited_line, delta, inclusive=False,
                                               skip_char=char,
                                               journal=shift_journals.setdefault(edited_file, []))
                    for _kind in ("add", "hide", "base"):
                        _target = insert_targets[_kind]
                        if _target and _target[0] == edited_file and _target[1] > edited_line:
                            insert_targets[_kind] = (edited_file, _target[1] + delta)
                changed += 1
                written.append(char)
            except Exception as exc:
                errors.append(char.get("tag", "?") + ": " + str(exc))
                if touched_file:
                    # The surgery may have modified the file before failing
                    # (an echo-check raise means it definitely did). Treat it
                    # like a verification failure so the restore path below
                    # puts the backup back instead of leaving the damage.
                    failed_now.add(touched_file)

        # The scene-level `with` statement, edited as one shared value.
        scene_with = store.wysiwyg_scene_with
        if wysiwyg_scene_with_dirty(scene_with):
            sw_file = wysiwyg_norm_path(scene_with.get("file", ""))
            sw_line = int(scene_with.get("line") or 0)
            try:
                if sw_file in WYSIWYG_RUNTIME.failed_files or sw_file in failed_now:
                    errors.append("scene with: saving to this file is disabled after a failed write - restart the game")
                elif sw_file.startswith("game/tl/"):
                    errors.append("scene with: cannot edit while playing a translation")
                else:
                    # Strip the trailing comment the same way import did, or
                    # a commented line would always read as "changed".
                    current_text = wysiwyg_strip_line_comment(str(wysiwyg_source_line_text(sw_file, sw_line) or "")).strip()
                    match = re.match(r"^with\s+(.+?)\s*$", current_text)
                    if not match or match.group(1) != str(scene_with.get("original")):
                        errors.append("scene with: the line changed since import - press Import Scene again")
                    else:
                        line_to_write = "with " + str(scene_with.get("expr"))
                        _backup_once(sw_file)
                        wysiwyg_log_debug("[SCENE-WITH] {0}:{1} code={2}".format(sw_file, sw_line, line_to_write))
                        delta = wysiwyg_scriptedit_replace(sw_file, sw_line, line_to_write)
                        if delta:
                            wysiwyg_shift_source_lines(sw_file, sw_line, delta, inclusive=False,
                                                       journal=shift_journals.setdefault(sw_file, []))
                        scene_with["written_original"] = scene_with.get("original")
                        scene_with["original"] = str(scene_with.get("expr"))
                        changed += 1
            except Exception as exc:
                errors.append("scene with: " + str(exc))
                failed_now.add(sw_file)

        if needs_motion_file:
            try:
                wysiwyg_ensure_motion_fx_file()
            except Exception as exc:
                errors.append("motion fx file: " + str(exc) + " - keep wysiwyg_editor.rpy installed or the saved Motion FX lines will not run without it")

        # Post-save verification: every touched file must still parse with the
        # engine parser. A failure restores the pre-save backup on the spot
        # and disables further saves to that file until the game restarts.
        for edited_file in batch_backups:
            if edited_file in failed_now:
                continue
            try:
                problem = wysiwyg_verify_file_parses(edited_file)
            except Exception as exc:
                problem = "verifier crashed: " + str(exc)
            if problem:
                failed_now.add(edited_file)
                errors.append(edited_file + ": save verification FAILED (" + problem + ")")

        for failed_file in failed_now:
            WYSIWYG_RUNTIME.failed_files.add(failed_file)
            backup = batch_backups.get(failed_file)
            if wysiwyg_file_matches_backup(failed_file, backup):
                errors.append(failed_file + ": nothing was written to this file; saving to it is disabled until the game restarts")
            elif wysiwyg_restore_backup(failed_file, backup):
                errors.append(failed_file + ": file restored from backup; restart the game before saving it again")
            else:
                errors.append(failed_file + ": AUTO-RESTORE FAILED, restore manually from " + str(backup))
            wysiwyg_log_debug("[VERIFY-FAIL] file={0} backup={1}".format(failed_file, backup))
            # Undo this file's bookkeeping: line shifts, and the "already
            # inserted" state of added sprites whose line just got reverted
            # (otherwise the close path would re-show a sprite whose show
            # line no longer exists on disk).
            wysiwyg_unshift_source_lines(shift_journals.pop(failed_file, []))
            for reverted_char in list(written):
                if wysiwyg_norm_path(reverted_char.get("source_file", "")) != failed_file:
                    continue
                written.remove(reverted_char)
                changed -= 1
                if id(reverted_char) in pending_written:
                    reverted_char["pending_insert"] = True
                    reverted_char["source_file"] = ""
                    reverted_char["source_line"] = 0
                    reverted_char["source_confidence"] = "new"
            scene_with = store.wysiwyg_scene_with
            if (scene_with and "written_original" in scene_with
                    and wysiwyg_norm_path(scene_with.get("file", "")) == failed_file):
                scene_with["original"] = scene_with.pop("written_original")
                changed -= 1
        if store.wysiwyg_scene_with:
            store.wysiwyg_scene_with.pop("written_original", None)

        # Resolve removals: a hide line that survived verification takes
        # its character off the editor's scene list (the tag was hidden
        # from the master layer at import, which now matches the script);
        # a reverted one puts the removal mark back for the next save.
        for hidden_char, hide_file in hidden_written:
            if hide_file in failed_now:
                hidden_char["pending_hide"] = True
                changed -= 1
            else:
                store.wysiwyg_chars = [c for c in store.wysiwyg_chars if c is not hidden_char]
                if store.wysiwyg_selected_tag == hidden_char.get("tag"):
                    store.wysiwyg_selected_tag = None

        if changed and not failed_now:
            # Remember WHERE this save happened. The autoreload that follows
            # wipes the engine line log; when the next import runs from this
            # exact statement, the current entries' sources carry over
            # instead of degrading to uncertain AST guesses. Two candidate
            # line numbers: within the session, the paused node keeps its
            # pre-insert linenumber (the engine never renumbers the node an
            # insert landed on), while after an autoreload the re-parsed
            # file reports the physically shifted one.
            pos_file = None
            candidates = []
            if insert_targets["hide"] is not None:
                pos_file = insert_targets["hide"][0]
                candidates.append(int(insert_targets["hide"][1]))
                if insert_targets["base"] is not None:
                    candidates.append(int(insert_targets["base"][1]))
            else:
                # No inserts happened, so ctx.current was never repointed
                # and can be read directly. Keep any previously recorded
                # aliases of this statement: the fetched number may be the
                # stale in-session one while the physical line (what an
                # autoreload will report) lives in the old candidate list.
                pos_f, pos_l = wysiwyg_get_current_position()
                if pos_f and pos_l:
                    pos_file = wysiwyg_norm_path(pos_f)
                    candidates.append(int(pos_l))
                    prev_sp = store.wysiwyg_saved_position
                    if prev_sp and str(prev_sp[0]) == pos_file:
                        try:
                            prev_lines = prev_sp[1] if isinstance(prev_sp[1], (list, tuple, set)) else [prev_sp[1]]
                            prev_lines = [int(l) for l in prev_lines]
                            if int(pos_l) in prev_lines:
                                candidates.extend(prev_lines)
                        except Exception:
                            pass
            if pos_file and candidates:
                store.wysiwyg_saved_position = (pos_file, sorted(set(candidates)))

        if changed:
            for char in written:
                wysiwyg_write_originals(char)
            store.wysiwyg_saved_runtime = True
            # The import-time snapshot now describes a PRE-save scene. If it
            # were kept, closing with any unsaved tweak after this save would
            # visually roll the scene back behind what the file says. From
            # now on closing reconstructs from the saved values instead.
            WYSIWYG_RUNTIME.master_snapshot = None

        # Written (or restored) files invalidate the code panel's cache.
        WYSIWYG_RUNTIME.source_text_cache = {}

        if errors:
            # The status bar shows two errors and then disappears; the log
            # keeps all of them, so "did that save actually run?" stays
            # answerable after the fact.
            for err in errors:
                wysiwyg_log_debug("[SAVE-ERROR] " + str(err))
            wysiwyg_set_status("Saved " + str(changed) + " line(s), errors: " + "; ".join(errors[:2]))
        elif changed:
            wysiwyg_set_status("Saved " + str(changed) + " changed line(s), verified. Backups in game/wysiwyg_backups/.")
        elif skipped_clean:
            wysiwyg_set_status("No changes to save - " + str(skipped_clean) + " character(s) match their source lines.")
        elif store.wysiwyg_chars:
            wysiwyg_set_status("No changes to save - all imported characters are locked.")
        else:
            wysiwyg_set_status("Nothing to save. Import Scene first.")

    # Save gate: rewriting a line the editor only GUESSED at (uncertain
    # source) can damage an unrelated statement. Instead of saving right
    # away, the toolbar button routes here; when any dirty character is
    # uncertain, a confirmation box lists them and offers Show Code first.
    def wysiwyg_request_save():
        if store.wysiwyg_confirm_close:
            # The close box is already asking about this work; a second
            # dialog on top of it would render at the same spot and fight
            # for the same clicks. Answer the close question first.
            return
        risky = sorted(set([
            str(c.get("tag")) for c in store.wysiwyg_chars
            if wysiwyg_char_save_kind(c) == "edited"
            and c.get("source_confidence") not in ("linelog", "carryover")
        ]))
        if risky:
            # A live inline input would keep taking keystrokes (and commit
            # on Enter) behind the frozen backdrop - drop it with the rest
            # of the frozen UI.
            wysiwyg_clear_edit_field()
            store.wysiwyg_confirm_save = risky
            renpy.restart_interaction()
            return
        wysiwyg_save_changes()

    def wysiwyg_confirm_save_proceed():
        store.wysiwyg_confirm_save = None
        wysiwyg_save_changes()

    def wysiwyg_confirm_save_review():
        store.wysiwyg_confirm_save = None
        if store.wysiwyg_panel != "code":
            wysiwyg_toggle_code_panel()
        else:
            renpy.restart_interaction()

    def wysiwyg_unsaved_changes():
        # Everything the next Save Changes would write, as short labels.
        # Built on the same predicates the save loop branches on, so the
        # close confirmation can neither cry wolf nor miss anything.
        kind_labels = {"added": " (added)", "removed": " (removed)", "edited": " (moved/edited)"}
        items = []
        for c in store.wysiwyg_chars:
            kind = wysiwyg_char_save_kind(c)
            if kind:
                items.append(str(c.get("tag")) + kind_labels[kind])
        if wysiwyg_scene_with_dirty():
            items.append("scene with")
        return items

    # Close gate: closing restores the imported scene and throws away
    # every unsaved edit. The Close button and the F5 toggle route here;
    # with unsaved work pending a confirmation lists what would be lost.
    def wysiwyg_request_close():
        if store.wysiwyg_confirm_close:
            # F5 while the box is up is its keyboard answer: discard and
            # close. That keeps the old F5-closes-and-discards behavior,
            # now one confirming press away instead of instant.
            wysiwyg_confirm_close_discard()
            return
        if store.wysiwyg_confirm_save:
            # F5 says "I want to close": withdraw the save question and
            # fall through to the close question - a silently dead key
            # would just look broken.
            store.wysiwyg_confirm_save = None
        if store.wysiwyg_active:
            unsaved = wysiwyg_unsaved_changes()
            if unsaved:
                # See wysiwyg_request_save: no live input behind the box.
                wysiwyg_clear_edit_field()
                store.wysiwyg_confirm_close = unsaved
                renpy.restart_interaction()
                return
        wysiwyg_toggle()

    def wysiwyg_confirm_close_discard():
        store.wysiwyg_confirm_close = None
        wysiwyg_toggle()

    def wysiwyg_confirm_close_save():
        # Save instead of closing: the editor stays open so the user can
        # check the status line (a save can still fail) and close cleanly.
        store.wysiwyg_confirm_close = None
        wysiwyg_request_save()

    def wysiwyg_on_drag(drags, drop):
        if not drags:
            return
        drag = drags[0]
        tag = drag.drag_name
        char = wysiwyg_find_char(tag)
        if char:
            if store.wysiwyg_selected_tag != tag:
                wysiwyg_clear_edit_field()
            store.wysiwyg_selected_tag = tag
            img_w = wysiwyg_float(char.get("img_w", char.get("original_w", 400.0)), 400.0)
            img_h = wysiwyg_float(char.get("img_h", char.get("original_h", 800.0)), 800.0)

            xzoom_val = abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0))
            yzoom_val = abs(wysiwyg_float(char.get("yzoom", 1.0), 1.0))

            box_w, box_h = wysiwyg_render_box(char)
            sw, sh = wysiwyg_render_size(char)
            new_cx = int(round(drag.x + int((box_w - sw) / 2.0) + sw / 2.0))
            new_cy = int(round(drag.y + int((box_h - sh) / 2.0) + sh / 2.0))

            new_x = new_cx - (img_w * xzoom_val) / 2.0
            new_y = new_cy - (img_h * yzoom_val) / 2.0

            # A click that releases without real movement must not mark the
            # scene dirty: after a save, a phantom 0px "drag" would switch
            # the close path away from the just-saved state.
            old_cx = int(round(wysiwyg_float(char.get("x", 0.0), 0.0) + (img_w * xzoom_val) / 2.0))
            old_cy = int(round(wysiwyg_float(char.get("y", 0.0), 0.0) + (img_h * yzoom_val) / 2.0))
            if old_cx != new_cx or old_cy != new_cy:
                wysiwyg_push_undo(char)
                char["x"] = new_x
                char["y"] = new_y
                store.wysiwyg_saved_runtime = False
                char["anchor_x"] = wysiwyg_float(char.get("x", 0), 0.0)
                char["anchor_y"] = wysiwyg_float(char.get("y", 0), 0.0)
                char["parsed_center_x"] = new_cx
                char["parsed_center_y"] = new_cy
                char["parsed_x"] = True
                char["parsed_y"] = True
        renpy.restart_interaction()


    # --- Undo ----------------------------------------------------------------
    # A simple snapshot stack (max 50). Every mutating action pushes the
    # character's full state before changing it.
    def wysiwyg_undo_move():
        while store.wysiwyg_undo_stack:
            item = store.wysiwyg_undo_stack.pop()
            char = wysiwyg_find_char(item.get("tag"))
            if char:
                char["x"] = wysiwyg_float(item.get("x", char.get("x", 0.0)), 0.0)
                char["y"] = wysiwyg_float(item.get("y", char.get("y", 0.0)), 0.0)
                char["w"] = wysiwyg_float(item.get("w", char.get("w", 0.0)), 0.0)
                char["h"] = wysiwyg_float(item.get("h", char.get("h", 0.0)), 0.0)
                char["anchor_x"] = wysiwyg_float(item.get("anchor_x", char.get("anchor_x", char.get("x", 0.0))), 0.0)
                char["anchor_y"] = wysiwyg_float(item.get("anchor_y", char.get("anchor_y", char.get("y", 0.0))), 0.0)
                char["rotate"] = wysiwyg_float(item.get("rotate", char.get("rotate", 0.0)), 0.0)
                char["xzoom"] = wysiwyg_float(item.get("xzoom", char.get("xzoom", 1.0)), 1.0)
                char["yzoom"] = wysiwyg_float(item.get("yzoom", char.get("yzoom", 1.0)), 1.0)
                char["alpha"] = wysiwyg_float(item.get("alpha", char.get("alpha", 1.0)), 1.0)
                char["filter_blur"] = wysiwyg_float(item.get("filter_blur", char.get("filter_blur", 0.0)), 0.0)
                char["filter_brightness"] = wysiwyg_float(item.get("filter_brightness", char.get("filter_brightness", 0.0)), 0.0)
                char["filter_contrast"] = wysiwyg_float(item.get("filter_contrast", char.get("filter_contrast", 1.0)), 1.0)
                char["filter_saturation"] = wysiwyg_float(item.get("filter_saturation", char.get("filter_saturation", 1.0)), 1.0)
                char["filter_hue"] = wysiwyg_float(item.get("filter_hue", char.get("filter_hue", 0.0)), 0.0)
                char["filter_invert"] = wysiwyg_float(item.get("filter_invert", char.get("filter_invert", 0.0)), 0.0)
                char["filter_sepia"] = bool(item.get("filter_sepia", char.get("filter_sepia", False)))
                char["motion_fx"] = str(item.get("motion_fx", char.get("motion_fx", "none")) or "none")
                char["motion_fx_strength"] = wysiwyg_float(item.get("motion_fx_strength", char.get("motion_fx_strength", 1.0)), 1.0)
                if "parsed_x" in item:
                    char["parsed_x"] = item["parsed_x"]
                if "parsed_y" in item:
                    char["parsed_y"] = item["parsed_y"]
                if "parsed_center_x" in item:
                    char["parsed_center_x"] = item["parsed_center_x"]
                if "parsed_center_y" in item:
                    char["parsed_center_y"] = item["parsed_center_y"]
                for transform_key in ("rotate", "xzoom", "yzoom", "alpha"):
                    store.wysiwyg_transform_memory[char.get("tag") + ":" + transform_key] = char[transform_key]
                store.wysiwyg_selected_tag = char.get("tag")
                store.wysiwyg_saved_runtime = False
                wysiwyg_set_status("Undid last move.")
                return
        wysiwyg_set_status("Nothing to undo.")

    def wysiwyg_nudge_selected(dx, dy):
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        wysiwyg_push_undo(char)
        char["x"] = wysiwyg_float(char.get("x", 0.0), 0.0) + dx
        char["y"] = wysiwyg_float(char.get("y", 0.0), 0.0) + dy
        w = wysiwyg_float(char.get("w", 0.0), 0.0)
        h = wysiwyg_float(char.get("h", 0.0), 0.0)
        char["anchor_x"] = char["x"]
        char["anchor_y"] = char["y"]
        char["parsed_center_x"] = round(char["x"] + w / 2.0)
        char["parsed_center_y"] = round(char["y"] + h / 2.0)
        char["parsed_x"] = True
        char["parsed_y"] = True
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_adjust_zorder(tag, delta):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        current = char.get("zorder")
        current = int(current) if current is not None else 0
        char["zorder"] = current + int(delta)
        store.wysiwyg_selected_tag = tag
        wysiwyg_mark_runtime_dirty()

    def wysiwyg_toggle_preview_hidden(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        char["preview_hidden"] = not bool(char.get("preview_hidden"))
        renpy.restart_interaction()

    def wysiwyg_active_filter_count(char):
        char = wysiwyg_ensure_color_filter_state(char)
        if not char:
            return 0
        count = 0
        for key, default in wysiwyg_default_color_filter_values().items():
            current = char.get(key, default)
            if isinstance(default, bool):
                if bool(current) != default:
                    count += 1
            elif abs(wysiwyg_float(current, default) - default) > 0.0001:
                count += 1
        return count

    def wysiwyg_begin_edit(field):
        # Clicking the field's own button again closes it, so a stray
        # click is undone by the same click (the s... buttons stay visible
        # while their input is open; pos/rot/scale swap into the input).
        if store.wysiwyg_edit_field == field:
            wysiwyg_cancel_edit()
            return
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char and field != "scenewithsec":
            # Only the scene-level field exists without a selection.
            return
        store.wysiwyg_edit_field = field
        if field == "pos":
            w = wysiwyg_float(char.get("w", 0.0), 0.0)
            h = wysiwyg_float(char.get("h", 0.0), 0.0)
            cx = int(round(wysiwyg_float(char.get("x", 0.0), 0.0) + w / 2.0))
            cy = int(round(wysiwyg_float(char.get("y", 0.0), 0.0) + h / 2.0))
            store.wysiwyg_edit_buffer = str(cx) + ", " + str(cy)
        elif field == "rot":
            store.wysiwyg_edit_buffer = wysiwyg_fmt_float(char.get("rotate", 0.0), 1)
        elif field == "scale":
            store.wysiwyg_edit_buffer = wysiwyg_fmt_float(abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0)), 3)
        elif field in ("withsec", "scenewithsec"):
            source = char.get("with_expr") if field == "withsec" else (store.wysiwyg_scene_with or {}).get("expr")
            key = wysiwyg_with_preset_key(source)
            store.wysiwyg_edit_buffer = wysiwyg_fmt_float(key[1], 2) if (key and key[0] == "dissolve") else "0.5"
        renpy.restart_interaction()

    def wysiwyg_clear_edit_field():
        # Drops a half-typed inline edit without restarting the
        # interaction - for callers that are mid-way through their own
        # state change and restart (or get restarted) on their own.
        store.wysiwyg_edit_field = None
        store.wysiwyg_edit_buffer = ""

    def wysiwyg_cancel_edit():
        wysiwyg_clear_edit_field()
        renpy.restart_interaction()

    def wysiwyg_commit_edit():
        field = store.wysiwyg_edit_field
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        store.wysiwyg_edit_field = None
        if not field or (not char and field != "scenewithsec"):
            renpy.restart_interaction()
            return
        text = str(store.wysiwyg_edit_buffer or "").strip()
        tag = char.get("tag") if char else None

        if field == "pos":
            try:
                parts = [p.strip() for p in text.replace(";", ",").split(",")]
                cx = int(float(parts[0]))
                cy = int(float(parts[1]))
            except Exception:
                wysiwyg_set_status("Position must be two numbers: x, y")
                return
            wysiwyg_push_undo(char)
            w = wysiwyg_float(char.get("w", 0.0), 0.0)
            h = wysiwyg_float(char.get("h", 0.0), 0.0)
            char["x"] = cx - w / 2.0
            char["y"] = cy - h / 2.0
            char["anchor_x"] = char["x"]
            char["anchor_y"] = char["y"]
            char["parsed_center_x"] = cx
            char["parsed_center_y"] = cy
            char["parsed_x"] = True
            char["parsed_y"] = True
            store.wysiwyg_saved_runtime = False
            wysiwyg_set_status("Moved center to " + str(cx) + ", " + str(cy) + ".")
        elif field == "rot":
            value = wysiwyg_float(text.replace(",", "."), None)
            if value is None:
                wysiwyg_set_status("Rotation must be a number.")
                return
            wysiwyg_set_char_transform(tag, "rotate", value)
        elif field == "scale":
            value = wysiwyg_float(text.replace(",", "."), None)
            if value is None:
                wysiwyg_set_status("Scale must be a number.")
                return
            wysiwyg_set_char_transform(tag, "xzoom", value)
            if not store.wysiwyg_scale_locked:
                wysiwyg_set_char_transform(tag, "yzoom", value)
        elif field in ("withsec", "scenewithsec"):
            value = wysiwyg_float(text.replace(",", "."), None)
            if value is None or value <= 0 or value > 30:
                wysiwyg_set_status("Dissolve time must be greater than 0, up to 30 seconds.")
                return
            expr = "Dissolve(" + wysiwyg_fmt_float(value, 2) + ")"
            if field == "withsec":
                wysiwyg_set_with_expr(tag, expr)
            else:
                wysiwyg_set_scene_with(expr)
        renpy.restart_interaction()

    def wysiwyg_reset_position_for(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character to reset.")
            return
        if char.get("locked"):
            wysiwyg_set_status("Locked: " + str(char.get("locked")) + " - this character is never modified.")
            return
        store.wysiwyg_selected_tag = tag
        wysiwyg_push_undo(char)
        
        orig_cx = wysiwyg_float(char.get("original_parsed_center_x"), None)
        orig_cy = wysiwyg_float(char.get("original_parsed_center_y"), None)
        
        if orig_cx is None or orig_cy is None:
            orig_w = wysiwyg_float(char.get("original_w", char.get("w", 0.0)), 0.0)
            orig_h = wysiwyg_float(char.get("original_h", char.get("h", 0.0)), 0.0)
            orig_x = wysiwyg_float(char.get("original_x", char.get("x", 0.0)), 0.0)
            orig_y = wysiwyg_float(char.get("original_y", char.get("y", 0.0)), 0.0)
            orig_xzoom = abs(wysiwyg_float(char.get("original_xzoom", 1.0), 1.0))
            orig_yzoom = abs(wysiwyg_float(char.get("original_yzoom", 1.0), 1.0))
            orig_cx = orig_x + (orig_w * orig_xzoom) / 2.0
            orig_cy = orig_y + (orig_h * orig_yzoom) / 2.0

        orig_cx = round(orig_cx)
        orig_cy = round(orig_cy)
        w = wysiwyg_float(char.get("w", 0.0), 0.0)
        h = wysiwyg_float(char.get("h", 0.0), 0.0)
        
        char["x"] = float(orig_cx - w / 2.0)
        char["y"] = float(orig_cy - h / 2.0)
        char["anchor_x"] = char["x"]
        char["anchor_y"] = char["y"]
        char["parsed_center_x"] = orig_cx
        char["parsed_center_y"] = orig_cy
        char["parsed_x"] = True
        char["parsed_y"] = True
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status("Reset selected character to its last imported/saved position.")

    def wysiwyg_reset_selected_position():
        wysiwyg_reset_position_for(store.wysiwyg_selected_tag)

    def wysiwyg_reset_editor():
        if store.wysiwyg_chars or store.wysiwyg_bg:
            wysiwyg_restore_imported_preview()
        WYSIWYG_RUNTIME.master_snapshot = None
        store.wysiwyg_bg = None
        store.wysiwyg_bg_source = None
        store.wysiwyg_scene_with = None
        store.wysiwyg_confirm_save = None
        store.wysiwyg_confirm_close = None
        wysiwyg_clear_edit_field()
        store.wysiwyg_chars = []
        store.wysiwyg_selected_tag = None
        store.wysiwyg_undo_stack = []
        store.wysiwyg_transform_memory = {}
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status("Editor cleared. Unsaved moves were discarded.")

    def wysiwyg_clear_editor_state():
        # If a mid-session error (and the error screen's Rollback) orphaned
        # the skipping stash, restore it here rather than cementing False.
        if WYSIWYG_RUNTIME.prev_allow_skipping is not None and not store.wysiwyg_active:
            config.allow_skipping = WYSIWYG_RUNTIME.prev_allow_skipping
            WYSIWYG_RUNTIME.prev_allow_skipping = None
        WYSIWYG_RUNTIME.source_text_cache = {}
        WYSIWYG_RUNTIME.master_snapshot = None
        store.wysiwyg_bg = None
        store.wysiwyg_bg_source = None
        store.wysiwyg_scene_with = None
        store.wysiwyg_confirm_save = None
        store.wysiwyg_confirm_close = None
        wysiwyg_clear_edit_field()
        store.wysiwyg_browser_hover = None
        store.wysiwyg_chars = []
        store.wysiwyg_selected_tag = None
        store.wysiwyg_undo_stack = []
        store.wysiwyg_transform_memory = {}
        store.wysiwyg_saved_runtime = False
        store.wysiwyg_status = ""
        store.wysiwyg_char_page = "main"

    def wysiwyg_toggle():
        if store.wysiwyg_active:
            if store.wysiwyg_chars or store.wysiwyg_bg:
                wysiwyg_restore_imported_preview()
            wysiwyg_clear_editor_state()
            # Re-enable skipping that was disabled while the editor was open.
            if WYSIWYG_RUNTIME.prev_allow_skipping is not None:
                config.allow_skipping = WYSIWYG_RUNTIME.prev_allow_skipping
                WYSIWYG_RUNTIME.prev_allow_skipping = None
        else:
            if not wysiwyg_enabled():
                # Development tool only: never activate in a shipped build.
                return
            wysiwyg_clear_editor_state()
            store.wysiwyg_status = "Editor opened. Click Import Scene to track the current scene."
            # While editing, nothing may advance the game: Ctrl-skip included.
            WYSIWYG_RUNTIME.prev_allow_skipping = getattr(config, "allow_skipping", True)
            config.allow_skipping = False
            try:
                config.skipping = None
            except Exception:
                pass
        store.wysiwyg_active = not store.wysiwyg_active
        renpy.restart_interaction()

    def wysiwyg_toggle_code_panel():
        if store.wysiwyg_panel == "code":
            store.wysiwyg_panel = "characters"
            renpy.restart_interaction()
            return
        store.wysiwyg_panel = "code"
        renpy.restart_interaction()

# init 10: so config.screen_width/height from the game's options.rpy (init 0)
# are already set and the styles can scale to the game's virtual resolution.
init 10:
    style wysiwyg_panel_frame is empty
    style wysiwyg_panel_frame:
        background Solid("#101418dd")
        padding (12, 12)
        margin (0, 0)

    style wysiwyg_toolbar_frame is empty
    style wysiwyg_toolbar_frame:
        background Solid("#101418ee")
        padding (8, 8)
        margin (0, 0)

    style wysiwyg_button is empty
    style wysiwyg_button:
        background Solid("#26313d")
        hover_background Solid("#385066")
        selected_background Solid("#4d7996")
        padding (10, 7)
        margin (0, 0)
        xminimum int(88 * config.screen_height / 1080.0)
        yminimum 0
        xsize None
        ysize None

    style wysiwyg_section_button is wysiwyg_button
    style wysiwyg_section_button:
        background Solid("#14424f")
        hover_background Solid("#1d6275")
        selected_background Solid("#2a8aa6")
        padding (14, 8)
        xminimum 0

    style wysiwyg_section_button_text is wysiwyg_button_text
    style wysiwyg_section_button_text:
        size int(16 * config.screen_height / 1080.0)
        color "#9fd8e8"
        hover_color "#ffffff"
        selected_color "#ffffff"
        layout "nobreak"

    style wysiwyg_danger_button is wysiwyg_button
    style wysiwyg_danger_button:
        background Solid("#5a2630")
        hover_background Solid("#7a3341")

    style wysiwyg_text is empty
    style wysiwyg_text:
        font "DejaVuSans.ttf"
        color "#f3f6f8"
        size int(18 * config.screen_height / 1080.0)
        outlines []
        line_spacing 0
        kerning 0

    style wysiwyg_small_text is wysiwyg_text
    style wysiwyg_small_text:
        size int(14 * config.screen_height / 1080.0)

    style wysiwyg_title_text is wysiwyg_text
    style wysiwyg_title_text:
        size int(22 * config.screen_height / 1080.0)
        bold True

    # Lines that show a CURRENT VALUE read from the script (as opposed to
    # help prose, which shares the same small size): amber and bold, so
    # they read as state, not as another sentence of explanation.
    style wysiwyg_value_text is wysiwyg_small_text
    style wysiwyg_value_text:
        color "#ffd28a"
        bold True

    style wysiwyg_button_text is empty
    style wysiwyg_button_text:
        font "DejaVuSans.ttf"
        color "#f3f6f8"
        size int(16 * config.screen_height / 1080.0)
        hover_color "#ffffff"
        selected_color "#ffffff"
        outlines []
        align (0.5, 0.5)

    # Based on the engine's vscrollbar so the thumb is sized proportionally
    # to the visible fraction and tracks the scroll position; just recolored.
    # "hide" removes the bar entirely when the content fits the viewport.
    style wysiwyg_vbar is vscrollbar
    style wysiwyg_vbar:
        xsize 10
        base_bar Solid("#203243")
        thumb Solid("#5fa8d3")
        unscrollable "hide"

    style wysiwyg_slider is empty
    style wysiwyg_slider:
        xfill True
        xmaximum int(330 * config.screen_height / 1080.0)
        ymaximum int(18 * config.screen_height / 1080.0)
        left_bar Solid("#5fa8d3")
        right_bar Solid("#203243")
        thumb Solid("#f3f6f8")
        thumb_offset 0
        margin (0, 0)
        padding (0, 0)

transform wysiwyg_float_motion(strength=1.0):
    yoffset 0
    ease 0.75 yoffset int(round(-18 * strength))
    ease 0.75 yoffset 0
    repeat

transform wysiwyg_shake_motion(strength=1.0):
    xoffset 0
    yoffset 0
    pause 0.04
    xoffset int(round(-8 * strength))
    yoffset int(round(4 * strength))
    pause 0.04
    xoffset int(round(7 * strength))
    yoffset int(round(-5 * strength))
    pause 0.04
    xoffset int(round(-6 * strength))
    yoffset int(round(3 * strength))
    pause 0.04
    xoffset int(round(5 * strength))
    yoffset int(round(-4 * strength))
    pause 0.04
    repeat

transform wysiwyg_bounce_motion(strength=1.0):
    yoffset 0
    ease 0.18 yoffset int(round(-24 * strength))
    ease 0.22 yoffset 0
    pause 0.08
    repeat

transform wysiwyg_sink_motion(strength=1.0):
    yoffset 0
    ease 0.75 yoffset int(round(18 * strength))
    ease 0.75 yoffset 0
    repeat

transform wysiwyg_breathe_motion(strength=1.0):
    yoffset 0
    zoom 1.0
    ease 0.75 yoffset int(round(-5 * strength)) zoom (1.0 + (0.025 * strength))
    ease 0.75 yoffset 0 zoom 1.0
    repeat

transform wysiwyg_sway_motion(strength=1.0):
    xoffset 0
    rotate 0
    ease 0.75 xoffset int(round(6 * strength)) rotate (3 * strength)
    ease 0.75 xoffset int(round(-6 * strength)) rotate (-3 * strength)
    ease 0.75 xoffset 0 rotate 0
    repeat

transform wysiwyg_blink_motion(strength=1.0):
    alpha 1.0
    pause 1.0
    alpha 0.0
    pause 0.12
    alpha 1.0
    pause 0.2
    repeat

# =============================================================================
# Screens
# =============================================================================
# wysiwyg_hotkey is registered as an overlay screen, so F5 works anywhere
# in the game. The editor itself is the modal wysiwyg_main screen.
screen wysiwyg_hotkey():
    zorder 300
    # The key is only bound while the editor can actually do something:
    # an unconditional Key would eat F5 (behavior.py raises IgnoreEvent
    # even for a None action) in games that bind it themselves - including
    # shipped builds where the author forgot to delete this file.
    if wysiwyg_active or wysiwyg_enabled():
        key "K_F5" action Function(wysiwyg_request_close)
    if wysiwyg_active:
        key "K_h" action NullAction()
        use wysiwyg_main

# Shared shell of the confirmation boxes; the caller transcludes its own
# extra body lines and button row, so both boxes always look alike.
screen wysiwyg_confirm_box(title, body):
    frame:
        background Solid("#101418f2")
        padding (16, 14)
        xalign 0.5
        yalign 0.4
        xmaximum 560
        vbox:
            spacing 8
            text title style "wysiwyg_text" bold True
            text body style "wysiwyg_small_text"
            transclude

# One inline edit-field row, used by every numeric field: the input
# itself commits on Enter, Escape cancels, and the two buttons repeat
# both for the mouse.
screen wysiwyg_edit_input_row(box_w=64, px_w=52, max_len=6, ok_min=0, cancel_label="Cancel", suffix=None):
    frame:
        background Solid("#101418cc")
        padding (6, 2)
        xsize box_w
        input value VariableInputValue("wysiwyg_edit_buffer") length max_len pixel_width px_w style "wysiwyg_small_text" action Function(wysiwyg_commit_edit)
    if suffix:
        text suffix style "wysiwyg_small_text" yalign 0.5
    textbutton "OK" style "wysiwyg_button" xminimum ok_min action Function(wysiwyg_commit_edit)
    textbutton cancel_label style "wysiwyg_button" xminimum 0 action Function(wysiwyg_cancel_edit) tooltip "Discard the typed value"
    key "K_ESCAPE" action Function(wysiwyg_cancel_edit)

# Scene reveal (with) controls. The value is scene-level, so the section
# shows both in the selected-character panel and when nothing is selected
# - deleting the last character must not lock the user out of it.
screen wysiwyg_scene_with_section():
    if wysiwyg_scene_with:
        null height 2
        text "Scene reveal (with)" style "wysiwyg_text" bold True
        text "The standalone `with` statement that reveals this scene - shared by everything shown with it. Saved into the script; no live preview." style "wysiwyg_small_text"
        $ _sw = wysiwyg_scene_with
        $ _sw_key = wysiwyg_with_preset_key(_sw.get("expr"))
        text wysiwyg_wrap_path("with " + str(_sw.get("expr")) + "  [" + str(_sw.get("file")) + ":" + str(_sw.get("line")) + "]") style "wysiwyg_value_text"
        hbox:
            spacing 4
            for _w_label, _w_expr in WYSIWYG_WITH_PRESETS:
                textbutton _w_label style "wysiwyg_button" xminimum 0 action Function(wysiwyg_set_scene_with, _w_expr) selected (_sw_key == wysiwyg_with_preset_key(_w_expr))
            textbutton "s..." style "wysiwyg_button" xminimum 0 action Function(wysiwyg_begin_edit, "scenewithsec") selected (wysiwyg_edit_field == "scenewithsec") tooltip "Type a custom dissolve time in seconds"
        if wysiwyg_edit_field == "scenewithsec":
            hbox:
                spacing 4
                use wysiwyg_edit_input_row(suffix="sec")
        if wysiwyg_scene_with_dirty(_sw):
            textbutton wysiwyg_ui_text("Restore original (with " + wysiwyg_short_expr(_sw.get("original")) + ")") style "wysiwyg_button" xminimum 0 action Function(wysiwyg_set_scene_with, _sw.get("original")) tooltip wysiwyg_ui_text("with " + str(_sw.get("original")))

# One color-filter slider row: label with the live value, the bar, and
# a per-filter reset arrow.
screen wysiwyg_filter_row(tag, char, label, key, rng, default, step=0.01, offset=0.0, decimals=2, suffix="", ui_s=1.0):
    $ _fv = wysiwyg_float(char.get(key, default), default)
    text (label + ": " + (str(int(_fv)) if decimals is None else wysiwyg_fmt_float(_fv, decimals)) + suffix) style "wysiwyg_small_text"
    hbox:
        spacing 6
        bar value DictValue(char, key, rng, offset=offset, step=step, action=Function(wysiwyg_on_color_filter_change, tag)) style "wysiwyg_slider" xsize int(240 * ui_s) yalign 0.5
        textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, tag, key)

# Motion-fx aware preview: placement effects wrap the preview in their
# placement transform, everything else is a plain add.
screen wysiwyg_preview_add(ch, px, py):
    if wysiwyg_motion_fx_uses_placement(ch):
        add wysiwyg_preview_displayable(ch, xpos=px, ypos=py) at wysiwyg_motion_fx_placement_transform(ch)
    else:
        add wysiwyg_preview_displayable(ch, xpos=px, ypos=py)

# Main overlay: character previews + drag handle, toolbar, side panel.
# Non-selected characters are drawn as plain `add`s (sorted by zorder);
# the selected one sits inside a drag whose geometry matches the renderer
# (see wysiwyg_render_box / wysiwyg_drag_pos).
screen wysiwyg_main():
    # NOTE: this modal has no effect (the screen is only ever transcluded
    # into wysiwyg_hotkey via `use`, and the engine reads modality from the
    # shown screen only). The real input protection is the key traps below.
    modal True
    zorder 250
    key "dismiss" action NullAction()
    # Esc / right-click would open the game menu, where Load or Main Menu
    # silently discards unsaved editor work with no close confirmation.
    key "game_menu" action Function(wysiwyg_set_status, "Close the editor (F5) before opening the game menu.")
    key "hide_windows" action NullAction()
    key "rollback" action NullAction()
    key "rollforward" action NullAction()
    key "skip" action NullAction()
    key "stop_skipping" action NullAction()
    key "toggle_skip" action NullAction()
    key "fast_skip" action NullAction()
    on "show" action Function(wysiwyg_hide_master_chars)
    on "replace" action Function(wysiwyg_hide_master_chars)
    $ _selected_drag_char = wysiwyg_find_char(wysiwyg_selected_tag) if wysiwyg_selected_tag else None
    if _selected_drag_char and (_selected_drag_char.get("preview_hidden") or _selected_drag_char.get("locked") or _selected_drag_char.get("pending_hide")):
        $ _selected_drag_char = None

    if wysiwyg_grid:
        use wysiwyg_grid_overlay

    # Nudge keys pause while a confirmation box is up - the box's list
    # must describe a scene that cannot change under it.
    if _selected_drag_char and not wysiwyg_confirm_save and not wysiwyg_confirm_close:
        $ _nstep = int(wysiwyg_nudge_step or 1)
        key "K_LEFT" action Function(wysiwyg_nudge_selected, -_nstep, 0)
        key "K_RIGHT" action Function(wysiwyg_nudge_selected, _nstep, 0)
        key "K_UP" action Function(wysiwyg_nudge_selected, 0, -_nstep)
        key "K_DOWN" action Function(wysiwyg_nudge_selected, 0, _nstep)
        key "repeat_K_LEFT" action Function(wysiwyg_nudge_selected, -_nstep, 0)
        key "repeat_K_RIGHT" action Function(wysiwyg_nudge_selected, _nstep, 0)
        key "repeat_K_UP" action Function(wysiwyg_nudge_selected, 0, -_nstep)
        key "repeat_K_DOWN" action Function(wysiwyg_nudge_selected, 0, _nstep)
        key "shift_K_LEFT" action Function(wysiwyg_nudge_selected, -10, 0)
        key "shift_K_RIGHT" action Function(wysiwyg_nudge_selected, 10, 0)
        key "shift_K_UP" action Function(wysiwyg_nudge_selected, 0, -10)
        key "shift_K_DOWN" action Function(wysiwyg_nudge_selected, 0, 10)

    for _wch in sorted(wysiwyg_chars, key=lambda c: int(c.get("zorder") or 0)):
        if _wch.get("tag") != wysiwyg_selected_tag and not _wch.get("preview_hidden") and not _wch.get("locked") and not _wch.get("pending_hide"):
            $ _wch_cx = int(round(_wch.get("x") + _wch.get("w") / 2.0))
            $ _wch_cy = int(round(_wch.get("y") + _wch.get("h") / 2.0))
            use wysiwyg_preview_add(_wch, _wch_cx, _wch_cy)

    if _selected_drag_char:
        $ _sel_cx = int(round(_selected_drag_char.get("x") + _selected_drag_char.get("w") / 2.0))
        $ _sel_cy = int(round(_selected_drag_char.get("y") + _selected_drag_char.get("h") / 2.0))
        if wysiwyg_confirm_save or wysiwyg_confirm_close:
            # While a confirmation box is up the selected sprite renders as a
            # plain preview: a live drag would keep its pointer grab and could
            # still move the sprite under the frozen backdrop.
            use wysiwyg_preview_add(_selected_drag_char, _sel_cx, _sel_cy)
        else:
            $ _sel_box_w, _sel_box_h = wysiwyg_render_box(_selected_drag_char)
            $ _sel_drag_x, _sel_drag_y = wysiwyg_drag_pos(_selected_drag_char, _sel_cx, _sel_cy, _sel_box_w, _sel_box_h)

            drag:
                drag_name _selected_drag_char.get("tag")
                style "empty"
                draggable True
                droppable False
                drag_offscreen True
                dragged wysiwyg_on_drag
                xpos _sel_drag_x
                ypos _sel_drag_y
                fixed:
                    xysize (_sel_box_w, _sel_box_h)
                    use wysiwyg_preview_add(_selected_drag_char, _sel_box_w // 2, _sel_box_h // 2)



    for _wch in wysiwyg_chars:
        if _wch.get("tag") != wysiwyg_selected_tag and not _wch.get("preview_hidden") and not _wch.get("pending_hide"):
            frame:
                background Solid("#000000aa")
                padding (8, 4)
                xpos int(_wch.get("x", 0))
                ypos max(0, int(_wch.get("y", 0)) - 28)
                if _wch.get("locked"):
                    text (wysiwyg_ui_text(wysiwyg_char_label(_wch.get("tag"))) + " (locked)") color wysiwyg_char_color(_wch.get("tag")) style "wysiwyg_small_text"
                else:
                    text wysiwyg_ui_text(wysiwyg_char_label(_wch.get("tag"))) color wysiwyg_char_color(_wch.get("tag")) style "wysiwyg_small_text"

    frame:
        style "wysiwyg_toolbar_frame"
        xfill True
        hbox:
            spacing 8
            # UI / Text mode buttons stay out of the toolbar until those
            # modes actually exist - greyed-out stubs only confuse users.
            textbutton "Characters" style "wysiwyg_button" action SetVariable("wysiwyg_panel", "characters") selected (wysiwyg_panel == "characters")
            null width 20
            textbutton "Import Scene" style "wysiwyg_button" action Function(wysiwyg_import_scene)
            textbutton ("Save Changes" + (" ●" if (wysiwyg_chars and not wysiwyg_saved_runtime) else "")) style "wysiwyg_button" action Function(wysiwyg_request_save)
            textbutton "Undo" style "wysiwyg_button" action Function(wysiwyg_undo_move)
            textbutton "Show Code" style "wysiwyg_button" action Function(wysiwyg_toggle_code_panel) selected (wysiwyg_panel == "code")
            textbutton "Grid" style "wysiwyg_button" action ToggleVariable("wysiwyg_grid") selected wysiwyg_grid
            textbutton "Clear Editor" style "wysiwyg_danger_button" action Function(wysiwyg_reset_editor)
            textbutton "Close" style "wysiwyg_danger_button" action Function(wysiwyg_request_close)

    if wysiwyg_selected_tag:
        frame:
            background Solid("#2d6f95dd")
            padding (10, 5)
            xalign 0.5
            ypos 60
            text wysiwyg_ui_text("Working on: " + wysiwyg_selected_tag) style "wysiwyg_small_text"

    frame:
        style "wysiwyg_panel_frame"
        xalign 1.0
        yalign 1.0
        xsize int(390 * wysiwyg_ui_scale())
        ysize config.screen_height - 66
        if wysiwyg_panel == "code":
            use wysiwyg_p_code
        else:
            use wysiwyg_p_characters

    # Floating preview of the image browser row under the mouse. Lives
    # here, next to the panel, because inside the browser's viewport it
    # would be clipped to the panel width.
    if wysiwyg_panel != "code" and wysiwyg_char_page == "add" and wysiwyg_browser_hover:
        $ _prev_row = wysiwyg_browser_hover
        $ _prev_s = wysiwyg_ui_scale()
        frame:
            background Solid("#101418ee")
            padding (8, 8)
            xanchor 1.0
            xpos config.screen_width - int(398 * _prev_s)
            ypos 120
            vbox:
                spacing 4
                add str(_prev_row.get("file", "")) fit "contain" xysize (int(300 * _prev_s), int(380 * _prev_s))
                text _prev_row.get("name_wrapped", "") style "wysiwyg_small_text"

    # Confirmation boxes. The full-screen backdrop button freezes every
    # control behind them (and the nudge keys pause above), so the list a
    # box shows stays true for as long as it is on screen. Esc backs out
    # of either box without doing anything.
    if wysiwyg_confirm_save or wysiwyg_confirm_close:
        button:
            xfill True
            yfill True
            background Solid("#00000066")
            action NullAction()
        key "K_ESCAPE" action [SetVariable("wysiwyg_confirm_save", None), SetVariable("wysiwyg_confirm_close", None)]

    # Box for saves that would rewrite uncertain source lines.
    if wysiwyg_confirm_save:
        use wysiwyg_confirm_box("Save with uncertain source lines?", wysiwyg_ui_text("These characters were matched from an ambiguous or scanned source, not from a verified execution order: " + ", ".join(wysiwyg_confirm_save) + ". The editor could rewrite a wrong line.")):
            text "Show Code displays exactly which lines will be rewritten - check them there first." style "wysiwyg_small_text"
            hbox:
                spacing 8
                textbutton "Show Code" style "wysiwyg_button" action Function(wysiwyg_confirm_save_review)
                textbutton "Save anyway" style "wysiwyg_danger_button" action Function(wysiwyg_confirm_save_proceed)
                textbutton "Cancel" style "wysiwyg_button" action SetVariable("wysiwyg_confirm_save", None)

    # Box for closing the editor with unsaved edits.
    if wysiwyg_confirm_close:
        use wysiwyg_confirm_box("Close without saving?", wysiwyg_ui_text("Unsaved changes: " + ", ".join(wysiwyg_confirm_close) + ". Closing discards them - these changes have not been written to any file. F5 discards and closes; Esc goes back.")):
            hbox:
                spacing 8
                textbutton "Save Changes" style "wysiwyg_button" action Function(wysiwyg_confirm_close_save)
                textbutton "Discard & Close" style "wysiwyg_danger_button" action Function(wysiwyg_confirm_close_discard)
                textbutton "Back" style "wysiwyg_button" action SetVariable("wysiwyg_confirm_close", None)

    # Hover help for buttons that declare a tooltip (the Del warning, full
    # names of truncated characters, exact-position hints) shares the
    # status bar's corner, stacked above the status line when both show.
    $ _wysiwyg_tt = GetTooltip()
    if wysiwyg_status or _wysiwyg_tt:
        frame:
            background Solid("#000000aa")
            padding (10, 6)
            xalign 0.0
            yalign 1.0
            vbox:
                spacing 4
                if _wysiwyg_tt:
                    text wysiwyg_wrap_path(_wysiwyg_tt) style "wysiwyg_small_text" xmaximum int(config.screen_width * 0.55)
                if wysiwyg_status:
                    # Long messages (paths, per-file errors) wrap instead
                    # of running under the side panel.
                    text wysiwyg_wrap_path(wysiwyg_status) style "wysiwyg_small_text" xmaximum int(config.screen_width * 0.55)

screen wysiwyg_grid_overlay():
    $ _w = int(config.screen_width)
    $ _h = int(config.screen_height)
    $ grid_step = 100
    
    # Vertical grid lines
    for x in range(grid_step, _w, grid_step):
        add Solid("#ffffff44") xpos x ypos 58 xsize 1 ysize (_h - 58)
        
    # Horizontal grid lines
    for y in range(58 + grid_step, _h, grid_step):
        add Solid("#ffffff44") xpos 0 ypos y xsize _w ysize 1

# Right-side panel, Characters mode. Fixed vertical layout that never
# shifts: On Scene list (fixed height, own scrollbar) -> selected header
# (name, unsaved dot, editable center, zorder) -> reset row -> section
# buttons -> scrollable controls area (main / color / fx page).
# Note: the built-in viewport `scrollbars` property renders empty in
# Ren'Py 8.5.3, hence the manual side + vbar construction.
screen wysiwyg_p_characters():
    $ _selected_char = wysiwyg_ensure_color_filter_state(wysiwyg_find_char(wysiwyg_selected_tag))
    $ _selected_char = wysiwyg_ensure_motion_fx_state(_selected_char) if _selected_char else None
    $ _sel_tag = _selected_char.get("tag") if _selected_char else None
    $ _ui_s = wysiwyg_ui_scale()
    $ _mid_h = max(int(220 * _ui_s), config.screen_height - 66 - int(430 * _ui_s))
    vbox:
        spacing 8

        # --- On Scene: fixed height list with a visible scrollbar, so the
        # --- controls below never move no matter how many characters exist.
        hbox:
            spacing 10
            text ("On Scene (" + str(len(wysiwyg_chars)) + ")") style "wysiwyg_title_text"
            textbutton ("Close list" if wysiwyg_char_page == "add" else "+ Add") style "wysiwyg_section_button" text_style "wysiwyg_section_button_text" action Function(wysiwyg_toggle_image_browser) selected (wysiwyg_char_page == "add")
        frame:
            background Solid("#00000066")
            padding (6, 6)
            xfill True
            ysize int(150 * _ui_s)
            side "c r":
                viewport:
                    id "wys_char_list"
                    mousewheel True
                    vbox:
                        spacing 4
                        if not wysiwyg_chars:
                            text "No tracked characters. Advance the game and press Import Scene." style "wysiwyg_small_text"
                        for _wch in wysiwyg_chars:
                            $ _w_tag = _wch.get("tag")
                            $ _w_locked = _wch.get("locked")
                            hbox:
                                spacing 4
                                if _w_locked:
                                    textbutton (wysiwyg_ui_text(wysiwyg_short_label(_w_tag, 9)) + " (locked)") style "wysiwyg_button" xsize int(140 * _ui_s) action Function(wysiwyg_set_status, "Locked: " + str(_w_locked) + " - stays live in the game, never modified or saved.") tooltip wysiwyg_ui_text(wysiwyg_char_label(_w_tag))
                                    text "live" style "wysiwyg_small_text" yalign 0.5
                                elif _wch.get("pending_hide"):
                                    text (wysiwyg_ui_text(wysiwyg_short_label(_w_tag, 8)) + " (removed)") style "wysiwyg_small_text" xsize int(140 * _ui_s) yalign 0.5
                                    textbutton "Undo remove" style "wysiwyg_button" xminimum 56 action Function(wysiwyg_unremove_character, _w_tag)
                                else:
                                    textbutton wysiwyg_ui_text(wysiwyg_short_label(_w_tag)) style "wysiwyg_button" xsize int(140 * _ui_s) action Function(wysiwyg_select_char, _w_tag) selected (_w_tag == wysiwyg_selected_tag) tooltip wysiwyg_ui_text(wysiwyg_char_label(_w_tag))
                                    textbutton ("Show" if _wch.get("preview_hidden") else "Hide") style "wysiwyg_button" xminimum 0 action Function(wysiwyg_toggle_preview_hidden, _w_tag)
                                    textbutton "Reset" style "wysiwyg_button" xminimum 0 action Function(wysiwyg_reset_position_for, _w_tag)
                                    textbutton "Del" style "wysiwyg_button" xminimum 0 action Function(wysiwyg_remove_character, _w_tag) tooltip ("Discard this unsaved sprite" if _wch.get("pending_insert") else "Remove from scene - Save Changes writes a hide line")
                vbar value YScrollValue("wys_char_list") style "wysiwyg_vbar"

        if _selected_char:
            # --- Header: always the same position regardless of list length.
            frame:
                background Solid("#2d6f9588")
                padding (8, 6)
                xfill True
                vbox:
                    spacing 2
                    hbox:
                        spacing 8
                        text wysiwyg_wrap_path(_selected_char.get("runtime_image") or _selected_char.get("image")) style "wysiwyg_text"
                        if not wysiwyg_saved_runtime:
                            text "●" color "#ffb347" style "wysiwyg_text"
                    hbox:
                        spacing 8
                        if wysiwyg_edit_field == "pos":
                            use wysiwyg_edit_input_row(box_w=150, px_w=138, max_len=14, ok_min=40, cancel_label="✕")
                        else:
                            textbutton ("Center: " + str(int(round(_selected_char.get("x", 0) + _selected_char.get("w", 0) / 2.0))) + ", " + str(int(round(_selected_char.get("y", 0) + _selected_char.get("h", 0) / 2.0)))) style "wysiwyg_button" action Function(wysiwyg_begin_edit, "pos") tooltip "Click to type exact position"
                        $ _z_val = int(_selected_char.get("zorder") or 0)
                        textbutton "−" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_adjust_zorder, _sel_tag, -1)
                        text ("Layer " + str(_z_val)) style "wysiwyg_small_text" yalign 0.5
                        textbutton "+" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_adjust_zorder, _sel_tag, 1)

            # --- Resets: fixed position, always visible.
            hbox:
                spacing 6
                textbutton "Reset Pos" style "wysiwyg_button" action Function(wysiwyg_reset_selected_position)
                textbutton "Reset Transform" style "wysiwyg_button" action Function(wysiwyg_reset_selected_transform)
                textbutton "Defaults" style "wysiwyg_button" action Function(wysiwyg_reset_selected_transform_to_defaults)

            # --- Section switcher: basic controls stay on "main", filters and
            # --- FX open as full pages so their options are always visible.
            $ _filter_count = wysiwyg_active_filter_count(_selected_char)
            $ _fx_name = str(_selected_char.get("motion_fx", "none") or "none").strip().lower()
            hbox:
                spacing 6
                textbutton ("Color Filters" + (" ●" if _filter_count else "")) style "wysiwyg_section_button" text_style "wysiwyg_section_button_text" action SetVariable("wysiwyg_char_page", "main" if wysiwyg_char_page == "color" else "color") selected (wysiwyg_char_page == "color")
                textbutton ("Motion FX" + (" ●" if _fx_name != "none" else "")) style "wysiwyg_section_button" text_style "wysiwyg_section_button_text" action SetVariable("wysiwyg_char_page", "main" if wysiwyg_char_page == "fx" else "fx") selected (wysiwyg_char_page == "fx")

            side "c r":
                viewport:
                    id "wys_char_controls"
                    mousewheel True
                    ysize _mid_h
                    xfill True
                    vbox:
                        spacing 8
                        xfill True

                        if wysiwyg_char_page == "add":
                            use wysiwyg_p_add_browser

                        elif wysiwyg_char_page == "color":
                            textbutton "Reset filters to defaults" style "wysiwyg_button" action Function(wysiwyg_reset_selected_color_filters_to_defaults)
                            null height 2
                            use wysiwyg_filter_row(_sel_tag, _selected_char, "Blur", "filter_blur", 20.0, 0.0, step=1.0, decimals=None, suffix=" px", ui_s=_ui_s)
                            use wysiwyg_filter_row(_sel_tag, _selected_char, "Brightness", "filter_brightness", 2.0, 0.0, offset=-1.0, ui_s=_ui_s)
                            use wysiwyg_filter_row(_sel_tag, _selected_char, "Contrast", "filter_contrast", 2.0, 1.0, ui_s=_ui_s)
                            use wysiwyg_filter_row(_sel_tag, _selected_char, "Saturation", "filter_saturation", 2.0, 1.0, ui_s=_ui_s)
                            use wysiwyg_filter_row(_sel_tag, _selected_char, "Hue", "filter_hue", 360.0, 0.0, step=1.0, offset=-180.0, decimals=1, suffix=" deg", ui_s=_ui_s)
                            use wysiwyg_filter_row(_sel_tag, _selected_char, "Invert", "filter_invert", 1.0, 0.0, ui_s=_ui_s)
                            textbutton ("Sepia: ON" if _selected_char.get("filter_sepia") else "Sepia: OFF") style "wysiwyg_button" action [Function(wysiwyg_toggle_char_bool, _sel_tag, "filter_sepia"), Function(wysiwyg_on_color_filter_change, _sel_tag)]

                        elif wysiwyg_char_page == "fx":
                            grid 3 3:
                                spacing 6
                                xfill True
                                textbutton "Breathe" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "breathe") selected (_fx_name == "breathe")
                                textbutton "Shake" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "shake") selected (_fx_name == "shake")
                                textbutton "Float" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "float") selected (_fx_name == "float")
                                textbutton "Sway" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "sway") selected (_fx_name == "sway")
                                textbutton "Bounce" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "bounce") selected (_fx_name == "bounce")
                                textbutton "Sink" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "sink") selected (_fx_name == "sink")
                                textbutton "Blink" style "wysiwyg_button" xfill True action Function(wysiwyg_set_motion_fx, _sel_tag, "blink") selected (_fx_name == "blink")
                                textbutton "None" style "wysiwyg_button" xfill True action Function(wysiwyg_reset_selected_motion_fx_to_defaults) selected (_fx_name == "none")
                                null
                            text ("Strength " + wysiwyg_fmt_float(wysiwyg_float(_selected_char.get("motion_fx_strength", 1.0), 1.0), 2)) style "wysiwyg_small_text"
                            bar value DictValue(_selected_char, "motion_fx_strength", 2.0, step=0.01, action=Function(wysiwyg_on_motion_fx_change, _sel_tag)) style "wysiwyg_slider"

                        else:
                            text "Move" style "wysiwyg_text" bold True
                            hbox:
                                spacing 4
                                textbutton "◄" style "wysiwyg_button" xminimum 44 action Function(wysiwyg_nudge_selected, -wysiwyg_nudge_step, 0)
                                textbutton "►" style "wysiwyg_button" xminimum 44 action Function(wysiwyg_nudge_selected, wysiwyg_nudge_step, 0)
                                textbutton "▲" style "wysiwyg_button" xminimum 44 action Function(wysiwyg_nudge_selected, 0, -wysiwyg_nudge_step)
                                textbutton "▼" style "wysiwyg_button" xminimum 44 action Function(wysiwyg_nudge_selected, 0, wysiwyg_nudge_step)
                                textbutton ("Step " + str(wysiwyg_nudge_step) + "px") style "wysiwyg_button" action SetVariable("wysiwyg_nudge_step", 10 if wysiwyg_nudge_step == 1 else 1)
                            text "Arrow keys move too while the sprite is visible (Shift = 10px)." style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                textbutton "At Left" style "wysiwyg_button" action Function(wysiwyg_place_selected_on_x_target, "left")
                                textbutton "At Center" style "wysiwyg_button" action Function(wysiwyg_place_selected_on_x_target, "center")
                                textbutton "At Right" style "wysiwyg_button" action Function(wysiwyg_place_selected_on_x_target, "right")
                            hbox:
                                spacing 6
                                textbutton "Flip H" style "wysiwyg_button" action Function(wysiwyg_flip_char, _sel_tag, "xzoom")
                                textbutton "Flip V" style "wysiwyg_button" action Function(wysiwyg_flip_char, _sel_tag, "yzoom")

                            null height 2
                            text "Rotation" style "wysiwyg_text" bold True
                            hbox:
                                spacing 6
                                if wysiwyg_edit_field == "rot":
                                    use wysiwyg_edit_input_row(box_w=90, px_w=78, max_len=8, ok_min=40, cancel_label="✕")
                                else:
                                    textbutton (wysiwyg_fmt_float(_selected_char.get("rotate", 0.0), 1) + "°") style "wysiwyg_button" xminimum 70 action Function(wysiwyg_begin_edit, "rot") tooltip "Click to type exact angle"
                                bar value DictValue(_selected_char, "rotate", 360.0, offset=-180.0, step=1.0, action=Function(wysiwyg_drag_transform_slider, _sel_tag, "rotate")) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5 released Function(wysiwyg_release_transform_slider, _sel_tag, "rotate")

                            null height 2
                            text "Scale" style "wysiwyg_text" bold True
                            hbox:
                                spacing 6
                                if wysiwyg_edit_field == "scale":
                                    use wysiwyg_edit_input_row(box_w=90, px_w=78, max_len=8, ok_min=40, cancel_label="✕")
                                else:
                                    textbutton (wysiwyg_fmt_float(abs(wysiwyg_float(_selected_char.get("xzoom", 1.0), 1.0)), 2) + "x") style "wysiwyg_button" xminimum 70 action Function(wysiwyg_begin_edit, "scale") tooltip "Click to type exact scale"
                                textbutton ("Linked" if wysiwyg_scale_locked else "Unlinked") style "wysiwyg_button" action Function(wysiwyg_toggle_scale_lock)
                            text ("X " + wysiwyg_fmt_float(abs(wysiwyg_float(_selected_char.get("xzoom", 1.0), 1.0)), 2)) style "wysiwyg_small_text"
                            bar value DictValue(_selected_char, "xzoom", 2.0, step=0.01, action=Function(wysiwyg_drag_transform_slider, _sel_tag, "xzoom")) style "wysiwyg_slider" released Function(wysiwyg_release_transform_slider, _sel_tag, "xzoom")
                            text ("Y " + wysiwyg_fmt_float(abs(wysiwyg_float(_selected_char.get("yzoom", 1.0), 1.0)), 2)) style "wysiwyg_small_text"
                            bar value DictValue(_selected_char, "yzoom", 2.0, step=0.01, action=Function(wysiwyg_drag_transform_slider, _sel_tag, "yzoom")) style "wysiwyg_slider" released Function(wysiwyg_release_transform_slider, _sel_tag, "yzoom")

                            null height 2
                            text ("Opacity " + wysiwyg_fmt_float(_selected_char.get("alpha", 1.0), 2)) style "wysiwyg_text" bold True
                            bar value DictValue(_selected_char, "alpha", 1.0, step=0.01, action=Function(wysiwyg_drag_transform_slider, _sel_tag, "alpha")) style "wysiwyg_slider" released Function(wysiwyg_release_transform_slider, _sel_tag, "alpha")

                            null height 2
                            text "Appear (with)" style "wysiwyg_text" bold True
                            text "Dissolve-in over the given time; fade goes through black. Edits the `with` on this show line only." style "wysiwyg_small_text"
                            $ _cur_with = _selected_char.get("with_expr") or None
                            $ _orig_with = _selected_char.get("original_with_expr") or None
                            $ _cur_with_key = wysiwyg_with_preset_key(_cur_with)
                            hbox:
                                spacing 4
                                for _w_label, _w_expr in WYSIWYG_WITH_PRESETS:
                                    textbutton _w_label style "wysiwyg_button" xminimum 0 action Function(wysiwyg_set_with_expr, _sel_tag, _w_expr) selected (_cur_with_key == wysiwyg_with_preset_key(_w_expr))
                                textbutton "s..." style "wysiwyg_button" xminimum 0 action Function(wysiwyg_begin_edit, "withsec") selected (wysiwyg_edit_field == "withsec") tooltip "Type a custom dissolve time in seconds"
                            if wysiwyg_edit_field == "withsec":
                                hbox:
                                    spacing 4
                                    use wysiwyg_edit_input_row(suffix="sec")
                            # Any current value no preset button represents
                            # (author's own transition OR a custom dissolve
                            # time like 0.75s) stays visible as text -
                            # otherwise the row would look like "None".
                            $ _cur_with_is_preset = any(_cur_with_key == wysiwyg_with_preset_key(_p[1]) for _p in WYSIWYG_WITH_PRESETS)
                            if _cur_with and not _cur_with_is_preset:
                                text wysiwyg_wrap_path("with " + str(_cur_with)) style "wysiwyg_value_text"
                            if wysiwyg_char_with_dirty(_selected_char):
                                $ _with_restore_label = ("Restore original (with " + wysiwyg_short_expr(_orig_with) + ")") if _orig_with else "Restore original (no transition)"
                                textbutton wysiwyg_ui_text(_with_restore_label) style "wysiwyg_button" xminimum 0 action Function(wysiwyg_set_with_expr, _sel_tag, _orig_with) tooltip (wysiwyg_ui_text("with " + str(_orig_with)) if _orig_with else None)

                            use wysiwyg_scene_with_section
                vbar value YScrollValue("wys_char_controls") style "wysiwyg_vbar"
        else:
            if wysiwyg_char_page == "add":
                side "c r":
                    viewport:
                        id "wys_add_browser"
                        mousewheel True
                        ysize _mid_h
                        xfill True
                        vbox:
                            spacing 8
                            xfill True
                            use wysiwyg_p_add_browser
                    vbar value YScrollValue("wys_add_browser") style "wysiwyg_vbar"
            else:
                frame:
                    background Solid("#00000066")
                    padding (8, 8)
                    xfill True
                    text "Select a character from On Scene." style "wysiwyg_small_text"
                if wysiwyg_scene_with:
                    # Same scrolling treatment as the character controls:
                    # on a short window the section must scroll, not run
                    # off the bottom of the panel.
                    side "c r":
                        viewport:
                            id "wys_scene_with"
                            mousewheel True
                            ysize _mid_h
                            xfill True
                            vbox:
                                spacing 8
                                xfill True
                                use wysiwyg_scene_with_section
                        vbar value YScrollValue("wys_scene_with") style "wysiwyg_vbar"

# Add-sprite browser: names come only from files enumerated under
# game/images/ - there is no path input, so nothing outside that folder
# can be picked.
# Hovering a name feeds the floating preview rendered by the main editor
# screen (rendering it here would clip it inside the panel viewport).
screen wysiwyg_p_browser_row(row):
    if row.get("problem"):
        vbox:
            spacing 0
            text row.get("name_wrapped", "") style "wysiwyg_small_text"
            text row.get("problem_line", "") style "wysiwyg_small_text"
    else:
        vbox:
            spacing 0
            textbutton wysiwyg_ui_text(row.get("name", "")) style "wysiwyg_button" xfill True action Function(wysiwyg_add_character, row.get("name")) hovered SetVariable("wysiwyg_browser_hover", row) unhovered SetVariable("wysiwyg_browser_hover", None)
            text row.get("file_wrapped", "") style "wysiwyg_small_text"

screen wysiwyg_p_add_browser():
    $ _ui_s = wysiwyg_ui_scale()
    text "Add sprite from game/images/" style "wysiwyg_text" bold True
    text "Pick a file; the sprite appears mid-screen. Hover a name to preview the image. Save Changes writes the show line into the script." style "wysiwyg_small_text"
    $ _rows = WYSIWYG_RUNTIME.image_browser or []
    if not _rows:
        text "No image files found in game/images/." style "wysiwyg_small_text"
    else:
        hbox:
            spacing 6
            text "Search" style "wysiwyg_small_text" yalign 0.5
            frame:
                background Solid("#101418cc")
                padding (6, 2)
                xsize int(200 * _ui_s)
                if wysiwyg_confirm_save or wysiwyg_confirm_close:
                    # Frozen with the rest of the panel: a live input would
                    # keep the caret and eat keystrokes behind the backdrop.
                    text wysiwyg_ui_text(wysiwyg_browser_filter) style "wysiwyg_small_text"
                else:
                    input value VariableInputValue("wysiwyg_browser_filter") length 40 pixel_width int(188 * _ui_s) style "wysiwyg_small_text"
            if wysiwyg_browser_filter:
                textbutton "Clear" style "wysiwyg_button" action SetVariable("wysiwyg_browser_filter", "")
        $ _groups = wysiwyg_browser_groups(_rows, wysiwyg_browser_filter)
        if str(wysiwyg_browser_filter or "").strip():
            $ _matches = _groups[0][1]
            text (str(len(_matches)) + " of " + str(len(_rows)) + " file(s) match") style "wysiwyg_small_text"
            for _row in _matches:
                use wysiwyg_p_browser_row(_row)
        else:
            for _prefix, _group_rows in _groups:
                $ _glabel = "other" if _prefix == WYSIWYG_BROWSER_UNGROUPED else _prefix
                $ _gopen = (_prefix in wysiwyg_browser_open_groups)
                textbutton (("- " if _gopen else "+ ") + wysiwyg_ui_text(_glabel) + " (" + str(len(_group_rows)) + ")") style "wysiwyg_button" xfill True action Function(wysiwyg_toggle_browser_group, _prefix)
                if _gopen:
                    for _row in _group_rows:
                        use wysiwyg_p_browser_row(_row)

# Right-side panel, Show Code mode: original source lines next to the
# lines Save Changes would write.
screen wysiwyg_p_code():
    vbox:
        spacing 10
        text "Code Compare" style "wysiwyg_title_text"
        text "Compare the original source lines with the code the editor will generate." style "wysiwyg_small_text"
        if not wysiwyg_chars and not wysiwyg_bg:
            frame:
                background Solid("#00000066")
                padding (8, 8)
                xfill True
                text "Nothing imported yet. Click Import Scene first." style "wysiwyg_small_text"
        else:
            # Column and text widths scale with the game's virtual
            # resolution like every other panel dimension - fixed pixels
            # overflow the panel below 1080p.
            $ _ui_s = wysiwyg_ui_scale()
            hbox:
                spacing 10
                frame:
                    background Solid("#00000066")
                    padding (8, 8)
                    xsize int(180 * _ui_s)
                    yfill True
                    vbox:
                        spacing 6
                        text "Original source" style "wysiwyg_text"
                        text "What is currently in the .rpy file." style "wysiwyg_small_text"
                        if wysiwyg_bg_source:
                            text "Scene" style "wysiwyg_small_text"
                            text wysiwyg_wrap_path(wysiwyg_bg_source.get("file") + ":" + str(wysiwyg_bg_source.get("line"))) style "wysiwyg_small_text"
                            text wysiwyg_wrap_path(wysiwyg_source_line_text_cached(wysiwyg_bg_source.get("file"), wysiwyg_bg_source.get("line"))) style "wysiwyg_small_text" xsize int(160 * _ui_s)
                        elif wysiwyg_bg:
                            text "Scene" style "wysiwyg_small_text"
                            text "No tracked background source line." style "wysiwyg_small_text"
                        for _wch in wysiwyg_chars:
                            frame:
                                background (Solid("#2d6f9588") if _wch.get("tag") == wysiwyg_selected_tag else Solid("#00000044"))
                                padding (6, 6)
                                xfill True
                                vbox:
                                    spacing 4
                                    text wysiwyg_wrap_path(_wch.get("image", "")) style "wysiwyg_small_text"
                                    if _wch.get("source_file"):
                                        text wysiwyg_wrap_path(_wch.get("source_file") + ":" + str(_wch.get("source_line"))) style "wysiwyg_small_text"
                                        text wysiwyg_wrap_path(wysiwyg_source_line_text_cached(_wch.get("source_file"), _wch.get("source_line"))) style "wysiwyg_small_text" xsize int(148 * _ui_s)
                                    else:
                                        text "No tracked show source line." style "wysiwyg_small_text"
                frame:
                    background Solid("#00000066")
                    padding (8, 8)
                    xsize int(180 * _ui_s)
                    yfill True
                    vbox:
                        spacing 6
                        text "Generated code" style "wysiwyg_text"
                        text "What Save Changes will write back." style "wysiwyg_small_text"
                        if wysiwyg_bg:
                            text "Scene" style "wysiwyg_small_text"
                            text "unchanged - the scene line is never rewritten" style "wysiwyg_small_text" xsize int(160 * _ui_s)
                        for _wch in wysiwyg_chars:
                            $ _save_kind = wysiwyg_char_save_kind(_wch)
                            frame:
                                background (Solid("#2d6f9588") if _wch.get("tag") == wysiwyg_selected_tag else Solid("#00000044"))
                                padding (6, 6)
                                xfill True
                                vbox:
                                    spacing 4
                                    text wysiwyg_wrap_path(_wch.get("image")) style "wysiwyg_small_text"
                                    if _wch.get("locked"):
                                        text wysiwyg_wrap_path("locked (" + str(_wch.get("locked")) + ") - never rewritten") style "wysiwyg_small_text" xsize int(148 * _ui_s)
                                    elif _save_kind in ("edited", "removed") and wysiwyg_norm_path(_wch.get("source_file") or "") in WYSIWYG_RUNTIME.failed_files:
                                        text "saving to this file is disabled after a failed write - restart the game" style "wysiwyg_small_text" xsize int(148 * _ui_s)
                                    elif _save_kind == "removed":
                                        text wysiwyg_wrap_path("hide " + str(_wch.get("tag")) + "  (inserted before the current statement)") style "wysiwyg_small_text" xsize int(148 * _ui_s)
                                    elif _save_kind == "added":
                                        text wysiwyg_wrap_path(wysiwyg_position_line_for_char(_wch) + "  (new line inserted)") style "wysiwyg_small_text" xsize int(148 * _ui_s)
                                    elif _save_kind is None:
                                        text "unchanged - will not be written" style "wysiwyg_small_text" xsize int(148 * _ui_s)
                                    else:
                                        text wysiwyg_wrap_path(wysiwyg_position_line_for_char(_wch)) style "wysiwyg_small_text" xsize int(148 * _ui_s)
                        if wysiwyg_scene_with_dirty(wysiwyg_scene_with):
                            frame:
                                background Solid("#00000044")
                                padding (6, 6)
                                xfill True
                                vbox:
                                    spacing 4
                                    text "Scene reveal" style "wysiwyg_small_text"
                                    text wysiwyg_wrap_path("with " + str(wysiwyg_scene_with.get("expr")) + "  (rewrites " + str(wysiwyg_scene_with.get("file")) + ":" + str(wysiwyg_scene_with.get("line")) + ")") style "wysiwyg_small_text" xsize int(148 * _ui_s)

init 999 python:
    wysiwyg_init()
    if "wysiwyg_hotkey" not in config.overlay_screens:
        config.overlay_screens.append("wysiwyg_hotkey")
    # Dev artifacts must never ship: Ren'Py's default build rules would
    # otherwise package the plaintext script backups and the debug log
    # into releases (they precede the "**" catch-all, so this works).
    try:
        build.classify("game/wysiwyg_backups/**", None)
        build.classify("game/wysiwyg_debug.txt", None)
    except Exception:
        pass
