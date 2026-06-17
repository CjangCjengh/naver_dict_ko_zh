#!/usr/bin/env python3
"""
生成完整的韩文 Hangul Syllables 种子。

U+AC00 ~ U+D7A3 共 11172 个音节，全部写入文件。
虽然很多组合在现代韩语中不常用，但Autocomplete 接口会自行过滤空结果。

用法：
    python generate_korean_seeds_full.py -o data/korean_seeds_full.txt
"""

import argparse


def all_hangul_syllables():
    """返回 U+AC00 ~ U+D7A3 的所有韩文音节。"""
    return [chr(code) for code in range(0xAC00, 0xD7A4)]


def main():
    parser = argparse.ArgumentParser(description="生成完整韩文音节种子")
    parser.add_argument("--output", "-o", default="data/korean_seeds_full.txt", help="输出文件")
    args = parser.parse_args()

    chars = all_hangul_syllables()
    with open(args.output, "w", encoding="utf-8") as f:
        for ch in chars:
            f.write(ch + "\n")

    print(f"已生成 {len(chars)} 个韩文音节种子，保存至 {args.output}")


if __name__ == "__main__":
    main()
