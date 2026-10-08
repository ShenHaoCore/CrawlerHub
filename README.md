# CrawlerHub

可扩展的多平台爬虫框架，采用模块化设计，每个平台作为独立模块，方便后续扩展小红书、B站等平台。

> ⚠️ **合规声明**：本框架仅供学习研究使用。请仅爬取公开数据，遵守目标平台的 `robots.txt`、服务条款及相关法律法规，合理控制请求频率。Cookie 必须由用户本人合法提供，本框架不实现任何 Cookie 窃取或绕过登录的逻辑。

## 功能特性

- **统一请求客户端**：内置 UA 轮换、代理 IP 池、请求重试、全局限速
- **Cookie 自动管理**：从 JSON 文件加载，支持定时刷新回调
- **数据去重**：基于 URL 或内容哈希，支持内存 / Redis 后端
- **结构化输出**：JSON / CSV / SQLite 三种存储后端可切换
- **统一异常与日志**：基于 loguru 的分级日志与自定义异常体系
- **模块化平台**：微博、抖音、中国行政区划已实现，新增平台只需注册即可
- **任务级并发隔离**：多平台任务异步并发执行，单个任务失败 / 超时 / 崩溃不影响其他任务
- **登录态失效可识别**：被重定向到登录页 / 微博 `ok=-100` / 抖音 `status_code=8` 均判定为任务失败，不伪装成「抓到 0 条」

## 项目结构

```
CrawlerHub/
├── pyproject.toml              # Poetry 依赖与项目配置
├── README.md
├── configs/
│   ├── base.yaml               # 全局配置
│   ├── tasks.yaml              # 多平台任务配置
│   └── platforms/
│       ├── weibo.yaml          # 微博平台配置
│       ├── douyin.yaml         # 抖音平台配置
│       └── xzqh.yaml           # 中国行政区划平台配置
├── crawlerhub/
│   ├── __init__.py
│   ├── __main__.py             # 任务调度入口（并发 + 任务级隔离）
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
│   │   ├── douyin/
│   │   │   ├── crawler.py      # 抖音爬虫
│   │   │   └── parser.py       # 抖音数据解析
│   │   └── xzqh/
│   │       ├── crawler.py      # 中国行政区划爬虫（含全国四级全量快照）
│   │       └── parser.py       # 区划树解析与摊平
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
- Poetry（可选，已有 `.venv` 时不需要）

## 安装步骤

```bash
# 1. 进入项目目录
cd CrawlerHub

# 2. 安装依赖 —— 二选一
poetry install                 # 方式 A：Poetry 可用时
# 或沿用仓库内已有的虚拟环境（跳过安装）
.venv/Scripts/python.exe -m pip install httpx loguru pyyaml tenacity fake-useragent sqlalchemy pytest pytest-asyncio

