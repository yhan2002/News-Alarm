"""Offline platform-boundary checks; no keys, external requests or worker writes."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

import article_sync
import collection_job
import first_pass
import openrouter_pilot as pilot
import reassess_impact


def stopped(call, message):
    try:
        call()
    except (SystemExit, ValueError) as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError('Expected unsupported-platform error')


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    key = root / 'openrouter-key.dpapi'
    with patch.object(pilot, 'KEY_FILE', key), patch.object(sys, 'platform', 'darwin'), \
         patch.dict(os.environ, {}, clear=True), \
         patch.object(pilot.subprocess, 'run', side_effect=AssertionError('No PowerShell')), \
         patch.object(pilot.getpass, 'getpass', return_value='unit-test-input') as prompt:
        stopped(pilot.save_key, 'requires Windows DPAPI')
        prompt.assert_not_called()
        assert not key.exists()
        assert pilot.load_key() == 'unit-test-input'
        key.write_text('encrypted-test-data', encoding='utf-8')
        prompt.reset_mock()
        stopped(pilot.load_key, 'cannot be opened on macOS')
        prompt.assert_not_called()
        assert key.read_text(encoding='utf-8') == 'encrypted-test-data'
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'unit-test-environment'}):
            assert pilot.load_key() == 'unit-test-environment'
        prompt.assert_not_called()

    work = root / 'untouched'
    with patch.dict(sys.modules, {'msvcrt': None}), \
         patch.object(collection_job, 'ROOT', work), \
         patch.object(first_pass, 'CACHE', work / 'first-pass.sqlite'), \
         patch.object(first_pass.ca, 'ROOT', work), \
         patch.object(article_sync.news, 'ROOT', work), \
         patch.object(first_pass.news, 'load_saved', return_value=([], {})), \
         contextlib.redirect_stdout(io.StringIO()):
        for args, call in [
            (['collection_job.py', '--dry-run'], collection_job.main),
            (['first_pass.py'], first_pass.main),
            (['article_sync.py'], lambda: article_sync.locked_sync({})),
            (['reassess_impact.py', '--run'], reassess_impact.main),
            (['openrouter_worker.py', '--activate'], lambda: runpy.run_path('openrouter_worker.py', run_name='__main__')),
        ]:
            with patch.object(sys, 'argv', args):
                stopped(call, 'requires Windows file locking')
            assert not work.exists(), args
print('PASS: Mac key handling and five Windows worker boundaries; no worker files created')
