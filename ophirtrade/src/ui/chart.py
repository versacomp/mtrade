import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout


class OphirTradeChart(QWidget):
    """
    The High-Frequency Charting Engine.
    Uses GPU-accelerated plotting for millions of data points.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # 1. Global Configuration
        pg.setConfigOptions(antialias=True)  # Smooth out the jagged lines

        # 2. Main Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 3. Initialize the Plot Widget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#1e1e1e')  # Match the IDE theme

        # 4. Professional Styling
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('left', 'Price', color='#aaaaaa', size='12pt')
        self.plot_widget.setLabel('bottom', 'Time (Ticks)', color='#aaaaaa', size='12pt')

        # Style the axes
        axis_pen = pg.mkPen(color='#555555', width=1)
        self.plot_widget.getAxis('left').setPen(axis_pen)
        self.plot_widget.getAxis('bottom').setPen(axis_pen)
        self.plot_widget.getAxis('left').setTextPen('#aaaaaa')
        self.plot_widget.getAxis('bottom').setTextPen('#aaaaaa')

        # 5. Create the Curve
        # We use the hacker green accent color for the live price line
        self.line_pen = pg.mkPen(color='#4af626', width=1.5)
        self.curve = self.plot_widget.plot(pen=self.line_pen)

        layout.addWidget(self.plot_widget)

        # 6. Load Initial Dummy Data (Simulating a market session)
        self._load_simulated_data()

    def _load_simulated_data(self):
        """Generates a massive 100,000-tick random walk to prove rendering speed."""
        num_points = 100_000

        # A simple random walk formula using numpy for blistering speed
        steps = np.random.choice([-1.5, 1.5], size=num_points)
        price_path = np.cumsum(steps) + 18500.0  # Start roughly around NQ levels

        x_axis = np.arange(num_points)

        # Slam the data into the GPU
        self.curve.setData(x_axis, price_path)

    def update_data(self, x, y):
        """
        Updates the chart with new data arrays.
        :param x: numpy array of x-coordinates (e.g., timestamps or tick indices)
        :param y: numpy array of y-coordinates (price levels)
        """
        self.curve.setData(x, y)
