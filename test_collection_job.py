"""Offline collection-to-publication checks; no RSS, model or upload calls."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import collection_job as job

with tempfile.TemporaryDirectory() as tmp, patch.object(job, 'ROOT', Path(tmp)):
    (Path(tmp)/'data').mkdir()
    for code in (None, 0, 2, 1):
        events = []
        def collect(args):
            assert args.tickers == 'CSQ_UNIV' and args.macro and args.engine == 'finbert'
            events.append('saved')
            return code
        with patch.object(job.app, 'collect', side_effect=collect), patch.object(job, 'publish_saved', side_effect=lambda:events.append('published')), contextlib.redirect_stdout(io.StringIO()):
            assert job.execute() == (code or 0)
        assert events == (['saved', 'published'] if code in (None, 0, 2) else ['saved'])
    with patch.object(job.app, 'collect', return_value=0) as collect, patch.object(job, 'publish_saved') as publish, contextlib.redirect_stdout(io.StringIO()):
        assert job.execute(dry_run=True) == 0
        assert collect.call_args.args[0].dry_run
        publish.assert_not_called()
        collect.reset_mock()
        assert job.execute(publish_only=True) == 0
        collect.assert_not_called()
        publish.assert_called_once()
    with patch.object(job.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as launch:
        try:
            job.publish_saved()
            raise AssertionError('Publication failure silently accepted')
        except RuntimeError:
            pass
        assert launch.call_args.kwargs['timeout'] == 300
        assert launch.call_args.args[0][-1] == '--upload'
    if os.name == 'nt':
        import msvcrt
        log = Path(tmp)/'data/auto-collect.out.log'
        log.write_text('existing collection log', encoding='utf-8')
        with patch.object(sys, 'argv', ['collection_job.py', '--dry-run']), patch.object(job, 'execute', return_value=0) as execute:
            with (Path(tmp)/'data/auto-collect.lock').open('a+b') as lock:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                assert job.main() == 3
                execute.assert_not_called()
                assert log.read_text(encoding='utf-8') == 'existing collection log'
            assert job.main() == 0
            execute.assert_called_once_with(dry_run=True, publish_only=False)
print('Save-before-publish, partial-feed success, failure propagation, recovery-only and worker lock passed.')
