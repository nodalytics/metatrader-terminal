import MetaTrader5 as mt5
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from .connector import mt5_connector

class HistoryService:
    """Deal and order history.

    **An omitted range is 30 days, not all history.** Both methods below
    default `date_from` to `now - 30 days`, which is a reasonable default and a
    poor silent one: a caller whose range parameter was dropped - by a typo, or
    by using this service's *other* spelling, `date_from` instead of
    `from_date` - gets a month of history and no indication that the window it
    asked for was never applied. `routers/history.py` now accepts both
    spellings for that reason.
    """

    def get_history_deals(self, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None, position: Optional[int] = None) -> Optional[List[Dict]]:
        mt5_connector.initialize()
        date_from = from_date if from_date else datetime.now() - timedelta(days=30)
        date_to = to_date if to_date else datetime.now()
        deals = mt5.history_deals_get(date_from, date_to, position=position) if position else mt5.history_deals_get(date_from, date_to)
        if deals is None: return []
        return [d._asdict() for d in deals]

    def get_history_orders(self, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None, ticket: Optional[int] = None) -> Optional[List[Dict]]:
        mt5_connector.initialize()
        if ticket:
            orders = mt5.history_orders_get(ticket=ticket)
        else:
            date_from = from_date if from_date else datetime.now() - timedelta(days=30)
            date_to = to_date if to_date else datetime.now()
            orders = mt5.history_orders_get(date_from, date_to)
        if orders is None: return []
        return [o._asdict() for o in orders]

history_service = HistoryService()
