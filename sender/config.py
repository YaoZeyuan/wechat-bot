"""配置加载与环境准备。"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.json"
CONFIG_PATH = DEFAULT_CONFIG
ADB_PATH_LOCK = Lock()


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """读取本机私有配置；换机器首次运行时自动从模板生成一份。"""
    path = Path(path)
    if not path.exists():
        example = ROOT / "config.example.json"
        if not example.exists():
            raise FileNotFoundError(f"缺少配置文件 {path}，也找不到模板 config.example.json")
        shutil.copyfile(example, path)
        print(f"[配置] 未找到 {path.name}，已从模板生成，请按本机环境修改后再用。")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def setup_adb(cfg: dict[str, Any]) -> None:
    """把本机 adb 目录临时塞进 PATH，让 adbutils 找得到 adb。"""
    adb_dir = cfg.get("adb_dir", "")
    if adb_dir and Path(adb_dir).is_dir():
        directory = str(Path(adb_dir).resolve())
        key = os.path.normcase(os.path.normpath(directory))
        with ADB_PATH_LOCK:
            current = os.environ.get("PATH", "")
            entries = current.split(os.pathsep) if current else []
            updated = os.pathsep.join([
                directory,
                *(entry for entry in entries if os.path.normcase(os.path.normpath(entry.strip('"'))) != key),
            ])
            if updated != current:
                os.environ["PATH"] = updated
