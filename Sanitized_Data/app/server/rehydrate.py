"""把模型回答里的假名换回真名。

换名版的映射表加密存在本机，模型回话里出现的是「陈志远」，
这个工具把它还原成「张伟」。

用法：
    python rehydrate.py --list
    python rehydrate.py --job j_ab12cd34 回答.txt 回答_还原.txt
    python rehydrate.py --job j_ab12cd34 --dump        # 看这份映射（会打印原值，谨慎）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yinzhan import vault  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据隐站 · 映射还原")
    ap.add_argument("--list", action="store_true", help="列出本机保存过的换名版")
    ap.add_argument("--job", help="任务号，如 j_ab12cd34")
    ap.add_argument("--dump", action="store_true", help="打印映射表（含原值）")
    ap.add_argument("src", nargs="?", help="含假名的文本文件")
    ap.add_argument("dst", nargs="?", help="还原后写到哪")
    args = ap.parse_args(argv)

    if args.list or not args.job:
        items = vault.list_vault()
        if not items:
            print("本机还没有保存过换名版映射。")
            return 0
        print(f"{'任务号':<14}{'大小':>10}  时间")
        for it in items:
            import datetime
            ts = datetime.datetime.fromtimestamp(it["mtime"]).strftime("%Y-%m-%d %H:%M")
            print(f"{it['job_id']:<14}{it['size']:>10}  {ts}")
        print("\n用 --job <任务号> 还原具体文件。")
        return 0

    mapping = vault.load_mapping(args.job)
    if mapping is None:
        print(f"找不到 {args.job} 的映射。可能这份文件当时导出成了「涂黑版」。")
        return 1

    if args.dump:
        print(json.dumps(mapping, ensure_ascii=False, indent=2))
        return 0

    if not args.src:
        print("请给出要还原的文本文件。")
        return 2

    src = Path(args.src)
    if not src.exists():
        print(f"文件不存在：{src}")
        return 2
    text = src.read_text(encoding="utf-8", errors="replace")
    restored = vault.rehydrate(text, mapping)
    dst = Path(args.dst) if args.dst else src.with_name(src.stem + "_还原" + src.suffix)
    dst.write_text(restored, encoding="utf-8")
    changed = sum(1 for a, b in zip(text.splitlines(), restored.splitlines()) if a != b)
    print(f"已还原 → {dst}（{changed} 行有变化）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
