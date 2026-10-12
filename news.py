"""Stock and macro release feeds with local financial news analysis."""
import hashlib
import html
import json
import math
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from functools import lru_cache
from universe import expand_universe, universe_status

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / 'models' / 'finbert'
MODEL_ID = 'ProsusAI/finbert'
UNIVERSE = {
    'NVDA': ('NVIDIA', ['nvidia'], ['AI 반도체']),
    'AMD': ('AMD', ['advanced micro devices'], ['AI 반도체']),
    'AVGO': ('Broadcom', ['broadcom'], ['AI 반도체']),
    'TSM': ('TSMC', ['tsmc', 'taiwan semiconductor'], ['AI 반도체']),
    'MU': ('Micron', ['micron'], ['AI 반도체']),
    'MSFT': ('Microsoft', ['microsoft'], ['클라우드·AI']),
    'GOOGL': ('Alphabet', ['alphabet', 'google'], ['클라우드·AI', '디지털 광고']),
    'AMZN': ('Amazon', ['amazon', 'aws'], ['클라우드·AI', '소비·커머스']),
    'META': ('Meta', ['meta platforms', 'facebook', 'instagram'], ['디지털 광고']),
    'AAPL': ('Apple', ['apple', 'iphone'], ['소비·커머스']),
    'TSLA': ('Tesla', ['tesla'], ['전기차·에너지']),
    'XOM': ('Exxon Mobil', ['exxon', 'exxonmobil'], ['전기차·에너지']),
    'JPM': ('JPMorgan', ['jpmorgan', 'jp morgan'], ['금융·금리']),
    'BAC': ('Bank of America', ['bank of america'], ['금융·금리']),
    'LLY': ('Eli Lilly', ['eli lilly', 'lilly'], ['헬스케어']),
    'UNH': ('UnitedHealth', ['unitedhealth'], ['헬스케어']),
    'WMT': ('Walmart', ['walmart'], ['소비·커머스']),
    'UBER': ('Uber Technologies', ['uber technologies', 'uber eats'], ['소비·커머스']),
    'COIN': ('Coinbase', ['coinbase'], ['디지털 자산']),
}
UNIVERSE = expand_universe(UNIVERSE)
THEMES = {
    'AI 반도체': ['semiconductor', 'chip', 'chips', 'gpu', 'gpus', 'hbm', 'foundry'],
    '클라우드·AI': ['artificial intelligence', 'ai', 'cloud', 'data center', 'datacenter', 'copilot'],
    '디지털 광고': ['advertising', 'ad revenue', 'social media', 'digital ads'],
    '소비·커머스': ['consumer', 'retail', 'e-commerce', 'iphone', 'shopping'],
    '전기차·에너지': ['electric vehicle', 'ev', 'oil', 'crude', 'battery', 'energy'],
    '금융·금리': ['interest rate', 'federal reserve', 'fed', 'bank', 'banking', 'inflation'],
    '헬스케어': ['healthcare', 'drug', 'fda', 'clinical', 'obesity', 'glp-1'],
    '디지털 자산': ['bitcoin', 'crypto', 'cryptocurrency', 'stablecoin', 'token'],
    '정책·지정학': ['tariff', 'tariffs', 'export control', 'sanction', 'war', 'geopolitical'],
}
MACRO_EVENTS = {
    'CPI': ('CPI · 소비자물가', ['cpi', 'consumer price index', 'consumer prices']),
    'PPI': ('PPI · 생산자물가', ['ppi', 'producer price index', 'producer prices']),
    'FOMC': ('FOMC · 연준', ['fomc', 'federal open market committee', 'federal reserve', 'fed', 'federal funds rate', 'dot plot']),
    'EMPLOYMENT': ('고용 · 실업률 · 임금', ['employment situation', 'jobs report', 'nonfarm', 'non-farm', 'payrolls', 'unemployment rate', 'average hourly earnings']),
    'JOLTS': ('JOLTS · 구인', ['jolts', 'job openings', 'labor turnover']),
    'CLAIMS': ('실업수당 청구', ['jobless claims', 'unemployment claims', 'initial claims', 'continuing claims']),
    'PCE': ('PCE · 개인소비', ['pce', 'personal consumption expenditures', 'personal income and outlays']),
    'GDP': ('GDP · 경제성장', ['gdp', 'gross domestic product']),
    'PMI': ('PMI · ISM', ['pmi', 'ism manufacturing', 'ism services', 'purchasing managers']),
    'RETAIL': ('소매판매', ['retail sales']),
    'OIL': ('원유·에너지', ['oil prices', 'oil price', 'crude', 'brent', 'wti', 'opec', 'oil demand', 'oil supply']),
    'RATES': ('채권·시장금리', ['treasury yield', 'treasury yields', 'bond yield', 'bond yields', 'yield curve', 'real yields', 'real yield', '10-year treasury', '2-year treasury', '30-year treasury', 'treasuries']),
    'DOLLAR_STRENGTH': ('달러 상대강도', ['usd/jpy', 'usd-jpy', 'dollar/yen', 'dollar-yen', 'yen', 'dollar index', 'dxy', 'dollar strength', 'stronger dollar', 'weaker dollar', 'dollar demand', 'gold demand', 'gold prices', 'gold price', 'bullion', 'gold buying', 'gold purchases', 'gold reserves', 'gold etf']),
}
for _key in ('OIL', 'RATES', 'DOLLAR_STRENGTH'):
    _label, _words = MACRO_EVENTS[_key]
    THEMES[_label] = _words
