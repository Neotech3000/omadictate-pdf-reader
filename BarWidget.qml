import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "neotech3000.omadictate-pdf-reader"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "PDF"
    tooltipText: "Open Omadictate PDF reader"

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) {
        var home = Quickshell.env("HOME") || ""
        Quickshell.execDetached([home + "/.local/bin/omadictate-pdf-reader"])
      }
    }
  }
}
