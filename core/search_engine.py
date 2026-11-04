"""智能搜索引擎 v2.0 - 多源聚合 + RAG 增强
=========================================
改进:
1. 消除重复的 SearchResultRanker (使用 search_strategy 中的统一版本)
2. 增加权威知识源 (萌娘百科/小鸡词典等)
3. 优化去重逻辑 (标题+URL+内容三重检查)
4. 增加 RAG 上下文构建接口
5. 增加事实核查集成
"""
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Dict

from .search_strategy import (
    FactChecker,
    SearchResponseFormatter,
)
from .search_strategy import (
    SearchResultRanker as UnifiedRanker,
)

_log = logging.getLogger("SearchEngine")

try:
    import requests as req_lib
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

try:
    import trafilatura as trafilatura_mod
    TRAFILATURA_OK = True
except ImportError:
    TRAFILATURA_OK = False


def clean_text(text: str) -> str:
    text = re.sub(r'\\u[0-9a-fA-F]{4}', '', text)
    text = re.sub(r'\\x[0-9a-fA-F]{2}', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('\\"', '"').replace("\\'", "'")
    return text.strip()


class SmartCache:
    # 【数据目录标准化】缓存文件统一落在 data/，不再污染项目根目录。
    _DATA_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

    def __init__(self, file_path: str, ttl_hours: int = 24, max_size: int = 200):
        if os.path.isabs(file_path) is False and '/' not in file_path.replace('\\', '/'):
            file_path = os.path.join(self._DATA_DIR, file_path)
        self._file_path = file_path
        self._ttl = ttl_hours * 3600
        self._max_size = max_size
        self._cache = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()
        # 注册 atexit，确保进程退出时强制落盘（避免 5%/10% 概率未落盘导致缓存丢失）
        try:
            import atexit
            atexit.register(lambda: self._dirty and self._save())
        except Exception:
            pass

    def _load(self):
        with self._lock:
            if os.path.exists(self._file_path):
                try:
                    with open(self._file_path, encoding='utf-8') as f:
                        data = json.load(f)
                        self._cache = data.get('cache', {})
                except Exception:
                    self._cache = {}

    def _save(self):
        with self._lock:
            try:
                with open(self._file_path, 'w', encoding='utf-8') as f:
                    json.dump({'cache': self._cache}, f, ensure_ascii=False, indent=2)
            except Exception as e:
                # 缓存落盘失败会导致重启后缓存丢失，应记录
                _log.warning(f"[Cache] 搜索缓存写入失败: {e}")

    def get(self, key: str):
        with self._lock:
            item = self._cache.get(key)
            if item:
                if time.time() - item.get('ts', 0) < self._ttl:
                    return item
            return None

    def set(self, key: str, value: dict):
        with self._lock:
            self._cache[key] = value
            if len(self._cache) > self._max_size:
                oldest = sorted(self._cache.keys(), key=lambda k: self._cache[k].get('ts', 0))
                overflow = len(self._cache) - self._max_size
                for k in oldest[:overflow]:
                    del self._cache[k]
            # 【调优】每次 set 都写磁盘太慢（360KB JSON 缩进格式化+flush几百毫秒）
            # 改为 maybe_save（10% 概率）+ 阈值脏标记兜底：保证缓存不会丢太多
            self._dirty = True
            if random.random() < 0.05:
                self._save()
                self._dirty = False

    def maybe_save(self):
        # 【调优】新增 _dirty 标记：若命中就写磁盘，避免程序退出缓存丢失
        if getattr(self, '_dirty', False) and random.random() < 0.1:
            self._save()
            self._dirty = False

    def save(self):
        self._save()


class IntelligentSearchEngine:
    """智能搜索引擎 - 多源聚合版"""

    # 权威度权重表 (domain -> quality score)
    SEARCH_AUTHORITY_WEIGHTS = {
        'baike.baidu.com': 0.9,
        'zh.wikipedia.org': 0.95,
        'en.wikipedia.org': 0.95,
        'zhihu.com': 0.85,
        'douban.com': 0.7,
        'xinhuanet.com': 0.9,
        'people.com.cn': 0.9,
        'gov.cn': 0.95,
        'bilibili.com': 0.75,
        'weibo.com': 0.65,
        'juejin.cn': 0.8,
        'csdn.net': 0.7,
        'blog.csdn.net': 0.7,
        'cnblogs.com': 0.75,
        'zh.moegirl.org.cn': 0.85,  # 萌娘百科
        'jikipedia.com': 0.8,        # 小鸡词典
        'store.steampowered.com': 0.7,  # Steam
    }

    # 【调优】降低超时阈值，解决"搜索太慢/超时"问题
    # 单源超时 15→8s，总超时 45→25s，避免用户长时间等待
    SEARCH_TIMEOUT = 8
    SEARCH_MAX_EVIDENCE_SOURCES = 10
    SEARCH_TOTAL_TIMEOUT = 25

    _ATEXIT_SHUTDOWN_REGISTERED = False  # 类级开关，确保只注册一次

    def __init__(self):
        self.cache = SmartCache("miaoyi_search_cache.json", 24, 200)
        self.ranker = UnifiedRanker()  # 使用统一的排序器
        self.fact_checker = FactChecker()
        self.stats = {'total': 0, 'success': 0, 'cache_hit': 0, 'fail': 0}
        self._user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.session = req_lib.Session() if REQUESTS_OK else None
        if self.session:
            self.session.headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Connection': 'keep-alive',
            })
        self.llm = None
        # 【重要】注册 atexit 一次性关闭全局 _FETCH_POOL（wait=False 直接丢弃未完成 future）
        # 【历史教训】_fetch 硬超时仅放弃 future.result，但线程池的 future 仍在后台线程里运行（
        # e.g. DNS 解析被操作系统阻塞 30s+）。默认解释器退出会等待所有线程池 future 执行完毕
        # → 脚本卡 60+ 秒不退出。注册 shutdown(wait=False) 后立即丢弃后台任务。
        if not IntelligentSearchEngine._ATEXIT_SHUTDOWN_REGISTERED:
            try:
                import atexit
                def _shutdown_fetch_pool():
                    pool = IntelligentSearchEngine._FETCH_POOL
                    if pool is not None:
                        try:
                            pool.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            # cancel_futures 参数仅 Python 3.9+ 支持
                            pool.shutdown(wait=False)
                        IntelligentSearchEngine._FETCH_POOL = None
                atexit.register(_shutdown_fetch_pool)
                IntelligentSearchEngine._ATEXIT_SHUTDOWN_REGISTERED = True
            except Exception:
                pass

    def set_llm(self, llm):
        self.llm = llm

    def _hash(self, q):
        return hashlib.md5(q.encode()).hexdigest()[:12]

    def search(self, query, max_r=5):
        """执行搜索 - 带缓存和超时"""
        self.stats['total'] += 1
        key = self._hash(query)
        cached = self.cache.get(key)
        if cached:
            self.stats['cache_hit'] += 1
            return cached.get('results', [])[:max_r]

        results_container = {'results': None}
        timeout_event = threading.Event()

        def search_with_timeout():
            try:
                results_container['results'] = self._multi_source_search(query)
            except Exception as e:
                _log.error(f"搜索执行异常: {e}")
                results_container['results'] = []
            finally:
                timeout_event.set()

        search_thread = threading.Thread(target=search_with_timeout, daemon=True)
        search_thread.start()

        timeout_event.wait(timeout=self.SEARCH_TOTAL_TIMEOUT)

        results = results_container['results']
        if results is None:
            _log.warning(f"搜索超时: {query}")
            self.stats['fail'] += 1
            return []

        if results:
            results = self.ranker.rank_results(results, query)
            self.stats['success'] += 1
            self.cache.set(key, {'results': results, 'ts': time.time()})
            self.cache.maybe_save()
            return results[:max_r]
        self.stats['fail'] += 1
        return []

    def search_and_answer(self, query, user_memories=None):
        """
        搜索并构建 RAG 增强回答上下文

        Returns:
            {
                'answer': '',
                'confidence': float,
                'sources': list,
                'formatted': str,  # RAG 上下文
                'evidence_text': str,
                'results': list,
                'memory_context': str,
                'fact_check': dict,  # 事实核查结果
                'cross_references': list,  # 交叉引用
            }
        """
        results = self.search(query, self.SEARCH_MAX_EVIDENCE_SOURCES)

        if not results:
            return {
                'answer': "抱歉，我暂时没有找到相关信息。如果是很新的内容，可能还没被收录呢~",
                'confidence': 0.0,
                'sources': [],
                'formatted': "未找到有效搜索结果",
                'results': [],
                'memory_context': "",
                'fact_check': {'recommendation': 'no_results'},
                'cross_references': [],
            }

        # 1. 构建用户记忆上下文
        memory_context = ""
        if user_memories:
            memory_context = "【用户记忆】\n"
            for memory in user_memories:
                memory_context += f"- {memory['key']}: {memory['value']}\n"
            memory_context += "\n"

        # 2. 使用新的 RAG 上下文构建器
        rag_context = SearchResponseFormatter.format_rag_context(
            results=results,
            query=query,
            user_memories=user_memories,
            max_sources=5,
        )

        # 3. 事实核查
        fact_check = self.fact_checker.assess_confidence(results, query)
        cross_refs = self.fact_checker.extract_cross_references(results)

        # 4. 构建证据文本 (供 LLM 直接引用)
        evidence_parts = []
        for i, r in enumerate(results[:5], 1):
            snippet = r.get('snippet', r.get('content', ''))
            title = r.get('title', '')
            if snippet:
                evidence_parts.append(f"[{i}] {title}: {snippet[:400]}")
        evidence_text = "\n\n".join(evidence_parts) if evidence_parts else ""

        confidence = fact_check['confidence']

        return {
            'answer': "",
            'confidence': confidence,
            'sources': [r.get('url', '') for r in results[:5]],
            'formatted': rag_context,
            'evidence_text': evidence_text,
            'results': results,
            'memory_context': memory_context,
            'fact_check': fact_check,
            'cross_references': cross_refs,
        }

    def get_answer(self, query):
        return self.search_and_answer(query)

    def _get_headers(self, referer='https://www.baidu.com'):
        return {
            'User-Agent': random.choice(self._user_agents),
            'Referer': referer,
        }

    # 共享线程池：避免每次 _fetch 都新建，减少开销 + 控制并发上限避免 DNS 阻塞
    _FETCH_POOL = None

    @classmethod
    def _get_fetch_pool(cls):
        if cls._FETCH_POOL is None:
            cls._FETCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='fetch')
        return cls._FETCH_POOL

    def _fetch_raw(self, url, headers, params, timeout):
        """最内层请求：纯 requests 调用，不再额外套 UA/重试/日志，交给 _fetch 外层控制"""
        try:
            if self.session:
                return self.session.get(url, headers=headers, params=params, timeout=timeout)
            return req_lib.get(url, headers=headers, params=params, timeout=timeout)
        except Exception as e:
            _log.debug(f"_fetch_raw 异常 {url[:50]}: {type(e).__name__}")
            return None

    def _fetch(self, url, headers=None, params=None, timeout=15, retries=1, hard_timeout=None):
        """发起 HTTP GET 请求，支持每次轮换 UA + 失败重试 + **硬超时兜底**。

        【历史教训】requests 的 timeout 仅限制每个 socket I/O 操作，DNS 解析/TLS 握手/
        操作系统底层阻塞时常会无视 timeout 硬挂 30s+。因此这里用线程池 + Future.result
        设置真正的硬超时上限，确保每个请求（含重试）总耗时 <= hard_timeout。

        Args:
            hard_timeout: 含重试在内的绝对超时上限(秒)，None 时 = (timeout + 1) * (retries+1) + 1
        """
        if hard_timeout is None:
            hard_timeout = (timeout + 0.5) * (retries + 1) + 0.5

        def inner_loop():
            for attempt in range(retries + 1):
                try:
                    final_headers = dict(headers) if headers else {}
                    final_headers['User-Agent'] = random.choice(self._user_agents)
                    resp = self._fetch_raw(url, final_headers, params, timeout)
                    if resp is not None and resp.status_code == 200:
                        return resp
                    if attempt < retries:
                        time.sleep(0.4)
                        continue
                    return resp
                except Exception as e:
                    _log.debug(f"_fetch 内层异常(attempt {attempt + 1}/{retries + 1}) {url[:50]}: {e}")
                    if attempt < retries:
                        time.sleep(0.4)
                        continue
                    return None
            return None

        pool = self._get_fetch_pool()
        future = pool.submit(inner_loop)
        try:
            return future.result(timeout=hard_timeout)
        except FutureTimeoutError:
            _log.warning(f"_fetch 硬超时({hard_timeout}s) -> 放弃: {url[:60]}")
            return None
        except Exception as e:
            _log.debug(f"_fetch 线程池异常: {e}")
            return None

    def _search_baidu(self, query, headers):
        results = []
        try:
            r = self._fetch("https://www.baidu.com/s", params={'wd': query},
                            headers=headers, timeout=self.SEARCH_TIMEOUT)
            if r and r.status_code == 200:
                titles = re.findall(r'"title":"([^"]+?)"', r.text)
                urls = re.findall(r'"url":"([^"]+?)"', r.text)
                snippets = re.findall(r'"content":"([^"]+?)"', r.text)
                for i in range(min(len(titles), len(urls), 10)):
                    if 'http' in urls[i]:
                        quality = 0.6
                        for domain, w in self.SEARCH_AUTHORITY_WEIGHTS.items():
                            if domain in urls[i]:
                                quality = w
                                break
                        results.append({
                            'title': clean_text(titles[i]), 'url': urls[i], 'source': 'baidu',
                            'snippet': clean_text(snippets[i][:500] if i < len(snippets) else ""),
                            'quality': quality,
                        })
        except Exception as e:
            _log.debug(f"百度搜索失败: {e}")
        return results

    def _search_bing(self, query, headers):
        results = []
        try:
            r = self._fetch("https://www.bing.com/search", params={'q': query},
                            headers=headers, timeout=self.SEARCH_TIMEOUT)
            if r and r.status_code == 200:
                if BS4_OK:
                    soup = BeautifulSoup(r.text, 'html.parser')
                    for li in soup.select('li.b_algo'):
                        a_tag = li.find('a')
                        if not a_tag:
                            continue
                        url = a_tag.get('href', '')
                        title = a_tag.get_text(strip=True)
                        p_tag = li.find('p')
                        snippet = p_tag.get_text(strip=True)[:500] if p_tag else ""
                        if title and 'http' in url:
                            quality = 0.5
                            for domain, w in self.SEARCH_AUTHORITY_WEIGHTS.items():
                                if domain in url:
                                    quality = w
                                    break
                            results.append({
                                'title': clean_text(title), 'url': url, 'source': 'bing',
                                'snippet': clean_text(snippet), 'quality': quality,
                            })
        except Exception as e:
            _log.debug(f"Bing搜索失败: {e}")
        return results

    def _search_baidu_baike(self, query, headers):
        from urllib.parse import quote_plus
        results = []
        try:
            url = f"https://baike.baidu.com/search/word?word={quote_plus(query)}"
            r = self._fetch(url, headers=headers, timeout=10)
            if r and r.status_code == 200 and BS4_OK:
                soup = BeautifulSoup(r.text, 'html.parser')
                for item in soup.select('dd.result-item'):
                    title_elem = item.select_one('a.result-title')
                    desc_elem = item.select_one('p.result-summary')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        snippet = desc_elem.get_text(strip=True)[:500] if desc_elem else ""
                        url = 'https://baike.baidu.com' + title_elem.get('href', '')
                        results.append({
                            'title': title, 'snippet': snippet,
                            'url': url, 'source': '百度百科',
                            'quality': 0.9,
                        })
                        if len(results) >= 5:
                            break
        except Exception as e:
            _log.debug(f"百度百科搜索失败: {e}")
        return results

    def _search_zhihu(self, query, headers):
        from urllib.parse import quote_plus
        results = []
        try:
            url = f"https://www.zhihu.com/search?type=content&q={quote_plus(query)}"
            r = self._fetch(url, headers=headers, timeout=10)
            if r and r.status_code == 200 and BS4_OK:
                soup = BeautifulSoup(r.text, 'html.parser')
                for item in soup.select('div.List-item'):
                    title_elem = item.select_one('h2.ContentItem-title a')
                    desc_elem = item.select_one('p.ContentItem-summary')
                    url_elem = item.select_one('a')
                    if title_elem and url_elem:
                        title = title_elem.get_text(strip=True)
                        snippet = desc_elem.get_text(strip=True)[:500] if desc_elem else ""
                        url = url_elem.get('href', '')
                        if not url.startswith('http'):
                            url = 'https://www.zhihu.com' + url
                        results.append({
                            'title': title, 'snippet': snippet,
                            'url': url, 'source': '知乎',
                            'quality': 0.85,
                        })
                        if len(results) >= 6:
                            break
        except Exception as e:
            _log.debug(f"知乎搜索失败: {e}")
        return results

    def _search_moegirl(self, query, headers):
        """萌娘百科搜索 - 二次元/ACG 专用知识源"""
        from urllib.parse import quote_plus
        results = []
        try:
            url = f"https://zh.moegirl.org.cn/index.php?search={quote_plus(query)}"
            r = self._fetch(url, headers=headers, timeout=10)
            if r and r.status_code == 200 and BS4_OK:
                soup = BeautifulSoup(r.text, 'html.parser')
                for item in soup.select('li.mw-search-result'):
                    title_elem = item.select_one('a')
                    desc_elem = item.select_one('.searchresult-snippet')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        snippet = desc_elem.get_text(strip=True)[:500] if desc_elem else ""
                        href = title_elem.get('href', '')
                        url = 'https://zh.moegirl.org.cn' + href if href.startswith('/') else href
                        results.append({
                            'title': title, 'snippet': snippet,
                            'url': url, 'source': '萌娘百科',
                            'quality': 0.85,
                        })
                        if len(results) >= 5:
                            break
        except Exception as e:
            _log.debug(f"萌娘百科搜索失败: {e}")
        return results

    def _search_bilibili(self, query, headers):
        from urllib.parse import quote_plus
        results = []
        try:
            url = f"https://search.bilibili.com/all?keyword={quote_plus(query)}"
            r = self._fetch(url, headers=headers, timeout=10)
            if r and r.status_code == 200 and BS4_OK:
                soup = BeautifulSoup(r.text, 'html.parser')
                for item in soup.select('div.video-item'):
                    title_elem = item.select_one('a.title')
                    desc_elem = item.select_one('div.desc')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        snippet = desc_elem.get_text(strip=True)[:500] if desc_elem else ""
                        url = title_elem.get('href', '')
                        if not url.startswith('http'):
                            url = 'https:' + url
                        results.append({
                            'title': title, 'snippet': snippet,
                            'url': url, 'source': 'B站',
                            'quality': 0.75,
                        })
                        if len(results) >= 5:
                            break
        except Exception as e:
            _log.debug(f"B站搜索失败: {e}")
        return results

    def _search_weibo(self, query, headers):
        from urllib.parse import quote_plus
        results = []
        try:
            url = f"https://s.weibo.com/weibo?q={quote_plus(query)}"
            r = self._fetch(url, headers=headers, timeout=10)
            if r and r.status_code == 200 and BS4_OK:
                soup = BeautifulSoup(r.text, 'html.parser')
                for item in soup.select('div.card-wrap'):
                    title_elem = item.select_one('p.txt')
                    if title_elem:
                        text = title_elem.get_text(strip=True)
                        if text:
                            results.append({
                                'title': text[:50] + '...',
                                'snippet': text[:500],
                                'url': '',
                                'source': '微博',
                                'quality': 0.65,
                            })
                        if len(results) >= 5:
                            break
        except Exception as e:
            _log.debug(f"微博搜索失败: {e}")
        return results

    def _search_duckduckgo(self, query, headers):
        """DuckDuckGo 搜索 - HTML 端点，无需 API Key，反爬较宽松
        【新增】作为第 8 个搜索源，提升"搜索返回空/结果少"时的召回率"""
        from urllib.parse import parse_qs, quote_plus, urlparse
        results = []
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            # 【调优】DuckDuckGo 境外节点慢且 requests timeout 偶发失效：
            # 禁用重试(retries=0)、设绝对硬超时 6s，确保不会卡住全局搜索。
            r = self._fetch(url, headers=headers, timeout=5, retries=0, hard_timeout=6)
            if r and r.status_code == 200 and BS4_OK:
                soup = BeautifulSoup(r.text, 'html.parser')
                for item in soup.select('div.result, div.web-result'):
                    title_elem = item.select_one('a.result__a')
                    snippet_elem = item.select_one('a.result__snippet')
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    href = title_elem.get('href', '')
                    # DuckDuckGo 的 href 是跳转链接 //duckduckgo.com/l/?uddg=<encoded_url>
                    # 提取真实 URL，避免存入无效跳转地址
                    if 'uddg=' in href:
                        parsed = urlparse('http:' + href if href.startswith('//') else href)
                        uddg = parse_qs(parsed.query).get('uddg', [''])[0]
                        if uddg:
                            href = uddg
                    snippet = snippet_elem.get_text(strip=True)[:500] if snippet_elem else ""
                    if title and href.startswith('http'):
                        quality = 0.6
                        for domain, w in self.SEARCH_AUTHORITY_WEIGHTS.items():
                            if domain in href:
                                quality = w
                                break
                        results.append({
                            'title': clean_text(title), 'url': href, 'source': 'duckduckgo',
                            'snippet': clean_text(snippet), 'quality': quality,
                        })
                        if len(results) >= 5:
                            break
        except Exception as e:
            _log.debug(f"DuckDuckGo搜索失败: {e}")
        return results

    def _extract_content(self, url, headers):
        try:
            # 【调优】正文爬取超时 15→8s，与 SEARCH_TIMEOUT 对齐，避免单页拖慢整体
            r = self._fetch(url, headers=headers, timeout=self.SEARCH_TIMEOUT)
            if not r or r.status_code != 200:
                return ""

            content = r.text
            if not content or len(content) < 100:
                return ""

            if TRAFILATURA_OK and trafilatura_mod:
                try:
                    extracted = trafilatura_mod.extract(content)
                    if extracted and len(extracted) > 100:
                        return clean_text(extracted[:3000])
                except Exception as e:
                    # trafilatura 抽取失败时降级到 BS4，记录便于排查
                    _log.debug(f"[Extract] trafilatura 抽取失败，降级处理: {e}")

            if BS4_OK:
                try:
                    soup = BeautifulSoup(content, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
                        tag.decompose()

                    content_selectors = [
                        'article', 'div.article-content', 'div.content',
                        'div.main-content', 'div.post-content', 'div.entry-content',
                        'div.text-content', 'div.body-content', 'div.detail-content',
                        'div.content-body', 'div.blog-content', 'div.page-content',
                        'div.news-content', 'div.content-inner', 'div.post-body',
                        'div.article-body',
                    ]

                    main_content = None
                    for selector in content_selectors:
                        main_content = soup.select_one(selector)
                        if main_content:
                            break

                    if not main_content:
                        main_content = soup.find('main') or soup.find('body')

                    if main_content:
                        text = main_content.get_text(separator='\n', strip=True)
                    else:
                        text = soup.get_text(separator='\n', strip=True)

                    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 5]
                    result = clean_text('\n'.join(lines[:80]))[:3000]
                    return result if len(result) > 100 else ""
                except Exception as e:
                    # BS4 解析失败时降级到原始截断，记录便于排查
                    _log.debug(f"[Extract] BS4 解析失败，降级处理: {e}")

            return clean_text(content[:3000]) if len(content) > 100 else ""
        except Exception as e:
            _log.debug(f"内容提取失败 {url}: {e}")
            return ""

    def _multi_source_search(self, query):
        if not REQUESTS_OK:
            return []

        headers = self._get_headers()
        results = []

        search_sources = [
            self._search_baidu,
            self._search_bing,
            self._search_baidu_baike,
            self._search_bilibili,
            self._search_weibo,
            self._search_zhihu,
            self._search_moegirl,   # 萌娘百科
            self._search_duckduckgo, # 【新增】DuckDuckGo，提升召回率
        ]

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for source_func in search_sources:
                futures.append(executor.submit(source_func, query, headers))

            # 【调优】as_completed 超时 35→15s，配合 SEARCH_TOTAL_TIMEOUT=25 留出爬取余量
            # （15s 搜索 + 6s 爬取 = 21s < 25s 总超时，避免总超时截断导致返回空）
            for future in as_completed(futures, timeout=15):
                try:
                    source_results = future.result()
                    if source_results:
                        results.extend(source_results)
                        _log.info(f"搜索源返回 {len(source_results)} 条结果")
                except TimeoutError as e:
                    _log.warning(f"搜索线程超时: {e}")
                except Exception as e:
                    _log.debug(f"搜索线程异常: {e}")

        _log.info(f"搜索完成，共获取 {len(results)} 条结果")

        # 深度爬取（限制数量）
        # 【调优】爬取数量 8→5，单页超时 10→6s，显著降低"搜索太慢"
        if results:
            with ThreadPoolExecutor(max_workers=5) as executor:
                crawl_futures = []
                for r in results[:5]:
                    if r.get('url') and (not r.get('content') or len(r.get('content', '')) < 300):
                        crawl_futures.append((r, executor.submit(self._extract_content, r.get('url'), headers)))
                    else:
                        crawl_futures.append((r, None))

                for r, future in crawl_futures:
                    if future:
                        try:
                            content = future.result(timeout=6)
                            if content and len(content) > 150:
                                r['snippet'] = content[:500]
                                r['content'] = content[:2000]
                        except Exception as e:
                            _log.debug(f"深度爬取失败: {e}")

        # 三重去重
        seen_titles = set()
        seen_urls = set()
        seen_content_hashes = set()
        unique = []

        for item in results:
            title = item.get('title', '').strip().lower()
            url = item.get('url', '')
            content = item.get('snippet', '') + item.get('content', '')

            title_hash = title[:50]
            content_hash = hashlib.md5(content[:200].encode()).hexdigest() if content else ""

            # 标题去重 (相同标题只保留第一个)
            if title_hash in seen_titles:
                continue
            # URL 去重
            if url and url in seen_urls:
                continue
            # 内容去重 (相同摘要只保留第一个)
            if content_hash and content_hash in seen_content_hashes:
                continue

            seen_titles.add(title_hash)
            if url:
                seen_urls.add(url)
            if content_hash:
                seen_content_hashes.add(content_hash)

            unique.append(item)

        return unique[:15]

    def shutdown(self):
        try:
            self.cache.save()
        except Exception as e:
            _log.debug(f"搜索引擎关闭失败: {e}")
