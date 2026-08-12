from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from .api_gateway import ApiGatewayService
from .backtest_service import BacktestService
from .broker_adapter import BrokerAdapterLayer
from .broker_reconciliation import BrokerReconciliationService
from .exit_risk import ExitRiskService
from .historical_data import HistoricalDataService
from .market_feed import MarketFeedService
from .notification_service import NotificationService
from .order_execution import OrderExecutionService
from .order_update import OrderUpdateService
from .position_service import PositionService
from .scheduler_service import SchedulerService
from .sector_ranking import SectorRankingService
from .signal_intake import SignalIntakeService
from .strategy_evaluation import StrategyEvaluationService
from .trade_decision import TradeDecisionService


class ServiceRuntime:
    """Container for long-lived modular services."""

    def __init__(
        self,
        *,
        store_provider,
        ws_manager,
        ensure_engine: Callable[[int], Awaitable[Any]],
        subscribe_symbols: Callable[[int, List[str]], Awaitable[None]],
        start_feed: Optional[Callable[[int], Awaitable[None]]] = None,
        stop_dhan_feed: Optional[Callable[[], Awaitable[None]]] = None,
        stop_kite_feed: Optional[Callable[[], Awaitable[None]]] = None,
        signal_workers: int = 4,
    ) -> None:
        async def _noop_user(_user_id: int) -> None:
            return None

        async def _noop() -> None:
            return None

        self.api_gateway: Optional[ApiGatewayService] = None
        self.notifications = NotificationService(store_provider, ws_manager)
        self.market_feed = MarketFeedService(
            start_feed=start_feed or _noop_user,
            stop_dhan_feed=stop_dhan_feed or _noop,
            stop_kite_feed=stop_kite_feed or _noop,
            subscribe_symbols=subscribe_symbols,
        )
        self.order_updates = OrderUpdateService(ensure_engine)
        self.strategy_evaluation = StrategyEvaluationService()
        self.sector_ranking = SectorRankingService(store_provider, ensure_engine)
        self.order_execution = OrderExecutionService(ensure_engine)
        self.broker_reconciliation = BrokerReconciliationService(ensure_engine)
        self.positions = PositionService(store_provider, ensure_engine)
        self.exit_risk = ExitRiskService(ensure_engine)
        self.historical_data = HistoricalDataService(ensure_engine)
        self.backtests = BacktestService()
        self.scheduler = SchedulerService()
        self.broker_adapter = BrokerAdapterLayer(ensure_engine)
        self.trade_decision = TradeDecisionService(ensure_engine)
        self.signal_intake = SignalIntakeService(
            subscribe_symbols=subscribe_symbols,
            notification_service=self.notifications,
            trade_decision_service=self.trade_decision,
            workers=signal_workers,
        )
        self.api_gateway = ApiGatewayService(self.signal_intake)

    async def start(self) -> None:
        await self.signal_intake.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.signal_intake.stop()

    def status(self) -> Dict[str, Any]:
        queue = self.signal_intake.queue.queue
        return {
            "api_gateway": self.api_gateway.status() if self.api_gateway else {"enabled": False},
            "market_feed": self.market_feed.status(),
            "order_update": self.order_updates.status(),
            "signal_intake": {
                "workers": self.signal_intake.queue.workers,
                "queue_depth": queue.qsize(),
                "queue_max": queue.maxsize,
            },
            "strategy_evaluation": self.strategy_evaluation.status(),
            "sector_ranking": self.sector_ranking.status(),
            "trade_decision": {"enabled": True},
            "order_execution": self.order_execution.status(),
            "broker_reconciliation": self.broker_reconciliation.status(),
            "position": self.positions.status(),
            "exit_risk": self.exit_risk.status(),
            "historical_data": self.historical_data.status(),
            "backtest": self.backtests.status(),
            "notification": {"enabled": True},
            "scheduler": self.scheduler.status(),
            "broker_adapter": self.broker_adapter.status(),
        }
