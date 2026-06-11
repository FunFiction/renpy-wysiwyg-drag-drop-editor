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

### 6. Code Compare Panel (Show Code)
![Code Compare](assets/editor_menu_06.jpg)
*Side-by-side comparison of the original script lines vs. the newly generated code before writing to disk.*

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
- **Grid Overlay**:
  - Toggleable background grid overlay with 100px steps to help with visual alignment.
- **Undo Stack**: Maintains a history of up to 50 operations.
- **Code Compare Panel**: View your original source lines side-by-side with the generated code before writing to disk.

---

## Interface & Controls Reference

### 1. Global Toolbar (Top)
- **Characters** (Tab): Selects the character editing panel. (Additional tabs for *UI* and *Text* are currently prepared for future expansions).
- **Import Scene**: Scans the active Ren'Py master layer, finds the exact source files and lines, and loads the sprites into the editor.
- **Save Changes**: Rewrites the mapped lines in your `.rpy` files in-place with the updated `Transform` code.
- **Undo**: Undoes the last modification (holds up to 50 steps).
- **Show Code**: Opens the side-by-side code comparison panel to preview modifications before writing to disk.
- **Grid**: Toggles a 100px-step background alignment grid to help with visual alignment.
- **Clear Editor**: Resets the editor state, discarding all unsaved transformations.
- **Close**: Safely exits the editor, restoring the scene back to its pre-editor state.

### 2. Base Controls Panel
- **Reset Pos**: Restores the character's X and Y coordinates to their initial imported state.
- **Reset Transform**: Restores the character's scale, rotation, and opacity back to the values originally defined in the script.
- **Defaults**: Resets the character to default parameters (rotation `0`, scale `1.0`, opacity `1.0`).
- **At Left / At Center / At Right**: Snaps the character to standard Ren'Py horizontal alignments.
- **Flip H / Flip V**: Mirrors the character horizontally or vertically (toggles negative `xzoom` or `yzoom`).
- **Rotation Slider**: Rotates the character between `-180°` and `180°`.
- **Linked / Unlinked**: Locks or unlocks the scale aspect ratio. When locked, scaling the X axis scales the Y axis proportionally.
- **Scale Sliders**: Changes the character's zoom level.
- **Opacity Slider**: Changes the character's transparency (`alpha`) from `0.0` (invisible) to `1.0` (opaque).
- **Nudge Buttons (`◄`, `►`, `▲`, `▼`)**: Move the selected character in 1px or 10px increments.
- **Nudge Step Toggle**: Click the **Step 1px / Step 10px** button to toggle the movement resolution for both the screen buttons and your keyboard's arrow keys.

> [!TIP]
> **In-place Value Editing**: You can click directly on the **Coordinates label** (`x=... y=...`), the **Rotation angle**, or the **Scale values** in the panel to turn them into manual input fields. This allows you to type exact numbers from your keyboard (e.g. typing `960, 540` to set the center position, `45.5` for rotation, or `0.85` for scale). Press **Enter** to commit the value.

### 3. Color Filters Panel
- **Defaults**: Resets all color matrix transformations back to their original values.
- **Reset (next to sliders)**: Resets only that specific filter back to its default state.
- **Blur**: Gaussian blur in pixels (`0px` to `20px`).
- **Brightness**: Adjusts image brightness matrix.
- **Contrast / Saturation / Hue / Invert**: Adjusts respective color filters dynamically.
- **Sepia ON/OFF**: Toggles a custom sepia matrix.

### 4. Motion FX Panel
- **Defaults**: Disables active motion animations.
- **Breathe / Shake / Float / Sway / Bounce / Sink / Blink**: Selects and runs the corresponding animation loop on the active character.
- **Strength**: Adjusts the speed, range, or amplitude of the selected animation.

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

### Non-interactive Quit Confirmation Block
If you click the OS window's close **"X"** button to exit the game while the editor overlay is open, Ren'Py's default quit confirmation prompt ("Are you sure you want to quit?") will appear, but you will be unable to click "Yes" or "No". 

This occurs because the active editor screen uses `modal True`, which intercepts all inputs and blocks interaction with the screens underneath it. To close the game, simply close the editor first (by pressing **F5** or clicking **Close**), and you will then be able to interact with the quit prompt.

---

## License

This project is open-source and free to use in any personal or commercial Ren'Py game.
