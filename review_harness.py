"""Daily Codex review packet, cached bodies, validated draft and at-most-once delivery."""
import argparse
import hashlib
import html
import json
import re
from collections import Counter
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import daily_schedule as d
import news
from content_assessment import fingerprint, fetch_body

ROOT = Path(__file__).resolve().parent
HOME = ROOT / 'data' / 'codex-review'
MAX_BODIES = 20


def luna_records(home=None, pending_only=False):
    path = (home or HOME)/'luna.sqlite'
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        query = 'SELECT key,body FROM luna_reviews'
        if pending_only:
            query += ' WHERE report_day IS NULL'
        return {key:json.loads(body) for key,body in conn.execute(query)}


def mark_luna_reported(snapshot, day):
    path = HOME/'luna.sqlite'
    if not path.exists():
        return
    # Only results actually included in this report snapshot leave the pending queue.
    rows = [(day,fingerprint(a),a['luna']['at']) for a in snapshot.values() if a.get('luna')]
    with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
        conn.executemany('UPDATE luna_reviews SET report_day=? WHERE key=? AND reviewed_at=? AND report_day IS NULL', rows)


def attach_web_reviews(articles, home=None):
    """Join completed daily reviews by content fingerprint; never mark all titles as body-reviewed."""
    home = home or HOME
    titles, selected = {}, {}
    folders = []
    for path in home.glob('*/draft.json'):
        packet = read(path.parent/'packet.json', {})
        if packet.get('version', 0) >= 2 and not packet.get('backlog'):
            folders.append((packet.get('prepared_at', ''), path.parent, packet))
    for _, folder, packet in sorted(folders):
        snapshot = read(folder/'snapshot.json', {})
        review = read(folder/'draft.json', {}).get('review', {})
        if set(review.get('reviewed_source_ids', [])) != set(snapshot):
            continue
        if set(review.get('reviewed_ids', [])) != {a['id'] for a in packet['articles']}:
            continue
        for a in snapshot.values():
            titles[fingerprint(a)] = {'level': '제목 검토 완료', 'basis': '전체 제목 검토 · 본문 중요도 미판정'}
        for item in review.get('items', []):
            if item.get('id') not in snapshot or item.get('level') not in ('필수 확인', '중요', '참고'):
                continue
            a = snapshot[item['id']]
            key = fingerprint(a)
            cached = read(home/'bodies'/(key+'.json'), {})
            evidence = item.get('evidence', '')
            if not cached or not evidence or evidence not in a['title']+'\n'+cached.get('body', ''):
                continue
            if item['level'] == '필수 확인' and cached.get('coverage') != '본문 추출':
                continue
            from content_assessment import LEVELS
            selected[key] = {'score': LEVELS[item['level']], 'level': item['level'],
                             'basis': '최종 검토 · '+cached['coverage'],
                             'reasons': [item['reason_ko']], 'high': item['level'] != '참고'}
    luna_cache = luna_records(home)
    for a in articles:
        a.pop('web_review', None)
        key = fingerprint(a)
        a['luna'] = luna_cache.get(key)
        if key in selected or key in titles:
            a['web_review'] = selected.get(key, titles.get(key))
        if key not in selected and a['luna']:
            summary = a['luna'].get('summary')
            a['web_review'] = {'level':'Luna 2차 검토 완료',
                              'basis':(summary['coverage'] if summary else '제목·RSS 요약')+' · 최종 중요도 미판정'}
    return articles


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def screening(article):
    from briefing import RULES, COMMENTARY
    head = dict(article, summary='')
    title_links = news.entities(head)[0]
    links = news.entities(article)[0]
    text = article['title'] + ' ' + article.get('summary', '')[:600]
    events = [name for name, _, pattern in RULES if re.search(pattern, text, re.I)]
    if re.search(r'\b(?:launch|launches|permit|approval|lawsuit|data center|investment)\b', text, re.I):
        events.append('사업·투자·규제')
    macro = bool(news.macro_events(article))
    related = bool(links) or macro
    commentary = bool(re.search(COMMENTARY, article['title'], re.I))
    # These rules route reading effort only; they never determine final importance.
    rank = (3 if related and events and not commentary else 2 if related and events else 1 if related else 0)
    if title_links or macro:
        rank += 1
    official = bool(article.get('official_source')) or bool(re.search(r'BUSINESS WIRE|GLOBE NEWSWIRE|PRNewswire', text))
    return {'rank':rank, 'events':events, 'symbols':[x['symbol'] for x in links],
            'relation':'제목 직접 언급' if title_links else '거시지표' if macro else '본문 언급' if links else '직접 관련 미확인',
            'official':official, 'commentary':commentary}


