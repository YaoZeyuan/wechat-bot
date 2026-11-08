"""Development entry points for the API and Vite."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main():
    import uvicorn

    uvicorn.run("server.main:app", host="127.0.0.1", port=8000)


def web_main():
    parser = argparse.ArgumentParser(description="Start the Vite development server on port 3000.")
    parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        parser.exit(1, "Install Node.js with npm and add it to PATH.\n")
    web = Path(__file__).resolve().parent.parent / "web"
    try:
        if not (web / "node_modules" / "vite").is_dir():
            subprocess.run([npm, "ci"], cwd=web, check=True)
        return subprocess.run([npm, "run", "dev", "--", "--strictPort"], cwd=web).returncode
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    main()
