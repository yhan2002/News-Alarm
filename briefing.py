"""Explainable event importance, independent of sentiment. No network or model calls."""
import html
import re
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import urlsplit

# ponytail: headline rules cannot understand every event; show reasons and retain every article on the web.
RULES = [
    ('인수·합병', 90, r'\b(?:acquires?|acquisition|merger|buyout|takeover|to buy)\b'),
    ('규제·임상', 85, r'\b(?:fda|phase [23]|phase iii?|clinical trial|antitrust|sec probe|doj|recall)\b'),
    ('가이던스', 85, r'\b(?:raises?|cuts?|lowers?|lifts?|withdraws?|boosts?)\b.{0,35}\b(?:guidance|earnings outlook|revenue forecast|sales forecast|profit forecast)\b|\b(?:guidance|earnings outlook)\b.{0,40}\b(?:raised|cut|lowered|lifted|withdrawn)\b'),
    ('실적', 75, r'\b(?:reports?|reported|posts|posted|announces?|announced|beats?|misses?)\b.{0,65}\b(?:earnings|quarterly results|financial results|eps|profit|revenue)\b|\b(?:earnings|eps|revenue|profit)\b.{0,35}\b(?:beats?|misses?|rose|fell|surged|jumped|grew|declined)\b'),
    ('대형 계약', 75, r'\b(?:wins?|secures?|signs?)\b.{0,60}\b(?:contract|deal|agreement)\b'),
    ('경영진 변경', 75, r'\b(?:ceo|cfo|chief executive|chief financial)\b.{0,60}\b(?:resigns?|steps down|retires?|appointed|replaced|departure)\b|\b(?:appoints?|names?)\b.{0,50}\b(?:ceo|cfo)\b'),
    ('자본·구조 변화', 80, r'\b(?:bankruptcy|chapter 11|stock split|share repurchase|buyback|spin-off|spinoff|delisting)\b'),
    ('정책·지표', 75, r'\b(?:cpi|ppi|fomc|nonfarm payrolls|jobs report|rate decision|export ban)\b'),
]
COMMENTARY = r'\b(?:should you|is .{0,45} a buy|(?:stocks?|etfs?) to buy|buy,? sell,? or hold|(?:to|will) report|price target|earnings preview|earnings estimate|expected to|what to expect|analysts?|which one|could|may|might)\b'


def importance(article):
    reviewed = article.get('web_review', {})
    if 'score' in reviewed:
        return dict(reviewed)
    assessment = article.get('assessment')
    if assessment and assessment.get('status') == 'assessed':
        from content_assessment import LEVELS
        level = assessment['level']
        return {'score': LEVELS[level], 'reasons': [assessment['reason_ko']],
                'high': LEVELS[level] >= 70, 'level': level, 'basis': assessment['coverage']}
    automatic = article.get('qwen', {})
    if automatic.get('status') == 'done' and automatic.get('facts'):
        from first_pass import current
        from content_assessment import classify, LEVELS, EVENTS
        if current(automatic):
            facts = automatic['facts']
            result = classify(facts, automatic['coverage'])
            level = result['level']
            if facts.get('impact', {}).get('version') == 1:
                return {'score': result['score'], 'reasons': [EVENTS[facts['event']]],
                        'reason': result['reason_ko'],
                        'high': result['score'] >= 70, 'level': level,
                        'basis': automatic['coverage']+' · 잠재적 가격 영향 · 자동 판정',
                        'impact': facts['impact']}
            reasons = ['사건·신규성 추가 확인' if facts.get('review_required') else EVENTS[facts['event']]]
            if facts['certainty'] == 'reported':
                reasons.append('미확정 보도')
            return {'score': LEVELS[level], 'reasons': reasons, 'high': LEVELS[level] >= 70,
                    'level': level, 'basis': automatic['coverage']+' · 자동 판정'}
    title = article['title']
    reasons = [(name, score) for name, score, pattern in RULES if re.search(pattern, title, re.I)]
    score = max((score for _, score in reasons), default=0)
    if score and re.search(COMMENTARY, title, re.I):
        score = max(0, score - 30)
    if score and re.search(r'\b(?:reportedly|in talks|rumou?rs?|considering)\b', title, re.I):
        score = max(0, score - 15)
        reasons.append(('미확정 보도', 0))
    if score and article.get('official_source'):
        score = min(100, score + 5)
    return {'score': score, 'reasons': [name for name, _ in reasons], 'high': score >= 70,
            'level': reviewed.get('level', '본문 평가 대기'),
            'basis': reviewed.get('basis', '제목 규칙 · 임시 선별')}


