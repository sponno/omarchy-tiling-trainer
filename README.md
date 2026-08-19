# Tiling Trainer for Omarchy

Learn Omarchy / Hyprland window management **by playing**.

Tiling Trainer is a terminal game that watches the *real* window manager
(via `hyprctl`) and ticks off each mission the moment you actually perform it
with the real keys. A live ASCII mini-map shows your tiles splitting, swapping,
floating and fullscreening as you press them.

Missions: open terminals · move focus · flip the split · swap · resize ·
fullscreen · float · workspaces · move windows between workspaces ·
scratchpad · close. Then a timed **boss round** with the hints hidden.

## Install (as an Omarchy shell plugin)

    omarchy plugin add https://github.com/sponno/omarchy-tiling-trainer.git --enable

A 󰊗 icon appears in your bar — click it to play. Remove with
`omarchy plugin remove io.github.sponno.tiling-trainer`.

## Or just run the script

No plugin needed — it's a single dependency-free Python file:

    curl -fsSL https://raw.githubusercontent.com/sponno/omarchy-tiling-trainer/main/tiling-game.py -o /tmp/tiling-game.py
    omarchy launch terminal python3 /tmp/tiling-game.py

## In-game keys

`h` hint on/off · `n` skip level · `q` quit.
Cheat sheet any time in Omarchy: **SUPER + K**.

## How it works

Every ~100 ms the game reads `hyprctl -j clients / monitors / activewindow`,
compares the state against the current mission (window count, focus, position,
size, floating/fullscreen flags, workspace ids) and advances when it matches.
It moves itself to an empty workspace at start so it has a clean arena, and it
finds its own window by walking its parent PIDs. The plugin's `BarWidget.qml`
is only a launcher; everything lives in `tiling-game.py`.

## License

MIT — see LICENSE.
