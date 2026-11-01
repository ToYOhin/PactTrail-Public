"""One user-selected UTF-8 snapshot; tool dispatch requires separate bound consent."""
import base64
import binascii
import hashlib

MAX_BYTES = 8192
MAX_LINES = 256
MAX_ENCODED = 4 * ((MAX_BYTES + 2) // 3)


def _error(code):
    from pacttrail.application.chat import chat_error
    return chat_error(code)


def _snapshot(name, raw):
    if (type(name) is not str or not 1 <= len(name) <= 80
            or not name.lower().endswith('.txt')
            or any(c in '/\\:' or ord(c) < 32 or ord(c) == 127
                   or 0xD800 <= ord(c) <= 0xDFFF for c in name)):
        raise _error('material_invalid')
    if len(raw) > MAX_BYTES:
        raise _error('material_limit')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise _error('material_invalid') from None
    if (not any(not c.isspace() and c != '\ufeff' for c in text)
            or any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127 for c in text)):
        raise _error('material_invalid')
    lines = len(text.replace('\r\n', '\n').replace('\r', '\n').split('\n'))
    if lines > MAX_LINES:
        raise _error('material_limit')
    return {'name': name, 'text': text, 'sha256': hashlib.sha256(raw).hexdigest(),
            'byte_count': len(raw), 'line_count': lines}


def from_upload(name, data):
    if type(data) is not str:
        raise _error('material_invalid')
    if len(data) > MAX_ENCODED:
        raise _error('material_limit')
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise _error('material_invalid') from None
    # Reject alternate encodings/padding so one byte sequence has one wire form.
    if base64.b64encode(raw).decode('ascii') != data:
        raise _error('material_invalid')
    return _snapshot(name, raw)


def validate_saved(value):
    if (type(value) is not dict or set(value) != {'name', 'text', 'sha256', 'byte_count', 'line_count'}
            or type(value['text']) is not str or len(value['text']) > MAX_BYTES
            or type(value['byte_count']) is not int or type(value['line_count']) is not int):
        raise _error('material_invalid')
    try:
        raw = value['text'].encode('utf-8')
    except UnicodeEncodeError:
        raise _error('material_invalid') from None
    expected = _snapshot(value['name'], raw)
    if value != expected:
        raise _error('material_invalid')
    return expected
