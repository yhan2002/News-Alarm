"""Explicit configuration must never read an adjacent project or portfolio."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import daily_schedule
import portfolio_events
import universe

with patch.dict(os.environ, {}, clear=True):
    symbols = universe.csq_symbols()
    assert symbols == ['NVDA', 'MSFT', 'AAPL', 'AMZN']
    with patch.object(Path, 'glob', side_effect=AssertionError('Implicit portfolio read')):
        result = portfolio_events.holdings()
        assert not result['symbols'] and 'NEWS_PORTFOLIO_ROOT' in result['errors'][0]
    with patch.object(Path, 'read_text', side_effect=AssertionError('Implicit secret read')):
        try:
            daily_schedule.quantbot_destination()
            raise AssertionError('Unconfigured Telegram accepted')
        except ValueError:
            pass
with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / 'universe.py'
    config.write_text('universe_groups = {"CSQ_UNIV": ["TEST"]}', encoding='utf-8')
    with patch.dict(os.environ, {'NEWS_UNIVERSE_CONFIG': str(config)}):
        assert universe.csq_symbols() == ['TEST']
with patch.dict(os.environ, {'NEWS_TELEGRAM_BOT_TOKEN': '123:fake_token', 'NEWS_TELEGRAM_CHAT_ID': '-456'}):
    assert daily_schedule.quantbot_destination() == ('123:fake_token', '-456')
with patch.dict(os.environ, {'NEWS_TELEGRAM_BOT_TOKEN': '123:fake_token', 'NEWS_TELEGRAM_CHAT_ID': ''}):
    try:
        daily_schedule.quantbot_destination()
        raise AssertionError('Partial Telegram configuration accepted')
    except ValueError:
        pass
print('Configuration defaults, explicit overrides and disabled integrations passed.')
