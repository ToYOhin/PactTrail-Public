"""Opt-in, workspace-scoped DeepSeek key storage in Windows Credential Manager.

Importing this module does not load WinAPI or inspect any credentials. The CLI
constructs the store only for an explicit --remember-model-key launch. Every
operation addresses one exact generic target; enumeration is never used.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
import re


_PREFIX = 'PactTrail/DeepSeek/v1/'
_GENERIC = 1
_LOCAL_MACHINE = 2  # Same Windows user, subsequent logons, this computer only.
_NOT_FOUND = 1168
_MAX_KEY_BYTES = 512


class CredentialStoreError(Exception):
    """Fixed public diagnostic: never propagate a backend message or key."""

    def __init__(self, code='credential_store_unavailable'):
        if code not in ('credential_store_unavailable', 'credential_store_invalid_key',
                        'credential_store_corrupt'):
            code = 'credential_store_unavailable'
        self.code = code
        super().__init__(code)


def _valid_key(key):
    return (type(key) is str and 1 <= len(key) <= _MAX_KEY_BYTES
            and all(33 <= ord(char) <= 126 for char in key))


def _valid_target(target):
    return (type(target) is str
            and re.fullmatch(re.escape(_PREFIX) + r'[0-9a-f]{64}', target) is not None)


class ModelCredentialStore:
    """Store only this workspace's key; an injected backend keeps tests offline.

    Backends implement read(target)->bytes|None, write(target, bytes), and
    delete(target). No key or workspace path is written to a manifest or log.
    """

    def __init__(self, workspace, *, backend=None):
        try:
            path = os.fspath(workspace)
            if type(path) is not str or not path or '\x00' in path:
                raise ValueError()
            canonical = os.path.normcase(os.path.abspath(path))
            self.target = _PREFIX + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        except Exception:
            raise CredentialStoreError() from None
        self._backend = WindowsCredentialBackend() if backend is None else backend
        if not all(callable(getattr(self._backend, name, None))
                   for name in ('read', 'write', 'delete')):
            raise CredentialStoreError()

    def load(self):
        try:
            blob = self._backend.read(self.target)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError() from None
        if blob is None:
            return None
        try:
            if type(blob) is not bytes or not 1 <= len(blob) <= _MAX_KEY_BYTES:
                raise ValueError()
            key = blob.decode('ascii')
            if not _valid_key(key):
                raise ValueError()
            return key
        except Exception:
            raise CredentialStoreError('credential_store_corrupt') from None

    def save(self, key):
        if not _valid_key(key):
            raise CredentialStoreError('credential_store_invalid_key')
        try:
            self._backend.write(self.target, key.encode('ascii'))
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError() from None

    def clear(self):
        try:
            self._backend.delete(self.target)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError() from None


class _Credential(ctypes.Structure):
    _fields_ = [
        ('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
        ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
        ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
        ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
        ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR),
    ]


class WindowsCredentialBackend:
    """Minimal CredReadW/CredWriteW/CredDeleteW wrapper, no enumeration API."""

    def __init__(self):
        if os.name != 'nt':
            raise CredentialStoreError()
        try:
            library = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
            self._read = library.CredReadW
            self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(_Credential))]
            self._read.restype = wintypes.BOOL
            self._write = library.CredWriteW
            self._write.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
            self._write.restype = wintypes.BOOL
            self._delete = library.CredDeleteW
            self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            self._delete.restype = wintypes.BOOL
            self._free = library.CredFree
            self._free.argtypes = [ctypes.c_void_p]
            self._free.restype = None
        except Exception:
            raise CredentialStoreError() from None

    @staticmethod
    def _check_target(target):
        if not _valid_target(target):
            raise CredentialStoreError()

    def read(self, target):
        self._check_target(target)
        pointer = ctypes.POINTER(_Credential)()
        if not self._read(target, _GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == _NOT_FOUND:
                return None
            raise CredentialStoreError()
        try:
            if not pointer:
                raise CredentialStoreError('credential_store_corrupt')
            credential = pointer.contents
            if (credential.Type != _GENERIC or credential.TargetName != target
                    or not 1 <= credential.CredentialBlobSize <= _MAX_KEY_BYTES
                    or not credential.CredentialBlob):
                raise CredentialStoreError('credential_store_corrupt')
            return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        finally:
            # CredRead returns one allocation, released even for invalid content.
            self._free(pointer)

    def write(self, target, blob):
        self._check_target(target)
        if type(blob) is not bytes or not 1 <= len(blob) <= _MAX_KEY_BYTES:
            raise CredentialStoreError('credential_store_invalid_key')
        secret = bytearray(blob)
        buffer = (ctypes.c_ubyte * len(secret)).from_buffer(secret)
        credential = _Credential()
        credential.Type = _GENERIC
        credential.TargetName = target
        credential.Comment = 'PactTrail local DeepSeek connection'
        credential.CredentialBlobSize = len(secret)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _LOCAL_MACHINE
        credential.UserName = 'PactTrail'
        try:
            if not self._write(ctypes.byref(credential), 0):
                raise CredentialStoreError()
        finally:
            ctypes.memset(buffer, 0, len(secret))

    def delete(self, target):
        self._check_target(target)
        if not self._delete(target, _GENERIC, 0) and ctypes.get_last_error() != _NOT_FOUND:
            raise CredentialStoreError()


# Explicit platform name for CLI composition; the same API supports fake backends.
WindowsModelCredentialStore = ModelCredentialStore
