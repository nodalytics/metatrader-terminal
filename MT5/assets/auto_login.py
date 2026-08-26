"""
VNC Client for MetaTrader 5 Docker Login

## Resources:
    - https://github.com/sibson/vncdotool/blob/main/docs/library.rst
"""

import os
import time
from vncdotool import api
from dotenv import load_dotenv


class VNClient:
    """
    A client to automate MetaTrader 5 login using VNC.

    Attributes:
        client (api.VNCDoToolClient): The VNC client connection object.
    """

    def __init__(self, server_url: str, password: str = None, timeout: int = 10):
        """
        Initialize the VNClient with the server URL and optional password.

        Args:
            server_url (str): The VNC server URL to connect to.
            password (str): The password for the VNC server (default is None).
            timeout (int): Timeout for the VNC client connection (default is 10 seconds).
        """
        self.client = api.connect(server_url, password=password)
        self.client.timeout = timeout

    def clear_and_type_value(self, value: str, empty_field: bool = False, next_field_count: int = 1):
        """
        Clears a form field, enters a new value, and optionally moves to the next field.

        Args:
            value (str): The string value to enter in the form field.
            empty_field (bool): Whether to empty the field before entering the value (default is False).
            next_field_count (int): The number of times to press 'Tab' to move to the next field (default is 1).
        """
        if empty_field:
            # Select all text and delete it
            self.client.keyPress('ctrl-A')
            self.client.keyPress('del')
            time.sleep(0.1)

        # Enter the new value with a slight delay between each character
        for character in value:
            self.client.keyPress(character)
            time.sleep(0.05)

        # Optionally, press Tab to move to the next field
        for _ in range(next_field_count):
            self.client.keyPress('tab')
            time.sleep(0.1)

        time.sleep(0.5)

    def wait_for_terminal_ready(self):
        """
        Waits for the MT5 terminal to finish loading before interacting.
        The terminal needs time to initialize the GUI after startup.
        """
        time.sleep(15)

    def dismiss_liveupdate(self):
        """
        Dismisses the LiveUpdate popup if present by clicking the 'Later'
        button. The popup typically appears 40-60s after terminal start,
        after the update has been downloaded. Must be called AFTER login
        to avoid blocking IPC with a modal dialog.
        """
        # LiveUpdate downloads take 40-60s from terminal start.
        # Login takes ~20s, so wait ~30s more for the popup to appear.
        time.sleep(30)

        # Click "Later" button directly (centered at ~689,454 in 1280x800)
        self.client.mouseMove(689, 454)
        self.client.mousePress(1)
        time.sleep(0.3)

        # Fallback: Tab from 'Restart' to 'Later', then press Enter
        self.client.keyPress('tab')
        time.sleep(0.1)
        self.client.keyPress('enter')
        time.sleep(1)

    def dismiss_popups(self):
        """
        Dismisses any remaining popups (Open Account, etc.)
        by pressing Escape and clicking known Cancel positions.
        """
        for _ in range(3):
            self.client.keyPress('esc')
            time.sleep(0.2)

        # Click Cancel on Open Account dialog if visible
        self.client.mouseMove(930, 645)
        self.client.mousePress(1)
        time.sleep(0.3)

    def ping_mt_server(self, server: str):
        """
        Ping MetaTrader's server by searching for the broker server in the list of company servers available.

        Args:
            server (str): The broker server name to search for.
        """
        time.sleep(2)

        # Click on the File tab
        self.client.mouseMove(20, 22)
        self.client.mousePress(1)
        time.sleep(0.3)

        # Click on Open an Account
        self.client.mouseMove(100, 310)
        self.client.mousePress(1)
        time.sleep(0.3)

        # Search for server in list of companies
        self.client.mouseMove(350, 250)
        self.client.mousePress(1)
        time.sleep(0.3)

        self.clear_and_type_value(server, empty_field=True)
        self.client.keyPress('enter')
        time.sleep(3)

        # Close Open Account dialog (Cancel button)
        self.client.mouseMove(930, 645)
        self.client.mousePress(1)
        time.sleep(0.3)

    def login_to_mt5(self, login: str, password: str, server: str):
        """
        Logs in to the MetaTrader 5 account.

        Args:
            login (str): The MetaTrader 5 account login number.
            password (str): The MetaTrader 5 account password.
            server (str): The MetaTrader 5 server name.
        """
        # Probe server
        self.ping_mt_server(server)
        time.sleep(0.5)

        # Click on the File tab
        self.client.mouseMove(20, 22)
        self.client.mousePress(1)
        time.sleep(0.3)

        # Click on Login to Trade Account
        self.client.mouseMove(100, 380)
        self.client.mousePress(1)

        # Fill the Login form
        self.clear_and_type_value(login)
        # Move to the next field
        self.clear_and_type_value(password, next_field_count=2)
        # Move to the next field
        self.clear_and_type_value(server, empty_field=True)

        time.sleep(0.2)
        self.client.keyPress('enter')
        time.sleep(5)

    def toggle_algo_trading(self):
        """Press Ctrl+E once. This is a **toggle**, not a switch."""
        self.client.keyDown('ctrl')
        self.client.keyPress('e')
        self.client.keyUp('ctrl')
        time.sleep(0.2)

    def enable_algo_trading(self, attempts: int = 3):
        """Leave algorithmic trading ON, whatever state it started in.

        Ctrl+E toggles. The previous version pressed it once and assumed the
        result, which is right exactly half the time: on a restart where MT5
        had restored AutoTrading as enabled, this script switched it **off**,
        and every order was then refused with `AutoTrading disabled by client`
        (10027) — an error naming the client that sent the order rather than
        the terminal that refused it.

        There is no GUI state to read back, so the terminal's own log is the
        authority. The subtlety is that the log is written **asynchronously**:
        a first attempt at this slept two seconds after toggling, read a line
        that had not been updated yet, believed AutoTrading was still off, and
        toggled a second time — on at 07:18:07, off again at 07:18:10.

        So the wait is for *evidence*, not for a duration. Count the transition
        lines before pressing, then wait for that count to rise. A new line is
        proof the terminal has acted; a timer is only a guess that it has.
        """
        for attempt in range(1, attempts + 1):
            count, state = self.algo_trading_log()
            if state is True:
                print(f"Algo trading is enabled (confirmed on attempt {attempt}).")
                return True

            print(f"Algo trading reads {state!r}; toggling (attempt {attempt}).")
            self.toggle_algo_trading()

            for _ in range(30):  # up to ~15s for the terminal to write it down
                time.sleep(0.5)
                fresh_count, fresh_state = self.algo_trading_log()
                if fresh_count > count:
                    state = fresh_state
                    break
            if state is True:
                print(f"Algo trading is enabled (confirmed on attempt {attempt}).")
                return True

        print("WARNING: could not confirm algo trading is enabled — orders will "
              "be refused with 'AutoTrading disabled by client' (10027).")
        return False

    @staticmethod
    def algo_trading_log():
        """(transitions logged, current state) from the terminal's own log.

        The count is what makes waiting reliable: it says whether the terminal
        has written anything *new*, which a timer cannot. State is True, False,
        or None when the terminal has not said either way yet.
        """
        import glob

        logs = sorted(
            glob.glob('/opt/wineprefix/drive_c/Metatrader-5/logs/*.log'),
            key=os.path.getmtime,
        )
        if not logs:
            return 0, None
        try:
            with open(logs[-1], 'rb') as handle:
                text = handle.read().decode('utf-16-le', errors='ignore')
        except OSError:
            return 0, None

        count, state = 0, None
        for line in text.splitlines():
            if 'automated trading is enabled' in line:
                count, state = count + 1, True
            elif 'automated trading is disabled' in line:
                count, state = count + 1, False
        return count, state

    def open_journal_tab(self):
        """
        Tries to open the Journal tab on MetaTrader 5 (optional).
        """
        # Try to open Journal tab (optional)
        self.client.mouseMove(690, 760)
        self.client.mousePress(1)
        time.sleep(0.2)

    def capture_screenshot(self, file_name: str = 'screenshot.png'):
        """
        Captures a screenshot of the current VNC screen.

        Args:
            file_name (str): The name of the file to save the screenshot (default is 'screenshot.png').
        """
        try:
            self.client.captureScreen(file_name)
        except TimeoutError:
            print('Timeout when capturing screen')

    def disconnect(self):
        """
        Disconnects the VNC client.
        """
        if self.client is not None:
            self.client.disconnect()
            self.client = None

    def _set_login_successful_env_var(self):
        """
        Sets an environment variable and marker file to indicate a successful login.
        The marker file is used by run-mt5.sh to know auto-login has completed.
        """
        os.environ['LOGIN_SUCCESSFUL'] = 'true'
        with open('/tmp/login_complete', 'w') as f:
            f.write('1')

    def verify_login(self, login: str, password: str, server: str, max_retries: int = 3) -> bool:
        """
        Verifies if the MetaTrader 5 login was successful.

        This method attempts to log in to the MetaTrader 5 server using the provided credentials.
        If the login is unsuccessful, it raises an exception with the error details.

        Args:
            login (str): The MetaTrader 5 account login number.
            password (str): The MetaTrader 5 account password.
            server (str): The MetaTrader 5 server name.
            max_retries (int): Maximum number of retry attempts (default is 3).

        Returns:
            bool: True if the login is successful, False otherwise.

        Raises:
            Exception: If the login fails, with the error code and description.
        """
        time.sleep(5)

        import MetaTrader5 as mt5

        # Attempt to establish a connection to the MetaTrader 5 terminal
        is_connected = mt5.initialize(
            "C:\\Metatrader-5\\terminal64.exe",
            login=int(login),
            password=password,
            server=server,
            timeout=5000,
            portable=True
        )

        if is_connected:
            print(f"Login successful: {is_connected}")
            self._set_login_successful_env_var()
            return True
        else:
            error_code, error_description = mt5.last_error()

            # Check for IPC timeout => -10005:
            if int(error_code) != mt5.RES_E_INTERNAL_FAIL_TIMEOUT:
                raise Exception(f"Login failed, error code = {error_code}, description = {error_description}")

            if max_retries <= 0:
                raise Exception(f"Login verification timed out after retries, error code = {error_code}, description = {error_description}")

            # Dismiss popups and retry
            self.dismiss_popups()
            self.ping_mt_server(server)
            time.sleep(0.5)
            return self.verify_login(login, password, server, max_retries=max_retries - 1)

