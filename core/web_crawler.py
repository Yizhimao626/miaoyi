"""
喵艺多源网络爬虫模块 - WebCrawler
====================================
为喵艺 AI 伴侣提供实时数据获取能力：
1. 天气查询 (wttr.in + 备用源)
2. 新闻资讯 (Google News RSS + 备用源)
3. 热搜榜单 (百度/微博热榜)
4. 知识百科 (Wikipedia API)

设计原则：
- 4 种数据源，每种都有主源和备用源
- 速率限制：每源每 3 秒最多 1 次请求
- 内存缓存：TTL 300 秒，避免重复请求
- 优雅降级：主源失败自动切换备用源
- 统一格式：所有结果返回 {'success', 'source', 'data', 'error'}
- 网络安全：超时保护、异常隔离，单源失败不影响全局

作者：喵艺灵魂工程师
版本：1.0.0
"""

import json
import logging
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote, quote_plus

_log = logging.getLogger("WebCrawler")


# ========== 可选依赖导入（优雅降级） ==========

try:
    import httpx
    HTTPX_OK = True
except ImportError:
    HTTPX_OK = False
    _log.warning("httpx 未安装，部分爬虫功能将使用同步 requests 降级")

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False
    _log.warning("beautifulsoup4 未安装，HTML 解析功能不可用")

try:
    import requests as req_lib
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


# ========== 天气描述映射（英文 -> 中文） ==========

WEATHER_DESC_MAP = {
    'clear': '晴',
    'sunny': '晴',
    'partly cloudy': '多云',
    'partly-cloudy': '多云',
    'cloudy': '阴',
    'overcast': '阴天',
    'mist': '薄雾',
    'fog': '雾',
    'freezing fog': '冻雾',
    'patchy rain possible': '可能有零星小雨',
    'patchy rain nearby': '附近有零星小雨',
    'patchy light rain': '零星小雨',
    'patchy rain': '零星小雨',
    'light rain': '小雨',
    'moderate rain at times': '间歇性中雨',
    'moderate rain': '中雨',
    'heavy rain at times': '间歇性大雨',
    'heavy rain': '大雨',
    'torrential rain shower': '暴雨',
    'patchy snow possible': '可能有零星小雪',
    'light snow': '小雪',
    'moderate snow': '中雪',
    'heavy snow': '大雪',
    'blizzard': '暴风雪',
    'thundery outbreaks possible': '可能有雷阵雨',
    'patchy sleet possible': '可能有零星雨夹雪',
    'patchy freezing drizzle possible': '可能有冻毛毛雨',
    'light drizzle': '毛毛雨',
    'freezing drizzle': '冻毛毛雨',
    'light rain shower': '小阵雨',
    'moderate or heavy rain shower': '中到大阵雨',
    'light snow showers': '小阵雪',
    'moderate or heavy snow showers': '中到大阵雪',
    'patchy light drizzle': '零星毛毛雨',
    'patchy heavy drizzle': '零星大毛毛雨',
    'light rain with thunder': '雷雨',
    'moderate or heavy rain with thunder': '中到大雷雨',
}


def _translate_weather_desc(desc: str) -> str:
    """将英文天气描述翻译为中文"""
    desc_lower = desc.lower().strip()
    # 精确匹配
    if desc_lower in WEATHER_DESC_MAP:
        return WEATHER_DESC_MAP[desc_lower]
    # 模糊匹配
    if 'thunder' in desc_lower or 'storm' in desc_lower:
        return '雷阵雨'
    if 'rain' in desc_lower and 'heavy' in desc_lower:
        return '大雨'
    if 'rain' in desc_lower and 'moderate' in desc_lower:
        return '中雨'
    if 'rain' in desc_lower:
        return '小雨'
    if 'snow' in desc_lower and 'heavy' in desc_lower:
        return '大雪'
    if 'snow' in desc_lower:
        return '小雪'
    if 'cloud' in desc_lower or 'overcast' in desc_lower:
        return '阴'
    if 'sun' in desc_lower or 'clear' in desc_lower:
        return '晴'
    if 'fog' in desc_lower or 'mist' in desc_lower:
        return '雾'
    # 无法翻译则返回原文
    return desc


# ========== 缓存系统 ==========

class _MemoryCache:
    """内存 TTL 缓存 - 线程安全"""

    def __init__(self, ttl: int = 300, max_size: int = 200):
        self._ttl = ttl
        self._max_size = max_size
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """获取缓存值，如果过期则返回 None"""
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            # 检查是否过期
            if time.time() - item['ts'] > self._ttl:
                del self._cache[key]
                return None
            return item['value']

    def set(self, key: str, value: Any):
        """设置缓存值"""
        with self._lock:
            self._cache[key] = {
                'ts': time.time(),
                'value': value
            }
            # 超出容量时淘汰最旧的
            if len(self._cache) > self._max_size:
                sorted_keys = sorted(
                    self._cache.keys(),
                    key=lambda k: self._cache[k]['ts']
                )
                for k in sorted_keys[:len(self._cache) - self._max_size]:
                    del self._cache[k]

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """返回缓存条目数"""
        with self._lock:
            return len(self._cache)


# ========== 限流系统 ==========

