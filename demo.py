"""Authored synthetic headlines, never represented as actual market news."""
import hashlib
from datetime import timedelta
from news import analyze, iso

SAMPLES = [
    (1, 'Nvidia raises outlook as AI chip demand beats expectations', 'Strong data center orders support revenue growth at NVIDIA.', 'NVDA'),
    (2, 'AMD gains as new AI GPU wins cloud customers', 'Advanced Micro Devices reports strong demand for semiconductor products.', 'AMD'),
    (3, 'Broadcom reports record AI chip revenue', 'Broadcom sees growth in custom chips for cloud infrastructure.', 'AVGO'),
    (4, 'TSMC raises capital spending on advanced semiconductor demand', 'Taiwan Semiconductor expects strong foundry growth.', 'TSM'),
    (6, 'Micron beats estimates as HBM orders surge', 'Micron reports record memory chip demand from data center customers.', 'MU'),
    (9, 'Nvidia announces a new semiconductor research facility', 'NVIDIA outlined its next GPU development program.', 'NVDA'),
    (1.5, 'Microsoft cloud growth beats forecasts', 'Microsoft raises its AI infrastructure outlook after strong Azure revenue.', 'MSFT'),
    (5, 'Amazon expands AWS data center capacity', 'Amazon outlines new cloud infrastructure and artificial intelligence services.', 'AMZN'),
    (8, 'Google faces probe over cloud competition', 'Alphabet faces a lawsuit over its cloud service contracts.', 'GOOGL'),
    (13, 'Microsoft Copilot wins new enterprise contracts', 'Microsoft reports strong growth in artificial intelligence subscriptions.', 'MSFT'),
    (7, 'Meta ad revenue beats estimates as margins gain', 'Meta Platforms reports record digital advertising revenue.', 'META'),
    (15, 'Google advertising growth slows as spending falls', 'Alphabet reports weak ad revenue in a cautious market.', 'GOOGL'),
    (3.5, 'Tesla cuts electric vehicle delivery outlook', 'Tesla faces weak demand and a battery recall.', 'TSLA'),
    (11, 'Exxon warns of weak oil demand', 'Exxon Mobil expects crude margins to decline as energy markets slump.', 'XOM'),
    (18, 'Tesla announces battery production update', 'Tesla provided a schedule for its electric vehicle factory.', 'TSLA'),
    (5.5, 'JPMorgan beats earnings estimates on strong trading', 'JPMorgan reports profit growth as banking activity gains.', 'JPM'),
    (10, 'Bank of America faces loss as credit risk grows', 'Bank of America reports weak consumer loan demand.', 'BAC'),
    (21, 'Federal Reserve signals interest rate review', 'The Fed discussed inflation and banking conditions at its latest meeting.', 'JPM'),
    (4.5, 'Eli Lilly gains after obesity drug approval', 'Eli Lilly reports strong clinical results for a GLP-1 treatment.', 'LLY'),
    (12, 'UnitedHealth cuts outlook as medical costs rise', 'UnitedHealth faces a probe and weak healthcare margins.', 'UNH'),
    (16, 'Apple reports weak iPhone demand', 'Apple faces a decline in consumer spending and retail orders.', 'AAPL'),
    (20, 'Walmart beats estimates on e-commerce growth', 'Walmart raises its consumer sales outlook after strong shopping demand.', 'WMT'),
    (2.5, 'Coinbase gains as bitcoin trading volume surges', 'Coinbase reports record crypto activity and stablecoin revenue growth.', 'COIN'),
    (14, 'New tariffs raise semiconductor supply risk', 'Export control proposals add risk to chip and energy supply chains.', 'NVDA'),
    (26, 'Nvidia announces semiconductor conference', 'NVIDIA shared an agenda for chip developers.', 'NVDA'),
    (29, 'Microsoft introduces cloud infrastructure services', 'Microsoft described an artificial intelligence product update.', 'MSFT'),
    (31, 'Tesla electric vehicle deliveries beat estimates', 'Tesla reports strong demand and battery production growth.', 'TSLA'),
    (34, 'Exxon gains as oil demand grows', 'Exxon reports strong energy sales and profit growth.', 'XOM'),
    (37, 'JPMorgan announces banking leadership update', 'JPMorgan outlined a new interest rate research team.', 'JPM'),
    (40, 'Apple iPhone sales beat forecasts', 'Apple reports strong consumer demand.', 'AAPL'),
    (43, 'Coinbase reports a crypto market update', 'Coinbase discussed bitcoin and stablecoin trading.', 'COIN'),
    (46, 'Meta announces advertising tools', 'Meta Platforms introduced new social media advertising services.', 'META'),
]


def demo_articles(now):
    articles = []
    for age, title, summary, symbol in SAMPLES:
        articles.append({'id': hashlib.sha256(title.encode()).hexdigest()[:24], 'title': title,
                         'summary': summary, 'url': '', 'source': '합성 예시 · 실제 기사 아님',
                         'published_at': iso(now - timedelta(hours=age)), 'feed_symbols': [symbol]})
    return analyze(articles), {'last_collected': None, 'history_start': iso(now - timedelta(hours=48)), 'errors': []}
