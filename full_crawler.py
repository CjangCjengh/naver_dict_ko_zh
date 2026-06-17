#!/usr/bin/env python3
"""
Naver 中韩词典（zh.dict.naver.com）全量词条爬虫

策略：
1. 自动补全接口（ac.dict.naver.com/zhko/ac）按前缀获取候选词条。
2. 用 BFS/DFS 从常用中文字、韩文词根扩展，收集所有 entry_id。
3. 对每个唯一词条调用 search API 获取完整释义，保存为 JSON Lines。

用法：
    python full_crawler.py --mode discover          # 仅发现词条（阶段1）
    python full_crawler.py --mode fetch             # 仅获取详情（阶段2）
    python full_crawler.py --mode both              # 完整流程（默认）
    python full_crawler.py --mode discover --max-queries 5000

注意：
- 请设置合理的 --delay，避免对 Naver 服务器造成压力。
- 全量爬取可能耗时很长，且无法保证 100% 覆盖。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Dict, List, Optional, Set, Tuple

import requests


SEARCH_URL_ZHKO = "https://zh.dict.naver.com/api3/zhko/search"
SEARCH_URL_KOZH = "https://korean.dict.naver.com/api3/kozh/search"
AC_URL = "https://ac.dict.naver.com/zhko/ac"
REFERER_ZHKO = "https://zh.dict.naver.com/"
REFERER_KOZH = "https://korean.dict.naver.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,ko;q=0.8,en;q=0.7",
}

# 常用中文字种子（前 500 个最常用字），用于启动 BFS
COMMON_CHINESE_CHARS = list(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取完举色或"  # noqa: E501
)

# 常用韩文词根种子（用于韩中方向 kozh）
COMMON_KOREAN_SEEDS = [
    "가", "나", "다", "라", "마", "바", "사", "아", "자", "차", "카", "타", "파", "하",
    "안녕", "사랑", "친구", "학교", "한국", "중국", "언어", "공부", "책", "집", "음식",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    return re.sub(r"<[^>]+>", "", text)


def http_get(session: requests.Session, url: str, params: Optional[dict] = None,
             max_retries: int = 3, timeout: int = 20,
             referer: str = REFERER_ZHKO) -> Optional[requests.Response]:
    headers = dict(HEADERS)
    headers["Referer"] = referer
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  请求失败（{max_retries}次重试）: {url} | {e}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def extract_hanja(item: dict) -> str:
    """从 Naver 返回的 alias 列表中提取汉字标注。"""
    aliases = item.get("expAliasGeneralAlwaysList", []) or []
    if not aliases:
        return ""
    values = [a.get("originLanguageValue", "") for a in aliases if a.get("originLanguageValue")]
    return "/".join(values)


def parse_search_item(item: dict) -> dict:
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


def autocomplete(session: requests.Session, query: str,
                 delay: float, max_retries: int = 3,
                 direction: str = "zhko") -> List[Tuple[str, str, str, str, str]]:
    """
    调用自动补全接口，返回 (entry, pinyin, ko_meaning, entry_id, direction) 列表。
    返回的 entries 包括以 query 开头和包含 query 的。
    当 direction='kozh' 时，Referer 用韩中词典页面。
    """
    referer = REFERER_KOZH if direction == "kozh" else REFERER_ZHKO
    params = {"q": query, "st": 11, "r_lt": 11}
    resp = http_get(session, AC_URL, params=params, max_retries=max_retries, timeout=10, referer=referer)
    if not resp:
        return []
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        print(f"  自动补全 JSON 解析失败: {query} | {e}")
        return []

    results = []
    for group in data.get("items", []):
        for item in group:
            try:
                entry = item[0][0]
                pinyin = item[2][0] if len(item) > 2 and item[2] else ""
                ko = item[3][0] if len(item) > 3 and item[3] else ""
                entry_id = item[4][0] if len(item) > 4 and item[4] else ""
                direction = item[5][0] if len(item) > 5 and item[5] else "zhko"
                if entry and entry_id:
                    results.append((entry, pinyin, ko, entry_id, direction))
            except (IndexError, TypeError):
                continue
    return results


def search_word(session: requests.Session, word: str,
                delay: float, max_retries: int = 3,
                direction: str = "zhko") -> Optional[dict]:
    params = {"query": word.strip()}
    search_url = SEARCH_URL_KOZH if direction == "kozh" else SEARCH_URL_ZHKO
    referer = REFERER_KOZH if direction == "kozh" else REFERER_ZHKO
    resp = http_get(session, search_url, params=params, max_retries=max_retries, timeout=15, referer=referer)
    if not resp:
        return None
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        print(f"  search JSON 解析失败: {word} | {e}")
        return None

    word_data = data.get("searchResultMap", {}).get("searchResultListMap", {}).get("WORD", {})
    items = [parse_search_item(item) for item in word_data.get("items", [])]
    return {
        "query": word,
        "total": word_data.get("total", 0),
        "items_count": len(items),
        "items": items,
        "crawled_at": now_iso(),
    }


class State:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.discovered_path = state_dir / "discovered.jsonl"
        self.queue_path = state_dir / "queue.txt"
        self.fetched_path = state_dir / "fetched.jsonl"
        self.done_ac_path = state_dir / "done_ac.txt"
        self.done_search_path = state_dir / "done_search.txt"

        self.discovered: Dict[str, dict] = {}  # entry_id -> {entry, pinyin, ko, direction}
        self.queue: List[str] = []
        self.done_ac: Set[str] = set()
        self.done_search: Set[str] = set()

        self._load()

    def _load(self):
        if self.discovered_path.exists():
            with open(self.discovered_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        self.discovered[obj["entry_id"]] = obj
                    except (json.JSONDecodeError, KeyError):
                        continue
        if self.queue_path.exists():
            with open(self.queue_path, "r", encoding="utf-8") as f:
                self.queue = [line.strip() for line in f if line.strip()]
        if self.done_ac_path.exists():
            with open(self.done_ac_path, "r", encoding="utf-8") as f:
                self.done_ac = {line.strip() for line in f if line.strip()}
        if self.done_search_path.exists():
            with open(self.done_search_path, "r", encoding="utf-8") as f:
                self.done_search = {line.strip() for line in f if line.strip()}
        print(f"状态加载：已发现 {len(self.discovered)} 条，队列 {len(self.queue)}，"
              f"已 AC {len(self.done_ac)}，已 search {len(self.done_search)}")

    def save_discovered(self, records: List[dict]):
        with open(self.discovered_path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def save_queue(self):
        with open(self.queue_path, "w", encoding="utf-8") as f:
            for q in self.queue:
                f.write(q + "\n")

    def save_done_ac(self, query: str):
        with open(self.done_ac_path, "a", encoding="utf-8") as f:
            f.write(query + "\n")

    def save_done_search(self, word: str):
        with open(self.done_search_path, "a", encoding="utf-8") as f:
            f.write(word + "\n")

    def save_search_result(self, result: dict, output_path: Path):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")


def discover(state: State, session: requests.Session, seeds: List[str],
             delay: float, max_depth: int, max_queries: int,
             direction: str = "zhko"):
    """通过自动补全发现词条。"""
    if not state.queue:
        # 过滤种子：如果 seed 在已完成的 AC 里则跳过
        state.queue = [s for s in seeds if s and s not in state.done_ac]
    state.save_queue()

    processed = 0
    while state.queue and processed < max_queries:
        query = state.queue.pop(0)
        if query in state.done_ac:
            continue

        print(f"[AC {processed+1}/{max_queries}] 前缀：{query}")
        results = autocomplete(session, query, delay=delay, direction=direction)
        # 过滤方向：只保留目标方向的词条
        results = [r for r in results if r[4] == direction]
        state.done_ac.add(query)
        state.save_done_ac(query)

        new_records = []
        new_queries = []
        for entry, pinyin, ko, entry_id, direction in results:
            if entry_id not in state.discovered:
                rec = {
                    "entry_id": entry_id,
                    "entry": entry,
                    "pinyin": pinyin,
                    "ko": ko,
                    "direction": direction,
                    "found_by": query,
                    "discovered_at": now_iso(),
                }
                state.discovered[entry_id] = rec
                new_records.append(rec)

            # 扩展策略：只对“以当前 query 开头”的词条继续探索，
            # 避免无限制地发散到包含 query 的其他词条。
            if entry.startswith(query) and len(entry) <= max_depth and entry not in state.done_ac and entry not in state.queue:
                new_queries.append(entry)
            elif direction == "kozh" and query in entry and len(entry) <= max_depth and entry not in state.done_ac and entry not in state.queue:
                # 韩文词可能不是严格 startswith（有词尾变化等），放宽为包含
                new_queries.append(entry)

        if new_records:
            state.save_discovered(new_records)
            print(f"  新增 {len(new_records)} 条，来自前缀 '{query}'")

        # 去重后加入队列
        for nq in new_queries:
            if nq not in state.queue:
                state.queue.append(nq)

        state.save_queue()
        processed += 1
        if processed < max_queries and state.queue:
            time.sleep(delay)

    print(f"发现阶段结束：共发现 {len(state.discovered)} 条唯一词条")


def fetch(state: State, session: requests.Session, output_path: Path,
          delay: float, max_queries: int, direction: str = "zhko"):
    """对发现的词条调用 search API 获取详情。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 按 entry 去重，准备查询
    to_fetch: List[str] = []
    seen_entry: Set[str] = set()
    for rec in state.discovered.values():
        entry = rec["entry"]
        if entry and entry not in state.done_search and entry not in seen_entry:
            to_fetch.append(entry)
            seen_entry.add(entry)

    print(f"详情阶段：待获取 {len(to_fetch)} 条")

    processed = 0
    for word in to_fetch:
        if processed >= max_queries:
            break
        if word in state.done_search:
            continue

        print(f"[Search {processed+1}/{min(len(to_fetch), max_queries)}] {word}")
        result = search_word(session, word, delay=delay, direction=direction)
        if result:
            state.save_search_result(result, output_path)
            state.done_search.add(word)
            state.save_done_search(word)
        processed += 1
        if processed < max_queries:
            time.sleep(delay)

    print(f"详情阶段结束：共获取 {len(state.done_search)} 条")


