# Treeedit

This package uses ST text view to render and navigate file system.
It looks like this:

```
C:/Users/<user>/AppData/Roaming/Sublime Text 3/Packages/treeedit
..
.git ▼
    COMMIT_EDITMSG
    HEAD
    config
    description
    fsmonitor--daemon ▶
    hooks ▶
    index
    info ▶
    logs ▶
    objects ▶
    refs ▶
Default.sublime-keymap
README.md
dependencies.json
treeedit.py
treeedit.sublime-commands
treeedit.sublime-syntax
```

## Usage

In command palette choose "Treeedit Show In Tree" to open tree witch current file.
Use arrow keys to navigate around. Enter to open file and to fold/unfold directories.
Use `u` key to to go up to enclosing directory.
Use `enter` on `..` to go up one level of directories.

## Installation

Clone reporsitory in your ST packages directory. In command palette choose "Package Control: Satisfy Dependencies".