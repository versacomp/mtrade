from PyQt6.QtWidgets import QWidget, QGridLayout, QLabel, QVBoxLayout, QFrame
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt


class OphirPerformanceDashboard(QWidget):
    """
    The Strategy Tester interface.
    Displays the final mathematical edge of the compiled Alpha.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setStyleSheet("background-color: #2B2B2B; color: #A9B7C6;")

        # Main layout
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Create the grid
        self.grid = QGridLayout()
        self.grid.setSpacing(15)
        layout.addLayout(self.grid)

        # 1. Initialize the UI Labels
        self.metrics = {
            "Net Profit": self._create_metric_widget("Net Profit", "$0.00"),
            "Win Rate": self._create_metric_widget("Win Rate", "0.00%"),
            "Total Trades": self._create_metric_widget("Total Trades", "0"),
            "Max Drawdown": self._create_metric_widget("Max Drawdown", "0.00%"),
            "Profit Factor": self._create_metric_widget("Profit Factor", "0.00"),
            "Sharpe Ratio": self._create_metric_widget("Sharpe Ratio", "0.00")
        }

        # 2. Arrange them in a 2x3 Grid
        self.grid.addWidget(self.metrics["Net Profit"], 0, 0)
        self.grid.addWidget(self.metrics["Win Rate"], 0, 1)
        self.grid.addWidget(self.metrics["Total Trades"], 0, 2)

        self.grid.addWidget(self.metrics["Max Drawdown"], 1, 0)
        self.grid.addWidget(self.metrics["Profit Factor"], 1, 1)
        self.grid.addWidget(self.metrics["Sharpe Ratio"], 1, 2)

    def _create_metric_widget(self, title, default_val):
        """Creates an individual 'card' for a specific metric."""
        container = QFrame()
        container.setStyleSheet("background-color: #313335; border-radius: 4px; padding: 10px;")

        vbox = QVBoxLayout(container)
        vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(title)
        title_label.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #808080; border: none;")  # Muted grey
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        val_label = QLabel(default_val)
        val_label.setFont(QFont("Consolas", 14, QFont.Weight.Bold))
        val_label.setStyleSheet("color: #A9B7C6; border: none;")  # Default Darcula text
        val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        vbox.addWidget(title_label)
        vbox.addWidget(val_label)

        # Store the value label so we can update it later
        container.val_label = val_label
        return container

    def update_stats(self, stats: dict):
        """Catches the dictionary from the background thread and updates the UI."""
        # Update Net Profit with color
        np_label = self.metrics["Net Profit"].val_label
        np_val = stats.get("net_profit", 0.0)
        np_label.setText(f"${np_val:,.2f}")
        np_label.setStyleSheet(f"color: {'#089981' if np_val >= 0 else '#f23645'}; border: none;")

        # Update Win Rate
        wr_label = self.metrics["Win Rate"].val_label
        wr_val = stats.get("win_rate", 0.0)
        wr_label.setText(f"{wr_val:.2f}%")
        wr_label.setStyleSheet(f"color: {'#089981' if wr_val >= 50 else '#f23645'}; border: none;")

        # Update the rest
        self.metrics["Total Trades"].val_label.setText(str(stats.get("total_trades", 0)))

        dd_label = self.metrics["Max Drawdown"].val_label
        dd_label.setText(f"{stats.get('max_drawdown', 0.0):.2f}%")
        dd_label.setStyleSheet("color: #f23645; border: none;")  # Drawdown is always red

        self.metrics["Profit Factor"].val_label.setText(f"{stats.get('profit_factor', 0.0):.2f}")
        self.metrics["Sharpe Ratio"].val_label.setText(f"{stats.get('sharpe_ratio', 0.0):.2f}")