"""Explicitly uploaded bounded project snapshot; never opens original paths."""
from pacttrail.application import chat_material
from pacttrail.domain.project_source import make_project_source
from pacttrail.domain.tool_task import DecisionError


def from_upload(name, files):
    if type(files) is not list or not 1 <= len(files) <= 4:
        raise chat_material._error('material_limit')
    snapshots = {}
    metadata = []
    for entry in files:
        if type(entry) is not dict or set(entry) != {'path', 'data'} or type(entry['path']) is not str:
            raise chat_material._error('material_invalid')
        snapshot = chat_material.from_upload('selected.txt', entry['data'])
        metadata.append({'path': entry['path'], **{key: snapshot[key]
                        for key in ('sha256', 'byte_count', 'line_count')}})
        snapshots[entry['path']] = snapshot['text']
    try:
        source = make_project_source(name, metadata)
    except DecisionError:
        raise chat_material._error('material_invalid') from None
    return {**source, 'files': [{**entry, 'text': snapshots[entry['path']]} for entry in source['files']]}


def validate_saved(value):
    import base64
    from pacttrail.domain.project_source import validate_project_source
    if (type(value) is not dict
            or set(value) != {'kind', 'version', 'name', 'sha256', 'byte_count', 'files'}
            or type(value['version']) is not int or type(value['byte_count']) is not int
            or type(value['files']) is not list or not 1 <= len(value['files']) <= 4):
        raise chat_material._error('material_invalid')
    metadata = []
    for entry in value['files']:
        if (type(entry) is not dict
                or set(entry) != {'file_id', 'path', 'sha256', 'byte_count', 'line_count', 'text'}
                    | ({'web'} if value['version'] == 2 else set())
                or type(entry['text']) is not str or len(entry['text']) > chat_material.MAX_BYTES
                or type(entry['byte_count']) is not int or type(entry['line_count']) is not int):
            raise chat_material._error('material_invalid')
        try:
            data = base64.b64encode(entry['text'].encode('utf-8')).decode('ascii')
        except UnicodeError:
            raise chat_material._error('material_invalid') from None
        actual = chat_material.from_upload('selected.txt', data)
        if any(actual[key] != entry[key] for key in ('sha256', 'byte_count', 'line_count')):
            raise chat_material._error('material_invalid')
        metadata.append({key: item for key, item in entry.items() if key != 'text'})
    try:
        source = validate_project_source({**value, 'files': metadata})
    except DecisionError:
        raise chat_material._error('material_invalid')
    return {**source, 'files': [{**entry, 'text': value['files'][index]['text']}
                              for index, entry in enumerate(source['files'])]}


def from_web_pages(pages, transport):
    """Only trusted acquisition composition can create typed web provenance."""
    import base64
    if type(pages) is not list or not 1 <= len(pages) <= 3 or transport not in ('http', 'synthetic'):
        raise chat_material._error('material_invalid')
    metadata, texts = [], []
    for index, page in enumerate(pages, 1):
        if (type(page) is not dict or set(page) != {
                'requested_url', 'final_url', 'fetched_at', 'media_type', 'text'} or type(page['text']) is not str):
            raise chat_material._error('material_invalid')
        try:
            encoded = base64.b64encode(page['text'].encode('utf-8')).decode('ascii')
        except UnicodeError:
            raise chat_material._error('material_invalid') from None
        snapshot = chat_material.from_upload('selected.txt', encoded)
        texts.append(snapshot['text'])
        metadata.append({'path': f'page-{index}.txt', **{key: snapshot[key]
                        for key in ('sha256', 'byte_count', 'line_count')},
                         'web': {**{key: item for key, item in page.items() if key != 'text'}, 'transport': transport}})
    if sum(entry['byte_count'] for entry in metadata) > 8192:
        raise chat_material._error('material_limit')
    try:
        source = make_project_source('web-research', metadata, version=2)
    except DecisionError:
        raise chat_material._error('material_invalid') from None
    return {**source, 'files': [{**entry, 'text': texts[index]} for index, entry in enumerate(source['files'])]}
