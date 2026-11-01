//+------------------------------------------------------------------+
//|  TillTickFeed.mq5                                                |
//|  Pushes every tick out of the terminal, so the bridge stops      |
//|  having to ask.                                                  |
//+------------------------------------------------------------------+
//
// ## Why this exists
//
// `symbol_info_tick` is a question, not a subscription. The Python side has
// no callback to register, so the bridge's websocket polls the terminal on a
// timer and sends what changed - which means the rate the desk learns things
// is set by how often it asks rather than by how often they move. Between
// polls it is blind, and a quote that moved and moved back never happened as
// far as anything downstream is concerned.
//
// Inside the terminal that limitation does not exist. `OnTick` is a real
// callback: MT5 calls it when a tick arrives for the chart's symbol. So the
// push has to start here, and this is the smallest thing that does it.
//
// ## Plain TCP and newline-delimited JSON, not a websocket
//
// MQL5 can open a socket (`SocketConnect`) but has no websocket client, so a
// websocket would mean writing the HTTP upgrade and the frame masking by hand
// in MQL5 - a protocol implementation in the one language in this stack with
// no test framework, carrying the live tick feed. `WebRequest` is the other
// option and is worse: it is synchronous, so the terminal would block on an
// HTTP round trip inside `OnTick`, on every tick.
//
// One line of JSON per tick over a plain socket is the whole protocol. The
// bridge's listener reads lines; a partial line is held until its newline
// arrives. The websocket stays where it belongs - between the bridge and its
// consumers, which are Python and can speak it properly.
//
// ## One chart per symbol, which is not a workaround
//
// `OnTick` only fires for the chart's own symbol. Covering twenty symbols is
// twenty charts each running this EA - and the alternative, one EA calling
// `SymbolInfoTick` for others on `OnTimer`, is polling again with extra
// steps. So the EA is deliberately single-symbol and the deployment is a
// chart per feed.
//
// ## What is sent, and what is deliberately not interpreted
//
// `flags` goes out raw. MT5's tick flags carry an **undocumented bit 128**,
// so a one-sided revision is `flags in (4, 130)` and never `flags in (2, 4)`
// - reading it wrong under-reports one-sidedness as 10.0% where the truth is
// 22.8%. Any interpretation belongs on the Python side where it can be
// tested; this sends the number.
//
// `time_msc` goes out as the terminal's own integer, unconverted. It is
// broker server time, which is not UTC on any broker this desk trades, and
// the offset is the consumer's problem to measure - converting here would
// bake an unmeasured constant into the feed.
//
// `last`, `volume` and `volume_real` are sent and are known to be useless on
// this feed: an MT5 tick is a quote update, and they read 0 or a constant
// 5000. They are included so the consumer can confirm that rather than take
// it on trust.
//
// `seq` is a per-symbol counter that never skips. A consumer seeing a gap
// knows it lost frames and refills from `copy_ticks_range`, which is the only
// way a push feed can be made complete rather than merely fast.
//
// ## Setup
//
// Compile in MetaEditor, attach to one chart per symbol, and allow the
// listener's address in Tools -> Options -> Expert Advisors -> "Allow
// WebRequest for listed URL" (MT5 checks that list for `SocketConnect` too).
// `127.0.0.1` is the expected value: the bridge runs in the same container.
//
//+------------------------------------------------------------------+

#property copyright "nodalytics"
#property version   "1.00"
#property strict
#property description "Pushes every tick to the local bridge over a plain TCP socket."

input string Host          = "127.0.0.1";  // Listener host. Loopback: the bridge is in this container.
input int    Port          = 18823;        // Listener port. Must match EA_FEED_PORT.
input int    ConnectMs     = 2000;         // Socket connect timeout, milliseconds.
input int    RetryMs       = 1000;         // How often to retry while disconnected.
input bool   Verbose       = false;        // Log every send. For bring-up only.

int    socket      = INVALID_HANDLE;
ulong  sequence    = 0;
long   sent        = 0;
long   dropped     = 0;
bool   warned      = false;

