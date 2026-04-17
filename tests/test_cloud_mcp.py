'''Tests for cloud MCP tools and sync preflight behavior.'''
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import KontextDB


def _make_request(method, tool_name=None, args=None, req_id=1):
    request = {'jsonrpc': '2.0', 'id': req_id, 'method': method}
    if tool_name:
        request['params'] = {'name': tool_name, 'arguments': args or {}}
    elif args:
        request['params'] = args
    return request


@pytest.fixture
def db(tmp_path):
    d = KontextDB(str(tmp_path / 'test.db'))
    yield d
    d.close()


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / 'memory'
    mem.mkdir()
    (mem / 'MEMORY.md').write_text(
        '''- [Test File](test_file.md) - test entries
''',
        encoding='utf-8',
    )
    (mem / 'test_file.md').write_text(
        '''---
name: test_file
description: test
type: user
---

## Active

- Fact one
''',
        encoding='utf-8',
    )
    return mem


@pytest.fixture
def entries(memory_dir):
    from mcp_server import index_memories

    return index_memories(memory_dir)


def test_tools_list_includes_cloud_tools(memory_dir, entries):
    from mcp_server import handle_request

    resp = handle_request(_make_request('tools/list'), memory_dir, entries)
    tool_names = {tool['name'] for tool in resp['result']['tools']}

    assert {
        'kontext_cloud_status',
        'kontext_cloud_link',
        'kontext_cloud_sync',
        'kontext_cloud_recover',
    } <= tool_names


def test_cloud_status_reports_unlinked(memory_dir, entries):
    from mcp_server import handle_request

    resp = handle_request(_make_request('tools/call', 'kontext_cloud_status'), memory_dir, entries)
    text = resp['result']['content'][0]['text'].lower()

    assert 'not linked' in text


def test_cloud_link_returns_linked_workspace(memory_dir, entries):
    from mcp_server import handle_request

    with patch('cloud.daemon.link_workspace', return_value={
        'workspace_id': 'ws-1',
        'device_id': 'dev-1',
        'server_url': 'http://cloud.test',
        'device_class': 'interactive',
        'linked': True,
    }):
        resp = handle_request(
            _make_request(
                'tools/call',
                'kontext_cloud_link',
                {
                    'server_url': 'http://cloud.test',
                    'workspace_id': 'ws-1',
                    'device_id': 'dev-1',
                    'label': 'Laptop',
                    'device_class': 'interactive',
                },
            ),
            memory_dir,
            entries,
        )

    text = resp['result']['content'][0]['text']
    assert 'ws-1' in text
    assert 'dev-1' in text
    assert 'http://cloud.test' in text


def test_cloud_sync_reports_push_and_pull_counts(memory_dir, entries):
    from mcp_server import handle_request

    with patch('cloud.daemon.sync_once', return_value={
        'linked': True,
        'pushed': 2,
        'pulled': 3,
        'cursor': 'op-3',
    }):
        resp = handle_request(_make_request('tools/call', 'kontext_cloud_sync'), memory_dir, entries)

    text = resp['result']['content'][0]['text'].lower()
    assert 'pushed 2' in text
    assert 'pulled 3' in text
    assert 'op-3' in text


def test_cloud_recover_reports_replayed_count(memory_dir, entries):
    from mcp_server import handle_request

    with patch('cloud.daemon.recover_workspace', return_value={
        'linked': True,
        'recovered': 5,
        'cursor': 'op-5',
    }):
        resp = handle_request(_make_request('tools/call', 'kontext_cloud_recover'), memory_dir, entries)

    text = resp['result']['content'][0]['text'].lower()
    assert 'recovered 5' in text
    assert 'op-5' in text


def test_sync_runs_cloud_pull_before_file_import(db, memory_dir):
    order = []

    def fake_cloud_pull(db_obj):
        order.append(('pull', db_obj.db_path))
        return 2

    def fake_parse_memory_file(_path):
        order.append(('parse', None))
        return []

    with patch('cloud.daemon.cloud_pull_once', side_effect=fake_cloud_pull):
        with patch('migrate.parse_memory_file', side_effect=fake_parse_memory_file):
            with patch('sync._run_decay', return_value=0):
                with patch('sync._maybe_dream', return_value=0):
                    from sync import sync

                    result = sync(memory_dir=memory_dir, db=db)

    assert order[0][0] == 'pull'
    assert order[1][0] == 'parse'
    assert result['cloud_pulled'] == 2


def test_sync_dry_run_skips_cloud_pull(db, memory_dir):
    with patch('cloud.daemon.cloud_pull_once') as mock_pull:
        with patch('sync._run_decay', return_value=0):
            with patch('sync._maybe_dream', return_value=0):
                from sync import sync

                result = sync(memory_dir=memory_dir, dry_run=True, db=db)

    mock_pull.assert_not_called()
    assert result['cloud_pulled'] == 0