def main():
    parser = argparse.ArgumentParser(description="Naver 中韩词典全量爬虫")
    parser.add_argument("--mode", choices=["discover", "fetch", "both"], default="both",
                        help="discover=仅发现词条, fetch=仅获取详情, both=完整流程")
    parser.add_argument("--direction", choices=["zhko", "kozh"], default="zhko",
                        help="zhko=中韩, kozh=韩中（默认中韩）")
    parser.add_argument("--state-dir", default="data/state", help="状态保存目录")
    parser.add_argument("--output", "-o", default="data/zhko_full.jsonl", help="详情输出文件")
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="请求间隔秒数")
    parser.add_argument("--max-depth", type=int, default=4,
                        help="自动补全 BFS 最大词条长度（控制深度）")
    parser.add_argument("--max-queries", type=int, default=100000,
                        help="每个阶段最大请求数")
    parser.add_argument("--seeds", help="自定义种子文件（每行一个）")
    args = parser.parse_args()

    state = State(Path(args.state_dir))

    # 种子
    if args.seeds:
        with open(args.seeds, "r", encoding="utf-8") as f:
            seeds = [line.strip() for line in f if line.strip()]
    else:
        seeds = COMMON_CHINESE_CHARS + COMMON_KOREAN_SEEDS

    session = requests.Session()
    # 如需代理，可设置环境变量：HTTP_PROXY / HTTPS_PROXY
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    output_path = Path(args.output)
    if args.direction == "kozh":
        output_path = Path(str(output_path).replace("zhko", "kozh").replace(".jsonl", "_kozh.jsonl") if "zhko" in str(output_path) else str(output_path).replace(".jsonl", "_kozh.jsonl"))

    if args.mode in ("discover", "both"):
        discover(state, session, seeds, args.delay, args.max_depth, args.max_queries, direction=args.direction)

    if args.mode in ("fetch", "both"):
        fetch(state, session, output_path, args.delay, args.max_queries, direction=args.direction)


if __name__ == "__main__":
    main()
