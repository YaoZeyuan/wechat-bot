"""环境自检：Python / adb / 设备 / uiautomator2 / 微信安装 / 界面层级。

用法：.venv/Scripts/python.exe check_env.py
无设备时会打印手机端准备清单并以退出码 1 结束。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

try:
    import uiautomator2 as u2
    from wx_sender import ROOT, load_config, setup_adb
except ModuleNotFoundError as exc:
    print(
        f"[环境错误] 缺少依赖 {exc.name}。请用项目虚拟环境运行：\n"
        "  ./.venv/Scripts/python.exe check_env.py\n"
        "若尚未安装依赖： ./.venv/Scripts/python.exe -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)

PREP = """
手机端准备清单：
  1. 设置 → 关于手机 → 连点「版本号」7 次，打开开发者选项
  2. 开发者选项 → 打开「USB 调试」；部分品牌还需「USB 调试(安全设置)」
  3. 数据线连接电脑，USB 用途选「传输文件(MTP)」
  4. 手机弹出「允许 USB 调试吗」→ 勾选一律允许并确定
  5. 保持手机解锁、屏幕常亮，且已登录微信
  6. 再次运行 .venv/Scripts/python.exe check_env.py

WiFi 连法（可选）：先 USB 连着执行 adb tcpip 5555，
再 adb connect 手机IP:5555，把「IP:端口」填进 config.json 的 serial。
"""


def adb_devices(adb: str) -> str:
    proc = subprocess.run(
        [adb, "devices", "-l"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip()


def main() -> int:
    cfg = load_config()
    setup_adb(cfg)
    print(f"[1/5] Python {sys.version.split()[0]}")
    try:
        print(f"[2/5] uiautomator2 {version('uiautomator2')}")
    except PackageNotFoundError:
        print("[2/5] uiautomator2 未安装：pip install -r requirements.txt")
        return 1

    adb = shutil.which("adb")
    print(f"[3/5] adb: {adb or '未找到'}")
    if not adb:
        print("把 config.json 的 adb_dir 指向 Android SDK 的 platform-tools 目录。")
        return 1

    out = adb_devices(adb)
    print("[4/5] 设备列表：\n" + out)
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
        elif len(parts) >= 2 and parts[1] in ("unauthorized", "offline"):
            print(f"[注意] 设备 {parts[0]} 状态是 {parts[1]}，需要在手机上授权或重插数据线")

    if not devices:
        print(PREP)
        return 1

    print(f"[5/5] 已连接 {len(devices)} 台设备，尝试启动 uiautomator2 服务 ...")
    try:
        d = u2.connect(devices[0])
        d.implicitly_wait(float(cfg.get("wait_timeout", 10.0)))
        print("设备信息:", d.info)
        res = d.shell(["pm", "path", cfg.get("wechat_package", "com.tencent.mm")])
        print("微信已安装" if res.output.strip() else "微信未安装：请先在手机上登录微信")

        debug = ROOT / cfg.get("debug_dir", "debug")
        debug.mkdir(exist_ok=True)
        path = debug / "hierarchy.xml"
        path.write_text(d.dump_hierarchy(compressed=False, pretty=True), encoding="utf-8")
        print(f"当前界面层级已保存: {path}")
        print('\n环境就绪。下一步：.venv/Scripts/python.exe send.py --text "发送链路测试" --dry-run')
        return 0
    except Exception as exc:
        print(f"连接失败: {type(exc).__name__}: {exc}")
        print(PREP)
        return 1


if __name__ == "__main__":
    sys.exit(main())