def load_mt5_credentials():
    """
    Loads environment variables and retrieves MetaTrader 5 credentials.

    :return: A tuple containing the login, password, and server values.
    """
    # Load environment variables
    load_dotenv()

    # Retrieve MetaTrader 5 credentials from environment variables
    login = os.getenv('MT5_LOGIN')
    password = os.getenv('MT5_PASSWORD')
    server = os.getenv('MT5_SERVER')

    return login, password, server

def main():
    # Load and check MetaTrader 5 credentials
    login, password, server = load_mt5_credentials()

    # Ensure required environment variables are set
    while not all([login, password, server]):
        print("Required environment variables (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER) are not set.")
        time.sleep(5)  # Wait for 5 seconds before checking again
        # Reload credentials in case they are set during the loop
        login, password, server = load_mt5_credentials()

    VNC_SERVER_URL = os.getenv('VNC_SERVER_HOST', 'localhost')
    VNC_SERVER_PASSWORD = os.getenv('VNC_PASSWORD')

    # Create a VNClient instance
    vnc_mt5_client = VNClient(server_url=VNC_SERVER_URL, password=VNC_SERVER_PASSWORD)

    try:
        # Wait for the terminal GUI to be ready
        vnc_mt5_client.wait_for_terminal_ready()

        # Log in to MetaTrader 5
        vnc_mt5_client.login_to_mt5(login, password, server)

        # Dismiss any remaining popups
        vnc_mt5_client.dismiss_popups()

        # Enable algorithmic trading
        vnc_mt5_client.enable_algo_trading()

        # Open the Journal tab
        vnc_mt5_client.open_journal_tab()

        # Wait for and dismiss the LiveUpdate popup before creating
        # the marker. The popup is modal and blocks MT5 IPC, so it
        # must be dismissed before the API server can connect.
        vnc_mt5_client.dismiss_liveupdate()

        # Create marker file so the API server knows VNC login is done.
        # The server will call mt5.initialize() in a background thread.
        with open('/tmp/login_complete', 'w') as f:
            f.write('1')

        print("Auto-login sequence completed.")
    except Exception as e:
        print(f"An error occurred during auto-login: {e}")

    finally:
        # Disconnect the VNC client
        vnc_mt5_client.disconnect()


if __name__ == "__main__":
    main()
