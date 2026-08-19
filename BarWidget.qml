import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// A single bar icon. Clicking it opens the Tiling Trainer game in a new
// terminal. The game itself is plain Python (tiling-game.py, next to this file)
// and talks to Hyprland through hyprctl, so nothing else is needed.
BarWidget {
  id: root
  moduleName: "io.github.sponno.tiling-trainer"

  readonly property string scriptPath:
    Qt.resolvedUrl("tiling-game.py").toString().replace(/^file:\/\//, "")

  function launch() {
    if (root.bar) root.bar.run("omarchy-launch-terminal python3 '" + scriptPath + "'")
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰊗"
    tooltipText: "Tiling Trainer — learn the window keys by playing"
    onPressed: root.launch()
  }
}