//+------------------------------------------------------------------+
int OnInit()
{
   // A millisecond timer rather than a second one: a disconnected EA should
   // reconnect inside a second, because every tick while it is down is a
   // tick the consumer has to refill from history.
   EventSetMillisecondTimer(RetryMs);
   Connect();
   PrintFormat("TillTickFeed: %s -> %s:%d", _Symbol, Host, Port);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Disconnect();
   PrintFormat("TillTickFeed: %s stopping (reason %d); sent %I64d, dropped %I64d",
               _Symbol, reason, sent, dropped);
}

//+------------------------------------------------------------------+
//| Reconnect when the socket is down. Never sends from here: a timer |
//| that also sent would duplicate the current tick on every retry.   |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(socket == INVALID_HANDLE || !SocketIsConnected(socket))
      Connect();
}

//+------------------------------------------------------------------+
void OnTick()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   // The sequence advances for every tick the terminal gave us, whether or
   // not it reached the bridge. That is the point of it: a consumer seeing a
   // gap learns that frames were lost, and a counter that only advanced on
   // success would hide exactly the loss it exists to report.
   sequence++;

   if(socket == INVALID_HANDLE || !SocketIsConnected(socket))
   {
      dropped++;
      return;
   }

   string line = Frame(tick);
   uchar  buffer[];
   // `StringLen` rather than `WHOLE_ARRAY`: the latter appends a terminating
   // zero byte, which would put a NUL inside the line protocol.
   int    size = StringToCharArray(line, buffer, 0, StringLen(line), CP_UTF8);
   if(size <= 0)
      return;

   if(SocketSend(socket, buffer, size) != size)
   {
      // A short write means the socket is going away. Dropped rather than
      // retried: a retry loop inside `OnTick` blocks the terminal, and the
      // gap in `seq` already tells the consumer to refill.
      dropped++;
      Disconnect();
      return;
   }

   sent++;
   if(Verbose)
      Print("TillTickFeed: ", line);
}

//+------------------------------------------------------------------+
//| One tick as one line of JSON. Hand-built because MQL5 has no JSON |
//| writer and the shape is four numbers and a name.                  |
//+------------------------------------------------------------------+
string Frame(const MqlTick &tick)
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   return StringFormat(
      "{\"type\":\"tick\",\"symbol\":\"%s\",\"bid\":%s,\"ask\":%s,\"last\":%s,"
      "\"volume\":%I64d,\"volume_real\":%s,\"time_msc\":%I64d,\"flags\":%d,"
      "\"seq\":%I64u,\"source\":\"ea\"}\n",
      _Symbol,
      DoubleToString(tick.bid, digits),
      DoubleToString(tick.ask, digits),
      DoubleToString(tick.last, digits),
      tick.volume,
      DoubleToString(tick.volume_real, 2),
      tick.time_msc,
      (int)tick.flags,
      sequence);
}

//+------------------------------------------------------------------+
void Connect()
{
   Disconnect();
   socket = SocketCreate();
   if(socket == INVALID_HANDLE)
   {
      Warn("SocketCreate failed - is the terminal allowed to open sockets?");
      return;
   }
   if(!SocketConnect(socket, Host, Port, ConnectMs))
   {
      // The usual cause is the address not being in Tools -> Options ->
      // Expert Advisors, which MT5 checks for `SocketConnect` as well as for
      // `WebRequest`. Said once rather than every retry.
      Warn(StringFormat("cannot reach %s:%d - error %d. Is the address allowed "
                        "in Options -> Expert Advisors, and is the listener up?",
                        Host, Port, GetLastError()));
      Disconnect();
      return;
   }
   warned = false;
   PrintFormat("TillTickFeed: %s connected to %s:%d", _Symbol, Host, Port);
}

//+------------------------------------------------------------------+
void Disconnect()
{
   if(socket != INVALID_HANDLE)
   {
      SocketClose(socket);
      socket = INVALID_HANDLE;
   }
}

//+------------------------------------------------------------------+
//| Once per outage, not once per retry. A terminal log filling at the |
//| retry interval is a log nobody reads on the day it matters.        |
//+------------------------------------------------------------------+
void Warn(const string message)
{
   if(warned)
      return;
   warned = true;
   Print("TillTickFeed: ", message);
}
//+------------------------------------------------------------------+