# Fixed public feed addresses; the dashboard never accepts arbitrary fetch URLs.
MACRO_FEEDS = {
    'bls-cpi': ('BLS', 'https://www.bls.gov/feed/cpi.rss', 'CPI'),
    'bls-ppi': ('BLS', 'https://www.bls.gov/feed/ppi.rss', 'PPI'),
    'bls-employment': ('BLS', 'https://www.bls.gov/feed/empsit.rss', 'EMPLOYMENT'),
    'bls-jolts': ('BLS', 'https://www.bls.gov/feed/jolts.rss', 'JOLTS'),
    'fed-monetary': ('Federal Reserve', 'https://www.federalreserve.gov/feeds/press_monetary.xml', 'FOMC'),
    'yahoo-market': ('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?s=%5EGSPC&region=US&lang=en-US', ''),
    'yahoo-oil': ('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?s=CL%3DF&region=US&lang=en-US', ''),
    'yahoo-treasury': ('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?s=%5ETNX&region=US&lang=en-US', ''),
    'yahoo-usdjpy': ('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?s=JPY%3DX&region=US&lang=en-US', ''),
    'yahoo-dollar': ('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?s=DX-Y.NYB&region=US&lang=en-US', ''),
    'yahoo-gold': ('Yahoo Finance', 'https://finance.yahoo.com/rss/headline?s=GC%3DF&region=US&lang=en-US', ''),
}
POSITIVE = {'beat', 'beats', 'growth', 'grows', 'surge', 'surges', 'raised', 'raises', 'record', 'strong', 'upgrade', 'approval', 'profit', 'gain', 'gains'}
NEGATIVE = {'miss', 'misses', 'decline', 'declines', 'cut', 'cuts', 'weak', 'loss', 'losses', 'probe', 'downgrade', 'slump', 'falls', 'delay', 'risk', 'recall', 'lawsuit'}


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone is required')
    return result.astimezone(timezone.utc)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value or '')
    parser.close()
    return ' '.join(html.unescape(' '.join(parser.parts)).split())


def clean_url(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return ''
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith('utm_') and k.lower() not in ('guccounter', 'guce_referrer', 'guce_referrer_sig')]
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ''))
    except ValueError:
        return ''


