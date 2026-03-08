import sys
import traceback
from PyQt6.QtCore import QThread, pyqtSignal


class OutputRedirector:
    """
    Hijacks standard output (sys.stdout) and routes it through a PyQt Signal.
    This allows print() statements in the background thread to show up in the UI.
    """

    def __init__(self, signal):
        self.signal = signal

    def write(self, text):
        # Prevent emitting empty strings or pure newlines which spam the UI
        if text.strip():
            self.signal.emit(text.strip())

    def flush(self):
        pass


class OphirExecutionEngine(QThread):
    """
    The isolated background thread that compiles and runs the quant's Python code.
    """
    # Define the signals that will safely communicate with the main GUI thread
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, code_string):
        super().__init__()
        self.code_string = code_string

    def run(self):
        """
        The QThread ignition sequence.
        Everything inside this method runs completely separate from the UI.
        """
        # 1. Hijack the standard output
        original_stdout = sys.stdout
        sys.stdout = OutputRedirector(self.log_signal)

        try:
            self.log_signal.emit("[ENGINE] Compiling Ophir-AI Citadel Protocol...")

            # 2. Create an isolated namespace for the script to run inside
            # This prevents the user's code from accidentally destroying the IDE's memory
            isolated_namespace = {}

            # 3. Execute the raw string of code
            # In Python, exec() runs dynamically generated code.
            exec(self.code_string, isolated_namespace)

            # 4. Check if the user defined an 'execute_trade' function and run it
            if 'execute_trade' in isolated_namespace:
                self.log_signal.emit("[ENGINE] Executing entry point: execute_trade()...")
                isolated_namespace['execute_trade']()
            else:
                self.log_signal.emit("[WARN] No 'execute_trade()' function found in script.")

        except Exception as e:
            # If the user writes bad Python code, catch the crash and print the traceback
            error_msg = f"[CRASH REPORT] {str(e)}\n{traceback.format_exc()}"
            self.error_signal.emit(error_msg)

        finally:
            # 5. Restore the standard output and signal completion
            sys.stdout = original_stdout
            self.finished_signal.emit()