def group_candidates(candidates):
    from briefing import same_story
    groups=[]
    # ponytail: conservative headline grouping; Codex must check whether reports describe the same event.
    for a in candidates:
        route=screening(a)
        enriched=dict(a, links=[{'symbol':s} for s in route['symbols']], importance={'reasons':route['events']})
        numbers=set(re.findall(r'\d+(?:\.\d+)?',a['title']))
        match=None
        for group in reversed(groups):
            lead=group['article']
            if abs((news.timestamp(a['published_at'])-news.timestamp(lead['published_at'])).total_seconds())>86400:
                continue
            exact=a['title'].strip().casefold()==lead['title'].strip().casefold() and a.get('summary','')==lead.get('summary','')
            # Keep different reported numbers and opposite developments separate.
            polarity=lambda t:set(re.findall(r'\b(?:raises?|cuts?|wins?|loses?|approved|rejected|blocked)\b',t.lower()))
            similar=(route['events'] and route['symbols'] and route['events']==group['route']['events']
                     and numbers==group['numbers'] and polarity(a['title'])==polarity(lead['title'])
                     and same_story(enriched,group['enriched']))
            if exact or similar:
                match=group;break
        if match:
            match['members'].append(a)
            quality=lambda item,r:(r['official'],len(item.get('summary','')),item['published_at'])
            if quality(a,route)>quality(match['article'],match['route']):
                match.update(article=a,route=route,enriched=enriched,numbers=numbers)
        else:
            groups.append({'article':a,'route':route,'enriched':enriched,'numbers':numbers,'members':[a]})
    return groups


