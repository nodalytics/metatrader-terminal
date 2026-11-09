from typing import Optional, List, Dict, Union
from datetime import datetime
from .connector import mt5_connector
from .market_data import market_data_service
from .trade import trade_service
from .history import history_service

class MT5Service:
    @property
    def _initialized(self):
        return mt5_connector._initialized

    def initialize(self):
        return mt5_connector.initialize()

    def get_timeframe(self, timeframe_str: str):
        return market_data_service.get_timeframe(timeframe_str)

    def get_symbols(self, *args, **kwargs):
        return market_data_service.get_symbols(*args, **kwargs)

    def send_market_order(self, *args, **kwargs):
        return trade_service.send_market_order(*args, **kwargs)

    def check_market_order(self, *args, **kwargs):
        return trade_service.check_market_order(*args, **kwargs)

    def send_pending_order(self, *args, **kwargs):
        return trade_service.send_pending_order(*args, **kwargs)

    def cancel_order(self, *args, **kwargs):
        return trade_service.cancel_order(*args, **kwargs)

    def modify_pending_order(self, *args, **kwargs):
        return trade_service.modify_pending_order(*args, **kwargs)

    def modify_sl_tp(self, *args, **kwargs):
        return trade_service.modify_sl_tp(*args, **kwargs)

    def close_position(self, *args, **kwargs):
        return trade_service.close_position(*args, **kwargs)

    def get_positions(self, *args, **kwargs):
        return trade_service.get_positions(*args, **kwargs)

    def select_symbol(self, *args, **kwargs):
        return market_data_service.select_symbol(*args, **kwargs)

    def get_symbol_info(self, *args, **kwargs):
        return market_data_service.get_symbol_info(*args, **kwargs)

    def get_symbol_info_tick(self, *args, **kwargs):
        return market_data_service.get_symbol_info_tick(*args, **kwargs)

    def close_all_positions(self, *args, **kwargs):
        return trade_service.close_all_positions(*args, **kwargs)

    def copy_rates_from_pos(self, *args, **kwargs):
        return market_data_service.copy_rates_from_pos(*args, **kwargs)

    def copy_rates_from(self, *args, **kwargs):
        return market_data_service.copy_rates_from(*args, **kwargs)

    def copy_rates_range(self, *args, **kwargs):
        return market_data_service.copy_rates_range(*args, **kwargs)

    def copy_ticks_from(self, *args, **kwargs):
        return market_data_service.copy_ticks_from(*args, **kwargs)

    def copy_ticks_range(self, *args, **kwargs):
        return market_data_service.copy_ticks_range(*args, **kwargs)

    def get_orders(self, *args, **kwargs):
        return trade_service.get_orders(*args, **kwargs)

    def get_orders_total(self, *args, **kwargs):
        return trade_service.get_orders_total(*args, **kwargs)

    def order_calc_margin(self, *args, **kwargs):
        return trade_service.order_calc_margin(*args, **kwargs)

    def order_calc_profit(self, *args, **kwargs):
        return trade_service.order_calc_profit(*args, **kwargs)

    def get_history_deals(self, *args, **kwargs):
        return history_service.get_history_deals(*args, **kwargs)

    def get_terminal_info(self, *args, **kwargs):
        return mt5_connector.get_terminal_info(*args, **kwargs)

    def get_account_info(self, *args, **kwargs):
        return mt5_connector.get_account_info(*args, **kwargs)

    def get_history_orders(self, *args, **kwargs):
        return history_service.get_history_orders(*args, **kwargs)

    def last_error(self):
        from app.services.terminal_transport import mt5
        return mt5.last_error()

mt5_service = MT5Service()
