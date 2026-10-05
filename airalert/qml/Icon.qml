import QtQuick
import QtQuick.Shapes
import "Icons.js" as Icons

// Vector icon drawn from Icons.js; recolors with `color`.
Item {
    id: root
    property string name: ""
    property color color: Theme.text
    property real size: 18
    property real stroke: 2.0
    readonly property var def: Icons.get(name)
    implicitWidth: size
    implicitHeight: size

    Shape {
        width: 24
        height: 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.def.fill ? "transparent" : root.color
            fillColor: root.def.fill ? root.color : "transparent"
            strokeWidth: root.def.fill ? 0 : root.stroke
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: root.def.d }
        }
    }
}
