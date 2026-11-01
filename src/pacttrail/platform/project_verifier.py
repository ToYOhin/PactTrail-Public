"""A fixed, bounded syntax worker over an already verified project snapshot."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from pacttrail.application.chat_project import validate_saved


def _result(status, exit_code=None, *, files=None, error=None, reaped=True):
    return {'status': status, 'exit_code': exit_code, 'files': files or [],
            'error': error, 'reaped': reaped}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(unused):
    raise ValueError()


class _Capture:
    def __init__(self, stream, limit, overflow):
        self.stream, self.limit, self.overflow = stream, limit, overflow
        self.raw, self.failed = bytearray(), False
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self):
        try:
            while True:
                chunk = self.stream.read(256)
                if not chunk:
                    break
                available = self.limit + 1 - len(self.raw)
                self.raw.extend(chunk[:available])
                if len(chunk) > available or len(self.raw) > self.limit:
                    self.overflow.set()
                    break
        except Exception:
            self.failed = True
        finally:
            try:
                self.stream.close()
            except Exception:
                self.failed = True


def _write_input(stream, raw, failed):
    try:
        remaining = memoryview(raw)
        while remaining:
            count = stream.write(remaining)
            if type(count) is not int or count <= 0:
                raise OSError()
            remaining = remaining[count:]
    except Exception:
        failed.set()
    finally:
        try:
            stream.close()
        except Exception:
            failed.set()


def _reap(process):
    """Never return a reusable state when this process cannot be confirmed reaped."""
    try:
        if process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            code = process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            code = process.wait(timeout=0.5)
        if type(code) is not int:
            return False, None
        return True, code
    except Exception:
        return False, None


def _environment(worker):
    # Do not copy the parent environment or discover any Provider configuration.
    return {'SystemRoot': r'C:\Windows', 'WINDIR': r'C:\Windows', 'SystemDrive': 'C:',
            'PATH': os.defpath, 'TEMP': str(worker.parent), 'TMP': str(worker.parent),
            'PYTHONUTF8': '1', 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'TOKENIZERS_PARALLELISM': 'false', 'RAYON_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
            'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}


def _output(raw, paths, code):
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if type(value) is not dict or set(value) != {'status', 'files', 'error'}:
        raise ValueError()
    if value['status'] == 'error':
        if code != 2 or value['files'] != [] or value['error'] not in ('worker_invalid', 'worker_error'):
            raise ValueError()
        return _result('error', code, error='verification_worker_error')
    if (value['status'] not in ('passed', 'failed') or value['error'] is not None
            or type(value['files']) is not list or len(value['files']) != len(paths)):
        raise ValueError()
    for path, item in zip(paths, value['files']):
        if (type(item) is not dict or set(item) != {'path', 'status', 'line'} or item['path'] != path
                or item['status'] not in ('passed', 'failed')
                or (item['line'] is not None and (type(item['line']) is not int or not 1 <= item['line'] <= 257))
                or item['status'] == 'passed' and item['line'] is not None):
            raise ValueError()
    failed = any(item['status'] == 'failed' for item in value['files'])
    if value['status'] != ('failed' if failed else 'passed') or code != (1 if failed else 0):
        raise ValueError()
    return _result(value['status'], code, files=value['files'])


class ProjectVerifier:
    def __init__(self, *, timeout_seconds=5):
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 5):
            raise ValueError('invalid verification timeout')
        self.timeout_seconds = timeout_seconds
        self._unreaped_process = None

    def run(self, project, cancel_event):
        if self._unreaped_process is not None:
            return _result('unknown', error='verification_unknown', reaped=False)
        try:
            if not isinstance(cancel_event, threading.Event):
                raise ValueError()
            snapshot = validate_saved(project)
            if snapshot['version'] != 1:
                raise ValueError()
            files = [{'path': item['path'], 'text': item['text']} for item in snapshot['files']
                     if item['path'].lower().endswith('.py')]
            if not files:
                raise ValueError()
            raw = json.dumps({'version': 1, 'files': files}, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
            if len(raw) > 65536:
                raise ValueError()
        except Exception:
            return _result('error', error='verification_invalid')
        if cancel_event.is_set():
            return _result('cancelled')
        try:
            worker = Path(__file__).with_name('python_syntax_worker.py').resolve(strict=True)
            process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(worker)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(worker.parent), env=_environment(worker), shell=False, close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, bufsize=0)
        except Exception:
            return _result('error', error='verification_unavailable')
        overflow, input_failed = threading.Event(), threading.Event()
        stdout, stderr = _Capture(process.stdout, 2048, overflow), _Capture(process.stderr, 1024, overflow)
        writer = threading.Thread(target=_write_input, args=(process.stdin, raw, input_failed), daemon=True)
        threads = [stdout.thread, stderr.thread, writer]
        reason = None
        try:
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if overflow.is_set():
                    reason = 'limit'
                    break
                if process.poll() is not None:
                    break
                if cancel_event.is_set():
                    reason = 'cancelled'
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = 'timed_out'
                    break
                cancel_event.wait(min(0.025, remaining))
            reaped, code = _reap(process)
            if not reaped:
                self._unreaped_process = process
                return _result('unknown', error='verification_unknown', reaped=False)
            for thread in threads:
                thread.join(timeout=0.25)
            if overflow.is_set():
                return _result('error', code, error='verification_output_limit')
            if reason in ('cancelled', 'timed_out'):
                return _result(reason, code)
            if (any(thread.is_alive() for thread in threads) or input_failed.is_set()
                    or stdout.failed or stderr.failed or stderr.raw):
                return _result('error', code, error='verification_output_invalid')
            try:
                return _output(bytes(stdout.raw), [item['path'] for item in files], code)
            except Exception:
                return _result('error', code, error='verification_output_invalid')
        except Exception:
            reaped, code = _reap(process)
            if not reaped:
                self._unreaped_process = process
                return _result('unknown', error='verification_unknown', reaped=False)
            return _result('error', code, error='verification_unavailable')
