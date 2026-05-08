"""Proactive file watcher that triggers Gemini vision on error."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("jarvis.watcher")

_broadcast_json: Callable[[dict[str, Any]], Awaitable[None]] | None = None
_observer: Observer | None = None

class ErrorLogHandler(FileSystemEventHandler):
    def __init__(self, log_path: str, loop: asyncio.AbstractEventLoop) -> None:
        self.log_path = log_path
        self.loop = loop
        self.last_size = 0
        self._debouncing = False
        
        # Initialize last_size if file exists
        if os.path.exists(self.log_path):
            self.last_size = os.path.getsize(self.log_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.src_path != self.log_path:
            return
            
        try:
            current_size = os.path.getsize(self.log_path)
            if current_size < self.last_size:
                # File was truncated/rotated
                self.last_size = 0
                
            if current_size > self.last_size:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    f.seek(self.last_size)
                    new_lines = f.readlines()
                self.last_size = current_size
                
                # Check for "Error" or "Exception"
                has_error = any("error" in line.lower() or "exception" in line.lower() for line in new_lines)
                if has_error and not self._debouncing:
                    self._debouncing = True
                    # Trigger async task from sync watchdog thread
                    asyncio.run_coroutine_threadsafe(self.trigger_vision(), self.loop)
        except Exception:
            logger.exception("Error checking log file modifications")

    async def trigger_vision(self) -> None:
        try:
            if not _broadcast_json:
                return

            logger.info("Error detected in log. Triggering vision...")
            from tools import _read_screen_gemini
            prompt = "An error was just thrown in the terminal. Identify the error and suggest a one-sentence fix."
            result = await _read_screen_gemini(prompt)
            
            payload = {
                "type": "proactive_alert",
                "message": result,
                "priority": "high",
                "speak": True
            }
            await _broadcast_json(payload)
        except Exception:
            logger.exception("Failed to trigger vision for error")
        finally:
            # Debounce to prevent multiple triggers in quick succession (10 seconds)
            await asyncio.sleep(10)
            self._debouncing = False

def start_log_watcher(broadcast_json: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    global _broadcast_json, _observer
    if _observer is not None:
        return
        
    _broadcast_json = broadcast_json
    
    log_path = os.environ.get("JARVIS_LOG_WATCH_PATH", "nextjs_build.log")
    
    # Ensure file exists so watchdog doesn't fail
    if not os.path.exists(log_path):
        try:
            with open(log_path, "w") as f:
                f.write("")
        except IOError:
            logger.warning("Could not create log file to watch: %s", log_path)
            return

    # Use the current event loop since watchdog runs in a separate thread
    loop = asyncio.get_running_loop()
    
    # Watchdog requires absolute paths for accurate matching usually, or at least directory watching
    abs_path = os.path.abspath(log_path)
    directory = os.path.dirname(abs_path)
    
    handler = ErrorLogHandler(abs_path, loop)
    
    _observer = Observer()
    _observer.schedule(handler, path=directory, recursive=False)
    _observer.start()
    logger.info("Log watcher started for %s", abs_path)

def shutdown_log_watcher() -> None:
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join()
        _observer = None
        logger.info("Log watcher stopped.")
