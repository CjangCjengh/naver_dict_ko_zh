#!/usr/bin/env python3
"""
Naver 中韩词典（zh.dict.naver.com）爬虫

功能：
- 根据查询词列表，调用 Naver api3/zhko/search 接口获取搜索结果
- 保存为 JSON Lines 格式（每行一个查询的完整返回）
- 支持断点续爬、错误重试、请求延迟

用法：
    python crawler.py --input words.txt --output data/zhko_results.jsonl
    python crawler.py --query "你好" --output data/zhko_results.jsonl
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests


BASE_URL_ZHKO = "https://zh.dict.naver.com/api3/zhko/search"
BASE_URL_KOZH = "https://korean.dict.naver.com/api3/kozh/search"
REFERER_ZHKO = "https://zh.dict.naver.com/"
REFERER_KOZH = "https://korean.dict.naver.com/"
HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,ko;q=0.8,en;q=0.7",
}


def clean_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    # 去除 <strong> 等高亮标签，保留内容
    return re.sub(r"<[^>]+>", "", text)


def extract_hanja(item: dict) -> str:
    """从 Naver 返回的 alias 列表中提取汉字标注。"""
    aliases = item.get("expAliasGeneralAlwaysList", []) or []
    if not aliases:
        return ""
    # 可能有多条，用 '/' 拼接
    values = [a.get("originLanguageValue", "") for a in aliases if a.get("originLanguageValue")]
    return "/".join(values)


def parse_item(item: dict) -> dict:
    """把原始 item 解析成更干净的结构。"""
    means = []
    for collector in item.get("meansCollector", []):
        part = collector.get("partOfSpeech", "")
        for mean in collector.get("means", []):
            means.append({
                "order": mean.get("order", ""),
                "value": mean.get("value", ""),
                "example": mean.get("exampleOri", ""),
                "example_trans": mean.get("exampleTrans", ""),
                "part": part,
            })

    return {
        "entry_id": item.get("entryId", ""),
        "entry": clean_html(item.get("expEntry", "")),
        "hanja": extract_hanja(item),
        "dict_name": item.get("sourceDictnameKO", ""),
        "dict_name_ori": item.get("sourceDictnameOri", ""),
        "match_type": item.get("matchType", ""),
        "language_code": item.get("languageCode", ""),
        "destination_link": item.get("destinationLink", ""),
        "destination_link_ko": item.get("destinationLinkKo", ""),
        "means": means,
        "has_example": item.get("hasExample", 0),
        "has_image": item.get("hasImage", 0),
        "has_origin": item.get("hasOrigin", 0),
    }


def search_word(session: requests.Session, word: str, max_retries: int = 3,
                direction: str = "zhko") -> dict:
    """查询单个词，返回解析后的结果。"""
    params = {"query": word.strip()}
    base_url = BASE_URL_KOZH if direction == "kozh" else BASE_URL_ZHKO
    referer = REFERER_KOZH if direction == "kozh" else REFERER_ZHKO
    headers = dict(HEADERS_BASE)
    headers["Referer"] = referer

    for attempt in range(max_retries):
        try:
            resp = session.get(base_url, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            word_data = data.get("searchResultMap", {}).get("searchResultListMap", {}).get("WORD", {})
            items = word_data.get("items", [])
            total = word_data.get("total", 0)

            return {
                "query": word,
                "total": total,
                "items_count": len(items),
                "items": [parse_item(item) for item in items],
                "raw": data,  # 保留原始返回，方便后续处理
                "crawled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        except Exception as e:
            if attempt == max_retries - 1:
                return {
                    "query": word,
                    "error": str(e),
                    "crawled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            time.sleep(1.5 * (attempt + 1))

    return {"query": word, "error": "unexpected", "crawled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


def load_word_list(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def load_finished_queries(path: str) -> set:
    if not os.path.exists(path):
        return set()
    finished = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
                finished.add(obj.get("query", ""))
            except json.JSONDecodeError:
                continue
    return finished


def main():
    parser = argparse.ArgumentParser(description="Naver 中韩词典爬虫")
    parser.add_argument("--input", "-i", help="查询词列表文件（每行一个词）")
    parser.add_argument("--output", "-o", default="data/zhko_results.jsonl", help="输出 JSONL 文件路径")
    parser.add_argument("--query", "-q", help="单个查询词")
    parser.add_argument("--direction", choices=["zhko", "kozh"], default="zhko",
                        help="zhko=中韩（默认）, kozh=韩中")
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="每次请求间隔秒数（默认 1.0）")
    parser.add_argument("--retries", "-r", type=int, default=3, help="失败重试次数")
    args = parser.parse_args()

    if not args.input and not args.query:
        parser.print_help()
        sys.exit(1)

    words = []
    if args.query:
        words.append(args.query)
    if args.input:
        words.extend(load_word_list(args.input))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    finished = load_finished_queries(str(output_path))
    words = [w for w in words if w not in finished]
    print(f"总查询词数：{len(words) + len(finished)}，待爬取：{len(words)}，已存在：{len(finished)}")

    session = requests.Session()
    # 如需代理，可设置环境变量：HTTP_PROXY / HTTPS_PROXY
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    with open(output_path, "a", encoding="utf-8") as f:
        for idx, word in enumerate(words, 1):
            print(f"[{idx}/{len(words)}] 查询：{word}")
            result = search_word(session, word, max_retries=args.retries, direction=args.direction)
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            if idx < len(words):
                time.sleep(args.delay)

    print(f"完成，结果保存至：{output_path}")


if __name__ == "__main__":
    main()
