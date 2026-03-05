import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables (API Key)
load_dotenv()

class MT5ApiClient:
    def __init__(self, base_url="http://localhost:8000", api_key=None):
        self.base_url = base_url.rstrip('/')
        
        # Determine API key from args or environment
        self.api_key = api_key or os.getenv("API_KEY")
        if not self.api_key:
            print("WARNING: No predefined API_KEY found. Generating derived API key from API_KEY_SEED...")
            seed = os.getenv("API_KEY_SEED", "default-seed-for-dev")
            import hashlib
            self.api_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        })

    def _handle_response(self, response):
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error: {e}")
            print(f"Response: {response.text}")
            return None
        except json.JSONDecodeError:
            print(f"Failed to decode JSON from response: {response.text}")
            return None

    def check_health(self):
        """Check the API health (Unprotected endpoint)."""
        print("\n--- Checking API Health ---")
        url = f"{self.base_url}/health"
        # We don't need auth for health, but sending headers is fine
        response = self.session.get(url) 
        return self._handle_response(response)

    def get_all_symbols(self):
        """Fetch all available MT5 symbols (Protected endpoint)."""
        print("\n--- Fetching All Allowed Symbols ---")
        url = f"{self.base_url}/api/v1/symbols/"
        response = self.session.get(url)
        return self._handle_response(response)

    def get_symbol_info(self, symbol: str):
        """Fetch detailed information for a specific symbol."""
        print(f"\n--- Fetching Info for {symbol} ---")
        url = f"{self.base_url}/api/v1/symbols/{symbol}"
        response = self.session.get(url)
        return self._handle_response(response)
    
    def get_historical_rates(self, symbol: str, timeframe: str = "H1", count: int = 5):
        """Fetch historical rates (candles) for a symbol."""
        print(f"\n--- Fetching Last {count} {timeframe} Candles for {symbol} ---")
        url = f"{self.base_url}/api/v1/symbols/rates/pos"
        params = {
            "symbol": symbol,
            "timeframe": timeframe,
            "num_bars": count
        }
        response = self.session.get(url, params=params)
        return self._handle_response(response)

    def place_market_order(self, symbol: str, volume: float, order_type: str, sl: float, tp: float = None):
        """Place a market order (BUY or SELL)."""
        print(f"\n--- Placing {order_type} Order for {volume} {symbol} ---")
        url = f"{self.base_url}/api/v1/trading/order"
        payload = {
            "symbol": symbol,
            "volume": volume,
            "order_type": order_type,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
        }
        response = self.session.post(url, json=payload)
        return self._handle_response(response)

if __name__ == "__main__":
    # If testing locally against Docker, change base_url to "http://localhost:8000"
    client = MT5ApiClient(base_url="http://localhost:8000")

    # 1. Test Health
    health = client.check_health()
    print("Health Status:", json.dumps(health, indent=2) if health else "FAILED")

    # 2. Get all symbols available
    symbols = client.get_all_symbols()
    if symbols:
        print(f"Total symbols accessible: {len(symbols)}")
        print(f"First 10 symbols: {symbols[:10]}")
        
    # 3. Choose a common symbol to test market data
    test_symbol = "EURUSD"
    if symbols and test_symbol in symbols:
        
        # 4. Get detailed symbol info
        info = client.get_symbol_info(test_symbol)
        if info:
            print(f"Spread: {info.get('spread')}, Min Volume: {info.get('volume_min')}")

        # 5. Get recent price candles
        rates = client.get_historical_rates(test_symbol, timeframe="M15", count=3)
        if rates:
            for rate in rates:
                print(f"Time: {rate['time']}, Close: {rate['close']}")

    else:
        print(f"Symbol {test_symbol} is not available in the broker's list.")

    # WARNING: Do not uncomment the line below unless you are on a demo account!
    # client.place_market_order("EURUSD", 0.01, "BUY", sl=1.0000, tp=1.1000)
    print("\n--- Example Script Completed ---")
