"""Development entry points for the API and Vite."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def create_app():
    from .logging_setup import configure_logging
    from .main import app

    configure_logging()
    return app


def main():
    import uvicorn
    from .logging_setup import configure_logging

    parser = argparse.ArgumentParser(description="Start the FastAPI development server.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="Disable Python file watching")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    configure_logging(write_file=False)
    uvicorn.run(
        "server.__main__:create_app", factory=True, host="127.0.0.1", port=args.port,
        reload=not args.no_reload, reload_dirs=[] if args.no_reload else [root / "server", root / "sender"],
        log_config=None, access_log=False,
    )


def web_main():
    parser = argparse.ArgumentParser(description="Start the Vite development server on port 3000.")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    pnpm = shutil.which("pnpm.exe") if os.name == "nt" else None
    pnpm = pnpm or shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if pnpm is None:
        parser.exit(1, "Install Node.js and pnpm and add them to PATH.\n")
    web = Path(__file__).resolve().parent.parent / "web"
    try:
        if not (web / "node_modules" / ".pnpm").is_dir():
            subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=web, check=True)
        return subprocess.run(
            [pnpm, "run", "dev", "--strictPort", "--port", str(args.port)], cwd=web,
        ).returncode
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    main()