# 3. 验证依赖与平台注册
.venv/Scripts/python.exe -c "from crawlerhub.platforms.registry import list_platforms; print(list_platforms())"
# 输出: ['weibo', 'douyin', 'xzqh']
```

> **注意**：`crawlerhub` 依赖装在**项目虚拟环境**里，系统 Python 直接跑会报
> `ModuleNotFoundError`。若 `poetry` 不在 PATH（如便携版环境），直接调用
> `.venv/Scripts/python.exe` 即可，无需激活。

## 运行

```bash
# 在项目根目录执行；无任何命令行参数
.venv/Scripts/python.exe -m crawlerhub
```

- **必须先配好 `configs/tasks.yaml`**（默认会跑 weibo + douyin 两个真实抓取任务）。
- **跑之前确认 Cookie 有效**——失效时该任务会直接判定失败（`status: error`），
  而不是伪装成「抓到 0 条」，见下方「登录态失效排查」。
- 运行结果为 JSON 摘要打印到 stdout，日志同时写入 `logs/crawlerhub.log`。
- 退出码：`0` 全部成功 / `1` 有任务失败 / `2` 配置有误。

### 登录态失效排查

登录态失效有三种藏法，**都不是明显的 HTTP 错误**，所以代码里做了显式识别：

| 表现 | 实际发生的事 | 识别方式 |
|---|---|---|
| **302 被静默跟随** | 请求被重定向到登录页，因客户端启用 `follow_redirects`，最终拿到 **HTTP 200 的 HTML** | 回看 `response.history` 的重定向链，命中登录页特征即判 `LoginRequiredError`（`LOGIN_REDIRECT_RE` 覆盖 `login`/`signin`/`sign-in`/`sign_in`/`passport`/`sso`） |
| **200 里藏业务码** | 微博部分接口返回 `{"ok": -100, "url": ".../login.php?..."}` | 业务层判定 `ok`，见 `weibo/parser.raise_if_login_required` |
| **200 里藏业务码** | 抖音返回 `{"status_code": 8, ...}` | 业务层判定 `status_code`，见 `douyin/crawler` |

识别到之后：

- **单用户抓取**路径 → 抛 `LoginRequiredError` 并中断该任务（登录态是全局共享的，
  一个用户拉不到，其余用户同样拉不到，继续试没有意义），任务摘要记 `status: error`、
  进程退出码为 `1`。
- **关联用户扩展**路径 → 只降级：记一条 WARNING 后继续，已抓到的用户数据保留。

> 历史上这里用过最朴素的做法：解析失败就当成「该用户没数据」吞掉。结果是
> Cookie 全面失效时任务仍报 `status: ok / 抓取 0 条`，摘要完全失去意义。
> 现在改为失败要能看得见。

定位时可直接用 curl 验证（注意必须带 `UA`/`Referer`，否则会被 WAF 拦成 403）：

```bash
# 微博：返回 302 且 Location 指向 login.sina.com.cn，即 Cookie 已失效
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" \
  -H "User-Agent: Mozilla/5.0" -H "Referer: https://weibo.com/u/<uid>" \
  -H "Cookie: <你的 cookie 串>" "https://weibo.com/ajax/profile/info?uid=<uid>"

# 微博粉丝/关注接口：失效时返回 200 + {"ok":-100,...}，不会 302，curl 也看不出异常
curl -s -H "User-Agent: Mozilla/5.0" -H "Referer: https://weibo.com/u/<uid>" \
  -H "Cookie: <你的 cookie 串>" \
  "https://weibo.com/ajax/friendships/friends?uid=<uid>&page=1&count=50&type=fans"
```

Cookie 文件里的 `"updated_at": 0` 意味着这份 Cookie 是**手工填入、从未被程序刷新过**，
排障时可先看时间戳判断新旧。

抖音 follower 接口在高频访问下会返回空响应并触发退避（日志中可见
「疑似限流，退避 30 秒」），降低 `max_related_per_user` 或加大请求间隔可缓解。

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

### 运行方式

**没有命令行参数**，全部由 `configs/tasks.yaml` 驱动（命令见上方「运行」一节）：

```bash
.venv/Scripts/python.exe -m crawlerhub
```

`platforms` 下的每个平台都是一个**独立任务**，并发启动、互不干扰——单个任务
失败、超时或崩溃，只影响它自己，其余任务照常跑完。全部任务结束后输出一份
JSON 摘要，有任务失败时进程返回码为 `1`（便于接入 CI）。

配置示例：

```yaml
max_concurrent: 0           # 0 = 不限并发，所有任务同时启动

schedule:
  enabled: false            # true 时按 interval 秒循环执行整批任务
  interval: 3600

platforms:
  weibo:
    uids: ["3967904204"]
    crawl_related: true
    max_related_per_user: 20
    max_depth: 2
    timeout: 1800           # 该任务超过 30 分钟即单独中止，不影响其他任务

  xzqh:
    full: true              # 全国四级全量快照（约 452 次请求）
    timeout: 1800
