"""Development entry points for the API and Vite."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main():
    import uvicorn
    from .logging_setup import configure_logging

    configure_logging()
    uvicorn.run("server.main:app", host="127.0.0.1", port=8000, log_config=None, access_log=False)


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
