import os
import mt5_mac as mt5

def find_terminal():
    wineprefix = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5")
    drive_c = os.path.join(wineprefix, "drive_c")
    found = []
    for root, dirs, files in os.walk(drive_c):
        for f in files:
            if f.lower() in ("terminal64.exe", "terminal.exe"):
                found.append(os.path.join(root, f))
    return found[0] if found else None

def find_config_ini():
    wineprefix = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5")
    drive_c = os.path.join(wineprefix, "drive_c")
    configs = []
    for root, dirs, files in os.walk(drive_c):
        for f in files:
            if f.lower() in ("common.ini", "terminal.ini"):
                configs.append(os.path.join(root, f))
    return configs

def main():
    terminal_path = find_terminal()
    print("Connecting to the local MT5 terminal...")
    try:
        # Convert path to Windows path inside Wine if found
        wine_terminal = None
        if terminal_path:
            wineprefix = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5")
            rel = os.path.relpath(terminal_path, os.path.join(wineprefix, "drive_c"))
            wine_terminal = "C:\\" + rel.replace("/", "\\")
            print("Windows terminal path:", wine_terminal)

        connected = mt5.initialize(path=wine_terminal) if wine_terminal else mt5.initialize()
        if not connected:
            print("INITIALIZE FAILED")
            print("Error:", mt5.last_error())
            return

        print("Terminal initialization succeeded.")

        account = mt5.account_info()
        if account is None:
            print("ACCOUNT INFO UNAVAILABLE")
            print("Error:", mt5.last_error())
            return

        # Show only non-secret verification details.
        print("\nACCOUNT CONNECTION VERIFIED")
        print("Login:", account.login)
        print("Server:", account.server)
        print("Company:", account.company)
        print("Currency:", account.currency)
        print("Trade mode:", account.trade_mode)
        print("Trade allowed (Broker side):", account.trade_allowed)
        print("Trade expert (Terminal AutoTrading/Algo Trading):", account.trade_expert)
        print("Connected account balance:", account.balance)

        if not account.trade_expert:
            print("\n⚠️ WARNING: 'trade_expert' is False!")
            print("To fix this, in MetaTrader 5 on your Mac:")
            print("1. Click the 'Algo Trading' button on the MT5 toolbar.")
            print("2. Or press Ctrl+O -> Expert Advisors tab -> Check 'Allow Algo Trading'.")
        else:
            print("\n✅ AutoTrading / trade_expert is ENABLED! Orders can be placed.")
            
        print("\nChecking symbols in MT5:")
        for s in ["XAUUSD", "XAUUSDm", "GOLD"]:
            info = mt5.symbol_info(s)
            tick = mt5.symbol_info_tick(s) if info else None
            print(f"Symbol {s}: exists={info is not None}, visible={getattr(info, 'visible', False)}, tick={tick}")

    except Exception as exc:
        print("CONNECTION TEST ERROR:", type(exc).__name__, str(exc))

    finally:
        try:
            mt5.shutdown()
            print("\nMT5 connection closed.")
        except Exception:
            pass

if __name__ == "__main__":
    main()
