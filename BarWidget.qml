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
    text: ""
    hasVisualContent: true
    tooltipText: "Open Omadictate PDF reader"

    Image {
      anchors.centerIn: parent
      width: 18
      height: 18
      source: Qt.resolvedUrl("assets/omadictate-pdf-reader.png")
      fillMode: Image.PreserveAspectFit
      smooth: true
    }

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) {
        var home = Quickshell.env("HOME") || ""
        Quickshell.execDetached([home + "/.local/bin/omadictate-pdf-reader"])
      }
    }
  }
}
