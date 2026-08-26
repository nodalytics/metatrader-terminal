import logging
from datetime import datetime
from typing import Optional, List, Dict
import MetaTrader5 as mt5
from .connector import mt5_connector
from app.utils.exceptions import MT5OrderError, MT5SymbolNotFoundError

logger = logging.getLogger(__name__)

class TradeService:
    def send_market_order(self, symbol: str, volume: float, order_type: str, sl: float, tp: float = None,
                          deviation: int = 20, comment: str = '', magic: int = 0, type_filling: str = 'FOK'):
        mt5_connector.initialize()

        order_type_map = {
            'BUY': mt5.ORDER_TYPE_BUY,
            'SELL': mt5.ORDER_TYPE_SELL
        }

        filling_map = {
            'IOC': mt5.ORDER_FILLING_IOC,
            'FOK': mt5.ORDER_FILLING_FOK,
            'RETURN': mt5.ORDER_FILLING_RETURN
        }

        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5SymbolNotFoundError(f"Failed to get tick for {symbol}")
            
        # Correctly mapping price for long/short
        price = tick.ask if order_type.upper() == 'BUY' else tick.bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type_map.get(order_type.upper()),
            "price": price,
            "sl": float(sl),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_map.get(type_filling.upper(), mt5.ORDER_FILLING_FOK),
        }
        
        if tp is not None:
            request["tp"] = float(tp)
            
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5OrderError(f"Order failed: {result.comment}", code=result.retcode)
        return result

    def send_pending_order(self, symbol: str, volume: float, order_type: str, price: float,
                          sl: float = None, tp: float = None, deviation: int = 20,
                          comment: str = '', magic: int = 0, type_filling: str = 'FOK',
                          type_time: str = 'GTC', expiration: datetime = None):
        mt5_connector.initialize()

        order_type_map = {
            'BUY_LIMIT': mt5.ORDER_TYPE_BUY_LIMIT,
            'SELL_LIMIT': mt5.ORDER_TYPE_SELL_LIMIT,
            'BUY_STOP': mt5.ORDER_TYPE_BUY_STOP,
            'SELL_STOP': mt5.ORDER_TYPE_SELL_STOP,
            'BUY_STOP_LIMIT': mt5.ORDER_TYPE_BUY_STOP_LIMIT,
            'SELL_STOP_LIMIT': mt5.ORDER_TYPE_SELL_STOP_LIMIT,
        }

        filling_map = {
            'IOC': mt5.ORDER_FILLING_IOC,
            'FOK': mt5.ORDER_FILLING_FOK,
            'RETURN': mt5.ORDER_FILLING_RETURN,
        }

        time_map = {
            'GTC': mt5.ORDER_TIME_GTC,
            'DAY': mt5.ORDER_TIME_DAY,
            'SPECIFIED': mt5.ORDER_TIME_SPECIFIED,
            'SPECIFIED_DAY': mt5.ORDER_TIME_SPECIFIED_DAY,
        }

        mt5_type = order_type_map.get(order_type.upper())
        if mt5_type is None:
            raise MT5OrderError(f"Invalid pending order type: {order_type}")

        mt5.symbol_select(symbol, True)

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5_type,
            "price": float(price),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": comment,
            "type_time": time_map.get(type_time.upper(), mt5.ORDER_TIME_GTC),
            "type_filling": filling_map.get(type_filling.upper(), mt5.ORDER_FILLING_FOK),
        }

        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        if expiration is not None:
            request["expiration"] = int(expiration.timestamp())

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5OrderError(f"Pending order failed: {result.comment}", code=result.retcode)
        return result

    def modify_pending_order(self, ticket: int, price: float, sl: float = None, tp: float = None,
                            type_time: str = None, expiration: datetime = None):
        mt5_connector.initialize()

        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            raise MT5OrderError(f"Pending order {ticket} not found")

        order = orders[0]

        time_map = {
            'GTC': mt5.ORDER_TIME_GTC,
            'DAY': mt5.ORDER_TIME_DAY,
            'SPECIFIED': mt5.ORDER_TIME_SPECIFIED,
            'SPECIFIED_DAY': mt5.ORDER_TIME_SPECIFIED_DAY,
        }

        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "symbol": order.symbol,
            "price": float(price),
            "type_time": time_map.get(type_time.upper(), order.type_time) if type_time else order.type_time,
        }

        if sl is not None:
            request["sl"] = float(sl)
        else:
            request["sl"] = float(order.sl)
        if tp is not None:
            request["tp"] = float(tp)
        else:
            request["tp"] = float(order.tp)
        if expiration is not None:
            request["expiration"] = int(expiration.timestamp())

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5OrderError(f"Modify pending order failed: {result.comment}", code=result.retcode)
        return result

    def cancel_order(self, ticket: int):
        mt5_connector.initialize()

        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5OrderError(f"Cancel order failed: {result.comment}", code=result.retcode)
        return result

    def modify_sl_tp(self, ticket: int, sl: float, tp: float = None):
        mt5_connector.initialize()
        
        position = mt5.positions_get(ticket=ticket)
        if not position:
            raise MT5OrderError(f"Position {ticket} not found")
            
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position[0].symbol,
            "position": ticket,
            "sl": float(sl),
        }
        
        if tp is not None:
            request["tp"] = float(tp)
        else:
            request["tp"] = float(position[0].tp)
            
        result = mt5.order_send(request)
        return result

    def close_position(self, ticket: int, volume: float = None, deviation: int = 20,
                       comment: str = '', type_filling: str = 'FOK'):
        mt5_connector.initialize()

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            raise MT5OrderError(f"Position {ticket} not found")

        pos = positions[0]
        close_volume = float(volume) if volume is not None else pos.volume
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        mt5.symbol_select(pos.symbol, True)
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        filling_map = {
            'IOC': mt5.ORDER_FILLING_IOC,
            'FOK': mt5.ORDER_FILLING_FOK,
            'RETURN': mt5.ORDER_FILLING_RETURN
        }

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": order_type,
            "price": price,
            "deviation": deviation,
            "magic": pos.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_map.get(type_filling.upper(), mt5.ORDER_FILLING_FOK),
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5OrderError(f"Close failed: {result.comment}", code=result.retcode)
        return result

    def get_positions(self, magic: int = None, symbol: str = None) -> List[Dict]:
        """Open positions, optionally only those carrying `magic`.

        `positions_get` accepts `symbol`, `group` or `ticket` — **not** `magic`.
        Passing it raises, so filtering by magic used to fail outright rather
        than return the wrong rows, which is at least the better failure. The
        filter is applied here instead.

        That filter is what lets a bot find its own trades and leave everything
        else alone, so it matters that it is exact: a magic of 0 is a real
        magic (it is what a hand-placed trade carries), and `if magic` would
        treat it as "no filter" and hand back the whole account.
        """
        mt5_connector.initialize()
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if positions is None:
            return []
        rows = [p._asdict() for p in positions]
        if magic is None:
            return rows
        return [row for row in rows if row.get("magic") == magic]

    def close_all_positions(self, order_type: str = "all", magic: Optional[int] = None, type_filling: str = 'FOK') -> List:
        mt5_connector.initialize()
        positions = self.get_positions(magic)
        results = []
        if not positions: return []

        for pos in positions:
            if order_type.upper() == 'BUY' and pos['type'] != mt5.ORDER_TYPE_BUY: continue
            if order_type.upper() == 'SELL' and pos['type'] != mt5.ORDER_TYPE_SELL: continue

            try:
                res = self.close_position(pos['ticket'], type_filling=type_filling)
                if res: results.append(res)
            except MT5OrderError as e:
                logger.error(f"Failed to close position {pos['ticket']}: {e}")
        return results

    def get_orders(self, symbol: str = None, ticket: int = None) -> List[Dict]:
        mt5_connector.initialize()
        if ticket:
            orders = mt5.orders_get(ticket=ticket)
        elif symbol:
            orders = mt5.orders_get(symbol=symbol)
        else:
            orders = mt5.orders_get()
        if orders is None: return []
        return [o._asdict() for o in orders]

    def get_orders_total(self) -> int:
        mt5_connector.initialize()
        return mt5.orders_total()

    def order_calc_margin(self, action: str, symbol: str, volume: float, price: float) -> Optional[float]:
        mt5_connector.initialize()
        action_map = {
            'BUY': mt5.ORDER_TYPE_BUY,
            'SELL': mt5.ORDER_TYPE_SELL,
        }
        mt5.symbol_select(symbol, True)
        result = mt5.order_calc_margin(action_map.get(action.upper(), mt5.ORDER_TYPE_BUY), symbol, volume, price)
        return result

    def order_calc_profit(self, action: str, symbol: str, volume: float, price_open: float, price_close: float) -> Optional[float]:
        mt5_connector.initialize()
        action_map = {
            'BUY': mt5.ORDER_TYPE_BUY,
            'SELL': mt5.ORDER_TYPE_SELL,
        }
        mt5.symbol_select(symbol, True)
        result = mt5.order_calc_profit(action_map.get(action.upper(), mt5.ORDER_TYPE_BUY), symbol, volume, price_open, price_close)
        return result

trade_service = TradeService()