```

`tasks.yaml` 里除调度键（`enabled` / `timeout` / `uids` / `full` /
`crawl_related` / `max_related_per_user` / `max_depth` / `storage` /
`cookie_file`）以外的键，会**覆盖** `configs/platforms/<平台>.yaml` 中的同名
参数，因此多数调整只需改任务文件一处。

### 任务隔离与并发

调度器在任务边界做了四层隔离：

| 维度 | 行为 |
|---|---|
| 配置 | 每个任务各自加载一份配置（base + 平台 + 任务级覆盖），无共享可变状态 |
| 资源 | HTTP 客户端 / Cookie / 去重器 / 存储实例按任务独立创建 |
| 异常 | 任务抛出的任何异常都收敛为 `status: "error"`，不会中断其余任务 |
| 超时 | `timeout` 秒后只终止该任务自身（`status: "timeout"`），`0` 表示不限 |

`max_concurrent` 用于收窄并发（如设为 `1` 即串行），默认不限以便各任务
尽快开始。注意各任务的请求限速是**各自独立**的（`request.rate_limit` 按
任务生效），任务数越多，对外总请求速率越高，按需调整 `max_concurrent`。

取消信号（Ctrl+C）会正常传播，调度器会先收回仍在运行的任务再退出。

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

## 中国行政区划数据（xzqh）

数据来源为**民政部·国家地名信息库**（<https://dmfw.mca.gov.cn>），覆盖省、地、县、乡四级，
每年 1 月发布截至上一年末的数据（当前版本数据截止 2025-12-31）。

### 输出

在 `tasks.yaml` 中给 xzqh 配置 `full: true`，会**覆盖写入**两个快照文件，路径在 `configs/platforms/xzqh.yaml` 的 `snapshot` 段配置：

| 文件 | 内容 |
|------|------|
| `data/output/xzqh_tree.json` | 嵌套树，结构与官方返回一致，便于直接做多级联动 |
| `data/output/xzqh_divisions.jsonl` | 扁平记录，每行一条，便于导入数据库或表格 |

扁平记录字段：

| 字段 | 说明 |
|------|------|
| `code` | 12 位行政区划代码 |
| `code6` | 前 6 位。省/地/县三级即 GB/T 2260 代码；乡级则指向所属县级单位 |
| `name` | 标准名称 |
| `level` / `level_name` | `1`~`4`，对应 省级 / 地级 / 县级 / 乡级 |
| `type` | 区划单位类型，如 `省` `自治区` `直辖市` `地级市` `市辖区` `县` `街道` `镇` |
| `parent_code` / `parent_name` | 直接上级（全国根节点会被忽略） |
| `full_name` | 自省到本级的完整名称，如 `湖北省武汉市江岸区大智街道` |
| `province_code` / `province_name` | 所属省级 |
| `prefecture_code` / `prefecture_name` | 所属地级（直辖市的区无此级） |
| `county_code` / `county_name` | 所属县级 |

> **上游数据注意**：民政部对**台湾省**返回的代码不是数字，而是占位文本 `资料暂缺`。
> 本模块保留该原文到 `code` 字段，同时把 `code6` 置空（不会截断成非法值）。
> 下游若按 6 位码关联，请对空串做兼容。

### 接口的两个坑

**1. `maxLevel` 的行为与官方文档不一致。** 文档写「最多支持 2 级深度」，实测是
「向根节点之下展开的层数」，但**乡级只在根为地级或县级时才会返回**——以省为根请求
最多只能拿到县级。所以全国四级必须两阶段抓取：

| 阶段 | 请求 | 拿到 |
|------|------|------|
| 一 | `getList?code=&maxLevel=3` | 省 / 地 / 县，约 3214 条，**1 次请求** |
| 二 | `getList?code=<地级码>&maxLevel=2`，逐个执行 | 该地级下的乡级，约 451 次请求 |

第二阶段的下钻对象是树的第 2 层节点，除地级市外还包含**直辖市的市辖区**和
**省直辖县级单位**，因此不能只遍历地级市。

**2. 接口挂在 WAF 之后**，缺失 `User-Agent` 或 `Referer` 会直接返回 403。
本模块已在请求头中固定带上 `Referer`，配置里的 `referer` 项不要删。

### 关于其他官方数据源

同类的另外几个渠道目前都不能用于程序化全量拉取：

| 渠道 | 状态 |
|------|------|
| 民政部·全国行政区划信息查询平台（`xzqh.mca.gov.cn`） | 页面仍在线，但属性查询接口（`getInfo.jsp` / `defaultQuery`）实测均返回空；只剩省级边界 TopoJSON（`/data/{6位省码}.json`）可用 |
| 国家统计局 统计用区划代码 | 自 2024 年 10 月起不再向全社会公开完整代码表，旧页面已 404 / 403 |
| GB/T 2260《中华人民共和国行政区划代码》 | 是标准依据而非数据源；2025-09-01 起由《行政区划代码管理办法》规范，代码统一通过国家地名信息库发布 |

数据口径与覆盖范围请以 <https://dmfw.mca.gov.cn/XzqhVersionPublish.html> 的说明为准。

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
