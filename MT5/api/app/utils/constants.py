from app.services.terminal_transport import mt5
from enum import Enum

class MT5Timeframe(Enum):
    M1 = mt5.TIMEFRAME_M1
    M2 = mt5.TIMEFRAME_M2
    M3 = mt5.TIMEFRAME_M3
    M4 = mt5.TIMEFRAME_M4
    M5 = mt5.TIMEFRAME_M5
    M6 = mt5.TIMEFRAME_M6
    M10 = mt5.TIMEFRAME_M10
    M12 = mt5.TIMEFRAME_M12
    M15 = mt5.TIMEFRAME_M15
    M20 = mt5.TIMEFRAME_M20
    M30 = mt5.TIMEFRAME_M30
    H1 = mt5.TIMEFRAME_H1
    H2 = mt5.TIMEFRAME_H2
    H3 = mt5.TIMEFRAME_H3
    H4 = mt5.TIMEFRAME_H4
    H6 = mt5.TIMEFRAME_H6
    H8 = mt5.TIMEFRAME_H8
    H12 = mt5.TIMEFRAME_H12
    D1 = mt5.TIMEFRAME_D1
    W1 = mt5.TIMEFRAME_W1
    MN1 = mt5.TIMEFRAME_MN1

RETCODE_DESCRIPTIONS = {
    10004: "Requote",
    10006: "Request rejected",
    10007: "Request canceled by trader",
    10008: "Order placed",
    10009: "Request completed",
    10010: "Only part of the request was completed",
    10011: "Request processing error",
    10012: "Request canceled by timeout",
    10013: "Invalid request",
    10014: "Invalid volume in the request",
    10015: "Invalid price in the request",
    10016: "Invalid stops in the request",
    10017: "Trade is disabled",
    10018: "Market is closed",
    10019: "There is not enough money to complete the request",
    10020: "Prices changed",
    10021: "There are no quotes to process the request",
    10022: "Invalid order expiration date in the request",
    10023: "Order state changed",
    10024: "Too frequent requests",
    10025: "No changes in request",
    10026: "Autotrading disabled by server",
    10027: "Autotrading disabled by client terminal",
    10028: "Request locked for processing",
    10029: "Order or position frozen",
    10030: "Invalid order filling type",
    10031: "No connection with the trade server",
    10032: "Operation is allowed only for live accounts",
    10033: "The number of pending orders has reached the limit",
    10034: "The volume of orders and positions for the symbol has reached the limit",
    10035: "Incorrect or prohibited order type",
    10036: "Position with the specified POSITION_IDENTIFIER has already been closed",
    10038: "A close volume exceeds the current position volume",
    10039: "A close order already exists for a specified position",
    10040: "The number of open positions simultaneously present on an account can be limited by the server settings",
    10041: "The pending order activation request is rejected, the order is canceled",
    10042: "The request is rejected, because the 'Only long positions are allowed' rule is set for the symbol",
    10043: "The request is rejected, because the 'Only short positions are allowed' rule is set for the symbol",
    10044: "The request is rejected, because the 'Only position closing is allowed' rule is set for the symbol",
    10045: "The request is rejected, because 'Position closing is allowed only by FIFO rule' flag is set for the trading account",
}

METALS = ['XAUUSD', 'XAGUSD', 'XAUEUR']
OILS = ['BRN', 'NG', 'WTI']
CRYPTOCURRENCIES = [
    'BITCOIN', 'ETHEREUM', 'SOLANA', 'DOGECOIN', 'LITECOIN', 'RIPPLE', 'BNB', 
    'UNISWAP', 'AVALANCH', 'CARDANO', 'CHAINLINK', 'POLKADOT', 'POLYGON', 'COSMOS', 'AXS'
]
CURRENCY_PAIRS = [
    'USDJPY','USDCHF','USDCAD','EURUSD','EURGBP','EURJPY','EURCHF','EURCAD','EURAUD','EURNZD',
    'GBPUSD','GBPJPY','GBPCHF','GBPCAD','GBPAUD','GBPNZD','CHFJPY','CADJPY','CADCHF',
    'AUDUSD','AUDJPY','AUDCHF','AUDCAD','AUDNZD','NZDUSD','NZDJPY','NZDCHF','NZDCAD'
]

# Add more constants as needed
ORDER_FILLING_RETURN = mt5.ORDER_FILLING_RETURN

# Mapping dictionaries for user-friendly string responses
TRADE_ACTION_STR_MAP = {
    mt5.TRADE_ACTION_DEAL: "TRADE_ACTION_DEAL",
    mt5.TRADE_ACTION_PENDING: "TRADE_ACTION_PENDING",
    mt5.TRADE_ACTION_SLTP: "TRADE_ACTION_SLTP",
    mt5.TRADE_ACTION_MODIFY: "TRADE_ACTION_MODIFY",
    mt5.TRADE_ACTION_REMOVE: "TRADE_ACTION_REMOVE",
}

