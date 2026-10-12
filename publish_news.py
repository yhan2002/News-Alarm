"""Export the existing dashboard for hosted viewing; upload only with --upload."""
import argparse
import base64
import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
import news

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'data/site-upload.json'


def export():
    articles,meta=news.load_saved(ROOT/'data/news.sqlite')
    # Keep RSS snippets and assessment facts, never the cached full article body.
    for a in articles:
        if a.get('assessment'):
            a['assessment']={k:v for k,v in a['assessment'].items() if k not in ('body','input','response')}
    now=news.utcnow(); snapshots=[]
    for hours in (24,72,168,720,0):
        data=news.dashboard(articles,meta,hours,now,'saved')
        raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
        compressed=base64.b64encode(gzip.compress(raw)).decode()
        if len(compressed)>4_000_000:
            raise ValueError('Snapshot exceeds hosted limit; nothing uploaded')
        snapshots.append({'hours':hours,'body':compressed})
    return {'generated_at':now.isoformat(timespec='microseconds'),'snapshots':snapshots}


def upload(payload,config):
    p=urlsplit(config['site_url'])
    if p.scheme!='https' or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:
        raise ValueError('Expected HTTPS site origin')
    headers={'Content-Type':'application/json','User-Agent':'CSQNewsPublisher/1.0','Authorization':'Bearer '+config['upload_token']}
    req=Request(config['site_url'].rstrip('/')+'/news/api/upload',data=json.dumps(payload).encode(),headers=headers,method='POST')
    with build_opener(ProxyHandler({})).open(req,timeout=30) as response:
        result=json.load(response)
    if result.get('ok') is not True:raise RuntimeError('Upload unconfirmed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upload',action='store_true')
    args=parser.parse_args()
    config=json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
    if args.upload and not config.get('enabled'):
        print('Hosted publication disabled; no upload.')
    elif args.upload and config.get('article_sync_enabled'):
        from article_sync import locked_sync
        locked_sync(config)
    else:
        result=export()
        path=ROOT/'data/news-site-snapshot.json'
        path.write_text(json.dumps(result),encoding='utf-8')
        print('Snapshot prepared:',len(result['snapshots']),'periods;',path.stat().st_size,'bytes')
        if args.upload:upload(result,config);print('Hosted snapshot updated.')
