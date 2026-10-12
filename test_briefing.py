"""Offline universe, importance and full-history checks."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import briefing as b
import news
from universe import active_symbols


def check():
    deal = b.transaction({'id':'deal','title':'CEG, SHEL Stocks Rise Overnight: Constellation Energy To Acquire Rhode Island State Energy Center From Shell For $715M'})
    assert deal['buyer'] == 'Constellation Energy' and deal['seller'] == 'Shell'
    assert deal['asset'] == 'Rhode Island State Energy Center' and deal['amount'] == '$715M'
    assert b.transaction({'id':'no','title':'Could CEG buy a power plant?'}) is None
    assert b.transaction({'id':'no','title':'Constellation to Acquire RISEC from Shell'})['amount'] is None
    assert not {'MASI', 'NUVL', 'LNWO'} & set(active_symbols())
    now = datetime(2026, 9, 10, 10, 30, tzinfo=timezone.utc)
    def article(title, age=1, label='neutral', url='https://example.com/1'):
        item = {'title': title, 'summary': '', 'published_at': news.iso(now-timedelta(hours=age)),
                'url': url, 'source': 'example.com', 'feed_symbols': ['NVDA'], 'id': url,
                'sentiment': {'score': 0, 'label': label, 'engine': 'test'}}
        item['links'], item['themes'] = news.entities(item)
        return item
    neutral = article('Nvidia raises revenue guidance')
    sample = article('Motorola Solutions Announces Increase to Share Repurchase Program')
    sample['summary'] = 'CHICAGO, September 09, 2026--Motorola Solutions, Inc. (NYSE: MSI) announced a $2 billion increase to its share repurchase program. Another sentence.'
    assert '$2 billion' in b.one_line(sample) and 'CHICAGO' not in b.one_line(sample)
    assert 'Another sentence' not in b.one_line(sample)
    sample['summary'] = 'A company profile without the event.'
    assert b.one_line(sample) == sample['title']
    assert b.importance(neutral)['high']
    assert b.importance(article('Nvidia stock surges', label='positive'))['score'] == 0
    assert not b.importance(article('Should you buy Nvidia before earnings?'))['high']
    assert not b.importance(article('Nvidia: Which One Costs Less Per Dollar Of Profit?'))['high']
    assert not b.importance(article('AMD CFO lifts chip market forecast toward $3 trillion'))['high']
    assert not b.importance(article('3 Monthly Dividend ETFs to Buy Hand Over Fist Before September Fed Meeting'))['high']
    assert not b.importance(article('Adobe to Report First Earnings Since Naming Next CEO'))['high']
    assert not b.importance(article('Marriott: Buy, Sell, or Hold Post Q2 Earnings?'))['high']
    assert b.importance(article('Nvidia reports record quarterly revenue'))['high']
    assert '미확정 보도' in b.importance(article('Salesforce reportedly in talks to acquire Listen Labs'))['reasons']
    assert not news.entities(article('AS CEO PM has an APP and CAT'))[0]
    assert any(link['symbol'] == 'CAT' for link in news.entities(article('Caterpillar (NYSE: CAT) wins contract'))[0])
    old = article('Nvidia acquires a supplier', 48, url='https://example.com/old')
    unrelated = article('UncoveredCo reports earnings', url='https://example.com/unrelated')
    similar = article('Nvidia raises revenue guidance!', url='https://another.example.com/2')
    assert len(b.select_news([neutral, old, unrelated, similar], now)) == 1
    first = article('CRM Stock Dips 2% — Salesforce Is Reportedly In Talks To Acquire AI Research Firm Listen Labs For $2B')
    second = article('Salesforce in talks to acquire AI platform Listen Labs for $2 billion - report', url='https://example.com/other')
    assert len(b.select_news([first, second], now)) == 1
    for a in [first, second]:
        a['importance'] = b.importance(a)
        a['macro_events'] = news.macro_events(a)
    issues = b.group_issues([first, second])
    assert len(issues) == 1 and len(issues[0]['article_ids']) == 2
    assert issues[0]['status'] == '미확정 보도'
    assert issues[0]['links'][0]['article_ids'] == [first['id'], second['id']]
    assert sum(issues[0]['sentiments'].values()) == 2
    separate = dict(second, id='later', published_at=news.iso(now+timedelta(days=2)))
    assert len(b.group_issues([first, separate])) == 2
    all_articles = [neutral, old, article('Nvidia old article', 1000)]
    meta = {'history_start': None, 'last_collected': news.iso(now), 'errors': []}
    result = news.dashboard(all_articles, meta, hours=0, now=now)
    assert len(result['articles']) == 3 and not result['baseline']
    assert len(news.dashboard(all_articles, meta, hours=24, now=now)['articles']) == 1
    with patch.object(news, 'load_saved', return_value=([neutral], meta)):
        message = b.compose('금일 주요 일정\n21:30 CPI', now)
        assert '중립' in message and '가이던스' in message and '21:30 CPI' in message
        assert 'href="https://example.com/1"' in message
        assert 'CSQ UNIV' not in message and '종목 피드' not in message
    print('Briefing checks passed (no network).')


if __name__ == '__main__':
    check()
