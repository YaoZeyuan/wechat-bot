"""Export a group's text messages from an offline WeChat database copy."""

import argparse
import getpass
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from .ciphertalk import SourceError
from .reader import load_profile
from .sqlcipher_reader import SqlCipherReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Offline account or db_storage directory")
    parser.add_argument("--account", required=True)
    parser.add_argument("--group", required=True, help="Exact group name or ID")
    parser.add_argument("--output", type=Path, required=True, help="Destination JSON Lines file")
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(1, "输出文件已存在，请选择其他文件名。\n")
    temporary = None
    try:
        profile = load_profile()
        key = (
            profile.database_key.get_secret_value()
            if profile and profile.account == args.account else getpass.getpass("数据库密钥：")
        )
        source = SqlCipherReader(args.snapshot.resolve(strict=True), key, args.account)
        groups = source.sessions(q=args.group, limit=500)["items"]
        matches = [group for group in groups if args.group in (group["sessionId"], group["displayName"])]
        if len(matches) != 1:
            parser.exit(1, "未找到唯一匹配的群，请使用群 ID。\n")
        group_id = matches[0]["sessionId"]
        count = 0
        offset = 0
        with NamedTemporaryFile(
            dir=args.output.parent, mode="w", encoding="utf-8", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            while rows := source.messages(group_id, offset=offset, limit=250)["items"]:
                for row in rows:
                    if row["kind"] == "text":
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        count += 1
                offset += len(rows)
        if not count:
            parser.exit(1, "此副本未找到该群的文本消息。\n")
        temporary.rename(args.output)
        print(f"已导出 {count} 条文本消息")
    except (OSError, SourceError) as exc:
        parser.exit(1, f"导出失败：{exc}\n")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