ORDER_TYPE_STR_MAP = {
    mt5.ORDER_TYPE_BUY: "ORDER_TYPE_BUY",
    mt5.ORDER_TYPE_SELL: "ORDER_TYPE_SELL",
    mt5.ORDER_TYPE_BUY_LIMIT: "ORDER_TYPE_BUY_LIMIT",
    mt5.ORDER_TYPE_SELL_LIMIT: "ORDER_TYPE_SELL_LIMIT",
    mt5.ORDER_TYPE_BUY_STOP: "ORDER_TYPE_BUY_STOP",
    mt5.ORDER_TYPE_SELL_STOP: "ORDER_TYPE_SELL_STOP",
}

ORDER_FILLING_STR_MAP = {
    mt5.ORDER_FILLING_FOK: "ORDER_FILLING_FOK",
    mt5.ORDER_FILLING_IOC: "ORDER_FILLING_IOC",
    mt5.ORDER_FILLING_RETURN: "ORDER_FILLING_RETURN",
}

TRAILING_STOP_STEPS = [
    {'trigger_pnl_multiplier': 4.00, 'new_sl_pnl_multiplier': 3.50},
    {'trigger_pnl_multiplier': 3.50, 'new_sl_pnl_multiplier': 3.00},
    {'trigger_pnl_multiplier': 3.00, 'new_sl_pnl_multiplier': 2.75},
    {'trigger_pnl_multiplier': 2.75, 'new_sl_pnl_multiplier': 2.50},
    {'trigger_pnl_multiplier': 2.50, 'new_sl_pnl_multiplier': 2.25},
    {'trigger_pnl_multiplier': 2.25, 'new_sl_pnl_multiplier': 2.00},
    {'trigger_pnl_multiplier': 2.00, 'new_sl_pnl_multiplier': 1.75},
    {'trigger_pnl_multiplier': 1.75, 'new_sl_pnl_multiplier': 1.50},
    {'trigger_pnl_multiplier': 1.50, 'new_sl_pnl_multiplier': 1.25},
    {'trigger_pnl_multiplier': 1.25, 'new_sl_pnl_multiplier': 1.00},
    {'trigger_pnl_multiplier': 1.00, 'new_sl_pnl_multiplier': 0.75},
    {'trigger_pnl_multiplier': 0.75, 'new_sl_pnl_multiplier': 0.45},
    {'trigger_pnl_multiplier': 0.50, 'new_sl_pnl_multiplier': 0.22},
    {'trigger_pnl_multiplier': 0.25, 'new_sl_pnl_multiplier': 0.12},
    {'trigger_pnl_multiplier': 0.12, 'new_sl_pnl_multiplier': 0.05},
    {'trigger_pnl_multiplier': 0.06, 'new_sl_pnl_multiplier': 0.025},
]

#: The longest `comment` the terminal will accept on an order request.
#:
#: **Measured against a live terminal on 2026-11-09, not read off a
#: reference.** A client sending 31 characters had every order refused with
#: `Invalid "comment" argument` and `mt5_code -2` - the `MetaTrader5` package
#: rejecting the request before it became an order, so no retcode, no deal and
#: nothing in the terminal to find. Swept at volume 0, where the answer flips
#: to `Invalid volume` as soon as the comment is acceptable:
#:
#:     31 -> Invalid "comment" argument
#:     30 -> Invalid "comment" argument
#:     28 -> Invalid volume   (i.e. the comment was accepted)
#:     25 -> Invalid volume
#:     20 -> Invalid volume
#:
#: 29 was not tested. 28 is the largest proven value and one more character is
#: not worth re-opening a fault that took two days to find, because the symptom
#: names the wrong field: the terminal blames `comment` only in `mt5_msg`, and
#: a caller reading the retcode alone sees `-2` and learns nothing.
#:
#: Declared as a request-validation limit rather than applied by truncating.
#: A comment is how a client correlates its own orders with the terminal's
#: deals, so silently shortening one would hand back a different identifier
#: than the caller sent; a 422 naming the field and the limit is something a
#: client can act on.
ORDER_COMMENT_MAX = 28

#: Retcodes a caller can usefully send the *same request* again for.
#:
#: **The distinction a client needs and could not make.** Every refusal is one
#: HTTP 400 with a number in it, so a client either retried everything - which
#: on `10019 not enough money` or `10018 market closed` is a loop that cannot
#: succeed - or retried nothing, which gives up on a requote. The set is
#: deliberately small: a code is in here only if the request is unchanged and
#: the condition is expected to clear by itself within seconds.
#:
#: `10027 Autotrading disabled by client terminal` is **not** here, although it
#: clears: it clears when somebody presses Ctrl+E, not with time, so a retry
#: loop on it is a busy-wait on a human. `/terminal/can-trade` is what a client
#: should read for that.
RETRYABLE_RETCODES = frozenset({
    10004,  # Requote
    10012,  # Request canceled by timeout
    10020,  # Prices changed
    10021,  # There are no quotes to process the request
    10024,  # Too frequent requests
    10028,  # Request locked for processing
    10029,  # Order or position frozen
    10031,  # No connection with the trade server
})
