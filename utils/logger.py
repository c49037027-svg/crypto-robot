"""
Rich 彩色日誌模組
"""
import logging
from datetime import datetime
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

THEME = Theme({
    "info":    "cyan",
    "warning": "yellow bold",
    "error":   "red bold",
    "success": "green bold",
    "trade":   "magenta bold",
    "profit":  "bright_green bold",
    "loss":    "bright_red bold",
})

console = Console(theme=THEME)

def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    return logging.getLogger(name)
