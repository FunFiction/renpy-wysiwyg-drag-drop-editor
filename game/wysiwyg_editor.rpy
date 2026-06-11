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
#   - "Save Changes" rewrites those exact source lines in place (a one-time
#     .wysiwyg.bak backup of each touched file is created automatically).
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
default wysiwyg_bg_runtime = None
default wysiwyg_bg_source = None
default wysiwyg_chars = []
default wysiwyg_status = ""
default wysiwyg_show_code = False
default wysiwyg_code = ""
default wysiwyg_saved_runtime = False
default wysiwyg_selected_tag = None
default wysiwyg_undo_stack = []
default wysiwyg_transform_memory = {}
default wysiwyg_rotation_input = ""
default wysiwyg_rotation_input_tag = None
default wysiwyg_scale_locked = True
default wysiwyg_grid = False
default wysiwyg_char_page = "main"
default wysiwyg_nudge_step = 1
default wysiwyg_pos_input_x = ""
default wysiwyg_pos_input_y = ""
default wysiwyg_pos_input_tag = None
default wysiwyg_edit_field = None
default wysiwyg_edit_buffer = ""

init -2 python:
    import os
    import re
    import io
    import math
    import struct
    import zlib

    WYSIWYG_VERSION = "0.2.0"
    WYSIWYG_BLACKLIST = set(["black", "white", "text", "vtext", "side", "icon", "ui", "button"])
    WYSIWYG_ALPHA_CACHE = {}

    def wysiwyg_init():
        config.line_log = True
        config.clear_lines = False

    def wysiwyg_game_dir():
        return getattr(config, "gamedir", renpy.config.gamedir)

    def wysiwyg_log_debug(msg):
        try:
            with open(os.path.join(wysiwyg_game_dir(), "wysiwyg_debug.txt"), "a") as f:
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

    def wysiwyg_set_status(text):
        store.wysiwyg_status = text
        renpy.restart_interaction()

    def wysiwyg_safe_name(value, fallback="sprite"):
        value = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_")
        if not value:
            value = fallback
        if value[0].isdigit():
            value = "_" + value
        return value

    def wysiwyg_source_path(filename):
        if not filename or not filename.startswith("game/"):
            return None
        return os.path.join(wysiwyg_game_dir(), filename[5:].replace("/", os.sep))

    def wysiwyg_image_file_for_name(image_name):
        image_name = str(image_name or "").strip()
        if not image_name:
            return None

        pattern = re.compile(r"^\s*image\s+" + re.escape(image_name) + r"\s*=\s*[\"']([^\"']+)[\"']")
        game_dir = wysiwyg_game_dir()

        for root, dirs, files in os.walk(game_dir):
            for filename in files:
                if not filename.endswith(".rpy"):
                    continue
                path = os.path.join(root, filename)
                try:
                    with io.open(path, "r", encoding="utf-8") as handle:
                        for line in handle:
                            match = pattern.match(line)
                            if match:
                                return os.path.join(game_dir, match.group(1).replace("/", os.sep))
                except Exception:
                    pass

        fallback = os.path.join(game_dir, image_name.replace(" ", "_") + ".png")
        if os.path.exists(fallback):
            return fallback
        return None

    # --- PNG alpha analysis -------------------------------------------------
    # Decodes a PNG manually (no PIL inside Ren'Py) to find the first and
    # last rows with visible pixels. Used for edge snapping so transparent
    # padding in sprite files does not count as part of the character.
    def wysiwyg_png_alpha_bounds(path):
        if not path or not os.path.exists(path):
            return None
        if path in WYSIWYG_ALPHA_CACHE:
            return WYSIWYG_ALPHA_CACHE[path]

        try:
            with open(path, "rb") as handle:
                data = handle.read()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                return None

            pos = 8
            width = height = color_type = bit_depth = None
            compressed = []

            while pos + 8 <= len(data):
                length = struct.unpack(">I", data[pos:pos + 4])[0]
                chunk = data[pos + 4:pos + 8]
                payload = data[pos + 8:pos + 8 + length]
                pos += 12 + length

                if chunk == b"IHDR":
                    width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
                elif chunk == b"IDAT":
                    compressed.append(payload)
                elif chunk == b"IEND":
                    break

            if not width or not height or bit_depth != 8:
                return None
            if color_type == 6:
                channels = 4
                alpha_index = 3
            elif color_type == 4:
                channels = 2
                alpha_index = 1
            else:
                result = {"width": width, "height": height, "top": 0, "bottom": 0}
                WYSIWYG_ALPHA_CACHE[path] = result
                return result

            raw = zlib.decompress(b"".join(compressed))
            stride = width * channels
            prev = bytearray(stride)
            offset = 0
            min_y = height
            max_y = -1

            for y in range(height):
                filter_type = raw[offset]
                offset += 1
                row = bytearray(raw[offset:offset + stride])
                offset += stride

                for i in range(stride):
                    left = row[i - channels] if i >= channels else 0
                    up = prev[i]
                    up_left = prev[i - channels] if i >= channels else 0

                    if filter_type == 1:
                        row[i] = (row[i] + left) & 0xff
                    elif filter_type == 2:
                        row[i] = (row[i] + up) & 0xff
                    elif filter_type == 3:
                        row[i] = (row[i] + ((left + up) >> 1)) & 0xff
                    elif filter_type == 4:
                        p = left + up - up_left
                        pa = abs(p - left)
                        pb = abs(p - up)
                        pc = abs(p - up_left)
                        pr = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                        row[i] = (row[i] + pr) & 0xff

                for x in range(width):
                    if row[x * channels + alpha_index] > 0:
                        if y < min_y:
                            min_y = y
                        if y > max_y:
                            max_y = y
                        break

                prev = row

            if max_y < 0:
                result = {"width": width, "height": height, "top": 0, "bottom": 0}
            else:
                result = {"width": width, "height": height, "top": min_y, "bottom": height - 1 - max_y}

            WYSIWYG_ALPHA_CACHE[path] = result
            return result
        except Exception:
            return None

    def wysiwyg_alpha_bounds_for_image(image_name):
        return wysiwyg_png_alpha_bounds(wysiwyg_image_file_for_name(image_name))

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

    def wysiwyg_backup_source(filename):
        path = wysiwyg_source_path(filename)
        if not path or not os.path.exists(path):
            return
        backup = path + ".wysiwyg.bak"
        if os.path.exists(backup):
            return
        with io.open(path, "r", encoding="utf-8") as handle:
            data = handle.read()
        with io.open(backup, "w", encoding="utf-8") as handle:
            handle.write(data)

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
        text = ("%." + str(digits) + "f") % float(value)
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

    def wysiwyg_char_anchor_pos(char, use_original=False):
        if use_original:
            if "original_anchor_x" in char and "original_anchor_y" in char:
                return (
                    wysiwyg_float(char.get("original_anchor_x"), 0.0),
                    wysiwyg_float(char.get("original_anchor_y"), 0.0),
                )
            x_key = "original_x"
            y_key = "original_y"
        else:
            if "anchor_x" in char and "anchor_y" in char:
                return (
                    wysiwyg_float(char.get("anchor_x"), 0.0),
                    wysiwyg_float(char.get("anchor_y"), 0.0),
                )
            x_key = "x"
            y_key = "y"
        x = wysiwyg_float(char.get(x_key, char.get("x", 0)), 0.0)
        y = wysiwyg_float(char.get(y_key, char.get("y", 0)), 0.0)
        w = wysiwyg_float(char.get("w", 0), 0.0)
        h = wysiwyg_float(char.get("h", 0), 0.0)
        return x + (w / 2.0), y + (h / 2.0)

    def wysiwyg_hide_master_chars():
        for char in store.wysiwyg_chars:
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
        img_w = wysiwyg_float(char.get("img_w", char.get("original_w", 400.0)), 400.0)
        img_h = wysiwyg_float(char.get("img_h", char.get("original_h", 800.0)), 800.0)
        w = img_w * abs(wysiwyg_float(char.get("xzoom", 1.0), 1.0))
        h = img_h * abs(wysiwyg_float(char.get("yzoom", 1.0), 1.0))
        rot = wysiwyg_float(char.get("rotate", 0.0), 0.0)
        if abs(rot) > 0.01:
            # Ren'Py rotates with rotate_pad=True by default: the rotated
            # render is padded to a hypot(w,h) square regardless of angle.
            # The drag container must match that exact size, otherwise the
            # inner blit offset goes negative and rounds differently than the
            # live master-layer blit, shifting the preview by 1px.
            bw = bh = math.hypot(w, h)
        else:
            bw, bh = w, h
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
        store.wysiwyg_saved_runtime = False
        if key == "rotate":
            store.wysiwyg_rotation_input_tag = tag
            store.wysiwyg_rotation_input = wysiwyg_fmt_float(value, 1)
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

    def wysiwyg_adjust_char_transform(tag, key, delta):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        wysiwyg_set_char_transform(tag, key, wysiwyg_float(char.get(key, 0.0), 0.0) + delta)

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
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

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
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

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

    def wysiwyg_motion_fx_pad(char):
        strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
        return int(round(10 + (22 * strength)))

    def wysiwyg_motion_fx_placement_transform(char):
        effect = str(char.get("motion_fx", "none") or "none").strip().lower()
        strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
        if effect == "float":
            return wysiwyg_float_motion(strength)
        if effect == "shake":
            return wysiwyg_shake_motion(strength)
        if effect == "bounce":
            return wysiwyg_bounce_motion(strength)
        if effect == "sink":
            return wysiwyg_sink_motion(strength)
        return Transform()

    def wysiwyg_motion_fx_transform_for_char(char, use_original=False):
        if use_original:
            effect = str(char.get("original_motion_fx", char.get("motion_fx", "none")) or "none").strip().lower()
            strength = wysiwyg_clamp(wysiwyg_float(char.get("original_motion_fx_strength", char.get("motion_fx_strength", 1.0)), 1.0), 0.0, 2.0)
        else:
            effect = str(char.get("motion_fx", "none") or "none").strip().lower()
            strength = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)

        if effect == "float":
            return wysiwyg_float_motion(strength)
        if effect == "shake":
            return wysiwyg_shake_motion(strength)
        if effect == "bounce":
            return wysiwyg_bounce_motion(strength)
        if effect == "sink":
            return wysiwyg_sink_motion(strength)
        if effect == "breathe":
            return wysiwyg_breathe_motion(strength)
        if effect == "sway":
            return wysiwyg_sway_motion(strength)
        if effect == "blink":
            return wysiwyg_blink_motion(strength)
        return None



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

        if abs(rotate_val) > 0.01:
            kwargs["rotate"] = rotate_val
        else:
            kwargs["rotate"] = None

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
        
        try:
            child = renpy.displayable(img_str)
        except Exception:
            child = img

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
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

    def wysiwyg_on_motion_fx_change(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        wysiwyg_ensure_motion_fx_state(char)
        char["motion_fx"] = str(char.get("motion_fx", "none") or "none").strip().lower()
        char["motion_fx_strength"] = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
        store.wysiwyg_selected_tag = tag
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

    def wysiwyg_reset_selected_color_filters():
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        wysiwyg_push_undo(char)
        wysiwyg_ensure_color_filter_state(char)
        char["filter_blur"] = wysiwyg_float(char.get("original_filter_blur", 0.0), 0.0)
        char["filter_brightness"] = wysiwyg_float(char.get("original_filter_brightness", 0.0), 0.0)
        char["filter_contrast"] = wysiwyg_float(char.get("original_filter_contrast", 1.0), 1.0)
        char["filter_saturation"] = wysiwyg_float(char.get("original_filter_saturation", 1.0), 1.0)
        char["filter_hue"] = wysiwyg_float(char.get("original_filter_hue", 0.0), 0.0)
        char["filter_invert"] = wysiwyg_float(char.get("original_filter_invert", 0.0), 0.0)
        char["filter_sepia"] = bool(char.get("original_filter_sepia", False))
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        wysiwyg_set_status("Reset selected color filters.")

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
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
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
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

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
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()
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
            store.wysiwyg_rotation_input_tag = tag
            store.wysiwyg_rotation_input = wysiwyg_fmt_float(char[key], 1)
        elif key == "alpha":
            char[key] = round(wysiwyg_clamp(current, 0.0, 1.0), 3)

        
        store.wysiwyg_selected_tag = tag
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

    def wysiwyg_release_transform_slider(tag, key):
        char = wysiwyg_find_char(tag)
        if not char:
            return
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
            store.wysiwyg_code = wysiwyg_build_code()
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

    def wysiwyg_rotation_input_text(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            store.wysiwyg_rotation_input_tag = None
            store.wysiwyg_rotation_input = ""
            return ""
        current_text = wysiwyg_fmt_float(char.get("rotate", 0.0), 1)
        if store.wysiwyg_rotation_input_tag != tag:
            store.wysiwyg_rotation_input_tag = tag
            store.wysiwyg_rotation_input = current_text
        return store.wysiwyg_rotation_input

    def wysiwyg_apply_rotation_input(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        text = str(store.wysiwyg_rotation_input or "").strip().replace(",", ".")
        if not text:
            store.wysiwyg_rotation_input = wysiwyg_fmt_float(char.get("rotate", 0.0), 1)
            wysiwyg_set_status("Rotation value cannot be empty.")
            return
        value = wysiwyg_float(text, None)
        if value is None:
            store.wysiwyg_rotation_input = wysiwyg_fmt_float(char.get("rotate", 0.0), 1)
            wysiwyg_set_status("Rotation must be a number.")
            return
        wysiwyg_set_char_transform(tag, "rotate", value)
        store.wysiwyg_rotation_input_tag = tag
        store.wysiwyg_rotation_input = wysiwyg_fmt_float(wysiwyg_find_char(tag).get("rotate", 0.0), 1)

    def wysiwyg_reset_selected_transform():
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character to reset.")
            return
        wysiwyg_push_undo(char)
        char["rotate"] = wysiwyg_float(char.get("original_rotate", 0.0), 0.0)
        char["xzoom"] = wysiwyg_float(char.get("original_xzoom", 1.0), 1.0)
        char["yzoom"] = wysiwyg_float(char.get("original_yzoom", 1.0), 1.0)
        char["alpha"] = wysiwyg_float(char.get("original_alpha", 1.0), 1.0)
        wysiwyg_update_char_size(char)
        store.wysiwyg_transform_memory[char.get("tag") + ":rotate"] = char["rotate"]
        store.wysiwyg_transform_memory[char.get("tag") + ":xzoom"] = char["xzoom"]
        store.wysiwyg_transform_memory[char.get("tag") + ":yzoom"] = char["yzoom"]
        store.wysiwyg_transform_memory[char.get("tag") + ":alpha"] = char["alpha"]
        store.wysiwyg_saved_runtime = False
        store.wysiwyg_rotation_input_tag = char.get("tag")
        store.wysiwyg_rotation_input = wysiwyg_fmt_float(char.get("rotate", 0.0), 1)
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        wysiwyg_set_status("Reset selected character transform.")

    def wysiwyg_reset_selected_transform_to_defaults():
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
            wysiwyg_set_status("No selected character to reset.")
            return
        wysiwyg_push_undo(char)
        char["rotate"] = 0.0
        char["xzoom"] = 1.0
        char["yzoom"] = 1.0
        char["alpha"] = 1.0
        wysiwyg_update_char_size(char)
        store.wysiwyg_transform_memory[char.get("tag") + ":rotate"] = 0.0
        store.wysiwyg_transform_memory[char.get("tag") + ":xzoom"] = 1.0
        store.wysiwyg_transform_memory[char.get("tag") + ":yzoom"] = 1.0
        store.wysiwyg_transform_memory[char.get("tag") + ":alpha"] = 1.0
        store.wysiwyg_saved_runtime = False
        store.wysiwyg_rotation_input_tag = char.get("tag")
        store.wysiwyg_rotation_input = "0"
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        wysiwyg_set_status("Reset selected character transform to defaults.")

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
            wysiwyg_set_status("Already on Ren'Py " + str(target_name) + ".")
            return

        wysiwyg_push_undo(char)
        char["x"] = adjusted_x
        char["anchor_x"] = adjusted_x
        char["parsed_center_x"] = cx
        char["parsed_x"] = True
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        wysiwyg_set_status("Placed on Ren'Py " + str(target_name) + ".")

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

        for node in getattr(renpy.game.script, "all_stmts", []):
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

        # Prefer the statement that actually executed most recently.
        for key in reversed(wysiwyg_executed_lines()):
            if key in candidates:
                best_node, best_node_image = candidates[key]
                break

        if not best_node:
            return None

        zorder_val = None
        behind = []
        imspec = getattr(best_node, "imspec", None)
        if imspec:
            if len(imspec) >= 6 and imspec[5] is not None:
                try:
                    zorder_val = int(str(imspec[5]))
                except Exception:
                    zorder_val = None
            if len(imspec) == 7:
                behind = [str(i) for i in (imspec[6] or [])]

        return {
            "key": tag,
            "tag": tag,
            "image": best_node_image or image_name or tag,
            "runtime_image": best_node_image or image_name or tag,
            "source_file": best_node.filename,
            "source_line": best_node.linenumber,
            "zorder": zorder_val,
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
        # render within a couple of pixels — in that case the parsed integers
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
        had_existing_import = bool(store.wysiwyg_chars or store.wysiwyg_bg)

        if store.wysiwyg_chars or store.wysiwyg_bg:
            wysiwyg_restore_imported_preview()

        store.wysiwyg_saved_runtime = False
        store.wysiwyg_bg = None
        store.wysiwyg_bg_runtime = None
        store.wysiwyg_bg_source = None
        store.wysiwyg_chars = []
        store.wysiwyg_transform_memory = {}
        store.wysiwyg_rotation_input_tag = None

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
            store.wysiwyg_bg_runtime = image_name
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
                    store.wysiwyg_bg_runtime = image_name
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
                    "source_file": "",
                    "source_line": 0,
                    "zorder": None,
                    "behind": [],
                    "unsaved": True,
                }

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
            wysiwyg_log_debug("[IMPORT-SRC] tag={0} bounds={1} center=({2},{3}) source={4}:{5} line={6!r}".format(
                tag, bounds, center_x, center_y, data.get("source_file"), data.get("source_line"),
                wysiwyg_source_line_text(data.get("source_file", ""), data.get("source_line", 0))
            ))
            chars.append(data)
            imported += 1

        if chars and not had_existing_import:
            store.wysiwyg_chars = chars
            store.wysiwyg_selected_tag = chars[0].get("tag")
            wysiwyg_restore_imported_preview()
            wysiwyg_refresh_char_bounds(chars, write_original=True)

        for char in chars:
            try:
                renpy.hide(char.get("tag"), layer="master")
            except Exception:
                pass

        store.wysiwyg_chars = chars
        store.wysiwyg_selected_tag = chars[0].get("tag") if chars else None

        if imported or bg_seen:
            wysiwyg_set_status("Imported " + str(imported) + " character(s) from exact source lines.")
        else:
            wysiwyg_set_status("No editable scene/show lines found. Advance the scene, then press Import Scene.")

    def wysiwyg_position_line_for_char(char):
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
        line = "show " + char.get("image", char.get("tag", "")) + " at " + ", ".join(at_parts)
        behind = char.get("behind") or []
        if behind:
            line += " behind " + ", ".join([str(i) for i in behind])
        zorder_val = char.get("zorder")
        if zorder_val is not None:
            line += " zorder " + str(int(zorder_val))
        return line

    def wysiwyg_scene_line():
        if not store.wysiwyg_bg:
            return None
        return "scene " + store.wysiwyg_bg

    def wysiwyg_scriptedit_replace(filename, line, code):
        # Apply the execution replace_node monkeypatch to avoid LabelNotFound exceptions
        try:
            import renpy.execution
            if not getattr(renpy.execution.Context, "_wysiwyg_patched", False):
                orig_replace_node = renpy.execution.Context.replace_node
                def patched_replace_node(self, old, new):
                    def replace_one(name):
                        try:
                            n = renpy.game.script.lookup(name)
                            if n is old:
                                return new.name
                        except Exception:
                            pass
                        return name
                    self.current = replace_one(self.current)
                    self.return_stack = [replace_one(i) for i in self.return_stack]
                renpy.execution.Context.replace_node = patched_replace_node
                renpy.execution.Context._wysiwyg_patched = True
        except Exception:
            pass

        filename = filename.replace("\\", "/")
        line = int(line)
        wysiwyg_backup_source(filename)
        try:
            renpy.set_autoreload(False)
        except Exception:
            pass
        renpy.scriptedit.add_to_ast_before(code, filename, line)
        renpy.scriptedit.insert_line_before(code, filename, line)
        renpy.scriptedit.remove_from_ast(filename, line + 1)
        renpy.scriptedit.remove_line(filename, line + 1)



    def wysiwyg_restore_imported_preview():
        bg_image = store.wysiwyg_bg_runtime or store.wysiwyg_bg
        if bg_image:
            try:
                renpy.scene(layer="master")
                renpy.show(bg_image, layer="master")
            except Exception:
                pass

        for char in store.wysiwyg_chars:
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
                renpy.show(image_name, at_list=at_list, layer="master", zorder=int(char.get("zorder") or 0))
            except Exception:
                try:
                    renpy.show(image_name, layer="master")
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
    def wysiwyg_save_changes():
        changed = 0
        errors = []

        for char in store.wysiwyg_chars:
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

        if store.wysiwyg_bg and store.wysiwyg_bg_source:
            try:
                wysiwyg_scriptedit_replace(store.wysiwyg_bg_source["file"], store.wysiwyg_bg_source["line"], wysiwyg_scene_line())
                changed += 1
            except Exception as exc:
                errors.append("background: " + str(exc))

        for char in store.wysiwyg_chars:
            try:
                if not char.get("source_file") or not char.get("source_line"):
                    errors.append(char.get("tag", "?") + ": no source line")
                    continue
                line_to_write = wysiwyg_position_line_for_char(char)
                wysiwyg_log_debug("[SAVE] tag={0} source={1}:{2} code={3}".format(
                    char.get("tag"), char.get("source_file"), char.get("source_line"), line_to_write
                ))
                wysiwyg_scriptedit_replace(char["source_file"], char["source_line"], line_to_write)
                changed += 1
            except Exception as exc:
                errors.append(char.get("tag", "?") + ": " + str(exc))

        if changed:
            for char in store.wysiwyg_chars:
                char["original_x"] = wysiwyg_float(char.get("x", 0.0), 0.0)
                char["original_y"] = wysiwyg_float(char.get("y", 0.0), 0.0)
                char["original_anchor_x"] = wysiwyg_float(char.get("anchor_x", char.get("x", 0.0)), 0.0)
                char["original_anchor_y"] = wysiwyg_float(char.get("anchor_y", char.get("y", 0.0)), 0.0)
                char["original_rotate"] = wysiwyg_float(char.get("rotate", 0.0), 0.0)
                char["original_xzoom"] = wysiwyg_float(char.get("xzoom", 1.0), 1.0)
                char["original_yzoom"] = wysiwyg_float(char.get("yzoom", 1.0), 1.0)
                char["original_alpha"] = wysiwyg_float(char.get("alpha", 1.0), 1.0)
                char["original_filter_blur"] = wysiwyg_float(char.get("filter_blur", 0.0), 0.0)
                char["original_filter_brightness"] = wysiwyg_float(char.get("filter_brightness", 0.0), 0.0)
                char["original_filter_contrast"] = wysiwyg_float(char.get("filter_contrast", 1.0), 1.0)
                char["original_filter_saturation"] = wysiwyg_float(char.get("filter_saturation", 1.0), 1.0)
                char["original_filter_hue"] = wysiwyg_float(char.get("filter_hue", 0.0), 0.0)
                char["original_filter_invert"] = wysiwyg_float(char.get("filter_invert", 0.0), 0.0)
                char["original_filter_sepia"] = bool(char.get("filter_sepia", False))
                char["original_motion_fx"] = str(char.get("motion_fx", "none") or "none").strip().lower()
                char["original_motion_fx_strength"] = wysiwyg_clamp(wysiwyg_float(char.get("motion_fx_strength", 1.0), 1.0), 0.0, 2.0)
                char["original_parsed_center_x"] = char.get("parsed_center_x", char.get("x", 0.0) + wysiwyg_float(char.get("w", 0.0), 0.0) / 2.0)
                char["original_parsed_center_y"] = char.get("parsed_center_y", char.get("y", 0.0) + wysiwyg_float(char.get("h", 0.0), 0.0) / 2.0)
            store.wysiwyg_saved_runtime = True

        if errors:
            wysiwyg_set_status("Saved " + str(changed) + " line(s), errors: " + "; ".join(errors[:2]))
        elif changed:
            wysiwyg_set_status("Saved " + str(changed) + " exact source line(s). Backup files end with .wysiwyg.bak.")
        else:
            wysiwyg_set_status("Nothing to save. Import Scene first.")

    def wysiwyg_on_drag(drags, drop):
        if not drags:
            return
        drag = drags[0]
        tag = drag.drag_name
        char = wysiwyg_find_char(tag)
        if char:
            store.wysiwyg_selected_tag = tag
            old_x = int(char.get("x", 0))
            old_y = int(char.get("y", 0))
            
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
            
            if old_x != new_x or old_y != new_y:
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
            store.wysiwyg_pos_input_tag = None
            if store.wysiwyg_panel == "code":
                store.wysiwyg_code = wysiwyg_build_code()
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
                store.wysiwyg_rotation_input_tag = None
                store.wysiwyg_saved_runtime = False
                if store.wysiwyg_panel == "code":
                    store.wysiwyg_code = wysiwyg_build_code()
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
        store.wysiwyg_pos_input_tag = None
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

    def wysiwyg_adjust_zorder(tag, delta):
        char = wysiwyg_find_char(tag)
        if not char:
            return
        current = char.get("zorder")
        current = int(current) if current is not None else 0
        char["zorder"] = current + int(delta)
        store.wysiwyg_selected_tag = tag
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        renpy.restart_interaction()

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
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        if not char:
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
        renpy.restart_interaction()

    def wysiwyg_cancel_edit():
        store.wysiwyg_edit_field = None
        store.wysiwyg_edit_buffer = ""
        renpy.restart_interaction()

    def wysiwyg_commit_edit():
        field = store.wysiwyg_edit_field
        char = wysiwyg_find_char(store.wysiwyg_selected_tag)
        store.wysiwyg_edit_field = None
        if not char or not field:
            renpy.restart_interaction()
            return
        text = str(store.wysiwyg_edit_buffer or "").strip()
        tag = char.get("tag")

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
            if store.wysiwyg_panel == "code":
                store.wysiwyg_code = wysiwyg_build_code()
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
        renpy.restart_interaction()

    def wysiwyg_pos_input_sync(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            store.wysiwyg_pos_input_tag = None
            store.wysiwyg_pos_input_x = ""
            store.wysiwyg_pos_input_y = ""
            return "", ""
        if store.wysiwyg_pos_input_tag != tag:
            store.wysiwyg_pos_input_tag = tag
            w = wysiwyg_float(char.get("w", 0.0), 0.0)
            h = wysiwyg_float(char.get("h", 0.0), 0.0)
            store.wysiwyg_pos_input_x = str(int(round(wysiwyg_float(char.get("x", 0.0), 0.0) + w / 2.0)))
            store.wysiwyg_pos_input_y = str(int(round(wysiwyg_float(char.get("y", 0.0), 0.0) + h / 2.0)))
        return store.wysiwyg_pos_input_x, store.wysiwyg_pos_input_y

    def wysiwyg_apply_pos_input(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character.")
            return
        try:
            cx = int(str(store.wysiwyg_pos_input_x or "").strip().replace(",", "."))
            cy = int(str(store.wysiwyg_pos_input_y or "").strip().replace(",", "."))
        except Exception:
            store.wysiwyg_pos_input_tag = None
            wysiwyg_set_status("Center position must be whole numbers.")
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
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        wysiwyg_set_status("Moved center to " + str(cx) + ", " + str(cy) + ".")

    def wysiwyg_reset_position_for(tag):
        char = wysiwyg_find_char(tag)
        if not char:
            wysiwyg_set_status("No selected character to reset.")
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
        store.wysiwyg_pos_input_tag = None
        store.wysiwyg_saved_runtime = False
        if store.wysiwyg_panel == "code":
            store.wysiwyg_code = wysiwyg_build_code()
        wysiwyg_set_status("Reset selected character to imported position.")

    def wysiwyg_reset_selected_position():
        wysiwyg_reset_position_for(store.wysiwyg_selected_tag)

    def wysiwyg_reset_editor():
        if store.wysiwyg_chars or store.wysiwyg_bg:
            wysiwyg_restore_imported_preview()
        store.wysiwyg_bg = None
        store.wysiwyg_bg_runtime = None
        store.wysiwyg_bg_source = None
        store.wysiwyg_chars = []
        store.wysiwyg_selected_tag = None
        store.wysiwyg_undo_stack = []
        store.wysiwyg_transform_memory = {}
        store.wysiwyg_saved_runtime = False
        wysiwyg_set_status("Editor cleared. Unsaved moves were discarded.")

    def wysiwyg_clear_editor_state():
        store.wysiwyg_bg = None
        store.wysiwyg_bg_runtime = None
        store.wysiwyg_bg_source = None
        store.wysiwyg_chars = []
        store.wysiwyg_selected_tag = None
        store.wysiwyg_undo_stack = []
        store.wysiwyg_transform_memory = {}
        store.wysiwyg_saved_runtime = False
        store.wysiwyg_show_code = False
        store.wysiwyg_code = ""
        store.wysiwyg_rotation_input = ""
        store.wysiwyg_rotation_input_tag = None
        store.wysiwyg_pos_input_x = ""
        store.wysiwyg_pos_input_y = ""
        store.wysiwyg_pos_input_tag = None
        store.wysiwyg_char_page = "main"

    def wysiwyg_toggle():
        if store.wysiwyg_active:
            if store.wysiwyg_chars or store.wysiwyg_bg:
                wysiwyg_restore_imported_preview()
            wysiwyg_clear_editor_state()
            # Re-enable skipping that was disabled while the editor was open.
            config.allow_skipping = getattr(store, "_wysiwyg_prev_allow_skipping", True)
        else:
            wysiwyg_clear_editor_state()
            store.wysiwyg_status = "Editor opened. Click Import Scene to track the current scene."
            # While editing, nothing may advance the game: Ctrl-skip included.
            store._wysiwyg_prev_allow_skipping = getattr(config, "allow_skipping", True)
            config.allow_skipping = False
            try:
                config.skipping = None
            except Exception:
                pass
        store.wysiwyg_active = not store.wysiwyg_active
        renpy.restart_interaction()

    def wysiwyg_build_code():
        lines = ["# Generated by WYSIWYG Scene Editor " + WYSIWYG_VERSION]
        if store.wysiwyg_bg:
            lines.append(wysiwyg_scene_line())
        for char in store.wysiwyg_chars:
            lines.append(wysiwyg_position_line_for_char(char))
        if len(lines) == 1:
            lines.append("# Nothing imported yet. Click Import Scene first.")
        return "\n".join(lines)

    def wysiwyg_toggle_code_panel():
        if store.wysiwyg_panel == "code":
            store.wysiwyg_panel = "characters"
            renpy.restart_interaction()
            return
        store.wysiwyg_code = wysiwyg_build_code()
        store.wysiwyg_show_code = True
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

    style wysiwyg_guide_text is wysiwyg_small_text
    style wysiwyg_guide_text:
        color "#ffffff"
        outlines [(1, "#000000cc", 0, 0)]

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
    key "K_F5" action Function(wysiwyg_toggle)
    if wysiwyg_active:
        key "K_h" action NullAction()
        use wysiwyg_main

# Main overlay: character previews + drag handle, toolbar, side panel.
# Non-selected characters are drawn as plain `add`s (sorted by zorder);
# the selected one sits inside a drag whose geometry matches the renderer
# (see wysiwyg_render_box / wysiwyg_drag_pos).
screen wysiwyg_main():
    modal True
    zorder 250
    key "dismiss" action NullAction()
    key "rollback" action NullAction()
    key "rollforward" action NullAction()
    key "skip" action NullAction()
    key "stop_skipping" action NullAction()
    key "toggle_skip" action NullAction()
    key "fast_skip" action NullAction()
    on "show" action Function(wysiwyg_hide_master_chars)
    on "replace" action Function(wysiwyg_hide_master_chars)
    $ _selected_drag_char = wysiwyg_find_char(wysiwyg_selected_tag) if wysiwyg_selected_tag else None
    if _selected_drag_char and _selected_drag_char.get("preview_hidden"):
        $ _selected_drag_char = None

    if wysiwyg_grid:
        use wysiwyg_grid_overlay

    if _selected_drag_char:
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
        if _wch.get("tag") != wysiwyg_selected_tag and not _wch.get("preview_hidden"):
            $ _wch_cx = int(round(_wch.get("x") + _wch.get("w") / 2.0))
            $ _wch_cy = int(round(_wch.get("y") + _wch.get("h") / 2.0))
            if wysiwyg_motion_fx_uses_placement(_wch):
                add wysiwyg_preview_displayable(_wch, xpos=_wch_cx, ypos=_wch_cy) at wysiwyg_motion_fx_placement_transform(_wch)
            else:
                add wysiwyg_preview_displayable(_wch, xpos=_wch_cx, ypos=_wch_cy)

    if _selected_drag_char:
        $ _sel_cx = int(round(_selected_drag_char.get("x") + _selected_drag_char.get("w") / 2.0))
        $ _sel_cy = int(round(_selected_drag_char.get("y") + _selected_drag_char.get("h") / 2.0))
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
                if wysiwyg_motion_fx_uses_placement(_selected_drag_char):
                    add wysiwyg_preview_displayable(_selected_drag_char, xpos=_sel_box_w // 2, ypos=_sel_box_h // 2) at wysiwyg_motion_fx_placement_transform(_selected_drag_char)
                else:
                    add wysiwyg_preview_displayable(_selected_drag_char, xpos=_sel_box_w // 2, ypos=_sel_box_h // 2)



    for _wch in wysiwyg_chars:
        if _wch.get("tag") != wysiwyg_selected_tag and not _wch.get("preview_hidden"):
            frame:
                background Solid("#000000aa")
                padding (8, 4)
                xpos int(_wch.get("x", 0))
                ypos max(0, int(_wch.get("y", 0)) - 28)
                text wysiwyg_ui_text(wysiwyg_char_label(_wch.get("tag"))) color wysiwyg_char_color(_wch.get("tag")) style "wysiwyg_small_text"

    frame:
        style "wysiwyg_toolbar_frame"
        xfill True
        hbox:
            spacing 8
            textbutton "Characters" style "wysiwyg_button" action SetVariable("wysiwyg_panel", "characters") selected (wysiwyg_panel == "characters")
            textbutton "UI" style "wysiwyg_button" action NullAction() sensitive False
            textbutton "Text" style "wysiwyg_button" action NullAction() sensitive False
            null width 20
            textbutton "Import Scene" style "wysiwyg_button" action Function(wysiwyg_import_scene)
            textbutton ("Save Changes" + (" ●" if (wysiwyg_chars and not wysiwyg_saved_runtime) else "")) style "wysiwyg_button" action Function(wysiwyg_save_changes)
            textbutton "Undo" style "wysiwyg_button" action Function(wysiwyg_undo_move)
            textbutton "Show Code" style "wysiwyg_button" action Function(wysiwyg_toggle_code_panel) selected (wysiwyg_panel == "code")
            textbutton "Grid" style "wysiwyg_button" action ToggleVariable("wysiwyg_grid") selected wysiwyg_grid
            textbutton "Clear Editor" style "wysiwyg_danger_button" action Function(wysiwyg_reset_editor)
            textbutton "Close" style "wysiwyg_danger_button" action Function(wysiwyg_toggle)

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

    if wysiwyg_status:
        frame:
            background Solid("#000000aa")
            padding (10, 6)
            xalign 0.0
            yalign 1.0
            text wysiwyg_ui_text(wysiwyg_status) style "wysiwyg_small_text"

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
        text ("On Scene (" + str(len(wysiwyg_chars)) + ")") style "wysiwyg_title_text"
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
                            hbox:
                                spacing 4
                                textbutton wysiwyg_ui_text(wysiwyg_char_label(_w_tag)) style "wysiwyg_button" xsize int(170 * _ui_s) action SetVariable("wysiwyg_selected_tag", _w_tag) selected (_w_tag == wysiwyg_selected_tag)
                                textbutton ("Show" if _wch.get("preview_hidden") else "Hide") style "wysiwyg_button" xminimum 56 action Function(wysiwyg_toggle_preview_hidden, _w_tag)
                                textbutton "Reset" style "wysiwyg_button" xminimum 56 action Function(wysiwyg_reset_position_for, _w_tag)
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
                        text wysiwyg_ui_text(_selected_char.get("runtime_image") or _selected_char.get("image")) style "wysiwyg_text"
                        if not wysiwyg_saved_runtime:
                            text "●" color "#ffb347" style "wysiwyg_text"
                    hbox:
                        spacing 8
                        if wysiwyg_edit_field == "pos":
                            frame:
                                background Solid("#101418cc")
                                padding (6, 2)
                                xsize 150
                                input value VariableInputValue("wysiwyg_edit_buffer") length 14 pixel_width 138 style "wysiwyg_small_text"
                            textbutton "OK" style "wysiwyg_button" xminimum 40 action Function(wysiwyg_commit_edit)
                            key "K_RETURN" action Function(wysiwyg_commit_edit)
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

                        if wysiwyg_char_page == "color":
                            textbutton "Reset filters to defaults" style "wysiwyg_button" action Function(wysiwyg_reset_selected_color_filters_to_defaults)
                            null height 2
                            text ("Blur: " + str(int(wysiwyg_float(_selected_char.get("filter_blur", 0.0), 0.0))) + " px") style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                bar value DictValue(_selected_char, "filter_blur", 20.0, step=1.0, action=Function(wysiwyg_on_color_filter_change, _sel_tag)) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5
                                textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, _sel_tag, "filter_blur")
                            text ("Brightness: " + wysiwyg_fmt_float(wysiwyg_float(_selected_char.get("filter_brightness", 0.0), 0.0), 2)) style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                bar value DictValue(_selected_char, "filter_brightness", 2.0, offset=-1.0, step=0.01, action=Function(wysiwyg_on_color_filter_change, _sel_tag)) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5
                                textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, _sel_tag, "filter_brightness")
                            text ("Contrast: " + wysiwyg_fmt_float(wysiwyg_float(_selected_char.get("filter_contrast", 1.0), 1.0), 2)) style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                bar value DictValue(_selected_char, "filter_contrast", 2.0, step=0.01, action=Function(wysiwyg_on_color_filter_change, _sel_tag)) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5
                                textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, _sel_tag, "filter_contrast")
                            text ("Saturation: " + wysiwyg_fmt_float(wysiwyg_float(_selected_char.get("filter_saturation", 1.0), 1.0), 2)) style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                bar value DictValue(_selected_char, "filter_saturation", 2.0, step=0.01, action=Function(wysiwyg_on_color_filter_change, _sel_tag)) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5
                                textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, _sel_tag, "filter_saturation")
                            text ("Hue: " + wysiwyg_fmt_float(wysiwyg_float(_selected_char.get("filter_hue", 0.0), 0.0), 1) + " deg") style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                bar value DictValue(_selected_char, "filter_hue", 360.0, offset=-180.0, step=1.0, action=Function(wysiwyg_on_color_filter_change, _sel_tag)) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5
                                textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, _sel_tag, "filter_hue")
                            text ("Invert: " + wysiwyg_fmt_float(wysiwyg_float(_selected_char.get("filter_invert", 0.0), 0.0), 2)) style "wysiwyg_small_text"
                            hbox:
                                spacing 6
                                bar value DictValue(_selected_char, "filter_invert", 1.0, step=0.01, action=Function(wysiwyg_on_color_filter_change, _sel_tag)) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5
                                textbutton "↺" style "wysiwyg_button" xminimum 36 action Function(wysiwyg_reset_selected_color_filter_key, _sel_tag, "filter_invert")
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
                            text "Arrow keys move too (Shift = 10px)." style "wysiwyg_small_text"
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
                                    frame:
                                        background Solid("#101418cc")
                                        padding (6, 2)
                                        xsize 90
                                        input value VariableInputValue("wysiwyg_edit_buffer") length 8 pixel_width 78 style "wysiwyg_small_text"
                                    textbutton "OK" style "wysiwyg_button" xminimum 40 action Function(wysiwyg_commit_edit)
                                    key "K_RETURN" action Function(wysiwyg_commit_edit)
                                else:
                                    textbutton (wysiwyg_fmt_float(_selected_char.get("rotate", 0.0), 1) + "°") style "wysiwyg_button" xminimum 70 action Function(wysiwyg_begin_edit, "rot") tooltip "Click to type exact angle"
                                bar value DictValue(_selected_char, "rotate", 360.0, offset=-180.0, step=1.0, action=Function(wysiwyg_drag_transform_slider, _sel_tag, "rotate")) style "wysiwyg_slider" xsize int(240 * _ui_s) yalign 0.5 released Function(wysiwyg_release_transform_slider, _sel_tag, "rotate")

                            null height 2
                            text "Scale" style "wysiwyg_text" bold True
                            hbox:
                                spacing 6
                                if wysiwyg_edit_field == "scale":
                                    frame:
                                        background Solid("#101418cc")
                                        padding (6, 2)
                                        xsize 90
                                        input value VariableInputValue("wysiwyg_edit_buffer") length 8 pixel_width 78 style "wysiwyg_small_text"
                                    textbutton "OK" style "wysiwyg_button" xminimum 40 action Function(wysiwyg_commit_edit)
                                    key "K_RETURN" action Function(wysiwyg_commit_edit)
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
                vbar value YScrollValue("wys_char_controls") style "wysiwyg_vbar"
        else:
            frame:
                background Solid("#00000066")
                padding (8, 8)
                xfill True
                text "Select a character from On Scene." style "wysiwyg_small_text"

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
            hbox:
                spacing 10
                frame:
                    background Solid("#00000066")
                    padding (8, 8)
                    xsize 180
                    yfill True
                    vbox:
                        spacing 6
                        text "Original source" style "wysiwyg_text"
                        text "What is currently in the .rpy file." style "wysiwyg_small_text"
                        if wysiwyg_bg_source:
                            text "Scene" style "wysiwyg_small_text"
                            text wysiwyg_ui_text(wysiwyg_bg_source.get("file") + ":" + str(wysiwyg_bg_source.get("line"))) style "wysiwyg_small_text"
                            text wysiwyg_ui_text(wysiwyg_source_line_text(wysiwyg_bg_source.get("file"), wysiwyg_bg_source.get("line"))) style "wysiwyg_small_text" xsize 160
                        elif wysiwyg_bg:
                            text "Scene" style "wysiwyg_small_text"
                            text "No tracked background source line." style "wysiwyg_small_text"
                        for _wch in wysiwyg_chars:
                            $ _selected = (_wch.get("tag") == wysiwyg_selected_tag)
                            $ _card_bg = Solid("#2d6f9588") if _selected else Solid("#00000044")
                            frame:
                                background _card_bg
                                padding (6, 6)
                                xfill True
                                vbox:
                                    spacing 4
                                    text wysiwyg_ui_text(_wch.get("image", "")) style "wysiwyg_small_text"
                                    if _wch.get("source_file"):
                                        text wysiwyg_ui_text(_wch.get("source_file") + ":" + str(_wch.get("source_line"))) style "wysiwyg_small_text"
                                        text wysiwyg_ui_text(wysiwyg_source_line_text(_wch.get("source_file"), _wch.get("source_line"))) style "wysiwyg_small_text" xsize 148
                                    else:
                                        text "No tracked show source line." style "wysiwyg_small_text"
                frame:
                    background Solid("#00000066")
                    padding (8, 8)
                    xsize 180
                    yfill True
                    vbox:
                        spacing 6
                        text "Generated code" style "wysiwyg_text"
                        text "What Save Changes will write back." style "wysiwyg_small_text"
                        if wysiwyg_bg:
                            text "Scene" style "wysiwyg_small_text"
                            text wysiwyg_ui_text(wysiwyg_scene_line()) style "wysiwyg_small_text" xsize 160
                        for _wch in wysiwyg_chars:
                            $ _selected = (_wch.get("tag") == wysiwyg_selected_tag)
                            $ _card_bg = Solid("#2d6f9588") if _selected else Solid("#00000044")
                            frame:
                                background _card_bg
                                padding (6, 6)
                                xfill True
                                vbox:
                                    spacing 4
                                    text wysiwyg_ui_text(_wch.get("image")) style "wysiwyg_small_text"
                                    text wysiwyg_ui_text(wysiwyg_position_line_for_char(_wch)) style "wysiwyg_small_text" xsize 148

init 999 python:
    wysiwyg_init()
    if "wysiwyg_hotkey" not in config.overlay_screens:
        config.overlay_screens.append("wysiwyg_hotkey")
