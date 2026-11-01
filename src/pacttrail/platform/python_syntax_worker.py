"""Trusted stdin-only parser. Never import, open, or execute selected project code."""
from __future__ import annotations

import ast
import json
import re
import sys


MAX_INPUT = 65536
MAX_OUTPUT = 2048
_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3]|CONIN\$|CONOUT\$)(?:\..*)?\Z', re.I)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _invalid_constant(unused):
    raise ValueError()


def _path(value):
    if type(value) is not str or not 1 <= len(value) <= 160 or not value.lower().endswith('.py'):
        raise ValueError()
    parts = value.split('/')
    if len(parts) > 8:
        raise ValueError()
    for part in parts:
        if (not 1 <= len(part) <= 80 or not part.strip() or part.startswith('.')
                or part.endswith(('.', ' ')) or part.casefold() in ('node_modules', '__pycache__')
                or _DEVICE.fullmatch(part) or any(c in '/\\:' or ord(c) < 32
                    or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in part)):
            raise ValueError()
    return value


def _files(raw):
    if len(raw) > MAX_INPUT:
        raise ValueError()
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if (type(value) is not dict or set(value) != {'version', 'files'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['files']) is not list or not 1 <= len(value['files']) <= 4):
        raise ValueError()
    used, total = set(), 0
    for item in value['files']:
        if type(item) is not dict or set(item) != {'path', 'text'} or type(item['text']) is not str:
            raise ValueError()
        path, text = _path(item['path']), item['text']
        if (path.casefold() in used or not text.strip()
                or len(text.splitlines()) > 256 or any(ord(c) < 32 and c not in '\r\n\t'
                    or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
            raise ValueError()
        used.add(path.casefold())
        total += len(text.encode('utf-8'))
        if total > 8192:
            raise ValueError()
    return value['files']


def _emit(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_OUTPUT:
        raise ValueError()
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main():
    try:
        files = _files(sys.stdin.buffer.read(MAX_INPUT + 1))
    except Exception:
        _emit({'status': 'error', 'files': [], 'error': 'worker_invalid'})
        return 2
    try:
        results = []
        for item in files:
            try:
                ast.parse(item['text'], filename=item['path'], mode='exec')
            except SyntaxError as error:
                line = error.lineno if type(error.lineno) is int and 1 <= error.lineno <= 257 else None
                results.append({'path': item['path'], 'status': 'failed', 'line': line})
            else:
                results.append({'path': item['path'], 'status': 'passed', 'line': None})
        failed = any(item['status'] == 'failed' for item in results)
        _emit({'status': 'failed' if failed else 'passed', 'files': results, 'error': None})
        return 1 if failed else 0
    except Exception:
        _emit({'status': 'error', 'files': [], 'error': 'worker_error'})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