class _RateLimiter:
    """请求速率限制器 - 每源每 3 秒最多 1 次请求"""

    def __init__(self, interval: float = 3.0):
        self._interval = interval
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def can_proceed(self, source: str) -> bool:
        """检查指定源是否可以发起请求"""
        with self._lock:
            now = time.time()
            last = self._last_request.get(source, 0)
            if now - last >= self._interval:
                self._last_request[source] = now
                return True
            return False

    def wait_time(self, source: str) -> float:
        """获取需要等待的秒数"""
        with self._lock:
            now = time.time()
            last = self._last_request.get(source, 0)
            remaining = self._interval - (now - last)
            return max(0, remaining)


# ========== 统一响应格式化 ==========

def _make_result(success: bool, source: str, data: Any = None,
                 error: str = "") -> dict[str, Any]:
    """构造统一的返回结果"""
    return {
        'success': success,
        'source': source,
        'data': data,
        'error': error
    }


# ========== WebCrawler 主类 ==========

class WebCrawler:
    """多源网络爬虫 - 为喵艺提供实时数据能力

    支持的数据源：
    - weather: 天气查询
    - news: 新闻资讯
    - hot_search: 热搜榜单
    - knowledge: 知识百科

    使用示例：
        crawler = WebCrawler()
        weather = crawler.get_weather("北京")
        news = crawler.get_news("科技", count=5)
        hot = crawler.get_hot_search("baidu", count=10)
        knowledge = crawler.get_knowledge("量子计算")
        result = crawler.crawl_all("今天北京天气怎么样")
        crawler.shutdown()
    """

    # 随机 User-Agent 列表，模拟浏览器请求
    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Edge/120.0.0.0",
    ]

    def __init__(self, cache_ttl: int = 300, rate_interval: float = 3.0):
        """初始化爬虫

        Args:
            cache_ttl: 缓存有效期（秒），默认 300 秒
            rate_interval: 每源请求间隔（秒），默认 3 秒
        """
        self._cache = _MemoryCache(ttl=cache_ttl)
        self._rate_limiter = _RateLimiter(interval=rate_interval)
        self._shutdown = False
        self._client = None  # httpx 客户端，按需创建

    # ---------- 内部辅助方法 ----------

    def _get_headers(self, referer: str = "") -> dict[str, str]:
        """获取随机请求头"""
        headers = {
            'User-Agent': random.choice(self._USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        }
        if referer:
            headers['Referer'] = referer
        return headers

    def _get_httpx_client(self) -> Optional['httpx.Client']:
        """获取或创建 httpx 客户端"""
        if not HTTPX_OK:
            return None
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=True,
                headers=self._get_headers()
            )
        return self._client

    def _http_get(self, url: str, params: dict | None = None,
                  timeout: float = 10.0) -> str | None:
        """统一的 HTTP GET 请求 - 优先使用 httpx，降级到 requests

        Args:
            url: 请求 URL
            params: 查询参数
            timeout: 超时时间（秒）

        Returns:
            响应文本，失败返回 None
        """
        # 优先使用 httpx
        if HTTPX_OK:
            try:
                client = self._get_httpx_client()
                if client:
                    resp = client.get(url, params=params, timeout=timeout)
                    if resp.status_code == 200:
                        return resp.text
                    _log.debug(f"HTTP {resp.status_code}: {url[:80]}")
                    return None
            except httpx.TimeoutException:
                _log.debug(f"httpx 超时: {url[:80]}")
                return None
            except httpx.ConnectError:
                _log.debug(f"httpx 连接失败: {url[:80]}")
                return None
            except Exception as e:
                _log.debug(f"httpx 异常: {url[:80]}, {e}")
                # 降级到 requests

        # 降级使用 requests
        if REQUESTS_OK:
            try:
                resp = req_lib.get(
                    url, params=params,
                    headers=self._get_headers(),
                    timeout=timeout
                )
                if resp.status_code == 200:
                    return resp.text
                _log.debug(f"requests HTTP {resp.status_code}: {url[:80]}")
                return None
            except req_lib.Timeout:
                _log.debug(f"requests 超时: {url[:80]}")
                return None
            except req_lib.ConnectionError:
                _log.debug(f"requests 连接失败: {url[:80]}")
                return None
            except Exception as e:
                _log.debug(f"requests 异常: {url[:80]}, {e}")
                return None

        # 无可用 HTTP 库
        if not HTTPX_OK and not REQUESTS_OK:
            _log.error("无可用的 HTTP 库（需要 httpx 或 requests）")
        return None

    def _http_get_json(self, url: str,
                       params: dict | None = None,
                       timeout: float = 10.0) -> Any | None:
        """HTTP GET 并解析 JSON"""
        text = self._http_get(url, params=params, timeout=timeout)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            _log.debug(f"JSON 解析失败: {url[:80]}")
            return None

    def _with_fallback(self, source_name: str,
                       primary_fn, *args, **kwargs) -> dict[str, Any]:
        """带降级链的请求封装

        流程：
        1. 检查缓存 → 命中直接返回
        2. 检查限流 → 超限则等待
        3. 执行主源请求 → 成功则缓存返回
        4. 主源失败 → 尝试备用源
        5. 全部失败 → 返回结构化错误

        Args:
            source_name: 源名称（用于缓存键和限流）
            primary_fn: 主源请求函数
            *args, **kwargs: 传递给请求函数的参数

        Returns:
            统一格式的结果
        """
        # 【加固】backup_fn/backup_source 是降级链参数，必须在主源调用前弹出——
        # 否则主源函数收到非法关键字参数报错，主源永远走不通（存量 bug）
        backup_fn = kwargs.pop('backup_fn', None)
        backup_source = kwargs.pop('backup_source', None)

        # 1. 检查缓存
        cache_key = f"{source_name}:{args}:{sorted(kwargs.items())}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            _log.debug(f"[缓存命中] {source_name}")
            return cached

        # 2. 检查限流
        if not self._rate_limiter.can_proceed(source_name):
            wait = self._rate_limiter.wait_time(source_name)
            _log.debug(f"[限流] {source_name} 需等待 {wait:.1f}s")
            time.sleep(wait)
            if not self._rate_limiter.can_proceed(source_name):
                return _make_result(
                    False, source_name, None,
                    "请求过于频繁，请稍后再试"
                )

        # 3. 尝试主源
        try:
            result = primary_fn(*args, **kwargs)
            if result and result.get('success'):
                self._cache.set(cache_key, result)
                return result
            _log.debug(f"[主源失败] {source_name}: {result.get('error', '未知错误') if result else 'None'}")
        except Exception as e:
            _log.error(f"[主源异常] {source_name}: {e}")

        # 4. 主源失败，尝试备用源（backup_fn/backup_source 已在方法开头弹出）
        if backup_fn and backup_source:
            try:
                if not self._rate_limiter.can_proceed(backup_source):
                    time.sleep(self._rate_limiter.wait_time(backup_source))
                result = backup_fn(*args, **kwargs)
                if result and result.get('success'):
                    self._cache.set(cache_key, result)
                    return result
                _log.debug(f"[备用源也失败] {backup_source}")
            except Exception as e:
                _log.error(f"[备用源异常] {backup_source}: {e}")

        # 5. 全部失败
        return _make_result(
            False, source_name, None,
            "所有数据源均不可用，请稍后重试"
        )

    # ---------- 天气爬虫 ----------

    def _crawl_weather_wttr(self, city: str) -> dict[str, Any]:
        """主源：wttr.in 免费天气服务

        wttr.in 特点：
        - 完全免费，无需 API Key
        - 支持全球城市查询
        - 返回 HTML 格式，用 BeautifulSoup 解析

        Args:
            city: 城市名（中文或英文）

        Returns:
            天气结果字典
        """
        if not BS4_OK:
            return _make_result(False, 'wttr.in', None, '需要 beautifulsoup4')

        try:
            # wttr.in 支持中文城市名，直接使用
            url = f"https://wttr.in/{quote(city)}?format=j1"
            data = self._http_get_json(url, timeout=6.0)

            if data is None:
                # 降级到 HTML 格式解析
                return self._crawl_weather_wttr_html(city)

            # 解析 JSON 格式
            current = data.get('current_condition', [{}])[0]
            weather_area = data.get('nearest_area', [{}])[0]
            weather_desc_list = current.get('weatherDesc', [{}])
            weather_desc = weather_desc_list[0].get('value', '') if weather_desc_list else ''

            # 提取关键信息
            temp_c = current.get('temp_C', 'N/A')
            humidity = current.get('humidity', 'N/A')
            windspeed = current.get('windspeedKmph', 'N/A')
            weather_code = current.get('weatherCode', '')

            # 风力等级描述
            wind_desc = self._get_wind_description(int(windspeed) if windspeed else 0)

            # 天气描述翻译
            weather_cn = _translate_weather_desc(weather_desc)

            # 城市名处理
            area_name = weather_area.get('areaName', [{}])
            area_cn = area_name[0].get('value', city) if area_name else city

            result = {
                'city': city,
                'temperature': f"{temp_c}℃",
                'weather': weather_cn,
                'weather_en': weather_desc,
                'humidity': f"{humidity}%",
                'wind': wind_desc,
                'windspeed_kmph': windspeed,
                'feels_like': f"{current.get('FeelsLikeC', 'N/A')}℃",
                'visibility': f"{current.get('visibility', 'N/A')}km",
                'pressure': f"{current.get('pressure', 'N/A')}hPa",
                # 喵艺风格的中文描述
                'description': f"{city}现在{temp_c}℃，{weather_cn}，湿度{humidity}%，{wind_desc}"
            }

            _log.info(f"[天气] {city}: {temp_c}℃, {weather_cn}")
            return _make_result(True, 'wttr.in', result)

        except Exception as e:
            _log.error(f"[wttr.in JSON 解析失败] {city}: {e}")
            # 降级到 HTML 格式
            return self._crawl_weather_wttr_html(city)

    def _crawl_weather_wttr_html(self, city: str) -> dict[str, Any]:
        """备用：wttr.in HTML 格式解析"""
        if not BS4_OK:
            return _make_result(False, 'wttr.in_html', None, '需要 beautifulsoup4')

        try:
            url = f"https://wttr.in/{quote(city)}"
            html = self._http_get(url, timeout=6.0)

            if html is None:
                return _make_result(False, 'wttr.in_html', None, '网络请求失败')

            soup = BeautifulSoup(html, 'html.parser')

            # 解析当前温度
            temp_elem = soup.find('span', class_='temp')
            temp_c = temp_elem.get_text(strip=True).replace('°C', '') if temp_elem else 'N/A'

            # 解析天气描述
            # wttr.in 的天气描述在 <img> alt 或下方文本中
            weather_elem = soup.find('div', class_='weather-info')
            weather_text = ''
            if weather_elem:
                img = weather_elem.find('img')
                if img and img.get('alt'):
                    weather_text = img['alt']
                else:
                    weather_text = weather_elem.get_text(strip=True)[:30]

            weather_cn = _translate_weather_desc(weather_text) if weather_text else '未知'

            # 解析其他信息
            stats = soup.find_all('div', class_='stat')
            humidity = 'N/A'
            wind = 'N/A'
            for stat in stats:
                label = stat.find('span', class_='label')
                value = stat.find('span', class_='value')
                if label and value:
                    label_text = label.get_text(strip=True)
                    value_text = value.get_text(strip=True)
                    if '湿度' in label_text or 'humidity' in label_text.lower():
                        humidity = value_text
                    elif '风' in label_text or 'wind' in label_text.lower():
                        wind = value_text

            result = {
                'city': city,
                'temperature': f"{temp_c}℃",
                'weather': weather_cn,
                'weather_en': weather_text,
                'humidity': humidity if humidity != 'N/A' else 'N/A',
                'wind': wind if wind != 'N/A' else '未知',
                'windspeed_kmph': 'N/A',
                'feels_like': 'N/A',
                'visibility': 'N/A',
                'pressure': 'N/A',
                'description': f"{city}现在{temp_c}℃，{weather_cn}，湿度{humidity}"
            }

            return _make_result(True, 'wttr.in_html', result)

        except Exception as e:
            _log.error(f"[wttr.in HTML 解析失败] {city}: {e}")
            return _make_result(False, 'wttr.in_html', None, str(e))

    def _get_wind_description(self, speed_kmph: int) -> str:
        """将风速(km/h)转换为风力等级描述"""
        if speed_kmph < 1:
            return '无风'
        elif speed_kmph < 6:
            return '微风'
        elif speed_kmph < 12:
            return '轻风'
        elif speed_kmph < 20:
            return '微风'
        elif speed_kmph < 29:
            return '和风'
        elif speed_kmph < 39:
            return '清风'
        elif speed_kmph < 50:
            return '强风'
        elif speed_kmph < 62:
            return '疾风'
        elif speed_kmph < 75:
            return '大风'
        elif speed_kmph < 89:
            return '烈风'
        elif speed_kmph < 103:
            return '狂风'
        else:
            return '暴风'

    # ---------- 新闻爬虫 ----------

    def _crawl_news_google_rss(self, keyword: str,
                                count: int = 5) -> list[dict]:
        """主源：Google News RSS 订阅

        Google News RSS 特点：
        - 免费无需注册
        - 支持关键词搜索
        - 返回标准 RSS 2.0 格式

        Args:
            keyword: 搜索关键词
            count: 返回条数

        Returns:
            新闻列表
        """
        try:
            # Google News RSS 支持 hl=zh-CN 和 gl=CN 参数
            url = (
                f"https://news.google.com/rss/search?"
                f"q={quote_plus(keyword)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            )
            xml_text = self._http_get(url, timeout=6.0)

            if xml_text is None:
                return []

            # 解析 RSS XML
            root = ET.fromstring(xml_text)
            channel = root.find('channel')
            if channel is None:
                return []

            items = channel.findall('item')
            results = []

            for item in items[:count]:
                title_elem = item.find('title')
                link_elem = item.find('link')
                pub_date_elem = item.find('pubDate')
                source_elem = item.find('source')
                desc_elem = item.find('description')

                title = title_elem.text if title_elem is not None else ''
                link = link_elem.text if link_elem is not None else ''
                pub_date = pub_date_elem.text if pub_date_elem is not None else ''
                source = source_elem.text if source_elem is not None else ''

                # 清理描述中的 HTML
                description = ''
                if desc_elem is not None and desc_elem.text:
                    description = re.sub(r'<[^>]+>', '', desc_elem.text)[:200]

                results.append({
                    'title': title,
                    'url': link,
                    'source': source,
                    'published': pub_date,
                    'description': description
                })

            _log.info(f"[Google News] 关键词: {keyword}, 获取 {len(results)} 条")
            return results

        except ET.ParseError as e:
            _log.error(f"[RSS 解析失败] {keyword}: {e}")
            return []
        except Exception as e:
            _log.error(f"[Google News 失败] {keyword}: {e}")
            return []

    def _crawl_news_baidu_rss(self, keyword: str,
                               count: int = 5) -> list[dict]:
        """备用源：百度新闻搜索

        使用百度新闻搜索结果作为备用
        """
        if not BS4_OK:
            return []

        try:
            url = f"https://www.baidu.com/s?wd={quote_plus(keyword)}&tn=news"
            html = self._http_get(url, timeout=6.0)

            if html is None:
                return []

            soup = BeautifulSoup(html, 'html.parser')
            results = []

            for item in soup.select('div.result'):
                title_elem = item.select_one('h3 a')
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                link = title_elem.get('href', '')

                # 摘要
                abstract_elem = item.select_one('.c-abstract, .c-span-last p')
                abstract = abstract_elem.get_text(strip=True)[:200] if abstract_elem else ''

                # 来源
                source_elem = item.select_one('.c-color-gray, .c-color-gray2')
                source_text = source_elem.get_text(strip=True) if source_elem else ''

                results.append({
                    'title': title,
                    'url': link,
                    'source': source_text,
                    'published': '',
                    'description': abstract
                })

                if len(results) >= count:
                    break

            _log.info(f"[百度新闻] 关键词: {keyword}, 获取 {len(results)} 条")
            return results

        except Exception as e:
            _log.error(f"[百度新闻失败] {keyword}: {e}")
            return []

    # ---------- 热搜爬虫 ----------

    def _crawl_hot_baidu(self, count: int = 10) -> list[dict]:
        """主源：百度热搜

        百度热搜特点：
        - 实时更新
        - 中文热搜榜单
        - 无需登录

        Args:
            count: 返回条数

        Returns:
            热搜列表
        """
        if not BS4_OK:
            return []

        try:
            url = "https://top.baidu.com/board?tab=realtime"
            html = self._http_get(url, timeout=6.0)

            if html is None:
                return []

            soup = BeautifulSoup(html, 'html.parser')
            results = []

            # 百度热搜使用 category-wrap 包裹每个热搜项
            items = soup.select('div.category-wrap_iQLoo, div.category-wrap')
            if not items:
                # 尝试其他选择器
                items = soup.select('div[class*="category-wrap"]')

            for item in items[:count]:
                # 提取排名
                rank_elem = item.select_one('div.rank_1Iw7q, .rank-index')
                rank = rank_elem.get_text(strip=True) if rank_elem else ''

                # 提取标题
                title_elem = item.select_one('div.c-single-text-ellipsis, .title_1Ij4D')
                title = title_elem.get_text(strip=True) if title_elem else ''

                # 提取热度
                hot_elem = item.select_one('div.hot-index_1Bl1a, .hot-index')
                hot_value = hot_elem.get_text(strip=True) if hot_elem else ''

                # 提取链接
                link_elem = item.select_one('a')
                link = link_elem.get('href', '') if link_elem else ''
                if link and not link.startswith('http'):
                    link = 'https://top.baidu.com' + link

                if title:
                    results.append({
                        'rank': rank,
                        'title': title,
                        'hot_value': hot_value,
                        'url': link,
                        'platform': 'baidu'
                    })

            # 如果解析失败，使用 JSON 接口
            if not results:
                return self._crawl_hot_baidu_api(count)

            _log.info(f"[百度热搜] 获取 {len(results)} 条")
            return results

        except Exception as e:
            _log.error(f"[百度热搜解析失败] {e}")
            return self._crawl_hot_baidu_api(count)

    def _crawl_hot_baidu_api(self, count: int = 10) -> list[dict]:
        """百度热搜 JSON API 备用"""
        try:
            url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
            data = self._http_get_json(url, timeout=8.0)

            if data is None:
                return []

            cards = data.get('data', {}).get('cards', [])
            results = []

            for card in cards:
                content_list = card.get('content', [])
                for item in content_list[:count]:
                    title = item.get('word', item.get('query', ''))
                    desc = item.get('desc', '')
                    url = item.get('url', '')
                    hot_score = item.get('hotScore', '')

                    if title:
                        results.append({
                            'rank': str(len(results) + 1),
                            'title': title,
                            'hot_value': str(hot_score),
                            'url': url,
                            'description': desc[:100] if desc else '',
                            'platform': 'baidu'
                        })

                    if len(results) >= count:
                        break

                if len(results) >= count:
                    break

            return results

        except Exception as e:
            _log.error(f"[百度热搜 API 失败] {e}")
            return []

    def _crawl_hot_weibo(self, count: int = 10) -> list[dict]:
        """备用源：微博热搜

        微博热搜特点：
        - 实时社会热点
        - 话题标签丰富

        Args:
            count: 返回条数

        Returns:
            热搜列表
        """
        if not BS4_OK:
            return []

        try:
            url = "https://s.weibo.com/top/summary"
            html = self._http_get(
                url, timeout=6.0,
            )

            if html is None:
                # 尝试移动端
                return self._crawl_hot_weibo_mobile(count)

            soup = BeautifulSoup(html, 'html.parser')
            results = []

            # 微博热搜表格解析
            table = soup.find('table')
            if table:
                rows = table.find_all('tr')
                for row in rows[:count + 1]:
                    td_list = row.find_all('td')
                    if len(td_list) >= 2:
                        rank_td = td_list[0]
                        title_td = td_list[1]

                        rank = rank_td.get_text(strip=True)
                        title_a = title_td.find('a')
                        title = title_a.get_text(strip=True) if title_a else ''
                        link = title_a.get('href', '') if title_a else ''
                        if link and not link.startswith('http'):
                            link = 'https://s.weibo.com' + link

                        hot_span = title_td.find('span')
                        hot_value = hot_span.get_text(strip=True) if hot_span else ''

                        if title and rank.isdigit():
                            results.append({
                                'rank': rank,
                                'title': title,
                                'hot_value': hot_value,
                                'url': link,
                                'platform': 'weibo'
                            })

            if not results:
                return self._crawl_hot_weibo_mobile(count)

            _log.info(f"[微博热搜] 获取 {len(results)} 条")
            return results

        except Exception as e:
            _log.error(f"[微博热搜解析失败] {e}")
            return self._crawl_hot_weibo_mobile(count)

    def _crawl_hot_weibo_mobile(self, count: int = 10) -> list[dict]:
        """微博移动端热搜备用"""
        try:
            url = "https://weibo.com/ajax/side/hotSearch"
            data = self._http_get_json(url, timeout=8.0)

            if data is None:
                return []

            realtime = data.get('data', {}).get('realtime', [])
            results = []

            for item in realtime[:count]:
                word = item.get('word', item.get('note', ''))
                num = item.get('num', '')
                label = item.get('label_name', '')
                icon_desc = item.get('icon_desc', '')

                if word:
                    results.append({
                        'rank': str(len(results) + 1),
                        'title': word,
                        'hot_value': str(num),
                        'url': f"https://s.weibo.com/weibo?q=%23{quote_plus(word)}%23",
                        'label': label,
                        'icon_desc': icon_desc,
                        'platform': 'weibo'
                    })

            return results

        except Exception as e:
            _log.error(f"[微博移动端热搜失败] {e}")
            return []

    # ---------- 知识爬虫 ----------

    def _crawl_wikipedia(self, query: str) -> dict[str, Any]:
        """主源：Wikipedia REST API

        Wikipedia API 特点：
        - 完全免费，无需注册
        - 支持多语言
        - 返回结构化 JSON

        Args:
            query: 查询关键词

        Returns:
            知识结果
        """
        try:
            # 先搜索匹配的页面
            search_url = (
                f"https://zh.wikipedia.org/w/api.php?"
                f"action=query&list=search&srsearch={quote_plus(query)}"
                f"&srlimit=1&format=json"
            )
            search_data = self._http_get_json(search_url, timeout=6.0)

            if search_data is None:
                return _make_result(False, 'wikipedia', None, '网络请求失败')

            # 提取页面标题
            search_results = search_data.get('query', {}).get('search', [])
            if not search_results:
                # 直接用原始查询获取摘要
                return self._crawl_wikipedia_summary(query)

            page_title = search_results[0].get('title', query)
            return self._crawl_wikipedia_summary(page_title)

        except Exception as e:
            _log.error(f"[Wikipedia 搜索失败] {query}: {e}")
            return _make_result(False, 'wikipedia', None, str(e))

    def _crawl_wikipedia_summary(self, title: str) -> dict[str, Any]:
        """获取 Wikipedia 页面摘要"""
        try:
            # REST API 获取页面摘要
            url = (
                f"https://zh.wikipedia.org/api/rest_v1/page/summary/"
                f"{quote(title)}"
            )
            data = self._http_get_json(url, timeout=6.0)

            if data is None:
                return _make_result(False, 'wikipedia_summary', None, '网络请求失败')

            page_title = data.get('title', title)
            summary = data.get('extract', '')
            full_url = data.get('content_urls', {}).get('desktop', {}).get('page', '')
            thumbnail = data.get('originalimage', {}).get('source', '')
            description = data.get('description', '')

            if not summary and not description:
                return _make_result(
                    False, 'wikipedia_summary', None,
                    f'未找到「{title}」的相关信息'
                )

            result = {
                'title': page_title,
                'summary': summary[:500] if summary else '',
                'description': description,
                'url': full_url,
                'thumbnail': thumbnail,
                'source': 'Wikipedia 中文'
            }

            _log.info(f"[Wikipedia] 获取「{page_title}」摘要成功")
            return _make_result(True, 'wikipedia_summary', result)

        except Exception as e:
            _log.error(f"[Wikipedia 摘要失败] {title}: {e}")
            return _make_result(False, 'wikipedia_summary', None, str(e))

    def _crawl_wikipedia_en(self, query: str) -> dict[str, Any]:
        """备用源：Wikipedia 英文版"""
        try:
            # 尝试英文版
            url = (
                f"https://en.wikipedia.org/api/rest_v1/page/summary/"
                f"{quote(query)}"
            )
            data = self._http_get_json(url, timeout=6.0)

            if data is None:
                return _make_result(False, 'wikipedia_en', None, '网络请求失败')

            page_title = data.get('title', query)
            summary = data.get('extract', '')
            full_url = data.get('content_urls', {}).get('desktop', {}).get('page', '')

            if not summary:
                return _make_result(
                    False, 'wikipedia_en', None,
                    f'未找到「{query}」的相关信息'
                )

            result = {
                'title': page_title,
                'summary': summary[:500],
                'description': data.get('description', ''),
                'url': full_url,
                'source': 'Wikipedia English'
            }

            return _make_result(True, 'wikipedia_en', result)

        except Exception as e:
            _log.error(f"[Wikipedia 英文失败] {query}: {e}")
            return _make_result(False, 'wikipedia_en', None, str(e))

    def _crawl_baidu_baike_knowledge(self, query: str) -> dict[str, Any]:
        """备用源：百度百科"""
        if not BS4_OK:
            return _make_result(False, 'baidu_baike', None, '需要 beautifulsoup4')

        try:
            url = f"https://baike.baidu.com/item/{quote(query)}"
            html = self._http_get(url, timeout=6.0)

            if html is None:
                return _make_result(False, 'baidu_baike', None, '网络请求失败')

            soup = BeautifulSoup(html, 'html.parser')

            # 提取摘要
            summary_elem = soup.find('div', class_='lemma-summary')
            if not summary_elem:
                summary_elem = soup.find('div', class_='para')

            if not summary_elem:
                return _make_result(False, 'baidu_baike', None, '未找到摘要')

            summary = summary_elem.get_text(strip=True)[:500]

            result = {
                'title': query,
                'summary': summary,
                'url': url,
                'source': '百度百科'
            }

            _log.info(f"[百度百科] 获取「{query}」摘要成功")
            return _make_result(True, 'baidu_baike', result)

        except Exception as e:
            _log.error(f"[百度百科失败] {query}: {e}")
            return _make_result(False, 'baidu_baike', None, str(e))

    # ---------- 公共 API ----------

    def get_weather(self, city: str) -> dict[str, Any]:
        """获取天气信息

        Args:
            city: 城市名（支持中文，如"北京"、"上海"）

        Returns:
            {
                'success': bool,
                'source': str,
                'data': {
                    'city': str,
                    'temperature': str,
                    'weather': str,          # 中文天气描述
                    'humidity': str,
                    'wind': str,
                    'description': str,     # 喵艺风格描述
                    ...
                },
                'error': str
            }

        示例：
            result = crawler.get_weather("北京")
            # data.description = "北京现在18℃，晴，湿度60%，微风"
        """
        _log.info(f"[天气查询] {city}")
        return self._with_fallback(
            source_name='weather',
            primary_fn=self._crawl_weather_wttr,
            city=city,
            backup_fn=self._crawl_weather_wttr_html,
            backup_source='weather_html'
        )

    def get_news(self, keyword: str, count: int = 5) -> dict[str, Any]:
        """获取新闻资讯

        Args:
            keyword: 搜索关键词（如"科技"、"AI"）
            count: 返回条数，默认 5

        Returns:
            {
                'success': bool,
                'source': str,
                'data': [
                    {
                        'title': str,
                        'url': str,
                        'source': str,
                        'published': str,
                        'description': str
                    },
                    ...
                ],
                'error': str
            }
        """
        _log.info(f"[新闻查询] {keyword}, count={count}")

        def primary():
            items = self._crawl_news_google_rss(keyword, count)
            if items:
                return _make_result(True, 'google_news', items)
            return _make_result(False, 'google_news', None, '无结果')

        def backup():
            items = self._crawl_news_baidu_rss(keyword, count)
            if items:
                return _make_result(True, 'baidu_news', items)
            return _make_result(False, 'baidu_news', None, '无结果')

        return self._with_fallback(
            source_name='news',
            primary_fn=primary,
            backup_fn=backup,
            backup_source='news_backup'
        )

    def get_hot_search(self, platform: str = 'baidu',
                       count: int = 10) -> dict[str, Any]:
        """获取热搜榜单

        Args:
            platform: 平台 ('baidu' 或 'weibo')
            count: 返回条数，默认 10

        Returns:
            {
                'success': bool,
                'source': str,
                'data': [
                    {
                        'rank': str,
                        'title': str,
                        'hot_value': str,
                        'url': str,
                        'platform': str
                    },
                    ...
                ],
                'error': str
            }
        """
        _log.info(f"[热搜查询] platform={platform}, count={count}")

        if platform == 'baidu':
            def primary():
                items = self._crawl_hot_baidu(count)
                if items:
                    return _make_result(True, 'baidu_hot', items)
                return _make_result(False, 'baidu_hot', None, '无结果')

            def backup():
                items = self._crawl_hot_weibo(count)
                if items:
                    return _make_result(True, 'weibo_hot', items)
                return _make_result(False, 'weibo_hot', None, '无结果')

            return self._with_fallback(
                source_name='hot_baidu',
                primary_fn=primary,
                backup_fn=backup,
                backup_source='hot_weibo'
            )

        elif platform == 'weibo':
            def primary():
                items = self._crawl_hot_weibo(count)
                if items:
                    return _make_result(True, 'weibo_hot', items)
                return _make_result(False, 'weibo_hot', None, '无结果')

            def backup():
                items = self._crawl_hot_baidu(count)
                if items:
                    return _make_result(True, 'baidu_hot', items)
                return _make_result(False, 'baidu_hot', None, '无结果')

            return self._with_fallback(
                source_name='hot_weibo',
                primary_fn=primary,
                backup_fn=backup,
                backup_source='hot_baidu'
            )

        else:
            # 未知平台，尝试百度
            return self.get_hot_search('baidu', count)

    def get_knowledge(self, query: str) -> dict[str, Any]:
        """获取知识百科信息

        Args:
            query: 查询关键词（如"量子计算"、"爱因斯坦"）

        Returns:
            {
                'success': bool,
                'source': str,
                'data': {
                    'title': str,
                    'summary': str,
                    'description': str,
                    'url': str,
                    'source': str
                },
                'error': str
            }
        """
        _log.info(f"[知识查询] {query}")

        def primary():
            return self._crawl_wikipedia(query)

        def backup_en():
            return self._crawl_wikipedia_en(query)

        def backup_baike():
            return self._crawl_baidu_baike_knowledge(query)

        # 多级降级链
        result = self._with_fallback(
            source_name='knowledge',
            primary_fn=primary,
            backup_fn=backup_en,
            backup_source='knowledge_en'
        )

        if not result.get('success'):
            # 再次尝试百度百科
            result = self._with_fallback(
                source_name='knowledge_baike',
                primary_fn=backup_baike
            )

        return result

    def crawl_all(self, query: str) -> dict[str, Any]:
        """智能爬虫 - 自动检测意图并调用对应的爬虫方法

        意图检测规则：
        - 天气类关键词 → get_weather
        - 新闻类关键词 → get_news
        - 热搜/榜单 → get_hot_search
        - 定义/介绍类 → get_knowledge

        Args:
            query: 用户查询文本

        Returns:
            对应爬虫的结果
        """
        query_lower = query.lower()

        # 天气意图检测
        weather_keywords = [
            '天气', '气温', '下雨', '下雪', '温度', '天气怎么样',
            '天气如何', '今天天气', '明天天气', '天气预报',
            'weather', '气温多少', '几度', '冷不冷', '热不热'
        ]
        if any(kw in query_lower for kw in weather_keywords):
            # 提取城市名
            city = self._extract_city(query)
            if city:
                return self.get_weather(city)
            # 默认尝试北京
            return self.get_weather("北京")

        # 新闻意图检测
        news_keywords = [
            '新闻', '最新', '最近', '今天', '实时', '资讯', '报道',
            'news', '最近发生', '最近有什么', '有什么新闻',
            '最新消息', '最新进展'
        ]
        if any(kw in query_lower for kw in news_keywords):
            # 尝试提取关键词
            keyword = self._extract_news_keyword(query)
            return self.get_news(keyword, count=5)

        # 热搜/榜单意图
        hot_keywords = [
            '热搜', '热榜', '榜单', '排行', '排名', 'top',
            '热门', '最火', '流行', ' trending', '榜单'
        ]
        if any(kw in query_lower for kw in hot_keywords):
            # 判断平台偏好
            if '微博' in query or 'weibo' in query_lower:
                return self.get_hot_search('weibo', count=10)
            return self.get_hot_search('baidu', count=10)

        # 知识意图检测
        knowledge_patterns = [
            r'^(什么是|什么叫|什么叫做)',
            r'^(介绍|简介|简述|了解)',
            r'^(.+)\s*(是什么|是什么东西|是谁)',
            r'^(关于|有关)',
            r'^(解释|说明)',
        ]
        for pattern in knowledge_patterns:
            if re.search(pattern, query, re.IGNORECASE):
                # 提取知识关键词
                keyword = self._extract_knowledge_keyword(query)
                return self.get_knowledge(keyword)

        # 默认尝试知识查询
        return self.get_knowledge(query)

    def _extract_city(self, text: str) -> str:
        """从文本中提取城市名"""
        # 常见城市列表
        major_cities = [
            '北京', '上海', '广州', '深圳', '杭州', '成都', '重庆',
            '武汉', '南京', '西安', '天津', '苏州', '郑州', '长沙',
            '青岛', '沈阳', '哈尔滨', '大连', '济南', '厦门',
            '昆明', '贵阳', '福州', '合肥', '南昌', '南宁', '海口',
            '兰州', '乌鲁木齐', '呼和浩特', '银川', '西宁', '拉萨',
            '香港', '澳门', '台北', 'Tokyo', '东京', 'New York', '纽约',
            'London', '伦敦', 'Paris', '巴黎', 'Seoul', '首尔',
        ]

        for city in major_cities:
            if city in text:
                return city

        # 使用正则提取可能的城市名
        patterns = [
            r'([\u4e00-\u9fa5]{2,4})天气',
            r'([\u4e00-\u9fa5]{2,4})的天气',
            r'([\u4e00-\u9fa5]{2,4})现在',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)

        return ""

    def _extract_news_keyword(self, text: str) -> str:
        """从文本中提取新闻关键词"""
        # 移除新闻类词汇
        stop_words = ['新闻', '最新', '最近', '今天', '实时', '资讯', '报道',
                       '的', '了', '有', '是', '在', '和', '与']
        keywords = text
        for word in stop_words:
            keywords = keywords.replace(word, '')
        keywords = keywords.strip()
        return keywords if keywords else '今日要闻'

    def _extract_knowledge_keyword(self, text: str) -> str:
        """从文本中提取知识关键词"""
        # 移除疑问词和前缀
        patterns = [
            r'^(什么是|什么叫|什么叫做)\s*(.+)$',
            r'^(介绍|简介|简述|了解)\s*(.+)$',
            r'^(.+)\s*(是什么|是什么东西|是谁)$',
            r'^(关于|有关)\s*(.+)$',
            r'^(解释|说明)\s*(.+)$',
        ]

        for pattern in patterns:
            match = re.match(pattern, text)
            if match:
                for group in match.groups():
                    if group and group not in [
                        '是什么', '是什么东西', '是谁',
                        '什么是', '什么叫', '什么叫做',
                        '介绍', '简介', '简述', '了解',
                        '关于', '有关', '解释', '说明', ''
                    ]:
                        return group.strip()

        return text.strip()

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        return {
            'cache_size': self._cache.size(),
            'cache_ttl': self._cache._ttl,
            'rate_interval': self._rate_limiter._interval,
            'is_shutdown': self._shutdown
        }

    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
        _log.info("[WebCrawler] 缓存已清空")

    def shutdown(self):
        """关闭爬虫，清理资源"""
        self._shutdown = True
        if self._client and not self._client.is_closed:
            try:
                self._client.close()
                _log.info("[WebCrawler] httpx 客户端已关闭")
            except Exception as e:
                _log.debug(f"[WebCrawler] 关闭客户端异常: {e}")
        self._cache.clear()
        _log.info("[WebCrawler] 已关闭")


# ========== 便捷函数 ==========

# 全局单例
_crawler_instance: WebCrawler | None = None
_crawler_lock = threading.Lock()


def get_web_crawler() -> WebCrawler:
    """获取 WebCrawler 单例"""
    global _crawler_instance
    with _crawler_lock:
        if _crawler_instance is None or _crawler_instance._shutdown:
            _crawler_instance = WebCrawler()
    return _crawler_instance


def quick_weather(city: str) -> dict[str, Any]:
    """快速获取天气"""
    return get_web_crawler().get_weather(city)


def quick_news(keyword: str, count: int = 5) -> dict[str, Any]:
    """快速获取新闻"""
    return get_web_crawler().get_news(keyword, count)


def quick_hot(platform: str = 'baidu', count: int = 10) -> dict[str, Any]:
    """快速获取热搜"""
    return get_web_crawler().get_hot_search(platform, count)


def quick_knowledge(query: str) -> dict[str, Any]:
    """快速获取知识"""
    return get_web_crawler().get_knowledge(query)


def quick_crawl(query: str) -> dict[str, Any]:
    """快速智能爬虫"""
    return get_web_crawler().crawl_all(query)