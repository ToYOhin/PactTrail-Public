"""Read-only source observations and untrusted model citation locations.

The observation can be byte-verified; a model's association between a claim and
that observation is always pending human inspection, including in-range lines.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re

from pacttrail.domain.source_anchors import anchor_key
from pacttrail.domain.task_record import TaskRecordError, record_digest
from pacttrail.platform.task_record_store import TaskRecordStore


MAX_CLAIMS = 64
_NUMBER = r'\d{1,8}'
_CHINESE = re.compile(r'第\s*(' + _NUMBER + r'(?:\s*(?:[、,，–—~～-]|至|到)\s*(?:第\s*)?'
                      + _NUMBER + r')*)\s*行')
_LOCATION = re.compile(r'(?P<name>[^\s`\[\]()（）{}<>，,；;：:]+):[Ll](?P<start>'
                       + _NUMBER + r')(?:\s*[-–—~～]\s*[Ll]?(?P<end>' + _NUMBER + r'))?')
_PART = re.compile(r'(' + _NUMBER + r')(?:\s*(?:[-–—~～]|至|到)\s*(?:第\s*)?(' + _NUMBER + r'))?')
_ANCHOR = re.compile(r'\[\[src:(?P<id>[a-f0-9]{16}):L(?P<start>[0-9]{1,8})'
                     r'(?:-L(?P<end>[0-9]{1,8}))?\]\]')
_BARE_LOCATION = re.compile(r'(?<![A-Za-z0-9_:/.\\-])[Ll](?P<start>' + _NUMBER
    + r')(?:\s*[-–—~～]\s*[Ll]?(?P<end>' + _NUMBER + r'))?(?![A-Za-z0-9_])')
_CLAUSE = re.compile(r'[。；;!?！？，,]|\.(?=\s|$)')
_BINDING_CLAUSE = re.compile(r'[。；;|（）()]|\.(?=\s|$)')
# These are bounded candidate cues, not a language/claim verifier. In particular,
# mentioning a whole document without one of these cues is not proof of reading.
_FULL_READ = re.compile(
    r'已(?:经)?(?:完整|全部|全面)(?:阅读|读取|通读)|已(?:经)?(?:阅读全文|读取全文|通读全文)'
    r'|已(?:经)?(?:阅读|读取|通读)[^。；;!?！？，,]{0,180}?(?:全文|完整内容|全部内容)'
    r'|阅读全文|(?:根据|依据|基于)(?:全文|完整内容|全部内容)|全文(?:说明|显示|表明|记载|确认)'
    r'|\b(?:read|reviewed|inspected)\s+(?:the\s+)?(?:entire|whole|complete|full)\s+(?:file|document|text)\b'
    r'|\b(?:entire|whole|complete|full)\s+(?:file|document|text)\s+'
    r'(?:(?:has\s+been|was|is)\s+)?(?:read|reviewed|inspected)\b', re.I)
_READ_LIMIT = re.compile(r'(?:未|没有|并非|不能|不应|需要|应该|应当|建议|请|是否|能否|计划|准备|待)'
    r'[^。；;!?！？]{0,8}$|\b(?:not|never|without|no|cannot|must|should|need|please|to)\b[^.!?;]{0,24}$', re.I)


def _clip(text, limit=240):
    return text.encode('utf-8')[:limit].decode('utf-8', errors='ignore')


def _clause_bounds(text, position, *, trailing_reference=False):
    separators = list(_CLAUSE.finditer(text))
    prior = [match for match in separators if match.start() < position]
    left = prior[-1].end() if prior else 0
    # Citation markers commonly follow a sentence's final punctuation. Keep
    # that sentence's cue, without borrowing a source identity for bare lines.
    if (trailing_reference and prior and prior[-1].group() in '。！？!?.'
            and not text[left:position].strip()):
        left = prior[-2].end() if len(prior) > 1 else 0
    right = next((match.start() for match in separators if match.start() >= position), len(text))
    return left, right


def _full_read_cues(text):
    result = []
    for match in _FULL_READ.finditer(text):
        left, _ = _clause_bounds(text, match.start())
        if not _READ_LIMIT.search(text[max(left, match.start() - 40):match.start()]):
            result.append(match)
    return result


def _quality_issues(file_ids, state, anchor, full_read, sources):
    if len(file_ids) != 1:
        return ['missing_source']
    source = next(source for source in sources if source['file_id'] == file_ids[0])
    issues = []
    search_anchor = anchor and anchor['observation'] == 'search'
    if search_anchor and (full_read or state in ('unobserved', 'out_of_range')):
        issues.append('search_scope')
    if full_read and (search_anchor or source['observation'] != 'read'):
        issues.append('read_required')
    return issues


def _aliases(sources):
    result = {}
    for source in sources:
        path = PurePosixPath(source['name'])
        for name in {source['name'], path.name, path.stem, source['file_id']}:
            result.setdefault(name.casefold(), set()).add(source['file_id'])
    return result


def _state(file_ids, start, end, sources):
    if len(file_ids) != 1 or start is None or end is None:
        return 'unresolved'
    source = next(source for source in sources if source['file_id'] == file_ids[0])
    if source['observation'] == 'none':
        return 'unobserved'
    if source['state'] != 'verified':
        return 'source_unavailable'
    if start < 1 or end < start or source['line_count'] is not None and end > source['line_count']:
        return 'out_of_range'
    if source['observation'] == 'search':
        hits = {match['line'] for match in source['matches'] if match['complete']}
        if end - start + 1 > len(hits) or any(line not in hits for line in range(start, end + 1)):
            return 'unobserved'
    return 'pending'


def _observations(record):
    """Rebuild keys only from actual retained tool observations.

    Read receipts identify the complete copied snapshot. Search anchors cover
    only the four retained 128-byte previews, even if the original tool found
    further lines. No new record fields or execution are needed to reopen them;
    legacy records do not imply a model previously received the new wire form.
    """
    result = {}
    for step in record['task']['steps']:
        observed = step['result']
        if step['kind'] != 'tool' or observed.get('status') != 'ok':
            continue
        if step['tool'] == 'read':
            files = {observed['file_id']: None}
        elif step['tool'] == 'search':
            files = {}
            for hit in observed['matches'][:4]:
                files.setdefault(hit['file_id'], []).append(hit)
        else:
            continue
        for file_id, hits in files.items():
            key = anchor_key(record['run_id'], step['index'], file_id)
            result.setdefault(key, []).append(dict(file_id=file_id, step=step['index'],
                observation=step['tool'], matches=hits))
    return result


def _anchored_location(match, observations, sources, start, end):
    anchor = dict(id=match['id'], step=None, observation=None, excerpt=None, truncated=False)
    candidates = observations.get(match['id'], [])
    if len(candidates) != 1:
        return [], 'unresolved', anchor
    observed = candidates[0]
    source = next((source for source in sources if source['file_id'] == observed['file_id']), None)
    if source is None:
        return [], 'unresolved', anchor
    anchor.update(step=observed['step'], observation=observed['observation'])
    file_ids = [source['file_id']]
    if start < 1 or end < 1:
        return file_ids, 'unresolved', anchor
    if source['observation'] == 'none' or observed['step'] not in source['steps']:
        return file_ids, 'unobserved', anchor
    if source['state'] != 'verified':
        return file_ids, 'source_unavailable', anchor
    if end < start or source['line_count'] is not None and end > source['line_count']:
        return file_ids, 'out_of_range', anchor
    if observed['observation'] == 'read':
        if source['text'] is None:
            return file_ids, 'unobserved', anchor
        lines = source['text'].replace('\r\n', '\n').replace('\r', '\n').split('\n')
        if end > len(lines):
            return file_ids, 'out_of_range', anchor
        excerpt = '\n'.join(lines[start - 1:end])
        shortened = False
    else:
        hits = {hit['line']: hit['text'] for hit in observed['matches']}
        if end - start + 1 > len(hits) or any(line not in hits for line in range(start, end + 1)):
            return file_ids, 'unobserved', anchor
        excerpt = '\n'.join(hits[line] for line in range(start, end + 1))
        complete = {hit['line']: hit['text'] for hit in source['matches'] if hit['complete']}
        shortened = any(complete.get(line) != hits[line] for line in range(start, end + 1))
    anchor.update(excerpt=_clip(excerpt, 1024), truncated=shortened or len(excerpt.encode('utf-8')) > 1024)
    return file_ids, 'pending', anchor


def _claims(documents, sources, observations=None):
    aliases = _aliases(sources)
    pattern = (re.compile(r'(?<![A-Za-z0-9_.-])(?:' + '|'.join(re.escape(name) for name in
               sorted(aliases, key=len, reverse=True)) + r')(?![A-Za-z0-9_-]|\.(?!\s|$))', re.I) if aliases else None)
    claims, truncated = [], False

    def add(text, path, line, file_ids, start, end, focus, *, anchor=None, state=None, full_read=False):
        nonlocal truncated
        if len(claims) >= MAX_CLAIMS:
            truncated = True
            return
        if start is None or end is None or start < 1 or end < 1:
            start = end = None
        file_ids = sorted(file_ids)
        offset = max(0, focus - 40) if len(text.encode('utf-8')) > 240 else 0
        claim = dict(id=len(claims) + 1, text=_clip(('…' if offset else '') + text[offset:]), artifact_path=path,
            artifact_line=line, file_ids=sorted(file_ids), start=start, end=end,
            state=state or _state(sorted(file_ids), start, end, sources))
        if anchor is not None:
            claim['anchor'] = anchor
        issues = _quality_issues(file_ids, claim['state'], anchor, full_read, sources)
        if issues:
            claim['issues'] = issues
        claims.append(claim)

    def named(prefix):
        names = list(pattern.finditer(prefix)) if pattern is not None else []
        return aliases.get(names[-1].group().casefold(), set()) if names else set()

    def named_in(text):
        names = list(pattern.finditer(text)) if pattern is not None else []
        return set().union(*(aliases[name.group().casefold()] for name in names)) if names else set()

    def reference_ids(kind, match, begin, line, previous):
        if kind == 'anchor':
            candidates = (observations or {}).get(match['id'], [])
            return {candidates[0]['file_id']} if len(candidates) == 1 else set()
        if kind == 'path':
            return aliases.get(match['name'].casefold(), set())
        return named(_BINDING_CLAUSE.split(line[previous:begin])[-1])

    for path, body in documents:
        for number, line in enumerate(body.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
            anchors = list(_ANCHOR.finditer(line))
            locations = [(match.start(), match.end(), 'anchor', match) for match in anchors]
            for kind, location_pattern in [('path', _LOCATION), ('chinese', _CHINESE), ('bare', _BARE_LOCATION)]:
                locations.extend((match.start(), match.end(), kind, match) for match in location_pattern.finditer(line)
                    if not any(match.start() < finish and match.end() > begin for begin, finish, _, _ in locations))
            references = tuple(sorted(locations))
            reference_sources, previous = {}, 0
            for begin, finish, kind, match in references:
                reference_sources[(begin, finish)] = reference_ids(kind, match, begin, line, previous)
                previous = max(previous, finish)
            cues, full_clauses = _full_read_cues(line), set()
            bounds_cache, names_cache = {}, {}

            def bounds_at(position, trailing=False):
                cache_key = position, trailing
                if cache_key not in bounds_cache:
                    bounds_cache[cache_key] = _clause_bounds(line, position, trailing_reference=trailing)
                return bounds_cache[cache_key]

            def names_at(bounds):
                if bounds not in names_cache:
                    names_cache[bounds] = named_in(line[bounds[0]:bounds[1]])
                return names_cache[bounds]

            def same_cue(cue, begin, finish, kind):
                left, right = bounds_at(begin, kind == 'anchor')
                if not left <= cue.start() < right:
                    return False
                explicit = names_at(bounds_at(cue.start()))
                # An explicit name owns this cue. An observation for a different
                # file cannot discharge it, even on the same artifact line.
                return not explicit or explicit == reference_sources.get((begin, finish), set())

            for cue in cues:
                bounds = bounds_at(cue.start())
                if (bounds not in full_clauses and not any(
                    same_cue(cue, begin, finish, kind)
                    for begin, finish, kind, _ in references)):
                    locations.append((cue.start(), cue.end(), 'full', cue))
                    full_clauses.add(bounds)
            prior = 0
            for begin, finish, kind, match in sorted(locations):
                left, right = bounds_at(begin, kind == 'anchor')
                full_read = kind != 'full' and any(same_cue(cue, begin, finish, kind) for cue in cues)
                if kind == 'anchor':
                    start, end = int(match['start']), int(match['end'] or match['start'])
                    file_ids, state, anchor = _anchored_location(match, observations or {}, sources, start, end)
                    add(line, path, number, file_ids, start, end, begin, anchor=anchor, state=state, full_read=full_read)
                elif kind == 'path':
                    file_ids = aliases.get(match['name'].casefold(), set())
                    start, end = int(match['start']), int(match['end'] or match['start'])
                    add(line, path, number, file_ids, start, end, begin, full_read=full_read)
                elif kind == 'full':
                    file_ids = names_at((left, right))
                    source = next((source for source in sources if source['file_id'] in file_ids), None)
                    if len(file_ids) != 1:
                        state = 'unresolved'
                    elif source['observation'] != 'none' and source['state'] != 'verified':
                        state = 'source_unavailable'
                    elif source['observation'] != 'read':
                        state = 'unobserved'
                    else:
                        # A retained read supports the mechanical reading claim;
                        # it does not verify any conclusion, so add no finding.
                        continue
                    add(line, path, number, file_ids, None, None, begin, state=state, full_read=True)
                else:
                    # A new reference with no name stays unresolved. Do not
                    # inherit the filename of a preceding citation or sentence.
                    prefix = line[prior:begin]
                    prefix = _BINDING_CLAUSE.split(prefix)[-1]
                    file_ids = named(prefix)
                    parts = _PART.finditer(match[1]) if kind == 'chinese' else [match]
                    for part in parts:
                        start = int(part[1] if kind == 'chinese' else part['start'])
                        last = part[2] if kind == 'chinese' else part['end']
                        end = int(last or start)
                        add(line, path, number, file_ids, start, end, begin, full_read=full_read)
                prior = max(prior, finish)
    return claims, truncated


def source_evidence(workspace, task_id, run_id, *, conversation_id, revision):
    result = dict(version=1, kind='source_evidence', conversation_id=conversation_id,
        revision=revision, task_id=task_id, run_id=run_id, record_sha256=None,
        record_state='unavailable', artifact_state='unavailable', artifact=None,
        sources=[], claims=[], truncated=False)
    try:
        store = TaskRecordStore(workspace.history_task_dir(task_id))
        evidence = store.source_evidence(run_id)
        record = evidence['record']
        result.update(record_state='verified', record_sha256=record_digest(record),
            sources=evidence['sources'], artifact_state=evidence['artifact_state'])
        report = evidence['report']
        documents = []
        if report is not None:
            kind = 'change_draft' if record['version'] in (4, 6) else 'report'
            result['artifact'] = dict(report, kind=kind)
            documents = ([(change['path'], change['new_text']) for change in json.loads(report['text'])['changes']]
                         if kind == 'change_draft' else [(None, report['text'])])
        elif (record['version'] == 5 and record['status'] == 'complete' and record['pending'] is None
                and record['task']['steps'] and record['task']['steps'][-1]['kind'] == 'finish'):
            text = record['task']['steps'][-1]['reason']
            raw = text.encode('utf-8')
            result.update(artifact_state='verified', artifact=dict(kind='answer', name=None,
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), text=text))
            documents = [(None, text)]
        result['claims'], result['truncated'] = _claims(documents, result['sources'], _observations(record))
    except TaskRecordError as error:
        if error.code == 'record_missing':
            result['record_state'] = 'missing'
            result['artifact_state'] = 'missing'
    except Exception:
        pass
    return result
