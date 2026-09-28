# CrawlerHub

可扩展的多平台爬虫框架，采用模块化设计，每个平台作为独立模块，方便后续扩展小红书、B站等平台。

> ⚠️ **合规声明**：本框架仅供学习研究使用。请仅爬取公开数据，遵守目标平台的 `robots.txt`、服务条款及相关法律法规，合理控制请求频率。Cookie 必须由用户本人合法提供，本框架不实现任何 Cookie 窃取或绕过登录的逻辑。

## 功能特性

- **统一请求客户端**：内置 UA 轮换、代理 IP 池、请求重试、全局限速
- **Cookie 自动管理**：从 JSON 文件加载，支持定时刷新回调
- **数据去重**：基于 URL 或内容哈希，支持内存 / Redis 后端
- **结构化输出**：JSON / CSV / SQLite 三种存储后端可切换
- **统一异常与日志**：基于 loguru 的分级日志与自定义异常体系
- **模块化平台**：微博、抖音已实现，新增平台只需注册即可

## 项目结构

```
CrawlerHub/
├── pyproject.toml              # Poetry 依赖与项目配置
├── README.md
├── configs/
│   ├── base.yaml               # 全局配置
│   └── platforms/
│       ├── weibo.yaml          # 微博平台配置
│       └── douyin.yaml         # 抖音平台配置
├── crawlerhub/
│   ├── __init__.py
│   ├── __main__.py             # CLI 入口
│   ├── core/
│   │   ├── base.py             # 爬虫基类 BaseCrawler
│   │   ├── client.py           # 统一 HTTP 客户端（UA/代理/重试/限速）
│   │   ├── config.py           # 配置加载
│   │   ├── cookie.py           # Cookie 管理
│   │   ├── dedup.py            # 去重模块（内存/Redis）
│   │   ├── exceptions.py       # 自定义异常体系
│   │   ├── logger.py           # 日志配置
│   │   └── storage.py          # 存储后端（JSON/CSV/SQLite）
│   ├── platforms/
│   │   ├── base.py             # 平台基类（重新导出）
│   │   ├── registry.py         # 平台注册工厂
│   │   ├── weibo/
│   │   │   ├── crawler.py      # 微博爬虫
│   │   │   └── parser.py       # 微博数据解析
│   │   └── douyin/
│   │       ├── crawler.py      # 抖音爬虫
│   │       └── parser.py       # 抖音数据解析
│   └── utils/
│       ├── hash.py             # 哈希工具（去重用）
│       └── proxy.py            # 代理池工具
├── data/
│   ├── cookies/                # Cookie 文件目录
│   │   ├── weibo.example.json
│   │   └── douyin.example.json
│   ├── proxies/                # 代理池文件目录
│   │   └── proxies.txt
│   └── output/                 # 爬取结果输出目录
└── tests/
```

## 环境要求

- Python 3.10+
- Poetry

## 安装步骤

```bash
# 1. 克隆项目
cd CrawlerHub

# 2. 使用 Poetry 安装依赖
poetry install

# 3. 激活虚拟环境
poetry shell

# 4. 验证安装
python -m crawlerhub --help
```

## 配置说明

### 全局配置 `configs/base.yaml`

| 配置项 | 说明 |
|--------|------|
| `request.timeout` | 请求超时（秒） |
| `request.max_retries` | 最大重试次数 |
| `request.rate_limit.requests_per_second` | 全局限速 |
| `proxy.enabled` | 是否启用代理池 |
| `user_agent.rotation` | 是否启用 UA 轮换 |
| `dedup.backend` | 去重后端（memory / redis） |
| `storage.default` | 存储后端（json / csv / sqlite） |

### Cookie 配置

将合法获取的 Cookie 放入 `data/cookies/<平台>.json`，格式：

```json
{
  "cookie": "SUB=xxx; SUBP=yyy;",
  "uid": "123456",
  "updated_at": 0
}
```

可直接复制 `weibo.example.json` / `douyin.example.json` 并填入真实 Cookie。

## 使用示例

### 命令行

```bash
# 爬取微博用户（数字 UID）
python -m crawlerhub --platform weibo --uids 123456 789012

# 爬取抖音用户（sec_uid）
python -m crawlerhub --platform douyin --uids MS4wLjABAAAAxxxxxxxx

# 指定存储后端为 SQLite
python -m crawlerhub --platform weibo --uids 123456 --storage sqlite

# 自定义配置与 Cookie 文件
python -m crawlerhub --platform douyin --uids xxx --config my_base.yaml --cookie-file my_cookie.json
```

### Python API

```python
import asyncio
from crawlerhub.core.config import load_config
from crawlerhub.core.cookie import CookieManager
from crawlerhub.core.logger import setup_logger
from crawlerhub.platforms.registry import get_platform_cls

async def main():
    setup_logger()

    # 加载配置
    config = load_config("configs/base.yaml", "configs/platforms/weibo.yaml")

    # Cookie 管理
    cookie_mgr = CookieManager("data/cookies/weibo.json")

    # 创建爬虫
    crawler_cls = get_platform_cls("weibo")
    crawler = crawler_cls(config=config, cookie_manager=cookie_mgr)

    # 爬取用户
    users = await crawler.run(["123456", "789012"])
    for u in users:
        print(u["screen_name"], u["followers_count"])

asyncio.run(main())
```

### 扩展新平台

以小红书为例：

1. 在 `crawlerhub/platforms/` 下新建 `xhs/` 子包，包含 `crawler.py` 和 `parser.py`
2. `crawler.py` 继承 `BaseCrawler`，实现 `platform_name` 和 `fetch_user`
3. 在 `crawlerhub/platforms/registry.py` 的 `PLATFORM_REGISTRY` 中注册：

```python
from crawlerhub.platforms.xhs.crawler import XhsCrawler

PLATFORM_REGISTRY = {
    "weibo": WeiboCrawler,
    "douyin": DouyinCrawler,
    "xhs": XhsCrawler,  # 新增
}
```

4. 新建 `configs/platforms/xhs.yaml` 平台配置

### 去重键策略

去重键生成优先级：`content_id` > `url` > `内容哈希`，统一格式为 `platform:key`。
推荐在数据写入前调用 `dedup.check_and_mark()`，并在数据库层建立唯一约束。

## 核心模块说明

| 模块 | 职责 |
|------|------|
| `core/client.py` | 统一 HTTP 客户端，封装 UA 轮换、代理、重试、限速 |
| `core/cookie.py` | Cookie 加载、解析、持久化、自动刷新 |
| `core/dedup.py` | 内存 / Redis 双后端去重，原子 check_and_mark |
| `core/storage.py` | JSON / CSV / SQLite 三种结构化输出 |
| `core/base.py` | 爬虫生命周期基类，统一 setup/run/cleanup |

## 许可证

MIT
