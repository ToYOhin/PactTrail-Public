"""Closed, bounded search observations; external text never grants authority."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

from pacttrail.domain.web_source import WebSourceError, validate_url


SEARCH_ERRORS = frozenset({'unavailable', 'empty', 'timeout', 'cancelled', 'response_limit',
                           'usage_unavailable', 'budget_limit'})
SEARCH_PROVIDERS = frozenset({'bing', 'deepseek'})


class WebSearchError(ValueError):
    def __init__(self, error='unavailable'):
        self.code = error if error in SEARCH_ERRORS else 'unavailable'
        super().__init__('web_search_error: ' + self.code)


def _text(value, maximum, *, empty=False):
    return (type(value) is str and (0 if empty else 1) <= len(value) <= maximum
            and not any(ord(char) < 32 or 127 <= ord(char) <= 159
                        or 0xD800 <= ord(char) <= 0xDFFF for char in value))


def make_query(goal):
    """Derive the visible query only; the caller retains the original question."""
    if type(goal) is not str:
        raise WebSearchError()
    query = ' '.join(goal.split())[:160]
    if not _text(query, 160) or not query.strip():
        raise WebSearchError()
    return query


# These are product identities, not discoveries of an unknown site's ownership.
# An explicit site/URL from the question always takes precedence over this table.
_PRODUCTS = (
    ('Python', r'python', 'python.org'),
    ('Node.js', r'node(?:\.js|js)', 'nodejs.org'),
    ('React', r'react', 'react.dev'),
    ('Django', r'django', 'djangoproject.com'),
    ('Rust', r'rust', 'rust-lang.org'),
    ('TypeScript', r'typescript', 'typescriptlang.org'),
    ('PostgreSQL', r'postgres(?:ql)?', 'postgresql.org'),
    ('PHP', r'php', 'php.net'),
)
_OFFICIAL = re.compile(r'官网|官方|official', re.I)
_RELEASE = re.compile(
    r'(?:当前|最新|稳定)\s*(?:的\s*)?(?:稳定\s*)?(?:版本|版)|'
    r'(?:latest|current|stable).{0,48}\b(?:release|version)\b', re.I)
_SITE = re.compile(r'(?<![a-z0-9_])site:\s*((?:[a-z0-9-]+\.)+[a-z]{2,63})(?![a-z0-9.-])', re.I)
_URL = re.compile(r'https?://[^\s，。；;！？!?<>"\']+', re.I)
_PORTALS = frozenset({
    'bing.com', 'www.bing.com', 'cn.bing.com', 'google.com', 'www.google.com',
    'google.com.hk', 'www.google.com.hk', 'baidu.com', 'www.baidu.com', 'm.baidu.com',
    'sogou.com', 'www.sogou.com', 'so.com', 'www.so.com', 'duckduckgo.com',
    'www.duckduckgo.com', 'search.yahoo.com', 'yandex.com', 'www.yandex.com',
    'search.brave.com', 'searx.space', 'www.searx.space',
})
_AGGREGATOR = re.compile(
    r'多引擎|聚合搜索|搜索聚合|搜索导航|搜索引擎大全|搜索引擎聚合|'
    r'multi[ -]?(?:engine|search)|meta[ -]?search|all[ -]in[ -]one search', re.I)
_FORMAT = re.compile(
    r'(?:请|并|且|然后|请用|用)?\s*(?:简短|简洁|简要|详细|一句话)\s*(?:地)?\s*(?:回答|回复|说明)?|'
    r'(?:并|且)?\s*(?:标出|标注|注明|附上|附|提供|列出)\s*(?:信息)?(?:来源|引用|链接)|'
    r'(?:优先|只看|仅看|来自)?\s*(?:官网|官方)(?:优先|来源|网站|内容)?|'
    r'(?:prefer|prioritize)\s+official(?:\s+(?:sources|sites|websites))?|'
    r'(?:answer|reply)\s+(?:briefly|concisely)|(?:cite|include)\s+(?:the\s+)?sources', re.I)
_STOP = frozenset({'the', 'a', 'an', 'and', 'or', 'of', 'for', 'to', 'in', 'is',
                   'what', 'which', 'please', 'search', 'find', 'latest', 'current',
                   'stable', 'release', 'version', 'official', 'source', 'sources'})


def _bounded_query(core, domains, query_limit):
    scope = ('site:' + domains[0] if len(domains) == 1 else
             '(' + ' OR '.join('site:' + host for host in domains) + ')') if domains else ''
    room = query_limit - len(scope) - (1 if scope else 0)
    if room < 1:
        raise WebSearchError('response_limit')
    topic = core[:room].rstrip(' ,，;；。')
    if not topic:
        raise WebSearchError('response_limit')
    result = topic + (' ' + scope if scope else '')
    return make_query(result)


def _query_terms(core):
    terms = []
    # Keep topic-bearing Latin tokens and short Chinese phrases without carrying
    # response-style directives into the relevance comparison.
    cleaned = re.sub(
        r'当前|最新|稳定版本|稳定版|最新版|版本|是什么|什么是|是多少|多少|'
        r'请介绍(?:一下)?|介绍一下|请解释(?:一下)?|解释一下|帮我了解|请问|有哪些|有什么|的', ' ', core)
    for term in re.findall(r'[a-z0-9]+(?:[.+-][a-z0-9]+)*|[\u3400-\u9fff]{2,12}', cleaned.lower()):
        if term not in _STOP and term not in terms:
            terms.append(term)
        if len(terms) == 12:
            break
    return tuple(terms)


def plan_query(goal, *, query_limit=160):
    """Separate search intent from answer style without inventing site ownership.

    The internal plan is deliberately local and deterministic. Unknown official
    sites leave ``official_domains`` empty: the caller must not label a result
    official merely because its title says so.
    """
    if type(goal) is not str or type(query_limit) is not int or query_limit not in (128, 160):
        raise WebSearchError()
    normalized = ' '.join(goal.split())
    if not _text(normalized, max(len(normalized), 1)):
        raise WebSearchError()
    domains = []
    try:
        for match in _SITE.finditer(normalized):
            host = validate_url('https://' + match[1] + '/')['host']
            if host not in domains:
                domains.append(host)
        for match in _URL.finditer(normalized):
            host = validate_url(match[0].rstrip('.,'))['host']
            if host not in domains:
                domains.append(host)
    except WebSourceError:
        raise WebSearchError() from None
    if query_limit == 128 and len(domains) > 3:
        raise WebSearchError('response_limit')
    domains = domains[:3]
    official_requested = bool(domains or _OFFICIAL.search(normalized))
    core = _URL.sub(' ', _SITE.sub(' ', normalized))
    core = _FORMAT.sub(' ', core)
    core = re.sub(r'^\s*(?:请帮我|帮我|请)?\s*(?:搜索|搜一下|查一下|查询|检索|查找|查看|search|look up|find)\s*',
                  '', core, flags=re.I)
    core = ' '.join(re.sub(r'[,，。；;！？!?]+', ' ', core).split()).strip()
    if not core:
        raise WebSearchError()
    products = [(name, host) for name, pattern, host in _PRODUCTS
                if re.search(r'(?<![a-z0-9_])' + pattern + r'(?![a-z0-9_])', core, re.I)]
    product = products[0] if len(products) == 1 else None
    release = bool(_RELEASE.search(core))
    if products and official_requested and not domains:
        domains = [product[1] for product in products[:3]]
    # Only normalize a release lookup, not a compatibility/comparison question
    # that happens to mention the current version.
    release_lookup = release and not re.search(
        r'支持|兼容|比较|区别|是否|为什么|如何|教程|说明|support|compatib|compar|difference|why|how', core, re.I)
    if product and release_lookup:
        qualifiers = re.findall(r'(?<![\w.])\d+(?:\.\d+){1,3}(?![\w.])|'
                                r'(?<![a-z])(?:Windows|Linux|macOS|Android|iOS|x86|x64|arm64)(?![a-z])',
                                core, re.I)
        subject = ' '.join([product[0], *dict.fromkeys(qualifiers)])
        query = _bounded_query(subject + ' latest stable release', domains, query_limit)
        secondary = _bounded_query(subject + ' download latest stable', domains, query_limit)
        terms = _query_terms(subject)
    else:
        terms = _query_terms(core)
        query = _bounded_query(core + (' official' if official_requested and not domains else ''), domains, query_limit)
        simple = ' '.join(terms[:8]) or core
        secondary = _bounded_query(simple + (' 官方网站' if official_requested and not domains else ' reference'), domains, query_limit)
    return {'query': query, 'secondary_query': secondary if secondary != query else None,
            'official_domains': tuple(domains), 'terms': terms,
            'official_requested': official_requested}


def rank_results(results, plan):
    """Return up to three owned relevant records; never trust a ``site:`` hint."""
    ranked, seen = [], set()
    for index, item in enumerate(results):
        if (type(item) is not dict or set(item) != {'title', 'url', 'snippet'}
                or not _text(item['title'], 160) or not item['title'].strip()
                or not _text(item['snippet'], 600, empty=True)):
            continue
        try:
            target = validate_url(item['url'])
        except WebSourceError:
            continue
        if (target['host'] in _PORTALS or _AGGREGATOR.search(item['title'])
                or target['url'] in seen):
            continue
        domains = plan['official_domains']
        if domains and not any(target['host'] == host or target['host'].endswith('.' + host)
                               for host in domains):
            continue
        title, snippet, path = item['title'].lower(), item['snippet'].lower(), urlsplit(target['url']).path.lower()
        terms = plan['terms']
        score = sum(4 * (term in title) + 2 * (term in snippet) + (term in path) for term in terms)
        if terms and not score:
            continue
        if _RELEASE.search(plan['query']) and re.search(r'download|release|version', title + ' ' + path):
            score += 5
        seen.add(target['url'])
        ranked.append((score, index, {'title': item['title'], 'url': target['url'], 'snippet': item['snippet']}))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return [entry[2] for entry in ranked[:3]]


def _timestamp(value):
    if (type(value) is not str
            or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value) is None
            or datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%dT%H:%M:%SZ') != value):
        raise ValueError()


def _host_allowed(host, domains):
    return not domains or any(host == domain or host.endswith('.' + domain) for domain in domains)


def _page(value, domains):
    if (type(value) is not dict or set(value) != {'url', 'fetched_at', 'text', 'selection', 'sha256'}
            or value['selection'] != 'relevant_excerpt' or type(value['text']) is not str
            or not value['text'].strip()
            or any(ord(char) < 32 and char not in '\n\t' or 127 <= ord(char) <= 159
                   or 0xD800 <= ord(char) <= 0xDFFF for char in value['text'])):
        raise ValueError()
    raw = value['text'].encode('utf-8')
    target = validate_url(value['url'])
    if (not 1 <= len(raw) <= 4096 or type(value['sha256']) is not str
            or value['sha256'] != hashlib.sha256(raw).hexdigest()
            or target['url'] != value['url'] or not _host_allowed(target['host'], domains)):
        raise ValueError()
    _timestamp(value['fetched_at'])


def validate_search(value):
    """Accept closed historical V1 and bounded researched V2 observations."""
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] not in (1, 2):
        raise WebSearchError()
    fields = {'version', 'query', 'searched_at', 'provider', 'transport', 'results'}
    if value['version'] == 2:
        fields |= {'queries', 'official_domains'}
    if (set(value) != fields
            or not _text(value['query'], 160) or not value['query'].strip()
            or type(value['provider']) is not str or value['provider'] not in SEARCH_PROVIDERS
            or value['transport'] not in ('http', 'synthetic')
            or type(value['results']) is not list or not 1 <= len(value['results']) <= 3):
        raise WebSearchError()
    try:
        _timestamp(value['searched_at'])
        domains = []
        if value['version'] == 2:
            queries, domains = value['queries'], value['official_domains']
            if (type(queries) is not list or not 1 <= len(queries) <= 2
                    or any(not _text(query, 160) or make_query(query) != query for query in queries)
                    or queries[0] != value['query'] or len(set(queries)) != len(queries)
                    or type(domains) is not list or len(domains) > 3):
                raise ValueError()
            for domain in domains:
                if (type(domain) is not str or validate_url('https://' + domain + '/')['host'] != domain
                        or domain.rsplit('.', 1)[-1].isdecimal() or ':' in domain):
                    raise ValueError()
            if len(set(domains)) != len(domains):
                raise ValueError()
        seen = set()
        for item in value['results']:
            if (type(item) is not dict
                    or set(item) not in ({'title', 'url', 'snippet'}, {'title', 'url', 'snippet', 'page'})
                    or value['version'] == 1 and 'page' in item
                    or not _text(item['title'], 160) or not item['title'].strip()
                    or not _text(item['snippet'], 600, empty=True)):
                raise ValueError()
            target = validate_url(item['url'])
            if (target['url'] != item['url'] or target['url'] in seen
                    or not _host_allowed(target['host'], domains)):
                raise ValueError()
            seen.add(target['url'])
            if 'page' in item:
                _page(item['page'], domains)
        if len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 8192:
            raise WebSearchError('response_limit')
    except WebSearchError:
        raise
    except (ValueError, TypeError, UnicodeError, WebSourceError):
        raise WebSearchError() from None
    return copy.deepcopy(value)
