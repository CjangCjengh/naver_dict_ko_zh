#!/usr/bin/env python3
"""
生成 Naver 中韩词典全量爬虫的种子词表。

目前包含：
1. GB2312 一级常用汉字（3755 个）
2. 部分常用韩文词根
3. 可选：用户自定义追加

用法：
    python generate_seeds.py --output data/seeds.txt
"""

import argparse


def gb2312_level1_chars():
    """GB2312 一级汉字：16区01位至55区89位，共 3755 个。"""
    chars = []
    for zone in range(16, 56):  # 16-55 区
        for pos in range(1, 95):  # 01-94 位
            if zone == 55 and pos > 89:
                break
            # GB2312 编码转 Unicode
            gb_code = bytes([0xA0 + zone, 0xA0 + pos])
            try:
                ch = gb_code.decode("gb2312")
                chars.append(ch)
            except UnicodeDecodeError:
                pass
    return chars


def main():
    parser = argparse.ArgumentParser(description="生成爬虫种子")
    parser.add_argument("--output", "-o", default="data/seeds.txt", help="输出种子文件")
    parser.add_argument("--append", help="追加自定义种子文件")
    args = parser.parse_args()

    chars = gb2312_level1_chars()

    # 常用韩文词根
    korean_seeds = [
        "가", "나", "다", "라", "마", "바", "사", "아", "자", "차", "카", "타", "파", "하",
        "안녕", "사랑", "친구", "학교", "한국", "중국", "언어", "공부", "책", "집", "음식",
        "사람", "생활", "시간", "년", "월", "일", "말", "일", "문제", "생각", "여자",
        "남자", "어머니", "아버지", "학생", "선생님", "회사", "업무", "길", "차", "물",
    ]

    seeds = chars + korean_seeds

    if args.append:
        with open(args.append, "r", encoding="utf-8") as f:
            extra = [line.strip() for line in f if line.strip()]
        seeds = list(dict.fromkeys(seeds + extra))

    with open(args.output, "w", encoding="utf-8") as f:
        for s in seeds:
            f.write(s + "\n")

    print(f"已生成 {len(seeds)} 个种子，保存至 {args.output}")


if __name__ == "__main__":
    main()
