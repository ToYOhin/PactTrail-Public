"""Closed public URL and numeric-address policy; no DNS or network side effects."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote, urlsplit


WEB_CODES = frozenset({'invalid_url', 'forbidden_address', 'invalid_response', 'response_limit',
                       'redirect_limit', 'timeout', 'cancelled', 'unavailable', 'busy'})
_TRANSITION = tuple(ipaddress.ip_network(value) for value in (
    '::/96', '64:ff9b::/96', '64:ff9b:1::/48', '2001::/23', '2002::/16', '3ffe::/16',
))
_SPECIAL_V4 = tuple(ipaddress.ip_network(value) for value in ('192.0.0.0/24', '192.88.99.0/24'))


class WebSourceError(ValueError):
    """Only a fixed category escapes the adapter, never URLs or server errors."""

    def __init__(self, code='unavailable'):
        self.code = code if code in WEB_CODES else 'unavailable'
        super().__init__('web_source_error: ' + self.code)


def public_address(value):
    try:
        if type(value) is not str or '%' in value:
            raise ValueError()
        address = ipaddress.ip_address(value)
        if (not address.is_global or address.is_private or address.is_loopback
                or address.is_link_local or address.is_multicast or address.is_reserved
                or address.is_unspecified
                or address.version == 4 and any(address in network for network in _SPECIAL_V4)
                or address.version == 6 and (address.ipv4_mapped is not None
                    or any(address in network for network in _TRANSITION))):
            raise ValueError()
        return address.compressed
    except (ValueError, TypeError):
        raise WebSourceError('forbidden_address') from None


def validate_url(value):
    """Return an owned canonical target while preserving origin identity for TLS."""
    try:
        if (type(value) is not str or not 1 <= len(value) <= 2048
                or any(char.isspace() or ord(char) < 33 or 127 <= ord(char) <= 159
                       or 0xD800 <= ord(char) <= 0xDFFF for char in value)
                or any(char in value for char in ('\\', '#'))
                or re.search(r'%(?![a-fA-F0-9]{2})', value)):
            raise ValueError()
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if (scheme not in ('http', 'https') or not parsed.netloc or not parsed.hostname
                or '@' in parsed.netloc or '%' in parsed.netloc or parsed.netloc.endswith(':')):
            raise ValueError()
        port = 443 if scheme == 'https' else 80
        if parsed.port is not None and parsed.port != port:
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if host.endswith('.') or len(host) > 253:
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            host = public_address(host)
        else:
            if (host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan'))):
                raise WebSourceError('forbidden_address')
            if (':' in host or '.' not in host
                    or re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*', host)
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in host.split('.'))):
                raise ValueError()
        authority = '[' + host + ']' if literal is not None and literal.version == 6 else host
        path = quote(parsed.path or '/', safe="/:@!$&'()*+,;=-._~%")
        query = quote(parsed.query, safe="/?:@!$&'()*+,;=-._~%")
        target = path + ('?' + query if query else '')
        target = re.sub(r'%[a-fA-F0-9]{2}', lambda match: match[0].upper(), target)
        url = scheme + '://' + authority + target
        if len(url) > 512 or not url.isascii():
            raise ValueError()
        return {'url': url, 'scheme': scheme, 'host': host, 'port': port, 'target': target}
    except WebSourceError:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise WebSourceError('invalid_url') from None


def validate_urls(values):
    if type(values) is not list or not 1 <= len(values) <= 3:
        raise WebSourceError('invalid_url')
    targets = [validate_url(value) for value in values]
    if len({target['url'] for target in targets}) != len(targets):
        raise WebSourceError('invalid_url')
    return targets
