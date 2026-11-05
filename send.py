"""命令行发送入口。

常用命令（统一用项目虚拟环境的 Python，系统 python 里没有依赖）：
  .venv/Scripts/python.exe check_env.py                     环境自检（先跑这个）
  .venv/Scripts/python.exe send.py --text "测试"            发到 config.json 的默认目标
  .venv/Scripts/python.exe send.py --to "某群名" --text "..." 发到指定群
  .venv/Scripts/python.exe send.py --text "..." --dry-run   只填文本不点发送
  .venv/Scripts/python.exe send.py --file rank.txt          从文件读多行文本
  .venv/Scripts/python.exe send.py --dump                   保存当前手机界面层级
  .venv/Scripts/python.exe send.py --devices                列出 adb 设备
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from wx_sender import SendError, WeChatSender, load_config, setup_adb
except ModuleNotFoundError as exc:
    print(
        f"[环境错误] 缺少依赖 {exc.name}。请用项目虚拟环境运行：\n"
        '  ./.venv/Scripts/python.exe send.py --text "测试"\n'
        "若尚未安装依赖： ./.venv/Scripts/python.exe -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)


def devices_text() -> str:
    cfg = load_config()
    setup_adb(cfg)
    adb = shutil.which("adb")
    if not adb:
        return "adb 未找到，检查 config.json 的 adb_dir"
    proc = subprocess.run(
        [adb, "devices", "-l"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="通过 uiautomator2 把文本发到微信会话")
    ap.add_argument("--to", help="目标会话名（群名 / 文件传输助手），默认取 config.json")
    ap.add_argument("--text", help="要发送的文本")
    ap.add_argument("--file", help="从 UTF-8 文本文件读取内容（多行榜单用这个）")
    ap.add_argument("--dry-run", action="store_true", help="只填文本、截图，不点发送")
    ap.add_argument("--serial", help="设备序列号，覆盖 config.json")
    ap.add_argument("--devices", action="store_true", help="列出 adb 设备后退出")
    ap.add_argument("--dump", action="store_true", help="保存当前界面层级后退出")
    args = ap.parse_args()

    if args.devices:
        print(devices_text())
        return 0

    cfg = load_config()
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = f"发送链路测试 {datetime.now():%Y-%m-%d %H:%M:%S}"

    target = args.to or cfg.get("default_target", "文件传输助手")
    dry_run = True if args.dry_run else None
    sender = WeChatSender(cfg=cfg, serial=args.serial, dry_run=dry_run)
    try:
        sender.connect()
        if args.dump:
            print("界面层级已保存:", sender.dump_ui())
            return 0
        ok = sender.send(target, text)
        return 0 if ok else 2
    except SendError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[异常] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