def prepare(folder, now, hours=None, *, exclude_keys=(), article_limit=None):
    from openrouter_worker import enabled
    single_stage = enabled() and article_limit is None
    if single_stage:
        hours = 24
    if hours is not None and not 1 <= hours <= 72:
        raise ValueError('Review window must be 1-72 hours')
    if (folder / 'packet.json').exists():
        previous=read(folder/'packet.json')
        if previous.get('backlog'):
            raise ValueError('Incomplete legacy packet: prepare a new folder for full title review')
        if hours is not None and news.timestamp(previous['prepared_at'])-news.timestamp(previous['window_start'])!=timedelta(hours=hours):
            raise ValueError('Existing packet has a different window; prepare a new folder')
        print('Reuse packet:', folder / 'packet.json')
        return
    articles, meta = news.load_saved(ROOT / 'data/news.sqlite')
    seen = read(HOME / 'reviewed.json', {})
    checkpoint=read(HOME/'checkpoint.json',{})
    start=max(now-timedelta(hours=72),news.timestamp(checkpoint['through'])-timedelta(hours=2)) if checkpoint else now-timedelta(hours=24)
    if hours is not None:
        start=now-timedelta(hours=hours)
    known=read(HOME/'observed.json',{})
    waiting_report = luna_records(pending_only=True) if article_limit is None and not single_stage else {}
    candidates=[]
    for a in articles:
        stamp=news.timestamp(a['published_at']);key=fingerprint(a)
        late=bool(checkpoint) and key not in known and now-timedelta(hours=72)<=stamp<=now
        if key in waiting_report or ((hours is not None or key not in seen) and (start<=stamp<=now or (hours is None and late))):
            candidates.append(a)
        known[key]=now.date().isoformat()
    write(HOME/'observed.json',{k:v for k,v in known.items() if v>=(now.date()-timedelta(days=14)).isoformat()})
    candidates = [a for a in candidates if fingerprint(a) not in exclude_keys]
    queued = len(candidates)
    if article_limit is not None:
        candidates = sorted(candidates, key=lambda a:a['published_at'])[:article_limit]
    groups=group_candidates(sorted(candidates,key=lambda a:a['published_at'],reverse=True))
    groups.sort(key=lambda g:(g['route']['rank'],g['route']['official'],g['article']['published_at']),reverse=True)
    recommendations=[]; company_counts=Counter()
    for g in groups:
        symbols=g['route']['symbols']
        if g['route']['rank']<2 or any(company_counts[s]>=3 for s in symbols):
            continue
        recommendations.append(g['article']['id']);company_counts.update(symbols)
        if len(recommendations)==MAX_BODIES-2:break
    remaining=[g for g in groups if g['article']['id'] not in recommendations]
    # Two rotating lower-priority samples test the routing rules for blind spots.
    remaining.sort(key=lambda g:hashlib.sha256((now.date().isoformat()+g['article']['id']).encode()).hexdigest())
    sample=[g['article']['id'] for g in remaining[:2]]
    all_snapshot={a['id']:a for g in groups for a in g['members']}
    from first_pass import attach
    attach(list(all_snapshot.values()))
    luna_cache = luna_records()
    for a in all_snapshot.values():
        a['luna'] = luna_cache.get(fingerprint(a))
    write(folder/'snapshot.json',all_snapshot)
    packet={'version':4 if single_stage else 3,'date':now.date().isoformat(),'prepared_at':now.isoformat(),'window_start':start.isoformat(), 'queued_after_batch':queued-len(candidates),
            'last_collected':meta.get('last_collected'),'collection_errors':meta.get('errors',[]),'backlog':0,
            'source_count':len(candidates),'group_count':len(groups),'body_limit':MAX_BODIES,
            'recommended_ids':recommendations,'sample_ids':sample,
            'articles':[{'id':g['article']['id'],'title':g['article']['title'],'summary':g['article'].get('summary','')[:160],
             'published_at':g['article']['published_at'],'symbols':g['route']['symbols'],'route':g['route'],
             'member_ids':[a['id'] for a in g['members']]} for g in groups]}
    write(folder/'packet.json',packet)
    write(folder/'luna-input.json',{'articles':[{'id':a['id'],'title':a['title'],
          'summary':a.get('summary','')[:1600],'published_at':a['published_at'],'qwen':a['qwen'],'luna':a['luna']}
          for a in all_snapshot.values()]})
    print(f'{len(candidates)} articles -> {len(groups)} candidate events; body recommendations {len(recommendations)} + samples {len(sample)}; {folder / "packet.json"}')


def bodies(folder, ids):
    snapshot = read(folder / 'snapshot.json')
    if not snapshot or any(i not in snapshot for i in ids):
        raise ValueError('Unknown article ID')
    attempts = read(folder / 'body-attempts.json', [])
    if len(set(attempts) | set(ids)) > MAX_BODIES:
        raise ValueError('Daily body budget exceeded')
    for i in dict.fromkeys(ids):
        a = snapshot[i]
        path = HOME / 'bodies' / (fingerprint(a)+'.json')
        cached = read(path)
        if cached is None:
            if i in attempts:
                raise ValueError('Previous fetch interrupted; do not retry automatically')
            attempts.append(i)
            write(folder / 'body-attempts.json',attempts)
            try:
                body,url,truncated=fetch_body(a['url'])
                cached={'id':i,'coverage':'본문 발췌' if truncated else '본문 추출','body':body,'url':url}
            except Exception as exc:
                cached={'id':i,'coverage':'제목·요약만 확인','body':a.get('summary',''), 'url':a['url'],'error':type(exc).__name__}
            write(path,cached)
        elif i not in attempts:
            attempts.append(i)
            write(folder / 'body-attempts.json',attempts)
        print(json.dumps(cached,ensure_ascii=False))


