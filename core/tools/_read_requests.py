"""读探针 `--requests` 留下的那份请求档：**每一轮里那句提醒到底在不在**。

用法：`python tools/_read_requests.py <那份 jsonl>`
"""
import io
import json
import sys


def main(where: str) -> int:
    rows = [json.loads(line) for line in io.open(where, encoding="utf-8") if line.strip()]
    print(f"共 {len(rows)} 轮请求（{where}）")
    for one in rows:
        messages = one["messages"]
        hit = [m for m in messages if "这一趟里" in str(m.get("content", ""))]
        last = len(str(messages[-1].get("content", ""))) if messages else 0
        print(f"  第 {one['轮']:>2} 轮｜条数 {len(messages):>3}｜"
              f"那句提醒 {'★在' if hit else '不在'}｜末条字符数：{last}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
