"""The double-click entry point of the packaged desktop app (the Windows installer).

Started with no arguments, it opens the map UI in the browser and keeps a
console window open as the sign that it is running: close the window to quit.
Results go to Documents\\Disturbed Ground Detector, because the program folder
is not the place for evidence and may not even be writable.

Started with arguments it is the ordinary ``dgd`` command line. That is also
how the UI runs each job: it starts this same program again with the job's
arguments (see ``Runner.command_for``).
"""

from __future__ import annotations

import os
import socket
import sys
import traceback
import urllib.request
import webbrowser
from pathlib import Path

from dgd import __version__

APP_NAME = "Disturbed Ground Detector"
PORT = 8765


def documents_dir() -> Path:
    """The user's Documents folder, wherever Windows put it (OneDrive likes to move it)."""
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_PERSONAL = 5 (Documents), SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def results_dir() -> Path:
    return documents_dir() / APP_NAME


def running_instance(port: int = PORT) -> str | None:
    """The address of a UI this app already started, if one is up.

    A quick socket check first: on Windows a refused connection to localhost
    takes a second or two to come back, and a double-click should feel instant.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            pass
    except OSError:
        return None
    url = f"http://127.0.0.1:{port}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
    try:
        with opener.open(url, timeout=3) as resp:
            return url if b"window.DGD" in resp.read() else None
    except Exception:
        return None  # something else lives on that port; serve() will pick another


def _set_title(title: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


def _pause() -> None:
    """Keep the window open long enough to read what went wrong."""
    try:
        input("\nPress Enter to close this window.")
    except (EOFError, KeyboardInterrupt):
        pass


def open_ui() -> int:
    _set_title(APP_NAME)
    url = running_instance()
    if url:
        print(f"{APP_NAME} is already running. Opening {url}")
        webbrowser.open(url)
        return 0
    out = results_dir()
    print(f"{APP_NAME} {__version__}\n")
    print("It opens in your web browser. If it does not, copy the address below into one.")
    print("Leave this window open while you work. Close it to quit.")
    print(f"Results are saved in {out}\n")
    from dgd.cli import main as cli_main

    try:
        rc = cli_main(["ui", "--output-dir", str(out)])
    except SystemExit as exc:  # serve() exits with a message when no port is free
        if isinstance(exc.code, str):
            print(exc.code)
            rc = 1
        else:
            rc = exc.code or 0
    except Exception:
        traceback.print_exc()
        rc = 1
    if rc:
        _pause()
    return rc


def main(argv: list[str] | None = None) -> int:
    import multiprocessing

    multiprocessing.freeze_support()  # a frozen app that starts helpers must say so first
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        from dgd.cli import main as cli_main

        return cli_main(argv)
    return open_ui()


if __name__ == "__main__":
    sys.exit(main())
