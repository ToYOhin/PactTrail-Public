"""Pinned local text estimates; no hosted-framing or billing guarantee.

The narrow chat renderer is derived from DeepSeek's MIT-licensed encoding.py at
df42c109f1defefcbfcedbe7d905718a12266e40 (Copyright 2023 DeepSeek).
See licenses/DeepSeek-tokenizer-LICENSE and THIRD_PARTY_NOTICES.md. PactTrail owns the
restricted request validation, verified resource loading and estimate binding.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import metadata, resources
import json

from pacttrail.application.model_evidence import model_evidence_error
from pacttrail.application import ordinary_chat


MODEL = 'deepseek-flash'
TOKENIZER_SHA256 = 'c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b'
CONFIG_SHA256 = '6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547'
RENDERER_ID = 'deepseek-v41-chat-text-v1'
TOOL_RENDERER_ID = 'deepseek-tool-json-local-v1'
TOOL_MESSAGE_ALLOWANCE = 32
TOKENIZERS_VERSION = '0.23.2'
TOKEN_BASIS = 'local-estimate'
_BOS = '<｜begin▁of▁sentence｜>'
_EOS = '<｜end▁of▁sentence｜>'
_SYSTEM = '<｜System｜>'
_USER = '<｜User｜>'
_ASSISTANT = '<｜Assistant｜>'


@dataclass(frozen=True)
class LocalTokenEstimate:
    request_sha256: str
    input_tokens: int
    model: str = MODEL
    basis: str = TOKEN_BASIS
    tokenizer_sha256: str = TOKENIZER_SHA256
    config_sha256: str = CONFIG_SHA256
    renderer_id: str = RENDERER_ID
    library_version: str = TOKENIZERS_VERSION


def _resource_bytes(name, size, expected_hash):
    resource = resources.files('pacttrail.platform').joinpath('deepseek_tokenizer').joinpath(name)
    with resource.open('rb') as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('resource')
    return raw


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('constant')


def _render(body, markers, request_mode='evidence'):
    if type(body) is not bytes or not 0 < len(body) <= 32768:
        raise ValueError('body')
    request = json.loads(body.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
    fields = {'model', 'stream', 'thinking', 'max_tokens', 'messages'}
    if request_mode == 'evidence':
        fields.add('response_format')
    if (type(request) is not dict or set(request) != fields
            or request['model'] != MODEL or request['stream'] is not False
            or request['thinking'] != {'type': 'disabled'}
            or type(request['max_tokens']) is not int or request['max_tokens'] != 512
            or (request_mode == 'evidence' and request['response_format'] != {'type': 'json_object'})
            or type(request['messages']) is not list or len(request['messages']) not in (2, 4, 6)):
        raise ValueError('request')
    parts = [_BOS]
    for index, message in enumerate(request['messages']):
        role = 'system' if index == 0 else 'user' if index % 2 else 'assistant'
        if (type(message) is not dict or set(message) != {'role', 'content'}
                or message['role'] != role or type(message['content']) is not str
                or not message['content'] or any(marker in message['content'] for marker in markers)):
            raise ValueError('message')
        content = message['content']
        if request_mode == 'ordinary' and role != 'system':
            limit = 256 if role == 'user' else ordinary_chat.MAX_ANSWER_CHARS
            if not ordinary_chat.valid_text(content, limit):
                raise ValueError('ordinary message')
        if role == 'system':
            parts.extend((_SYSTEM, content))
        elif role == 'user':
            parts.extend((_USER, content, _ASSISTANT, '</think>'))
        else:
            parts.extend((content, _EOS))
    prompt = ''.join(parts)
    # JSON can contain escaped unpaired surrogates even after UTF-8 decoding.
    prompt.encode('utf-8')
    return prompt


def _render_tools(body, markers):
    """Count closed canonical JSON, without claiming hosted tool-chat framing."""
    from pacttrail.platform.deepseek_tool_request import encode, validate_request
    request = validate_request(body)

    def check(value):
        if type(value) is str:
            if any(marker in value for marker in markers):
                raise ValueError('marker')
            value.encode('utf-8')
        elif type(value) is dict:
            for key, item in value.items():
                check(key)
                check(item)
        elif type(value) is list:
            for item in value:
                check(item)

    check(request)
    # Arguments and tool observations are JSON strings on the wire. Inspect
    # decoded data too, so JSON Unicode escapes cannot hide a reserved marker.
    for message in request['messages']:
        if message['role'] == 'assistant':
            for call in message['tool_calls']:
                check(json.loads(call['function']['arguments'],
                                 object_pairs_hook=_unique, parse_constant=_constant))
        elif message['role'] == 'tool':
            check(json.loads(message['content'], object_pairs_hook=_unique, parse_constant=_constant))
    return encode(request).decode('utf-8'), len(request['messages'])


class DeepSeekTokenEstimator:
    """Load only fixed bundled data when explicitly constructed by trusted code."""

    def __init__(self, *, request_mode='evidence'):
        if type(request_mode) is not str or request_mode not in ('evidence', 'ordinary', 'tools'):
            raise model_evidence_error('invalid_request')
        self._request_mode = request_mode
        try:
            if metadata.version('tokenizers') != TOKENIZERS_VERSION:
                raise ValueError('version')
            raw = _resource_bytes('tokenizer.json', 6367257, TOKENIZER_SHA256)
            _resource_bytes('tokenizer_config.json', 801, CONFIG_SHA256)
            document = json.loads(raw)
            # Role/think/DSML tokens have special=false upstream; include all.
            self._markers = tuple(token['content'] for token in document['added_tokens'])
            import tokenizers
            if tokenizers.__version__ != TOKENIZERS_VERSION:
                raise ValueError('version')
            self._tokenizer = tokenizers.Tokenizer.from_str(raw.decode('utf-8'))
            if self._tokenizer.truncation is not None or self._tokenizer.padding is not None:
                raise ValueError('count options')
        except Exception:
            raise model_evidence_error('token_count_unavailable') from None

    def __call__(self, body: bytes) -> LocalTokenEstimate:
        try:
            if self._request_mode == 'tools':
                prompt, message_count = _render_tools(body, self._markers)
                allowance, renderer_id = message_count * TOOL_MESSAGE_ALLOWANCE, TOOL_RENDERER_ID
            else:
                prompt = _render(body, self._markers, self._request_mode)
                allowance, renderer_id = 0, RENDERER_ID
            count = len(self._tokenizer.encode(prompt, add_special_tokens=False).ids) + allowance
            if count <= 0:
                raise ValueError('count')
            return LocalTokenEstimate(hashlib.sha256(body).hexdigest(), count, renderer_id=renderer_id)
        except Exception:
            raise model_evidence_error('token_count_unavailable') from None