def same_story(article, prior):
    def tokens(title):
        stop = set('the a an to for of in on and or is its as at by from with stock shares stocks report reported reportedly billion million firm ai'.split())
        return set(re.findall(r'[a-z]{2,}', title.lower())) - stop
    if article['url'] == prior['url']:
        return True
    if {link['symbol'] for link in article['links']} != {link['symbol'] for link in prior['links']}:
        return False
    title, other = (re.sub(r'\W+', ' ', a['title'].lower()).strip() for a in (article, prior))
    shared = len(tokens(title) & tokens(other))
    return SequenceMatcher(None, title, other).ratio() >= .72 or (
        article['importance']['reasons'] == prior['importance']['reasons'] and shared >= 4 and
        shared / max(1, min(len(tokens(title)), len(tokens(other)))) >= .6)


def transaction(article):
    # Extract only explicit acquisition sentences; ambiguous roles remain unassigned.
    title = article['title']
    match = re.search(r'(?:^|:\s)([^:]{2,100}?)\s+(?:to acquire|acquires?)\s+(.{2,120}?)\s+from\s+(.{2,80}?)(?:\s+for\s+(\$[\d,.]+\s*(?:billion|million|[BM])?))?[.!]?$', title, re.I)
    if not match:
        return None
    buyer, asset, seller, amount = match.groups()
    return {'buyer': buyer, 'asset': asset, 'seller': seller, 'amount': amount,
            'evidence': title, 'article_id': article['id']}


def group_issues(articles):
    """Group similar headlines within 24h, retaining every source and article ID."""
    from news import timestamp
    from collections import defaultdict, Counter
    buckets = defaultdict(list)
    for article in sorted(articles, key=lambda a: a['published_at']):
        key = (tuple(sorted(link['symbol'] for link in article['links'])), tuple(article['importance']['reasons']))
        bucket = buckets[key]
        if not article['importance']['high'] or not (article['links'] or article.get('macro_events')):
            bucket.append([article])
            continue
        # ponytail: headline similarity is a grouping hint, not proof of the same event.
        match = next((items for items in reversed(bucket)
                      if timestamp(article['published_at']) - timestamp(items[0]['published_at']) <= timedelta(hours=24)
                      and same_story(article, items[0])), None)
        if match is None:
            bucket.append([article])
        else:
            match.append(article)
    issues = []
    for bucket in buckets.values():
        for items in bucket:
            lead = max(items, key=lambda a: (a['importance']['score'], a['published_at']))
            counts = Counter(a['sentiment']['label'] for a in items)
            links = {}
            themes = {}
            for a in items:
                for link in a['links']:
                    links.setdefault(link['symbol'], {'symbol': link['symbol'], 'name': link['name'], 'evidence': set(), 'article_ids': []})
                    links[link['symbol']]['evidence'].update(link['evidence'])
                    links[link['symbol']]['article_ids'].append(a['id'])
                for name, words in a['themes'].items():
                    themes.setdefault(name, {'name': name, 'evidence': set(), 'article_ids': []})
                    themes[name]['evidence'].update(words)
                    themes[name]['article_ids'].append(a['id'])
            edges = [dict(v, evidence=sorted(v['evidence'])) for v in links.values()]
            theme_edges = [dict(v, evidence=sorted(v['evidence'])) for v in themes.values()]
            transactions = [t for a in items if (t := transaction(a))]
            issues.append({'id': items[0]['id'], 'title': lead['title'], 'summary': lead['summary'],
                           'transaction': next((t for t in transactions if t['amount']), transactions[0] if transactions else None),
                           'in_scope': bool(edges) or any(a.get('macro_events') for a in items),
                           'score': lead['importance']['score'], 'reasons': lead['importance']['reasons'],
                           'level': lead['importance'].get('level', '본문 평가 대기'),
                           'basis': lead['importance'].get('basis', '제목 규칙 · 임시 선별'),
                           'category': next((r for r in lead['importance']['reasons'] if r != '미확정 보도'), '일반 뉴스'),
                           'status': '미확정 보도' if any('미확정 보도' in a['importance']['reasons'] for a in items)
                           else '공식 출처 포함' if any(a.get('official_source') for a in items) else '언론 보도 · 확정 여부 미판정',
                           'article_ids': [a['id'] for a in items], 'first_at': items[0]['published_at'], 'last_at': items[-1]['published_at'],
                           'sources': sorted({a['source'] for a in items}), 'sentiments': dict(counts),
                           'links': edges, 'themes': theme_edges})
    return sorted(issues, key=lambda i: (i['score'], i['last_at']), reverse=True)


