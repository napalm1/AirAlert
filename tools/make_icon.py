"""Render the AirAlert icon (assets/airalert.ico + .png) from an inline SVG."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2f7df6"/>
      <stop offset="1" stop-color="#14b8a4"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="240" height="240" rx="58" fill="url(#bg)"/>
  <g transform="translate(22,26) scale(8.8)" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round">
    <path d="M12 3 A9 9 0 1 0 21 12"/>
    <path d="M12 7 A5 5 0 1 0 17 12"/>
    <path d="M12 12 L18.4 5.6"/>
  </g>
  <circle cx="127.6" cy="131.6" r="12" fill="#ffffff"/>
  <circle cx="196" cy="62" r="34" fill="#ffb224" stroke="#ffffff" stroke-width="10"/>
</svg>"""


def main():
    from airalert.app import _qt_dll_search_path
    _qt_dll_search_path()
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(QByteArray(SVG.encode()))
    images = []
    for size in (256, 128, 64, 48, 32, 24, 16):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        images.append(Image.open(io.BytesIO(bytes(buffer.data()))).convert('RGBA'))
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    images[0].save(assets / 'airalert.png')
    images[0].save(assets / 'airalert.ico', sizes=[(i.width, i.height) for i in images], append_images=images[1:])
    (assets / 'airalert.svg').write_text(SVG, 'utf-8')
    print('wrote', assets / 'airalert.ico')
    del app


if __name__ == '__main__':
    main()
