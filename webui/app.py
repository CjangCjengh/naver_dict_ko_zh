#!/usr/bin/env python3
"""
Naver 韩中词典本地 WebUI

加载 full_crawler.py 抓取的 kozh_full_kozh.jsonl，
提供类似 Naver 词典的搜索页面。

特性：
- 本地索引搜索
- 搜索时同步回源 Naver，若本地没有则补充到本地数据和索引

用法：
    cd webui && python app.py
    然后打开 http://127.0.0.1:5001
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)

DATA_PATH = Path(__file__).parent.parent / "data" / "kozh_full_kozh.jsonl"
BACKUP_PATH = Path(__file__).parent.parent / "data" / "kozh_full_kozh.jsonl.backup"

NAVER_SEARCH_URL = "https://korean.dict.naver.com/api3/kozh/search"
NAVER_REFERER = "https://korean.dict.naver.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": NAVER_REFERER,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
}

# 全局索引
entries: Dict[str, List[dict]] = {}  # entry -> list of items
all_entries: List[str] = []          # 所有 entry 列表，用于排序
lock = threading.Lock()

# 记录最近从 Naver 补充过的 query，避免重复请求
recently_fetched: Dict[str, float] = {}
FETCH_COOLDOWN = 300  # 同一个 query 5 分钟内只回源一次

executor = ThreadPoolExecutor(max_workers=2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)


def extract_hanja(item: dict) -> str:
    """从 Naver 返回的 alias 列表中提取汉字标注。"""
    aliases = item.get("expAliasGeneralAlwaysList", []) or []
    if not aliases:
        return ""
    values = [a.get("originLanguageValue", "") for a in aliases if a.get("originLanguageValue")]
    return "/".join(values)


def parse_naver_item(item: dict) -> dict:
    """把 Naver 原始 item 解析成本地存储格式。"""
    means = []
    for collector in item.get("meansCollector", []):
        part = collector.get("partOfSpeech", "")
        for mean in collector.get("means", []):
            means.append({
                "order": mean.get("order", ""),
                "value": clean_html(mean.get("value", "")),
                "example": clean_html(mean.get("exampleOri", "")),
                "example_trans": clean_html(mean.get("exampleTrans", "")),
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


def load_data():
    global entries, all_entries
    print(f"正在加载数据: {DATA_PATH}")
    if not DATA_PATH.exists():
        print("数据文件不存在，启动空索引")
        entries = {}
        all_entries = []
        return

    count = 0
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in obj:
                continue
            for item in obj.get("items", []):
                entry = item.get("entry", "").strip()
                if not entry:
                    continue
                # 清理释义中的 HTML
                for mean in item.get("means", []):
                    mean["value"] = clean_html(mean["value"])
                    mean["example"] = clean_html(mean["example"])
                    mean["example_trans"] = clean_html(mean["example_trans"])
                if entry not in entries:
                    entries[entry] = []
                entries[entry].append(item)
                count += 1

    all_entries = sorted(entries.keys(), key=lambda x: (len(x), x))
    print(f"加载完成：{len(entries)} 个唯一词条，{count} 条条目")


def append_to_datafile(result: dict):
    """把从 Naver 补充的数据追加到 JSONL 文件。"""
    try:
        with open(DATA_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"追加数据文件失败: {e}")


def add_entry_to_index(item: dict):
    """把单个 item 加入内存索引。"""
    entry = item.get("entry", "").strip()
    if not entry:
        return
    # 清理 HTML
    for mean in item.get("means", []):
        mean["value"] = clean_html(mean["value"])
        mean["example"] = clean_html(mean["example"])
        mean["example_trans"] = clean_html(mean["example_trans"])

    with lock:
        if entry not in entries:
            entries[entry] = []
            all_entries.append(entry)
            all_entries.sort(key=lambda x: (len(x), x))
        entries[entry].append(item)


def fetch_from_naver(query: str) -> Optional[dict]:
    """从 Naver 查询单个词，返回本地格式的结果。"""
    try:
        session = requests.Session()
        # 如需代理，可设置环境变量：HTTP_PROXY / HTTPS_PROXY
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        resp = session.get(
            NAVER_SEARCH_URL,
            params={"query": query.strip()},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        word_data = data.get("searchResultMap", {}).get("searchResultListMap", {}).get("WORD", {})
        items = [parse_naver_item(item) for item in word_data.get("items", [])]
        if not items:
            return None
        return {
            "query": query,
            "total": word_data.get("total", 0),
            "items_count": len(items),
            "items": items,
            "crawled_at": now_iso(),
            "source": "naver_fallback",
        }
    except Exception as e:
        print(f"Naver 回源失败 [{query}]: {e}")
        return None


def should_fetch_from_naver(query: str) -> bool:
    """判断是否需要向 Naver 回源。"""
    now = time.time()
    last = recently_fetched.get(query, 0)
    if now - last < FETCH_COOLDOWN:
        return False
    recently_fetched[query] = now
    return True


def background_naver_fetch(query: str):
    """后台任务：从 Naver 获取数据并补充到本地。"""
    result = fetch_from_naver(query)
    if not result:
        return

    added_count = 0
    for item in result["items"]:
        entry = item.get("entry", "").strip()
        if not entry:
            continue
        # 只补充本地没有的 entry
        with lock:
            exists = entry in entries
        if not exists:
            add_entry_to_index(item)
            added_count += 1

    if added_count > 0:
        append_to_datafile(result)
        print(f"[Naver 补充] query={query}, 新增 {added_count} 个词条")


def search_entries(query: str, limit: int = 50) -> List[dict]:
    """搜索逻辑：精确 > 前缀 > 包含。"""
    if not query:
        return []

    query_lower = query.strip().lower()
    exact = []
    prefix = []
    contains = []

    with lock:
        entries_keys = list(entries.keys())

    for entry in entries_keys:
        entry_lower = entry.lower()
        if entry_lower == query_lower:
            exact.append(entry)
        elif entry_lower.startswith(query_lower):
            prefix.append(entry)
        elif query_lower in entry_lower:
            contains.append(entry)

    # 合并并去重，限制数量
    results = exact + prefix + contains
    seen = set()
    unique = []
    for r in results:
        if r not in seen:
            seen.add(r)
            unique.append(r)
        if len(unique) >= limit:
            break

    # 返回每个 entry 聚合后的数据
    output = []
    for entry in unique:
        items = entries[entry]
        # 按词典来源分组
        by_dict = {}
        for item in items:
            dict_name = item.get("dict_name", "")
            if dict_name not in by_dict:
                by_dict[dict_name] = {
                    "dict_name": dict_name,
                    "dict_name_ori": item.get("dict_name_ori", ""),
                    "language_code": item.get("language_code", ""),
                    "match_type": item.get("match_type", ""),
                    "hanja": set(),
                    "means": [],
                }
            if item.get("hanja"):
                for h in str(item["hanja"]).split("/"):
                    h = h.strip()
                    if h:
                        by_dict[dict_name]["hanja"].add(h)
            by_dict[dict_name]["means"].extend(item.get("means", []))

        for d in by_dict.values():
            d["hanja"] = "/".join(sorted(d["hanja"])) if d["hanja"] else ""

        output.append({
            "entry": entry,
            "items": list(by_dict.values()),
        })
    return output


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"query": "", "results": []})

    # 本地搜索
    results = search_entries(q)

    # 如果本地没有精确匹配，且满足回源条件，启动后台回源
    has_exact = any(r["entry"].lower() == q.lower() for r in results)
    if not has_exact and should_fetch_from_naver(q):
        executor.submit(background_naver_fetch, q)

    return jsonify({"query": q, "results": results})


@app.route("/api/suggest")
def api_suggest():
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify([])
    suggestions = []
    with lock:
        for entry in all_entries:
            if entry.lower().startswith(q):
                suggestions.append(entry)
            if len(suggestions) >= 10:
                break
    return jsonify(suggestions)


if __name__ == "__main__":
    load_data()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
