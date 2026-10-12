"""One scheduled process owns collection and publication; Qwen runs independently."""
import argparse
import os
import subprocess
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr

import app
from news import ROOT, utcnow


def publish_saved():
    with (ROOT/'data/site-upload.out.log').open('w', encoding='utf-8') as out, \
         (ROOT/'data/site-upload.err.log').open('w', encoding='utf-8') as err:
        result = subprocess.run([sys.executable, '-u', str(ROOT/'publish_news.py'), '--upload'],
                                cwd=ROOT, stdout=out, stderr=err, timeout=300)
    if result.returncode:
        raise RuntimeError('Publication failed; saved news is retained. See site-upload.err.log.')


def execute(*, dry_run=False, publish_only=False):
    result = 0
    if not publish_only:
        print('Collection started:', utcnow().isoformat(), flush=True)
        result = app.collect(argparse.Namespace(tickers='CSQ_UNIV', macro=True, macro_only=False,
                                               limit=20, engine='finbert', dry_run=dry_run)) or 0
    if result not in (0, 2) or dry_run:
        return result
    print('Publishing saved news:', utcnow().isoformat(), flush=True)
    publish_saved()
    print('Collection/publication complete:', utcnow().isoformat(), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--publish-only', action='store_true', help='Recover publication without collecting or running models')
    args = parser.parse_args()
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        os.environ[name] = '1'
    data = ROOT/'data'
    data.mkdir(parents=True, exist_ok=True)
    import msvcrt
    try:
        lock = (data/'auto-collect.lock').open('a+b')
    except OSError:
        return 3
    with lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 3
        # Acquire the worker lock before truncating logs; competing launches leave them intact.
        prefix = 'publish-recovery' if args.publish_only else 'auto-collect'
        with (data/(prefix+'.out.log')).open('w', encoding='utf-8', buffering=1) as out, \
             (data/(prefix+'.err.log')).open('w', encoding='utf-8', buffering=1) as err, \
             redirect_stdout(out), redirect_stderr(err):
            try:
                return execute(dry_run=args.dry_run, publish_only=args.publish_only)
            except Exception:
                traceback.print_exc()
                return 1


if __name__ == '__main__':
    sys.exit(main())
