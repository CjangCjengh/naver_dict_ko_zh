# Naver 韩中词典爬虫与本地 WebUI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个用于抓取 [Naver 韩中词典](https://korean.dict.naver.com/)（kozh）并搭建本地查询 WebUI 的开源工具。

主要特性：
- **批量爬虫**：给定词表批量抓取韩中释义。
- **全量发现**：通过 Naver 自动补全接口 + BFS 发现大量词条。
- **本地 WebUI**：类似 Naver 词典的搜索页面，支持实时建议、多词典来源展示。
- **在线回源补充**：本地搜不到的词自动向 Naver 查询并补充到本地数据库。

## 目录

```
.
├── crawler.py                  # 基础查询爬虫
├── full_crawler.py             # 全量词条发现与详情抓取
├── generate_seeds.py           # 生成中韩种子（GB2312 汉字）
├── generate_korean_seeds_full.py # 生成完整韩文音节种子
├── webui/                      # 本地 WebUI
│   ├── app.py
│   ├── templates/index.html
│   └── static/style.css
├── data/                       # 数据与种子
│   ├── kozh_full_kozh.jsonl    # 抓取到的韩中词典数据
│   ├── korean_seeds.txt        # 常用韩文种子
│   └── korean_seeds_full.txt   # 完整韩文音节种子
├── examples/sample.jsonl       # 数据样例
├── requirements.txt
├── LICENSE
└── README.md
```

## 安装

```bash
git clone https://github.com/CjangCjengh/naver_dict_ko_zh
cd naver_dict_zh_ko
pip install -r requirements.txt
```

## 1. 基础查询爬虫

适合已有明确韩文词表，批量抓取释义。

```bash
# 单个词
python crawler.py --direction kozh -q "사랑" -o data/kozh_results.jsonl

# 从文件读取词表
python crawler.py --direction kozh -i words.txt -o data/kozh_results.jsonl -d 1.5
```

参数：
- `--direction`：`zhko`（中韩）或 `kozh`（韩中，默认）
- `-i, --input`：词表文件
- `-o, --output`：输出 JSONL 文件
- `-d, --delay`：请求间隔秒数（默认 1.0）
- `-r, --retries`：失败重试次数（默认 3）

## 2. 全量爬虫

Naver 没有公开的完整词条列表，搜索 API 每次也只返回前 5 条。本工具通过自动补全接口 + BFS 尽可能覆盖。

```bash
# 韩中完整流程
python full_crawler.py --mode both --direction kozh \
  -o data/kozh_full.jsonl \
  --seeds data/korean_seeds.txt \
  -d 1.0
```

常用参数：
- `--mode`：`discover` / `fetch` / `both`
- `--max-queries`：每个阶段最大请求数
- `--max-depth`：自动补全 BFS 最大词条长度（默认 4）
- `--state-dir`：断点续爬状态目录

### 生成种子

```bash
# 完整韩文音节种子（11,172 个）
python generate_korean_seeds_full.py -o data/korean_seeds_full.txt
```

## 3. 本地 WebUI

### 启动

```bash
cd webui
python app.py
```

默认运行在 `http://127.0.0.1:5001`，监听所有地址（`0.0.0.0`）。

如需换端口：

```bash
PORT=8080 python app.py
```

### 特性

- 搜索时实时显示候选建议
- 按词条聚合，展示多个词典来源
- 显示词性、释义、例句
- **本地缓存 + Naver 在线回源**：本地没有的词条会自动向 Naver 查询，查到后补充进本地索引和数据文件

## 数据说明

`data/kozh_full_kozh.jsonl` 是通过爬虫和 WebUI 回源积累的韩中词典数据，JSON Lines 格式。

**数据版权说明**：
- 原始数据版权归 Naver 及其合作词典出版社所有。
- 仓库中附带的数据文件仅供学习研究参考，不代表作者拥有其版权。
- 请勿将数据用于商业用途或公开大规模分发。
- 如需自行获取数据，可运行本项目的爬虫脚本。

## 代理配置

如需代理访问 Naver，设置环境变量即可：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
python crawler.py ...
```

## 数据样例

```json
{
  "query": "사랑",
  "total": 76,
  "items_count": 5,
  "items": [
    {
      "entry_id": "...",
      "entry": "사랑",
      "dict_name": "고려대 한한중사전",
      "match_type": "exact:word",
      "language_code": "KOZH",
      "means": [
        {"order": "", "value": "爱；爱情。", "example": "", "example_trans": "", "part": "명사"}
      ]
    }
  ],
  "crawled_at": "2026-06-16T15:00:00Z"
}
```

## 免责声明

本项目仅供学习研究使用。使用本工具时请遵守 Naver 的服务条款，合理控制请求频率，避免对 Naver 服务器造成压力。因使用本工具产生的任何法律责任由使用者自行承担。

## License

[MIT](LICENSE)