def parse_feed(payload, symbol='', limit=20, now=None):
    now = now or utcnow()
    if not 1 <= limit <= 50:
        raise ValueError('피드당 최대 기사 수는 1~50 범위입니다')
    root = ET.fromstring(payload)
    atom = root.tag == '{http://www.w3.org/2005/Atom}feed'
    ns = '{http://www.w3.org/2005/Atom}' if atom else ''
    if not atom and (root.tag != 'rss' or root.find('channel') is None):
        raise ValueError('지원하는 RSS/Atom 형식이 아닙니다')
    items = root.findall(ns + 'entry') if atom else root.findall('./channel/item')
    articles, rejected = [], 0
    for item in items[:limit]:
        title = plain(item.findtext(ns + 'title'))[:1000]
        link = next((link.get('href', '') for link in item.findall(ns + 'link')
                     if link.get('rel', 'alternate') == 'alternate'), '') if atom else item.findtext('link')
        url = clean_url(link or '')
        description = item.find(ns + 'content') if atom else item.find('description')
        if description is None and atom:
            description = item.find(ns + 'summary')
        try:
            # Missing publication dates never fall back to updated/fetch time.
            published = timestamp(item.findtext(ns + 'published') or '') if atom else parsedate_to_datetime(item.findtext('pubDate') or '')
            if published.tzinfo is None:
                raise ValueError('Missing timezone')
            published = published.astimezone(timezone.utc)
            if published > now + timedelta(minutes=5):
                raise ValueError('Future publication date')
            if not title or not url:
                raise ValueError('Missing title or URL')
        except (ValueError, TypeError, OverflowError):
            rejected += 1
            continue
        articles.append({
            'id': hashlib.sha256(url.encode()).hexdigest()[:24],
            'title': title, 'summary': plain(' '.join(description.itertext()) if description is not None else '')[:4000],
            'url': url, 'published_at': iso(published),
            'source': plain(item.findtext('source')) or urlsplit(url).hostname,
            'feed_symbols': [symbol] if symbol else [],
        })
    return articles, rejected


def fetch_yahoo(symbol, limit=20):
    if symbol not in UNIVERSE or not 1 <= limit <= 50:
        raise ValueError('등록된 종목과 1~50건 범위를 사용하세요')
    query = urlencode({'s': symbol, 'region': 'US', 'lang': 'en-US'})
    return fetch_feed('https://finance.yahoo.com/rss/headline?' + query, symbol, limit)


