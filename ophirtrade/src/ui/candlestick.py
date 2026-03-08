import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPicture, QPainter


class CandlestickItem(pg.GraphicsObject):
    """
    A highly optimized custom graphics object for rendering OHLC data.
    """

    def __init__(self, data):
        super().__init__()
        # Data format: list of tuples (time_index, open, close, low, high)
        self.data = data
        self.picture = QPicture()
        self._generate_picture()

    def _generate_picture(self):
        """
        Pre-computes the drawing instructions. 
        This is the secret to 60 FPS panning and zooming.
        """
        painter = QPainter(self.picture)

        # Standard TradingView-style colors
        bullish_color = '#089981'  # Green
        bearish_color = '#f23645'  # Red

        # The width of the candlestick body
        w = 0.3

        # Pre-calculate Pens and Brushes for performance
        bull_pen = pg.mkPen(color=bullish_color, width=1.0)
        bull_brush = pg.mkBrush(bullish_color)
        bear_pen = pg.mkPen(color=bearish_color, width=1.0)
        bear_brush = pg.mkBrush(bearish_color)

        for (t, open_price, close_price, low_price, high_price) in self.data:
            # 1. Determine the color and rect boundaries based on price action
            if close_price >= open_price:
                painter.setPen(bull_pen)
                painter.setBrush(bull_brush)
            else:
                painter.setPen(bear_pen)
                painter.setBrush(bear_brush)

            # 2. Draw the Wick (Low to High)
            painter.drawLine(pg.Point(t, low_price), pg.Point(t, high_price))

            # 3. Draw the Body (Open to Close)
            # In pyqtgraph/Qt, we draw from one corner with a specific width/height.
            # Using max() for the top bound because price increases upwards.
            y_high = max(open_price, close_price)
            y_low = min(open_price, close_price)
            painter.drawRect(QRectF(t - w, y_low, w * 2, y_high - y_low))

        painter.end()

    def paint(self, painter, *args):
        # When pyqtgraph asks this object to draw itself, we just hand it the pre-rendered picture
        painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        # pyqtgraph needs to know the physical dimensions of our drawing for auto-scaling
        return QRectF(self.picture.boundingRect())
    