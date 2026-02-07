import pandas as pd
from datetime import datetime, timezone, timedelta
from core.data.pipeline import MarketDataPipeline

class _CountingThrottler:
    def __init__(self):
        self.calls = 0
        self.last_limit_id = None
    def execute_sync(self, limit_id, func, *args, **kwargs):
        self.calls += 1
        self.last_limit_id = limit_id
        return func(*args, **kwargs)

def test_market_data_pipeline_uses_throttler_execute_sync(monkeypatch):
    throttler = _CountingThrottler()

    # Build pipeline with fake alpaca client
    p = MarketDataPipeline(
        alpaca_api_key="x",
        alpaca_api_secret="y",
        max_staleness_seconds=9999,
        cache_ttl_seconds=0,
        throttler=throttler,
    )

    class _FakeBars:
        def __init__(self, df):
            self._df = df
        def __contains__(self, key):
            return True
        def __getitem__(self, key):
            class _Obj:
                def __init__(self, df):
                    self.df = df
            return _Obj(self._df)

    def fake_get_stock_bars(request):
        now = datetime.now(timezone.utc)
        closed_minute = (now.replace(second=0, microsecond=0) - timedelta(minutes=1))
        idx = pd.DatetimeIndex([closed_minute])

        df = pd.DataFrame(
            [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            index=idx
        )
        return _FakeBars(df)

    monkeypatch.setattr(p.alpaca_client, "get_stock_bars", fake_get_stock_bars)

    df = p.get_latest_bars("SPY", lookback_bars=1, timeframe="1Min")
    assert not df.empty
    assert throttler.calls == 1
    assert throttler.last_limit_id == "alpaca_data"