def select_news(articles, now, limit=10):
    from news import entities, macro_events, timestamp
    candidates = []
    for article in articles:
        if not now - timedelta(hours=24) <= timestamp(article['published_at']) <= now:
            continue
        links, _ = entities(article)
        score = importance(article)
        if not score['high'] or not (links or macro_events(article)):
            continue
        url = urlsplit(article['url'])
        if url.scheme not in ('http', 'https') or url.username or url.password:
            continue
        candidates.append({**article, 'links': links, 'importance': score})
    candidates.sort(key=lambda a: (a['importance']['score'], a['published_at']), reverse=True)
    selected = []
    for article in candidates:
        duplicate = any(same_story(article, prior) for prior in selected)
        if not duplicate:
            selected.append(article)
        if len(selected) >= limit:
            break
    return selected


def one_line(article):
    """Extract the event sentence from RSS; never invent facts or translate partially."""
    if article.get('assessment', {}).get('status') == 'assessed':
        return article['assessment'].get('summary_text') or article['assessment'].get('summary_ko') or article['title']
    title = re.split(r'\s+[—–]\s+.*\bIn Focus\b', article['title'], flags=re.I)[0]
    title = re.sub(r'^.*?\s+[—–]\s+(?=\w+ Is Reportedly)', '', title)
    summary = re.sub(r'^.{0,70},\s+[A-Z][a-z]+ \d{1,2}, \d{4}--', '', article.get('summary', ''))
    # Split only at sentence boundaries, retaining decimals and company abbreviations.
    sentences = re.split(r'(?<!Inc)(?<!U.S)(?<!Co\.)[.!?]\s+(?=[A-Z])', summary)
    reasons = set(importance(article)['reasons']) - {'미확정 보도'}
    candidate = next((s for s in sentences if reasons & set(importance({'title': s})['reasons'])), title)
    value = ' '.join(candidate.split()).strip()
    # ponytail: extractive English summary only; generative Korean needs a separate language model.
    if len(value) > 220:
        value = value[:217].rsplit(' ', 1)[0] + '…'
    return value


def compose(agenda, now, db=None):
    from news import ROOT, load_saved, timestamp
    from daily_schedule import document
    articles, metadata = load_saved(db or ROOT / 'data' / 'news.sqlite')
    chosen = select_news(articles, now)
    last = metadata.get('last_collected')
    header = f'데일리 뉴스 · {now:%m/%d %H:%M} KST\n'
    if not last or now - timestamp(last) > timedelta(hours=2):
        header += '⚠ 최신 수집이 없거나 2시간 이상 지났습니다.\n'
    if metadata.get('errors'):
        header += '⚠ 최근 뉴스 수집 일부 누락\n'
    blocks = []
    labels = {'positive': '긍정', 'negative': '부정', 'neutral': '중립'}
    for a in chosen:
        symbols = ', '.join(link['symbol'] for link in a['links']) or '거시지표'
        title = one_line(a)
        detail = html.escape('·'.join(a['importance']['reasons'][:2]))
        label = labels.get(a['sentiment']['label'], '미분류')
        blocks.append(f'<a href="{html.escape(a["url"], quote=True)}">[{html.escape(symbols)}] {html.escape(title)}</a>\n'
                      f'{a["importance"]["level"]} · {html.escape(a["importance"]["basis"])} · {label}\n{detail}')
    if not blocks:
        blocks = ['저장된 최근 24시간 기사 중 중요도 기준을 충족한 뉴스가 없습니다.']
    # One Telegram message; reserve room for the agenda and disclose omitted news.
    total = len(blocks)
    while True:
        omitted = f'\n뉴스 {total - len(blocks)}건은 메시지 길이 때문에 생략했습니다. 웹에서 확인하세요.' if len(blocks) < total else ''
        message = html.escape(header) + '\n' + '\n\n'.join(blocks) + omitted + '\n\n' + agenda
        if len(document('<div>' + message + '</div>').text_content().encode('utf-16-le')) // 2 <= 3900:
            return message
        if not blocks:
            raise ValueError('일정만으로 Telegram 길이 제한 초과')
        blocks.pop()
