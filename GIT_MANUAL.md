# Git Workflow Manual for Ren'Py WYSIWYG Editor

This manual outlines the Git workflow for maintaining and updating the **Ren'Py WYSIWYG Drag-and-Drop Editor** across multiple active game projects.

---

## 1. Directory Structure & File Map

In your local environment, you have multiple copies of your active games and a central Git repository for the editor:

```
[your-working-directory]/
├── renpy-drag-drop-wysiwyg-editor/   <-- Central Git Repository (This folder)
│   ├── .gitignore
│   ├── README.md
│   ├── GIT_MANUAL.md
│   ├── assets/
│   └── game/
│       └── wysiwyg_editor.rpy         <-- Master script file
│
└── your-renpy-projects/               <-- Active game development folders
    ├── my-game-1/
    │   └── game/
    │       └── wysiwyg_editor.rpy
    └── my-game-2/
        └── game/
            └── wysiwyg_editor.rpy
```

---

## 2. Synchronization Workflow

Since the editor is a single-file drop-in tool (`wysiwyg_editor.rpy`) used across multiple games, follow this workflow when updating it:

### Step A: Edit & Test (in a single game)
Make your modifications to the script directly in your primary testing game (e.g., `my-game-1/game/wysiwyg_editor.rpy`). Test your changes in the game to ensure they are fully stable.

### Step B: Sync with the Git Repository
Once the code is verified, copy the updated file back into this central Git repository directory to prepare it for committing:
```powershell
copy [path-to-your-games]/my-game-1/game/wysiwyg_editor.rpy [path-to-repo]/renpy-drag-drop-wysiwyg-editor/game/wysiwyg_editor.rpy
```

### Step C: Sync with other games
Copy the updated master script to your other active game folders (e.g., `my-game-2`) so all copies remain identical:
```powershell
copy [path-to-repo]/renpy-drag-drop-wysiwyg-editor/game/wysiwyg_editor.rpy [path-to-games]/my-game-2/game/wysiwyg_editor.rpy
```

> [!TIP]
> You can create a simple sync script (`sync.bat` or `sync.ps1`) in your repository root to automate these copy steps in a single click.

---

## 3. Git Command Guide

Open your terminal or PowerShell in the repository root directory (`renpy-drag-drop-wysiwyg-editor/`) to run the following commands:

### Check status of files
See which files have been modified or added:
```bash
git status
```

### Review changes
Inspect exactly what lines of code changed:
```bash
git diff
```

### Stage changes for commit
Add the updated file to the staging area:
```bash
git add game/wysiwyg_editor.rpy
```

### Commit your changes
Create a commit with a clean, descriptive message:
```bash
git commit -m "Add real-time color filters and motion FX animations"
```

### Push changes to GitHub
Publish your local commits to your remote GitHub repository:
```bash
git push origin main
```

### Pull latest updates
If you edit the code from another machine or someone else contributes, pull the latest changes:
```bash
git pull origin main
```

---

## 4. Handling Backups and Compiled Files

### Ignore Compiled Bytecode (`.rpyc`)
Ren'Py compiles all `.rpy` scripts into `.rpyc` binaries during startup. These compiled files are ignored by our `.gitignore` and must **never** be committed to Git.

### Managing WYSIWYG backups
The editor automatically creates backups named `<file>.rpy.wysiwyg.bak` inside the game's folder before making modifications.
- **One-time baseline behavior:** The editor **never overwrites** an existing `.wysiwyg.bak` file, meaning it permanently preserves the original state of your code from before the editor first touched it.
- **Refreshing backups:** If you make manual code edits in an external IDE and want to refresh the backup baseline, delete the old `.wysiwyg.bak` file. A new one will be generated on the next save in the editor.
- **Restoring:** To roll back, replace the modified `.rpy` file with the corresponding `.bak` backup file.

### Non-interactive Quit Confirmation Block
If you click the OS window's close **"X"** button to exit the game while the editor overlay is open, Ren'Py's default quit confirmation prompt will appear, but you will be unable to click "Yes" or "No". 

This is because the active editor screen uses `modal True`, which intercepts all inputs and blocks interaction with the screens underneath it. To close the game, simply close the editor first (by pressing **F5** or clicking **Close**), and you will then be able to interact with the quit prompt.