def agenda(folder, now):
    if (folder / 'agenda.json').exists():
        print('Reuse agenda:',folder / 'agenda.json')
        return
    events,good,errors=d.collect(now.astimezone(d.ET).date())
    text,selected=d.render(events,now.date(),errors,start_time=d.time(19))
    write(folder / 'agenda.json',{'version':2,'events':events,'session_day':now.date().isoformat(),
          'start_time':'19:00','html':text,'ok':good,'errors':errors,'at':now.isoformat()})
    print(text)


def validated_agenda(ag):
    if ag.get('version') == 2:
        rendered, _ = d.render(ag['events'], d.date.fromisoformat(ag['session_day']),
                               ag['errors'], start_time=d.time.fromisoformat(ag['start_time']))
        if ag['html'] != rendered:
            raise ValueError('일정 원본과 메시지가 다릅니다: events를 보완한 뒤 render로 재생성하세요')
    return ag['html']


def luna_filter(folder):
    """Validate Luna's complete second pass; no article is deleted by this shortlist."""
    snapshot=read(folder/'snapshot.json')
    result=read(folder/'luna-filter.json',{})
    rows=result.get('items',[])
    ids=[r.get('id') for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(snapshot):
        raise ValueError('Luna must classify every source ID')
    for r in rows:
        if r.get('decision') not in ('candidate','routine','uncertain'):
            raise ValueError('Invalid Luna decision')
        if not isinstance(r.get('reason'),str) or not 1<=len(r['reason'])<=300:
            raise ValueError('Luna reason required')
        a=snapshot[r['id']]; source=a['title']+'\n'+a.get('summary','')[:1600]
        quote=r.get('evidence')
        if not isinstance(quote,str) or not 1<=len(quote)<=400 or quote not in source:
            raise ValueError('Luna evidence must quote title/RSS summary')
    selected=result.get('selected_ids',[])
    packet=read(folder/'packet.json')
    limit=packet.get('body_limit',20)-2
    if not 0<=limit<=18:
        raise ValueError('Invalid body review budget')
    eligible={r['id'] for r in rows if r['decision']!='routine'}
    if not isinstance(selected,list) or len(selected)>limit or len(selected)!=len(set(selected)) or not set(selected)<=eligible:
        raise ValueError(f'Select up to {limit} candidate/uncertain IDs')
    remaining=[i for i in snapshot if i not in selected]
    remaining.sort(key=lambda i:hashlib.sha256((packet['date']+i).encode()).hexdigest())
    return selected+remaining[:2]


def validate_luna_summaries(folder):
    expected=luna_filter(folder)
    result=read(folder/'luna-summaries.json',{})
    rows=result.get('items',[]); ids=[r.get('id') for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected):
        raise ValueError('Luna summaries must cover shortlisted IDs and samples')
    snapshot=read(folder/'snapshot.json')
    for r in rows:
        a=snapshot[r['id']]; cached=read(HOME/'bodies'/(fingerprint(a)+'.json'))
        if not cached or r.get('coverage')!=cached['coverage']:
            raise ValueError('Luna coverage mismatch')
        summary=r.get('summary_en'); quote=r.get('evidence')
        if not isinstance(summary,str) or not 1<=len(summary)<=280 or '\n' in summary:
            raise ValueError('Invalid Luna summary')
        if not isinstance(quote,str) or not 1<=len(quote)<=500 or quote not in a['title']+'\n'+cached['body']:
            raise ValueError('Luna summary evidence mismatch')
    return expected


def draft(folder, review):
    packet,snapshot=read(folder/'packet.json'),read(folder/'snapshot.json')
    if packet and packet.get('backlog'):
        raise ValueError('Incomplete legacy packet: review all candidate titles before drafting')
    if packet and packet.get('version',0)==3:
        validate_luna_summaries(folder)
    if not packet or not snapshot and packet['articles']:
        raise ValueError('Missing packet')
    reviewed=review.get('reviewed_ids')
    if not isinstance(reviewed,list) or len(reviewed)!=len(set(reviewed)) or set(reviewed)!={a['id'] for a in packet['articles']}:
        raise ValueError('Explicitly review every packet ID; unreviewed backlog remains pending')
    sources=review.get('reviewed_source_ids')
    if not isinstance(sources,list) or len(sources)!=len(set(sources)) or set(sources)!=set(snapshot):
        raise ValueError('Review every source title, including grouped members')
    items=review.get('items')
    if not isinstance(items,list) or len(items)>8:
        raise ValueError('Select at most 8 news items')
    selected=set()
    selected_groups=set()
    group_ids={i:g['id'] for g in packet['articles'] for i in g.get('member_ids',[g['id']])}
    attempts=read(folder/'body-attempts.json',[])
    required=(luna_filter(folder) if packet.get('version',0)==3 else packet.get('sample_ids',[]))
    if packet.get('version',0)>=2 and not set(required)<=set(attempts):
        raise ValueError('Inspect lower-priority samples before drafting')
    lines=[f'<b>데일리 뉴스 · {packet["date"]} KST</b>','']
    for item in items:
        i=item['id']
        if i not in snapshot or i in selected or item['level'] not in ('필수 확인','중요','참고'):
            raise ValueError('Invalid selection')
        group=group_ids[i]
        if group in selected_groups:
            raise ValueError('Select only one report per candidate event group')
        selected_groups.add(group)
        selected.add(i)
        for key,limit in [('summary_en',280),('reason_ko',200)]:
            if not isinstance(item.get(key),str) or not item[key].strip() or len(item[key])>limit or '\n' in item[key]:
                raise ValueError('Invalid summary or reason')
        a=snapshot[i]
        cached=read(HOME/'bodies'/(fingerprint(a)+'.json'))
        if cached is None:
            raise ValueError('Selected article needs body attempt or cached source')
        evidence=item.get('evidence')
        source=a['title']+'\n'+cached['body']
        if not isinstance(evidence,str) or not 20<=len(evidence)<=500 or evidence not in source:
            raise ValueError('Selection requires an exact source quote')
        if item['level']=='필수 확인' and cached['coverage']!='본문 추출':
            raise ValueError('Highest priority requires body')
        url=a['url']
        if d.urlsplit(url).scheme not in ('http','https') or d.urlsplit(url).username:
            raise ValueError('Invalid source URL')
        links=news.entities(a)[0] or news.entities(dict(a,summary=cached['body'][:6000]))[0]
        symbols=', '.join(x['symbol'] for x in links) or '시장·지표'
        lines += [f'<a href="{html.escape(url,quote=True)}">[{html.escape(symbols)}] {html.escape(item["summary_en"])}</a>',
                  html.escape(f'{item["level"]} · {item["reason_ko"]} · {cached["coverage"]}'),'']
    if not items:
        lines.append('검토 범위에서 새로 전달할 중요 뉴스가 없습니다.')
    last=packet['last_collected']
    if not last or news.timestamp(packet['prepared_at'])-news.timestamp(last)>timedelta(hours=2):
        lines.append('⚠ 저장 뉴스가 최신이 아닐 수 있습니다.')
    ag=read(folder/'agenda.json')
    if not ag:
        raise ValueError('Refresh agenda before drafting')
    agenda_text=validated_agenda(ag)
    messages={'news':'\n'.join(lines),'agenda':agenda_text}
    for message in messages.values():
        if len(d.document('<div>'+message+'</div>').text_content().encode('utf-16-le'))//2>3900:
            raise ValueError('One message exceeds length limit')
    digest=hashlib.sha256(json.dumps(messages,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    result={'messages':messages,'review':review,'digest':digest}
    previous=read(folder/'draft.json')
    if (folder/'dispatch.json').exists() and previous!=result:
        raise ValueError('Dispatch started; draft is immutable')
    write(folder/'draft.json',result)
    mark_luna_reported(snapshot, packet['date'])
    for kind,message in messages.items():
        print(kind, d.document('<div>'+message+'</div>').text_content())


def send(folder,now):
    if d.telegram_paused():
        print('오늘 Telegram 발송은 사용자 요청으로 생략합니다.')
        return
    if not 19<=now.hour<23 or folder.name!=now.date().isoformat():
        raise ValueError('Send only same day 19:00-23:00 KST')
    ready=read(folder/'draft.json')
    if not ready:
        raise ValueError('Missing validated draft')
    draft(folder,ready['review'])
    ready=read(folder/'draft.json')
    token,chats=d.destinations()
    prior=read(folder/'dispatch.json')
    dispatch={'digest':ready['digest'],'recipients':chats}
    if prior and prior!=dispatch:
        raise ValueError('Dispatch settings changed')
    write(folder/'dispatch.json',dispatch)
    # Separate ledgers allow a partial batch to resume without resending its first part.
    for index,chat in enumerate(chats):
        name='agenda-delivery' if index==0 else 'agenda-delivery-'+hashlib.sha256(chat.encode()).hexdigest()
        legacy=d.DATA/(name+'.sqlite')
        if legacy.exists():
            with closing(sqlite3.connect(legacy)) as conn:
                old=conn.execute('SELECT status FROM delivery WHERE day=?',(now.date().isoformat(),)).fetchone()
            if old:
                raise ValueError('Legacy combined delivery already attempted; inspect before sending split messages')
        for kind,message in ready['messages'].items():
            if kind == 'news' and now.weekday() >= 5:
                continue
            path=d.DATA/(name+'-'+kind+'.sqlite')
            digest=hashlib.sha256(message.encode()).hexdigest()
            if path.exists():
                with closing(sqlite3.connect(path)) as conn:
                    row=conn.execute('SELECT status,digest FROM delivery WHERE day=?',(now.date().isoformat(),)).fetchone()
                if row:
                    if row==('sent',digest):
                        continue
                    raise ValueError('Part already sent differently or delivery uncertain; inspect manually')
            if not d.send_once(message,now.date(),token,chat,path):
                raise ValueError('Delivery not confirmed')
    seen=read(HOME/'reviewed.json',{})
    snapshot=read(folder/'snapshot.json')
    packet=read(folder/'packet.json')
    reviewed=set(ready['review']['reviewed_ids'])
    for group in packet['articles']:
        if group['id'] in reviewed:
            for i in group.get('member_ids',[group['id']]):
                seen[fingerprint(snapshot[i])]=now.date().isoformat()
    write(HOME/'checkpoint.json',{'through':packet['prepared_at']})
    seen={k:v for k,v in seen.items() if v>=(now.date()-timedelta(days=14)).isoformat()}
    write(HOME/'reviewed.json',seen)
    write(folder/'done.json',{'at':now.isoformat(),'recipients':len(chats)})
    print('All recipients delivered; reviewed records saved.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','bodies','agenda','luna-filter','luna-summaries','draft','send','status'])
    p.add_argument('--ids',nargs='+',default=[])
    p.add_argument('--review',type=Path)
    p.add_argument('--hours',type=int,help='Explicit catch-up window (1-72 hours), including previously reviewed titles')
    a=p.parse_args(); now=d.datetime.now(d.KST); folder=HOME/now.date().isoformat()
    if a.command=='prepare':prepare(folder,now,a.hours)
    elif a.command=='luna-filter':
        ids=luna_filter(folder)
        write(folder/'summary-request.json',{'ids':ids})
        print(json.dumps({'ids':ids}))
    elif a.command=='luna-summaries':
        print('Validated summaries:',len(validate_luna_summaries(folder)))
    elif a.command=='bodies':bodies(folder,a.ids)
    elif a.command=='agenda':agenda(folder,now)
    elif a.command=='draft':draft(folder,read(a.review) if a.review else {})
    elif a.command=='send':send(folder,now)
    else: print(json.dumps({'folder':str(folder),'done':read(folder/'done.json'),'draft_ready':(folder/'draft.json').exists()},ensure_ascii=False))

if __name__=='__main__':
    main()
