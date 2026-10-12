"""Run the script-style regression suite in a temporary, synthetic checkout.

No external sockets, subprocesses from tests, model downloads or real data.
Localhost sockets are allowed for the authentication HTTP integration test.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def child(test):
    import ipaddress
    import runpy
    import socket

    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def local(address):
        host = address[0]
        if host == 'localhost':
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise RuntimeError('External network disabled in offline tests')

    def guarded_connect(sock, address):
        local(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        local(address)
        return connect_ex(sock, address)

    def guarded_resolve(host, port, *args, **kwargs):
        local((host, port))
        return getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_resolve

    def audit(event, args):
        if event in ('subprocess.Popen', 'os.system', 'os.startfile'):
            raise RuntimeError('Child processes disabled in offline tests')
        if event in ('socket.connect', 'socket.sendto'):
            local(args[1])
    sys.addaudithook(audit)
    sys.path.insert(0, str(Path.cwd()))
    if test != 'test_config_safety.py':
        # Synthetic company directory for named-company regression fixtures.
        # No production universe or downloaded listing cache is copied.
        import universe
        known = next(n.value for n in ast.parse(Path('news.py').read_text(encoding='utf-8')).body
                     if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'UNIVERSE' for t in n.targets))
        names = {k: v[0] for k, v in ast.literal_eval(known).items()}
        names.update({'CAT': 'Caterpillar', 'CRM': 'Salesforce', 'GE': 'GE Aerospace',
                      'GEV': 'GE Vernova', 'CEG': 'Constellation Energy', 'SHEL': 'Shell',
                      'MSI': 'Motorola Solutions', 'ADBE': 'Adobe', 'ORCL': 'Oracle',
                      'DASH': 'DoorDash, Inc.', 'GRAL': 'GRAIL, Inc.', 'ARM': 'Arm Holdings plc',
                      'PGR': 'Progressive Corporation', 'BPOP': 'Popular Inc.',
                      'TPR': 'Tapestry Inc.', 'HOOD': 'Robinhood Markets Inc.'})
        universe.csq_symbols = lambda path=None: list(names)
        universe.listing_names = lambda directory=None: names
    sys.argv = [test]
    runpy.run_path(test, run_name='__main__')


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        child(sys.argv[2])
        return
    tests = sorted(p.name for p in ROOT.glob('test_*.py'))
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET', 'NEWS_', 'QUANTBOT_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    failures = []
    with tempfile.TemporaryDirectory(prefix='news-alarm-offline-') as tmp:
        checkout = Path(tmp) / 'source'
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(
            '.git', '.venv', 'venv', '__pycache__', 'node_modules', 'data', 'models',
            'output', 'tmp', '.env', '.dev.vars', '.openai', '.wrangler', '.vinext', 'dist'))
        for test in tests:
            result = subprocess.run([sys.executable, '-B', str(checkout/'tools/run_offline_tests.py'), '--child', test],
                                    cwd=checkout, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
            print(('PASS ' if result.returncode == 0 else 'FAIL ') + test, flush=True)
            if result.returncode:
                failures.append(test)
                print(result.stdout + result.stderr, flush=True)
        print(json.dumps({'scripts': len(tests), 'passed': len(tests)-len(failures), 'failed': failures}))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
