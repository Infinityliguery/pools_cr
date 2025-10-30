import threading
import time
from typing import Callable, Any, Literal

# Define status types for better type checking and clarity
HealthStatus = Literal["healthy", "unhealthy", "unknown"]

class HealthChecker:
    """
    A utility class to periodically check the health of an object (pool item).

    This class runs a given health check function in a background thread at a
    specified interval. It maintains the health status of the item, which can be
    queried by the pool manager.

    Attributes:
        item (Any): The object/resource to be monitored.
        status (HealthStatus): The current health status ('healthy', 'unhealthy', 'unknown').
    """

    def __init__(
        self,
        item: Any,
        check_func: Callable[[Any], bool],
        interval: int = 60,
    ):
        """
        Initializes the HealthChecker.

        Args:
            item (Any): The object/resource to monitor.
            check_func (Callable[[Any], bool]): A function that takes the item
                as an argument and returns True if healthy, False otherwise.
            interval (int): The interval in seconds between health checks.
        """
        if not isinstance(interval, int) or interval <= 0:
            raise ValueError("Interval must be a positive integer.")

        self.item = item
        self.check_func = check_func
        self.interval = interval
        self.status: HealthStatus = "unknown"

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _run_checks(self) -> None:
        """The target method for the background thread to run checks."""
        while not self._stop_event.is_set():
            try:
                is_healthy = self.check_func(self.item)
                self.status = "healthy" if is_healthy else "unhealthy"
            except Exception:
                self.status = "unhealthy"

            # Wait for the interval, but allow for a quicker exit if stopped
            self._stop_event.wait(self.interval)

    def start(self) -> None:
        """Starts the background health checking thread."""
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_checks, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stops the background health checking thread gracefully."""
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            # Join to ensure the thread has fully terminated before proceeding
            self._thread.join(timeout=1.0)

    @property
    def is_healthy(self) -> bool:
        """A property that returns True if the item's status is 'healthy'."""
        return self.status == "healthy"

    def __repr__(self) -> str:
        """Provides a string representation of the HealthChecker instance."""
        return f"HealthChecker(item={self.item!r}, status='{self.status}')"