def fetch_feed(url, symbol='', limit=20):
    if not 1 <= limit <= 50:
        raise ValueError('피드당 최대 기사 수는 1~50 범위입니다')
    request = Request(url, headers={'User-Agent': 'NewsAlarm/1.0', 'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                payload = response.read(2_000_001)
            break
        except HTTPError as exc:
            if exc.code not in (500, 502, 503, 504) or attempt == 2:
                raise
            exc.close()
            time.sleep(2 ** attempt)
    if len(payload) > 2_000_000:
        raise ValueError('RSS 응답이 2 MB 제한을 초과했습니다')
    return parse_feed(payload, symbol, limit)


def macro_events(article):
    text = article['title'] + ' ' + article['summary']
    events = {key: [word for word in words if contains(text, word)] for key, (_, words) in MACRO_EVENTS.items()}
    for key in article.get('feed_topics', []):
        if key in events:
            events[key].append('공식 발표 피드')
    return {key: evidence for key, evidence in events.items() if evidence}


def fetch_macro(feed_id, limit=20):
    if feed_id not in MACRO_FEEDS:
        raise ValueError('등록되지 않은 지표 피드입니다')
    source, url, topic = MACRO_FEEDS[feed_id]
    articles, rejected = fetch_feed(url, limit=limit)
    kept = []
    for article in articles:
        article['feed_sources'] = [feed_id]
        # Only the publisher's own linked releases receive the official label.
        official = bool(topic) and urlsplit(article['url']).hostname == urlsplit(url).hostname
        article['feed_topics'] = [topic] if official else []
        article['official_source'] = source if official else ''
        if official:
            article['source'] = source
        if macro_events(article):
            kept.append(article)
    return kept, rejected


def deduplicate(articles):
    # ponytail: URL or same-day exact title only; semantic clustering can handle rewrites later.
    kept, keys = {}, {}
    for original in articles:
        article = dict(original)
        # Recurring releases (e.g. FOMC statements) reuse titles on different dates.
        title_key = (re.sub(r'\W+', ' ', article['title'].lower()).strip(), timestamp(article['published_at']).date())
        candidates = [('title', title_key)]
        if article['url']:
            candidates.append(('url', clean_url(article['url'])))
        matches = sorted({keys[key] for key in candidates if key in keys})
        if not matches:
            group = len(keys)
            previous = article
            previous['feed_symbols'] = sorted(set(article['feed_symbols']))
            previous['_dedupe_keys'] = set(candidates)
            kept[group] = previous
        else:
            group = matches[0]
            previous = kept[group]
            previous['feed_symbols'] = sorted(set(previous['feed_symbols']) | set(article['feed_symbols']))
            for other_group in matches[1:]:
                other = kept.pop(other_group)
                previous['feed_symbols'] = sorted(set(previous['feed_symbols']) | set(other['feed_symbols']))
                previous['_dedupe_keys'].update(other['_dedupe_keys'])
                for field in ('feed_sources', 'feed_topics'):
                    if other.get(field):
                        previous[field] = sorted(set(previous.get(field, [])) | set(other[field]))
            previous['_dedupe_keys'].update(candidates)
        for key in previous['_dedupe_keys']:
            keys[key] = group
        for field in ('feed_sources', 'feed_topics'):
            values = set(previous.get(field, [])) | set(article.get(field, []))
            if values:
                previous[field] = sorted(values)
    result = list(kept.values())
    for article in result:
        article.pop('_dedupe_keys')
    return result


@lru_cache(maxsize=4096)
def phrase_pattern(phrase):
    return re.compile(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', re.I)


def contains(text, phrase):
    return phrase_pattern(phrase).search(text) is not None


def candidate_entities(article):
    text = article['title'] + ' ' + article['summary']
    links = []
    for symbol, (name, aliases, _) in UNIVERSE.items():
        matches = [alias for alias in aliases if contains(text, alias)]
        # Short company names remain candidates, not confirmed identities.
        if symbol in ('META', 'ARM'):
            short = 'Meta' if symbol == 'META' else 'Arm'
            if re.search(r'\b' + short + r'\b', text):
                matches.append(short)
            context = re.search(r'\b' + short + r"(?:['’]s)?\s+(?:stock|shares|holdings|platforms|CPU|chip|Muse)\b", text, re.I)
            if context:
                matches.append(context.group())
        # Ambiguous English words require a cashtag or parenthesized ticker.
        ticker_pattern = r'(?<!\w)' + re.escape(symbol) + r'(?!\w)'
        if len(symbol) <= 2 or symbol in ('META', 'COIN', 'MU', 'BAC', 'APP', 'ARM', 'CAT', 'NOW', 'ALL', 'HAS', 'BRO', 'PM', 'AS', 'CAVA'):
            ticker_pattern = r'\$' + symbol + r'\b|\(' + symbol + r'\)'
        if re.search(ticker_pattern, text):
            matches.append(symbol)
        if matches:
            links.append({'symbol': symbol, 'name': name, 'evidence': sorted(set(matches))})
    themes = {theme: [word for word in words if contains(text, word)] for theme, words in THEMES.items()}
    themes = {theme: words for theme, words in themes.items() if words}
    return links, themes


def entities(article):
    from company_links import conservative
    links, themes = candidate_entities(article)
    return conservative(article['title']+' '+article.get('summary',''), links), themes


def lexicon_sentiment(text):
    # ponytail: English lexicon misses context, negation and entity direction; use FinBERT for research.
    words = re.findall(r'[a-z]+', text.lower())
    pos, neg = sum(w in POSITIVE for w in words), sum(w in NEGATIVE for w in words)
    score = (pos - neg) / (pos + neg + 2)
    return {'score': score, 'label': 'positive' if score > .15 else 'negative' if score < -.15 else 'neutral', 'confidence': None, 'engine': 'lexicon', 'probabilities': None}


def load_finbert():
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
    except ImportError as exc:
        raise RuntimeError('FinBERT 패키지가 없습니다. README의 설치 명령을 실행하세요.') from exc
    if not (MODEL_DIR / 'config.json').exists():
        raise RuntimeError('로컬 모델이 없습니다. 먼저 .\\run.ps1 download-model 을 실행하세요.')
    tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR, local_files_only=True)
    model.eval()
    labels = {int(k): v.lower() for k, v in model.config.id2label.items()}
    if set(labels.values()) != {'positive', 'negative', 'neutral'}:
        raise RuntimeError('FinBERT 라벨 구성이 올바르지 않습니다')

    def score(texts):
        output = []
        for start in range(0, len(texts), 8):
            tokens = tokenizer(texts[start:start + 8], return_tensors='pt', padding=True, truncation=True, max_length=512)
            with torch.inference_mode():
                rows = model(**tokens).logits.softmax(dim=-1).tolist()
            for row in rows:
                probabilities = {labels[i]: value for i, value in enumerate(row)}
                label = max(probabilities, key=probabilities.get)
                output.append({'score': probabilities['positive'] - probabilities['negative'], 'label': label, 'confidence': probabilities[label], 'probabilities': probabilities, 'engine': MODEL_ID})
        return output
    return score


def analyze(articles, engine='lexicon', scorer=None, cached=()):
    if engine not in ('lexicon', 'finbert'):
        raise ValueError('Unknown sentiment engine')
    model_id = MODEL_ID if engine == 'finbert' else 'lexicon'
    # ponytail: model ID identifies the cache; version this key if local weights change.
    scores_by_text = {(a['title'], a['summary']): a['sentiment'] for a in cached
                      if a.get('sentiment', {}).get('engine') == model_id}
    missing = list(dict.fromkeys((a['title'], a['summary']) for a in articles
                                if (a['title'], a['summary']) not in scores_by_text))
    texts = [title + '. ' + summary for title, summary in missing]
    scores = ((scorer or load_finbert())(texts) if engine == 'finbert'
              else [lexicon_sentiment(text) for text in texts]) if texts else []
    if len(scores) != len(missing):
        raise ValueError('Sentiment result count mismatch')
    scores_by_text.update(zip(missing, scores))
    results = []
    for article in articles:
        sentiment = dict(scores_by_text[article['title'], article['summary']])
        links, themes = entities(article)
        results.append({**article, 'links': links, 'themes': themes, 'sentiment': sentiment})
    return results


def save_batch(db_path, articles, errors, collected_at=None):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS runs (at TEXT NOT NULL, body TEXT NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        old = [json.loads(row[0]) for row in conn.execute('SELECT body FROM articles')]
        previous = {a['id']: a for a in old}
        articles = [dict(a) for a in articles]
        first_seen = {a['id']: a.get('first_collected_at') for a in old}
        for article in articles:
            article['first_collected_at'] = first_seen.get(article['id'], collected_at or iso(utcnow()))
        # Current analysis wins; old feed associations survive repeated collections.
        merged = deduplicate(articles + old)
        for article in merged:
            if article['id'] in first_seen:
                article['first_collected_at'] = first_seen[article['id']]
        current_ids = {a['id'] for a in merged}
        conn.executemany('DELETE FROM articles WHERE id=?', [(id,) for id in previous.keys() - current_ids])
        conn.executemany('INSERT INTO articles VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                         [(a['id'], json.dumps(a, ensure_ascii=False)) for a in merged if previous.get(a['id']) != a])
        conn.execute('INSERT INTO runs VALUES (?, ?)', (collected_at or iso(utcnow()), json.dumps({'received': len(articles), 'errors': errors}, ensure_ascii=False)))
    return len(merged)


def load_saved(db_path):
    if not Path(db_path).exists():
        return [], {'last_collected': None, 'history_start': None, 'errors': []}
    with closing(sqlite3.connect(db_path)) as conn:
        articles = [json.loads(row[0]) for row in conn.execute('SELECT body FROM articles')]
        last = conn.execute('SELECT at, body FROM runs ORDER BY at DESC LIMIT 1').fetchone()
        first = conn.execute('SELECT MIN(at) FROM runs').fetchone()[0]
    from content_assessment import attach
    from review_harness import attach_web_reviews
    from first_pass import attach as attach_first_pass
    from company_links import resolve
    articles = attach_first_pass(attach_web_reviews(attach(articles)))
    for article in articles:
        article['links'] = resolve(article)
    return articles, {'last_collected': last[0] if last else None, 'history_start': first, 'errors': json.loads(last[1])['errors'] if last else []}


def metrics(articles, previous, hours, now, baseline):
    weights = [2 ** (-max(0, (now - timestamp(a['published_at'])).total_seconds() / 3600) / 24) for a in articles]
    volume = sum(weights)
    sentiment = sum(a['sentiment']['score'] * w for a, w in zip(articles, weights)) / volume if volume else 0
    intensity = 100 * (1 - math.exp(-volume / 5)) * (.6 + .4 * abs(sentiment))
    count, prior = len(articles), len(previous)
    momentum = (count - prior) / (count + prior) if baseline and count + prior else None
    return {'count': count, 'previous_count': prior if baseline else None, 'sentiment': round(sentiment, 4), 'intensity': round(intensity, 2), 'momentum': momentum}


def dashboard(articles, metadata, hours=24, now=None, mode='saved'):
    if hours not in (0, 24, 72, 168, 720):
        raise ValueError('허용 기간: 전체(0), 24, 72, 168, 720시간')
    # Classify at read time too, so existing saved rows need no migration/re-analysis.
    from briefing import importance, group_issues
    from first_pass import attach as attach_qwen
    from semantics import attach as attach_semantics
    if mode != 'demo':
        articles = [attach_semantics(dict(a)) for a in attach_qwen([dict(a) for a in articles])]
    articles = [{**article, 'links': article.get('links', []) if mode != 'demo' else entities(article)[0], 'macro_events': macro_events(article), 'importance': importance(article)} for article in articles]
    now = now or utcnow()
    all_history = hours == 0
    if all_history:
        hours = max(24, math.ceil(max(((now - timestamp(a['published_at'])).total_seconds() / 3600 for a in articles), default=24)) + 1)
    cutoff, prior_cutoff = now - timedelta(hours=hours), now - timedelta(hours=hours * 2)
    current = [a for a in articles if cutoff <= timestamp(a['published_at']) <= now]
    previous = [a for a in articles if prior_cutoff <= timestamp(a['published_at']) < cutoff]
    baseline = not all_history and bool(metadata.get('history_start') and timestamp(metadata['history_start']) <= prior_cutoff)
    themes = []
    for name in THEMES:
        subset = [a for a in current if name in a['themes']]
        prior = [a for a in previous if name in a['themes']]
        if not subset and not prior:
            continue
        keywords = Counter(word for a in subset for word in a['themes'][name])
        direct = sorted({link['symbol'] for a in subset for link in a['links']})
        exposure = [symbol for symbol, (_, _, groups) in UNIVERSE.items() if name in groups and symbol not in direct]
        themes.append({'name': name, **metrics(subset, prior, hours, now, baseline), 'keywords': keywords.most_common(6), 'symbols': direct, 'exposure': exposure})
    from market_caps import load_caps
    caps = load_caps()
    stocks = []
    for symbol, (name, _, groups) in UNIVERSE.items():
        subset = [a for a in current if any(link['symbol'] == symbol for link in a['links'])]
        prior = [a for a in previous if any(link['symbol'] == symbol for link in a['links'])]
        stocks.append({'symbol': symbol, 'name': name, 'themes': groups, **caps.get(symbol, {}), **metrics(subset, prior, hours, now, baseline)})
    themes.sort(key=lambda item: item['intensity'], reverse=True)
    stocks.sort(key=lambda item: item['intensity'], reverse=True)
    current.sort(key=lambda item: item['published_at'], reverse=True)
    from first_pass import progress
    from review_harness import luna_records, fingerprint
    luna = luna_records() if mode == 'saved' else {}
    processing = {'qwen': progress(current, now) if mode == 'saved' else {},
                  'luna_reviewed': sum(fingerprint(a) in luna for a in current),
                  'luna_bodies': sum(bool(luna.get(fingerprint(a), {}).get('summary')) for a in current),
                  'luna_last': max((r.get('at', '') for r in luna.values()), default=None)}
    return {'mode': mode, 'as_of': iso(now), 'hours': 0 if all_history else hours, 'metadata': metadata, 'baseline': baseline, 'universe': universe_status(),
            'processing': processing,
            'summary': {**metrics(current, previous, hours, now, baseline), 'mapped': sum(bool(a['links']) for a in current), 'active_stocks': sum(s['count'] > 0 for s in stocks), 'engines': sorted({a['sentiment']['engine'] for a in current})},
            'themes': themes, 'stocks': stocks, 'articles': current, 'issues': group_issues(current),
            'macro_sources': list(MACRO_FEEDS),
            'indicators': [{'id': key, 'name': name, 'count': sum(key in a['macro_events'] for a in current)}
                           for key, (name, _) in MACRO_EVENTS.items()]}
