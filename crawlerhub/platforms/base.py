"""平台爬虫基类（从 core.base 重新导出）。

平台模块通过 ``from crawlerhub.platforms.base import BaseCrawler`` 引入，
保持平台层的导入路径简洁。
"""

from crawlerhub.core.base import BaseCrawler

__all__ = ["BaseCrawler"]
