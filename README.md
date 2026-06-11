# Ren'Py Drag-and-Drop WYSIWYG Editor (v0.2.0)

A powerful, single-file overlay tool for Ren'Py games that allows creators to arrange, rotate, scale, animate, and filter sprites visually in real-time, writing the code directly back to the `.rpy` source files.

No more guess-and-test positioning or editing coordinates in your text editor. Just hit **F5**, drag your characters to the perfect spots, tweak their properties, and save.

---

## Screenshots

### 1. Initial Launch Overlay (F5)
![Initial Launch](assets/editor_menu_01.jpg)
*The clean editor overlay immediately after launching it with the F5 key.*

### 2. Main Interface (After Scene Import)
![Main Interface](assets/editor_menu_02.jpg)
*The main panel displaying imported sprites, character lists, and basic layout buttons.*

### 3. Color Filters Menu in Action
![Color Filters Menu](assets/editor_menu_03.jpg)
*Real-time rendering of filters including blur, brightness, contrast, saturation, hue shift, inversion, and sepia.*

### 4. Motion FX Animations
![Motion FX Menu](assets/editor_menu_04.jpg)
*Configuring dynamic animations like breathe, shake, float, sway, bounce, sink, and blink with custom strengths.*

### 5. Advanced Transformations (Rotation & Scaling)
![Transformations](assets/editor_menu_05.jpg)
*Demonstration of precise rotation adjustments, linked/unlinked scaling, and image flipping.*

---

## Key Features

- **Live Drag-and-Drop Layout**: Click and drag any sprite on the screen to reposition it instantly.
- **Direct Source Code Rewriting**: When you click **Save Changes**, the editor locates the exact `show` or `scene` statements that rendered the active sprites (using Ren'Py's execution line log `config.line_log`) and rewrites the parameters in-place inside your `.rpy` files using `renpy.scriptedit`.
- **Automatic Backups**: Creates a `.wysiwyg.bak` backup file of any edited script before modifying it, ensuring your work is always safe.
- **Bypasses Default Anchors**: Grabs live bounds via `renpy.get_image_bounds`. The parsed source line is only trusted if it matches the live render within 2 pixels, making the editor work in any game regardless of its custom anchors or menu branches.
- **Center-Based Anchoring**: Saves lines in the format `show TAG at Transform(xpos=CX, ypos=CY, xanchor=0.5, yanchor=0.5, ...)`. Center anchors are invariant under rotation and scaling, and explicit anchors prevent issues with default game configurations.
- **Rotated Bounding Box Match**: The drag container matches the renderer's exact rotated bounding box (incorporating `rotate_pad=True` calculations and integer clipping) to avoid 1px shifting bugs when saving.
- **Virtual Resolution Scaling**: Designed at 1080p, the editor UI automatically scales using `config.screen_height / 1080.0`, keeping it proportional at any resolution (e.g. 720p or 4K).
- **Core Transform Controls**:
  - Horizontal/Vertical flipping (`xzoom`/`yzoom`).
  - Linked/Unlinked scale locking.
  - Rotation slider & manual degree input.
  - Opacity/Alpha slider.
  - Layout presets (At Left, At Center, At Right).
- **Color Filters**: Apply live filters including Blur (px), Brightness, Contrast, Saturation, Hue rotation, Inversion, and Sepia matrices using `matrixcolor`.
- **Motion FX Animations**: Toggle built-in animated effects (breathe, shake, float, sway, bounce, sink, blink) with adjustable strength.
- **Grid & Alignment Snapping**:
  - Snap sprites to Left (25%), Center (50%), Right (75%), Top, or Bottom screen lines.
  - Toggleable grid overlay with 100px steps.
- **Undo Stack**: Maintains a history of up to 50 operations.
- **Code Compare Panel**: View your original source lines side-by-side with the generated code before writing to disk.

---

## Installation

1. Copy the file `wysiwyg_editor.rpy` into the `game/` directory of your Ren'Py project.
2. Launch your game.
3. The editor is now integrated and ready to use!

---

## Usage Guide

1. Play your game until you reach a scene containing sprites you want to edit.
2. Press **F5** on your keyboard to open the editor overlay.
3. Click **Import Scene** to read the characters currently shown on screen.
4. Select a character from the **On Scene** list or click them directly.
5. Drag the sprite to your desired position. You can also:
   - Use the **arrow keys** on your keyboard to nudge the sprite by 1px (or hold **Shift** to nudge by 10px).
   - Go to **Base Controls** to adjust rotation, scale, opacity, or flip the character.
   - Go to **Color Filters** to add blurs, brightness/contrast adjustments, or hue shifts.
   - Go to **Motion FX** to apply animations.
6. Click **Show Code** to compare your changes with the original code.
7. Click **Save Changes** to write the updated coordinates directly to your `.rpy` files.
8. Click **Close** (or press **F5**) to exit the editor. Unsaved changes will be discarded, restoring the scene to its initial state.

---

## Technical Notes

### Viewport Vertical Scrollbar Workaround
Ren'Py 8.5.x has a known bug where `scrollbars "vertical"` inside a `viewport` silently breaks child rendering or shows an empty viewport. This editor bypasses the issue entirely by using a separate `vbar` component mapped to `YScrollValue`, combined with `mousewheel True` and `draggable True` on the viewport.

### File Backups
Before modifying any file, the editor automatically creates a backup copy with a `.wysiwyg.bak` extension in the same folder. 

> [!IMPORTANT]
> This is a **one-time baseline backup**. The editor will **never overwrite** an existing `.wysiwyg.bak` file, meaning it permanently preserves the original state of your code from before the editor touched it. 
> 
> If you make manual code changes in an external editor and want to refresh this baseline backup, simply delete the old `.wysiwyg.bak` file. A new one will be generated automatically on your next save.

To restore an original file, replace the modified `.rpy` file with the corresponding `.bak` backup.

---

## License

This project is open-source and free to use in any personal or commercial Ren'Py game.
