"""Opt-in local estimation and published-price accounting for one protocol.

The local count is an estimate, not hosted counting or a provider billing cap.
No credentials, HTTP client or live selector; synthetic compatibility is retained.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import threading

from pacttrail.application.model_evidence import model_evidence_error
from pacttrail.platform.deepseek_tokens import (
    CONFIG_SHA256, MODEL, RENDERER_ID, TOOL_RENDERER_ID, TOKEN_BASIS, TOKENIZER_SHA256,
    TOKENIZERS_VERSION, DeepSeekTokenEstimator, LocalTokenEstimate,
)


@dataclass(frozen=True)
class SyntheticTokenCount:
    request_sha256: str
    input_tokens: int
    model: str = 'deepseek-flash'
    basis: str = 'synthetic-test-only'


@dataclass(frozen=True)
class SearchReservation:
    """Opaque dispatch identity; neither queries nor credentials enter the ledger."""

    request_id: str
    index: int
    reservation_nano_usd: int


class DeepSeekAdmission:
    """One ephemeral three-dispatch budget; both count paths remain opt-in."""

    MAX_CALLS = 3
    MAX_ESTIMATE_TOKENS = 4096
    MAX_INPUT_TOKENS = 8192
    MAX_OUTPUT_TOKENS = 512
    ESTIMATE_RENDERER_ID = RENDERER_ID
    ESTIMATE_MODES = frozenset(('evidence', 'ordinary'))
    ESTIMATE_REQUIRED = False

    def __init__(self, *, count_tokens=None, estimate_tokens=None, budget_nano_usd=100_000_000):
        if ((count_tokens is not None and not callable(count_tokens))
                or (estimate_tokens is not None and type(estimate_tokens) is not DeepSeekTokenEstimator)
                or (estimate_tokens is not None and (
                    type(getattr(estimate_tokens, '_request_mode', None)) is not str
                    or estimate_tokens._request_mode not in self.ESTIMATE_MODES))
                or (self.ESTIMATE_REQUIRED and estimate_tokens is None)
                or (count_tokens is not None and estimate_tokens is not None)
                or type(budget_nano_usd) is not int or not 1 <= budget_nano_usd <= 100_000_000):
            raise model_evidence_error('invalid_request')
        self._estimated = estimate_tokens is not None
        self._count_tokens = estimate_tokens if self._estimated else count_tokens
        self._lock = threading.Lock()
        self._input_tokens = None
        self._reserved_output_tokens = self.MAX_OUTPUT_TOKENS
        self._last_usage = None
        self._state = {
            'calls': 0, 'reserved_nano_usd': 0, 'accounted_nano_usd': 0,
            'budget_nano_usd': budget_nano_usd, 'cost_known': True,
            'stopped': False, 'stop_reason': None,
            'token_basis': TOKEN_BASIS if self._estimated else 'synthetic-test-only',
            'pricing_basis': 'deepseek-peak-2026-09-11', 'live_ready': False,
        }

    def status(self):
        # State objects are replaced, never mutated; readers also work in transport.
        return dict(self._state)

    def metrics(self):
        """Detached token counts only; no prompt, response or credential text."""
        return {'input_estimate_tokens': self._input_tokens,
                'usage': None if self._last_usage is None else dict(self._last_usage)}

    def _update(self, **fields):
        self._state = {**self._state, **fields}

    def _acquire(self):
        if not self._lock.acquire(blocking=False):
            raise model_evidence_error('provider_unavailable')

    def _release(self):
        self._lock.release()

    def _output_limit(self, body):
        return self.MAX_OUTPUT_TOKENS

    def _prepare(self, body):
        if self._state['stopped']:
            raise model_evidence_error(self._state['stop_reason'])
        if self._state['calls'] >= self.MAX_CALLS:
            raise model_evidence_error('request_limit')
        fact = self._estimate(body)
        if fact.input_tokens > (self.MAX_ESTIMATE_TOKENS if self._estimated else self.MAX_INPUT_TOKENS):
            raise model_evidence_error('token_limit')
        reservation = self._reservation(body, fact.input_tokens)
        if self._state['accounted_nano_usd'] + reservation > self._state['budget_nano_usd']:
            raise model_evidence_error('budget_limit')
        return fact.input_tokens, reservation

    def _estimate(self, body):
        """Validate a local fact without publishing metrics or charging it."""
        if self._count_tokens is None:
            raise model_evidence_error('token_count_unavailable')
        try:
            fact = self._count_tokens(body)
        except Exception:
            raise model_evidence_error('token_count_unavailable') from None
        fact_type = LocalTokenEstimate if self._estimated else SyntheticTokenCount
        basis = TOKEN_BASIS if self._estimated else 'synthetic-test-only'
        if (type(fact) is not fact_type
                or type(fact.request_sha256) is not str
                or fact.request_sha256 != hashlib.sha256(body).hexdigest()
                or type(fact.model) is not str or fact.model != MODEL
                or type(fact.basis) is not str or fact.basis != basis
                or type(fact.input_tokens) is not int or fact.input_tokens <= 0):
            raise model_evidence_error('token_count_unavailable')
        if self._estimated and any(
                type(getattr(fact, field)) is not str or getattr(fact, field) != expected
                for field, expected in (
                    ('tokenizer_sha256', TOKENIZER_SHA256), ('config_sha256', CONFIG_SHA256),
                    ('renderer_id', self.ESTIMATE_RENDERER_ID), ('library_version', TOKENIZERS_VERSION))):
            raise model_evidence_error('token_count_unavailable')
        return fact

    def _reservation(self, body, input_tokens):
        return ((self.MAX_INPUT_TOKENS if self._estimated else input_tokens) * 300
                + self._output_limit(body) * 1200)

    def _reserve(self, permit):
        self._input_tokens, reservation = permit
        # Keep the established two-field permit contract. The exact reservation
        # carries this dispatch's output allowance without changing shared caps.
        input_reservation = (self.MAX_INPUT_TOKENS if self._estimated else self._input_tokens) * 300
        self._reserved_output_tokens = (reservation - input_reservation) // 1200
        self._last_usage = None
        self._update(calls=self._state['calls'] + 1, reserved_nano_usd=reservation,
                     cost_known=False)

    def _settle(self, usage):
        fields = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                  'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
        if (type(usage) is not dict
                or any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields)):
            raise model_evidence_error('usage_unavailable')
        prompt, output, total, hit, miss = (usage[key] for key in fields)
        if prompt != hit + miss or total != prompt + output:
            raise model_evidence_error('usage_unavailable')
        for key, detail, expected in (
                ('completion_tokens_details', 'reasoning_tokens', 0),
                ('prompt_tokens_details', 'cached_tokens', hit)):
            details = usage.get(key)
            if details is not None and (
                    type(details) is not dict or
                    (detail in details and (type(details[detail]) is not int or details[detail] != expected))):
                raise model_evidence_error('usage_unavailable')
        amount = miss * 300 + hit * 6 + output * 1200
        if not self._estimated and (
                prompt != self._input_tokens or output > self._reserved_output_tokens
                or amount > self._state['reserved_nano_usd']):
            raise model_evidence_error('usage_unavailable')
        # Trusted usage is known even when it violates acceptance or the budget.
        self._last_usage = dict(zip(fields, (prompt, output, total, hit, miss)))
        self._update(accounted_nano_usd=self._state['accounted_nano_usd'] + amount,
                     reserved_nano_usd=0, cost_known=True)
        if self._estimated:
            reason = ('budget_limit' if self._state['accounted_nano_usd'] > self._state['budget_nano_usd'] else
                      'token_limit' if prompt > self.MAX_INPUT_TOKENS else
                      'response_limit' if output > self._reserved_output_tokens
                      else None)
            if reason is not None:
                self._update(stopped=True, stop_reason=reason)
                raise model_evidence_error(reason)

    def _stop(self):
        # Unknown processing retains its full reservation, never a guessed refund.
        reason = self._state['stop_reason'] or (
            'provider_unavailable' if self._state['cost_known'] else 'usage_unavailable')
        self._update(stopped=True, stop_reason=reason)


class DeepSeekToolAdmission(DeepSeekAdmission):
    """Shared accounting with the separate bounded tool-decision policy."""

    MAX_CALLS = 48
    MAX_ESTIMATE_TOKENS = 8192
    MAX_INPUT_TOKENS = 16384
    MAX_OUTPUT_TOKENS = 2048
    ESTIMATE_RENDERER_ID = TOOL_RENDERER_ID
    ESTIMATE_MODES = frozenset(('tools',))
    ESTIMATE_REQUIRED = True
    MAX_SAFE_INTEGER = 2 ** 53 - 1
    MAX_SEARCH_CALLS = 48
    SEARCH_NANO_USD = 750_000
    SEARCH_PRICING_BASIS = 'deepseek-search-per-success-0.00075-usd'

    def __init__(self, *, estimate_tokens=None, budget_nano_usd=100_000_000):
        super().__init__(estimate_tokens=estimate_tokens, budget_nano_usd=budget_nano_usd)
        self._search_reservation = None
        self._update(ledger_version=1, search_calls=0, search_accounted_nano_usd=0,
                     search_reserved_nano_usd=0, search_pricing_basis=self.SEARCH_PRICING_BASIS)

    def _update(self, **fields):
        # Publish both totals and their breakdown in one immutable state swap.
        if fields.get('ledger_version', self._state.get('ledger_version')) == 1:
            for total, search, model in (
                    ('accounted_nano_usd', 'search_accounted_nano_usd', 'model_accounted_nano_usd'),
                    ('reserved_nano_usd', 'search_reserved_nano_usd', 'model_reserved_nano_usd')):
                fields[model] = fields.get(total, self._state[total]) - fields.get(
                    search, self._state.get(search, 0))
        super()._update(**fields)

    def _prepare(self, body):
        if not self._state['cost_known'] and not self._state['stopped']:
            raise model_evidence_error('usage_unavailable')
        return super()._prepare(body)

    def resume_known(self):
        """Explicit new-task activation retains the entire settled process ledger."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            state = self._state
            if (not state['cost_known'] or state['reserved_nano_usd'] != 0
                    or self._search_reservation is not None
                    or state['calls'] >= self.MAX_CALLS
                    or state['accounted_nano_usd'] >= state['budget_nano_usd']):
                return False
            self._update(stopped=False, stop_reason=None)
            return True
        finally:
            self._lock.release()

    def reserve_search(self, request_id):
        """Reserve one separately bounded Search attempt before durable dispatch."""
        if type(request_id) is not str or re.fullmatch('[a-f0-9]{32}', request_id) is None:
            raise model_evidence_error('invalid_request')
        self._acquire()
        try:
            state = self._state
            if state['stopped']:
                raise model_evidence_error(state['stop_reason'])
            if not state['cost_known']:
                raise model_evidence_error('usage_unavailable')
            if state['search_calls'] >= self.MAX_SEARCH_CALLS:
                raise model_evidence_error('request_limit')
            if (state['accounted_nano_usd'] + state['reserved_nano_usd']
                    + self.SEARCH_NANO_USD > state['budget_nano_usd']):
                raise model_evidence_error('budget_limit')
            permit = SearchReservation(request_id, state['search_calls'] + 1, self.SEARCH_NANO_USD)
            self._search_reservation = permit
            self._update(search_calls=permit.index, reserved_nano_usd=self.SEARCH_NANO_USD,
                         search_reserved_nano_usd=self.SEARCH_NANO_USD, cost_known=False)
            return permit
        finally:
            self._release()

    def settle_search(self, permit, outcome):
        """Known responses settle even after cancellation; unknown never refunds."""
        self._acquire()
        try:
            if (permit is not self._search_reservation or type(permit) is not SearchReservation
                    or type(outcome) is not str or outcome not in ('billable', 'free', 'unknown')):
                raise model_evidence_error('invalid_request')
            if outcome == 'unknown':
                self._stop()
                return
            amount = permit.reservation_nano_usd if outcome == 'billable' else 0
            self._update(accounted_nano_usd=self._state['accounted_nano_usd'] + amount,
                         search_accounted_nano_usd=self._state['search_accounted_nano_usd'] + amount,
                         reserved_nano_usd=0, search_reserved_nano_usd=0, cost_known=True)
            self._search_reservation = None
        finally:
            self._release()

    def _output_limit(self, body):
        # The closed wire grammar allows a changed budget only for an ordinary
        # answer with its canonical capacity binding and conversation envelope.
        from pacttrail.platform.deepseek_tool_request import validate_request
        return validate_request(body)['max_tokens']

    def preview(self, body):
        """Read-only first-dispatch estimate; send always prepares again.

        Unknown prior usage retains its reservation and has no guessed balance.
        A preview never changes calls, metrics, stop state or reserved costs.
        """
        result = dict(estimated_input_tokens=None, output_reserved_tokens=self._output_limit(body),
                      reservation_nano_usd=None, admission_error=None, estimate_error=None,
                      input_limit_tokens=self.MAX_ESTIMATE_TOKENS, excess_input_tokens=None)
        if not self._lock.acquire(blocking=False):
            result['estimate_error'] = 'busy'
            return result
        try:
            state = self.status()
            try:
                fact = self._estimate(body)
            except Exception:
                result['estimate_error'] = 'token_count_unavailable'
                return result
            result.update(estimated_input_tokens=fact.input_tokens,
                          excess_input_tokens=max(0, fact.input_tokens - self.MAX_ESTIMATE_TOKENS),
                          reservation_nano_usd=self._reservation(body, fact.input_tokens))
            result['admission_error'] = (
                state['stop_reason'] if state['stopped'] else
                'completion_request_limit' if state['calls'] >= self.MAX_CALLS else
                'usage_unavailable' if not state['cost_known'] else
                'token_limit' if fact.input_tokens > self.MAX_ESTIMATE_TOKENS else
                'budget_limit' if state['accounted_nano_usd'] + result['reservation_nano_usd']
                    > state['budget_nano_usd'] else None)
            return result
        finally:
            self._lock.release()

    def _settle(self, usage):
        if self._search_reservation is not None:
            raise model_evidence_error('usage_unavailable')
        # The task workbench transports these integers as JSON numbers. Usage
        # beyond exact representation stays unknown; never publish rounded fees.
        if type(usage) is dict:
            fields = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                      'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
            values = tuple(usage.get(key) for key in fields)
            if any(type(value) is int and value > self.MAX_SAFE_INTEGER for value in values):
                raise model_evidence_error('usage_unavailable')
            if all(type(value) is int and value >= 0 for value in values):
                _, output, _, hit, miss = values
                amount = miss * 300 + hit * 6 + output * 1200
                if amount + self._state['accounted_nano_usd'] > self.MAX_SAFE_INTEGER:
                    raise model_evidence_error('usage_unavailable')
        super()._settle(usage)
