"""One run's bounded query, relevance filtering and public-page reading."""
from __future__ import annotations

import copy
import math
import threading
import time

from pacttrail.domain.web_search import WebSearchError, plan_query, rank_results, validate_search


class SearchResearch:
    accepts_goal = True

    def __init__(self, search_factory, page_factory, *, clock=time.monotonic, query_limit=160):
        if (not all(callable(value) for value in (search_factory, page_factory, clock))
                or type(query_limit) is not int or query_limit not in (128, 160)):
            raise WebSearchError()
        self._search_factory, self._page_factory, self._clock = search_factory, page_factory, clock
        self._query_limit = query_limit
        self._lock = threading.RLock()
        self._active = None
        self._closed = self._used = False

    def close(self):
        with self._lock:
            self._closed = True
            active = self._active
        if active is not None:
            try:
                active.close()
            except Exception:
                pass

    def _guard(self, cancel, deadline):
        with self._lock:
            if self._closed or cancel.is_set():
                raise WebSearchError('cancelled')
        if self._clock() >= deadline:
            raise WebSearchError('timeout')

    def _call(self, factory, method, args, kwargs, cancel, deadline):
        self._guard(cancel, deadline)
        adapter = factory()
        try:
            with self._lock:
                self._guard(cancel, deadline)
                self._active = adapter
            value = getattr(adapter, method)(*args, **kwargs)
            self._guard(cancel, deadline)
            return value
        except WebSearchError:
            raise
        except Exception:
            raise WebSearchError() from None
        finally:
            try:
                adapter.close()
            except Exception:
                pass
            with self._lock:
                if self._active is adapter:
                    self._active = None

    def search(self, goal, *, cancel, deadline):
        if (not isinstance(cancel, threading.Event) or type(deadline) not in (int, float)
                or not math.isfinite(deadline)):
            raise WebSearchError()
        plan = plan_query(goal, query_limit=self._query_limit)
        with self._lock:
            self._guard(cancel, deadline)
            if self._used:
                raise WebSearchError()
            self._used = True
        end = min(deadline, self._clock() + 8)
        # A search title is not proof of who owns an unknown official site.
        if plan['official_requested'] and not plan['official_domains']:
            raise WebSearchError('empty')
        queries, candidates, visited = [], [], set()
        provider = None
        last_error = 'empty'
        for query in (plan['query'], plan['secondary_query']):
            if query is None or len(visited) >= 2:
                break
            self._guard(cancel, end)
            queries.append(query)
            try:
                observed = validate_search(self._call(self._search_factory, 'search', (query,),
                    dict(cancel=cancel, deadline=end), cancel, end))
            except WebSearchError as error:
                if error.code != 'empty':
                    raise
                continue
            if observed['query'] != query:
                raise WebSearchError()
            if provider is not None and observed['provider'] != provider:
                raise WebSearchError()
            provider = observed['provider']
            relevant = rank_results(observed['results'], plan)
            candidates = rank_results([*candidates, *relevant], plan)
            for item in relevant:
                if item['url'] in visited or len(visited) >= 2:
                    continue
                visited.add(item['url'])
                try:
                    page = self._call(self._page_factory, 'read', (item['url'],),
                        dict(terms=plan['terms'], official_domains=plan['official_domains'],
                             cancel=cancel, deadline=end), cancel, end)
                    result = dict(version=2, query=queries[0], queries=list(queries),
                        official_domains=list(plan['official_domains']), searched_at=observed['searched_at'],
                        provider=observed['provider'], transport=observed['transport'],
                        results=[dict(item, page=copy.deepcopy(page))])
                    result = validate_search(result)
                    # Optional unread summaries must not crowd out the intact read page.
                    for entry in candidates:
                        if entry['url'] == item['url'] or len(result['results']) >= 3:
                            continue
                        try:
                            result = validate_search(dict(result, results=[*result['results'], entry]))
                        except WebSearchError as error:
                            if error.code != 'response_limit':
                                raise
                    self._guard(cancel, end)
                    return result
                except WebSearchError as error:
                    if error.code in ('cancelled', 'usage_unavailable', 'budget_limit'):
                        raise
                    self._guard(cancel, end)
                    last_error = error.code
        self._guard(cancel, end)
        raise WebSearchError(last_error)
