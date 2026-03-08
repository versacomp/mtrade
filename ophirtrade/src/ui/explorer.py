import os
from PyQt6.QtWidgets import QTreeView
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtCore import pyqtSignal


class OphirFileExplorer(QTreeView):
    """
    A native OS file explorer docked into the IDE.
    Emits a signal containing the file's text when double-clicked.
    """
    # Define a custom signal that passes (file_path, file_content)
    file_loaded = pyqtSignal(str, str)

    def __init__(self, workspace_dir="./strategies"):
        super().__init__()

        # 1. Ensure the workspace directory exists on the hard drive
        self.workspace_dir = workspace_dir
        if not os.path.exists(self.workspace_dir):
            os.makedirs(self.workspace_dir)

        # 2. Hook into the Operating System
        self.model = QFileSystemModel()
        self.model.setRootPath(self.workspace_dir)

        # 3. Configure the Tree View
        self.setModel(self.model)
        self.setRootIndex(self.model.index(self.workspace_dir))

        # Hide the size, type, and date columns for a cleaner, VS Code-style look
        for col in range(1, 4):
            self.setColumnHidden(col, True)
        self.setHeaderHidden(True)

        # 4. Wire up the double-click event
        self.doubleClicked.connect(self._on_double_click)

    def _on_double_click(self, index):
        """Fires when the user double-clicks a file in the dock."""
        file_path = self.model.filePath(index)

        # Only open Python files to prevent crashing the editor with binary data
        if os.path.isfile(file_path) and file_path.endswith('.py'):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Emit the data up to the main window
            self.file_loaded.emit(file_path, content)