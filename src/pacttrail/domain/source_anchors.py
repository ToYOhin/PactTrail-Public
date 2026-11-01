"""Run-owned source locations; binding a position never verifies a claim."""
from __future__ import annotations

import hashlib
import json
import re


def anchor_key(run_id, step, file_id):
    """Identify an actual observation, not a filename supplied by the model."""
    if (type(run_id) is not str or re.fullmatch(r'[a-f0-9]{32}', run_id) is None
            or type(step) is not int or not 1 <= step <= 8
            or type(file_id) is not str or not file_id or len(file_id) > 160):
        raise ValueError('invalid source anchor')
    identity = ['pacttrail-source-anchor-v1', run_id, step, file_id]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()[:16]


def located_text(text):
    """Prefix actual decoded lines without changing original bytes/endings."""
    parts = re.split(r'(\r\n|\r|\n)', text)
    return ''.join(f'L{index // 2 + 1} | {part}' if index % 2 == 0 else part
                   for index, part in enumerate(parts))


def original_text(text):
    if type(text) is not str or len(text) > 65536:
        raise ValueError('invalid located text')
    parts = re.split(r'(\r\n|\r|\n)', text)
    for index in range(0, len(parts), 2):
        prefix = f'L{index // 2 + 1} | '
        if not parts[index].startswith(prefix):
            raise ValueError('invalid line location')
        parts[index] = parts[index][len(prefix):]
    return ''.join(parts)
