# tasks/trading_tasks.py
from celery import Celery
from logger_util import push_log
import json, datetime, time, gc
from zoneinfo import ZoneInfo
# ---- import your broker and helper modules ----
import Upstox as us
import Zerodha as zr
import AngelOne as ar
import Groww as gr
import Fivepaisa as fp
import Next_Now_intervals as nni
import combinding_dataframes as cdf
import indicators as ind
from time import sleep as gsleep
import redis
import os

# ---- Celery setup ----
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "astavyuha_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL"
)


r = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

stock_map = {
        "RELIANCE INDUSTRIES LTD": "RELIANCE",
        "HDFC BANK LTD": "HDFCBANK",
        "ICICI BANK LTD.": "ICICIBANK",
        "INFOSYS LIMITED": "INFY",
        "TATA CONSULTANCY SERV LT": "TCS",
        "STATE BANK OF INDIA": "SBIN",
        "AXIS BANK LTD": "AXISBANK",
        "KOTAK MAHINDRA BANK LTD": "KOTAKBANK",
        "ITC LTD": "ITC",
        "LARSEN & TOUBRO LTD.": "LT",
        "BAJAJ FINANCE LIMITED": "BAJFINANCE",
        "HINDUSTAN UNILEVER LTD": "HINDUNILVR",
        "SUN PHARMACEUTICAL IND L": "SUNPHARMA",
        "MARUTI SUZUKI INDIA LTD": "MARUTI",
        "NTPC LTD": "NTPC",
        "HCL TECHNOLOGIES LTD": "HCLTECH",
        "ULTRATECH CEMENT LIMITED": "ULTRACEMCO",
        "TATA MOTORS LIMITED": "TATAMOTORS",
        "TITAN COMPANY LIMITED": "TITAN",
        "BHARAT ELECTRONICS LTD": "BEL",
        "POWER GRID CORP. LTD": "POWERGRID",
        "TATA STEEL LIMITED": "TATASTEEL",
        "TRENT LTD": "TRENT",
        "ASIAN PAINTS LIMITED": "ASIANPAINT",
        "JIO FIN SERVICES LTD": "JIOFIN",
        "BAJAJ FINSERV LTD": "BAJAJFINSV",
        "GRASIM INDUSTRIES LTD": "GRASIM",
        "ADANI PORT & SEZ LTD": "ADANIPORTS",
        "JSW STEEL LIMITED": "JSWSTEEL",
        "HINDALCO INDUSTRIES LTD": "HINDALCO",
        "OIL AND NATURAL GAS CORP": "ONGC",
        "TECH MAHINDRA LIMITED": "TECHM",
        "BAJAJ AUTO LIMITED": "BAJAJ-AUTO",
        "SHRIRAM FINANCE LIMITED": "SHRIRAMFIN",
        "CIPLA LTD": "CIPLA",
        "COAL INDIA LTD": "COALINDIA",
        "SBI LIFE INSURANCE CO LTD": "SBILIFE",
        "HDFC LIFE INS CO LTD": "HDFCLIFE",
        "NESTLE INDIA LIMITED": "NESTLEIND",
        "DR. REDDY S LABORATORIES": "DRREDDY",
        "APOLLO HOSPITALS ENTER. L": "APOLLOHOSP",
        "EICHER MOTORS LTD": "EICHERMOT",
        "WIPRO LTD": "WIPRO",
        "TATA CONSUMER PRODUCT LTD": "TATACONSUM",
        "ADANI ENTERPRISES LIMITED": "ADANIENT",
        "HERO MOTOCORP LIMITED": "HEROMOTOCO",
        "INDUSIND BANK LIMITED": "INDUSINDBK",
        "Nifty 50": "NIFTY",
        "Nifty Bank": "BANKNIFTY",
        "Nifty Fin Service": "FINNIFTY",
        "NIFTY MID SELECT": "MIDCPNIFTY",
    }
# keep same broker maps
broker_map = {
    "u": "Upstox",
    "z": "Zerodha",
    "a": "AngelOne",
    "g": "Groww",
    "5": "5paisa"
}

celery_app.conf.task_track_started = True
celery_app.conf.worker_concurrency = 4

# ---- shared global maps ----
active_trades = {}
broker_map = {"u": "upstox", "z": "zerodha", "a": "angelone", "f": "5paisa", "g": "groww"}
reverse_stock_map = {}   # optional - fill if you have mapping


@celery_app.task(bind=True, name="tasks.trading_tasks.start_trading_loop")
def start_trading_loop(self):
    """
    Celery entrypoint for executing the full trading logic.
    Reads config file saved by FastAPI and runs trading loop.
    """
    push_log("🚀 Trading engine started inside Celery worker.")
    try:
        with open("trading_config.json", "r") as f:
            config = json.load(f)
        trading_parameters = config.get("tradingParameters", [])
        selected_brokers = config.get("selectedBrokers", [])
    except Exception as e:
        push_log(f"❌ Could not read trading_config.json: {e}", "error")
        return

    try:
        run_trading_logic_for_all(trading_parameters, selected_brokers)
    except Exception as e:
        push_log(f"💥 Trading loop crashed: {e}", "error")
        return

    push_log("✅ Trading engine task finished successfully.")


def run_trading_logic_for_all(trading_parameters, selected_brokers):
    """Your full trading logic integrated to Celery context."""
    import gc

    push_log("✅ Trading loop started for all selected stocks")
    push_log("⏳ Starting trading cycle setup...")
    # Activate trades
    for stock in trading_parameters:
        active_trades[stock['symbol_value']] = True

    # STEP 1: Fetch instrument keys
    for stock in trading_parameters:
        if not active_trades.get(stock['symbol_value']):
            continue

        broker_key = stock.get('broker')
        broker_name = broker_map.get(broker_key, "unknown")
        symbol = stock.get('symbol_value')
        name = stock.get('symbol_key')
        strategy = stock.get('strategy')
        company = stock.get("symbol_key", symbol)
        interval = stock.get('interval')
        exchange_type = stock.get('type')

        push_log(f"🔑 Fetching instrument key for company : {company}, Name : {name} symbol :{symbol} via Broker : {broker_name}...")

        instrument_key = None
        try:
            if exchange_type == "EQUITY":
                if broker_name == "upstox":
                    instrument_key = us.upstox_equity_instrument_key(company)
                elif broker_name == "zerodha":
                    broker_info = next((b for b in selected_brokers if b['name'] == broker_key), None)
                    if broker_info:
                        api_key = broker_info['credentials'].get("api_key")
                        access_token = broker_info['credentials'].get("access_token")
                        instrument_key = zr.zerodha_instruments_token(api_key, access_token, symbol)
                elif broker_name == "angelone":
                    instrument_key = ar.angelone_get_token_by_name(symbol)
                elif broker_name == "5paisa":
                    instrument_key = fp.fivepaisa_scripcode_fetch(symbol)

            elif exchange_type == "COMMODITY" and broker_name == "upstox":
                matched = us.upstox_commodity_instrument_key(name, symbol)
                instrument_key = matched['instrument_key'].iloc[0]

            if instrument_key:
                stock['instrument_key'] = instrument_key
                push_log(f"✅ Found instrument key {instrument_key} for {symbol}")
            else:
                push_log(f"⚠️ No instrument key found for {symbol}, skipping.", "warning")
                active_trades[symbol] = False

        except Exception as e:
            push_log(f"❌ Error fetching instrument key for {symbol}: {e}", "error")
            active_trades[symbol] = False

    # STEP 2: Interval handling setup
    interval = trading_parameters[0].get("interval", "1minute")
    now_interval, next_interval = nni.round_to_next_interval(interval)
    push_log(f"🕓 Present Interval Start: {now_interval}, Next Interval: {next_interval}")

    r = redis.StrictRedis(host="localhost", port=6379, db=5, decode_responses=True)

    # STEP 2.5: Initialize active trades in Redis safely
    symbols = [s["symbol_value"] for s in trading_parameters if s.get("symbol_value")]
    if not symbols:
        push_log("⚠️ No valid symbols to start trading. Exiting.", "warning")
        return

    r.delete("active_trades")
    r.sadd("active_trades", *symbols)
    push_log(f"🟢 Active trades initialized in Redis: {', '.join(symbols)}")

    # Small buffer for Redis sync
    time.sleep(0.5)
    # STEP 3: Core trading loop
    while True:
        active_symbols = set(r.smembers("active_trades"))

        # remove any symbols no longer active
        trading_parameters = [s for s in trading_parameters if s["symbol_value"] in active_symbols]

        if not trading_parameters:
            push_log("🏁 All trades stopped — exiting trading loop.")
            break

        for stock in trading_parameters:
            symbol = stock["symbol_value"]

            # ✅ Skip if symbol was disconnected mid-loop
            if symbol not in active_symbols:
                push_log(f"🛑 Skipping {symbol} — disconnected by user.")
                continue

        now = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
        if now >= next_interval:
            now_interval, next_interval = nni.round_to_next_interval(interval)
            push_log(f"⏱ New interval reached: {now_interval}")

            # Fetch candles for each stock
            for stock in trading_parameters:
                symbol = stock.get('symbol_value')
                broker_key = stock.get('broker')
                broker_name = broker_map.get(broker_key)
                company = stock.get('symbol_key')
                interval = stock.get('interval')
                instrument_key = stock.get('instrument_key')
                strategy = stock.get('strategy')
                exchange_type = stock.get('type')
                tick_size = stock.get('tick_size')

                push_log(f"🕯 Fetching candles for {symbol}-{company} from {broker_name}")

                combined_df = None
                try:
                    if broker_name == "upstox":
                        access_token = next(
                            (b['credentials']['access_token'] for b in selected_brokers if b['name'] == broker_key),
                            None
                        )
                        if access_token:
                            hdf = us.upstox_fetch_historical_data_with_retry(access_token, instrument_key, interval)
                            idf = us.upstox_fetch_intraday_data(access_token, instrument_key, interval)
                            if hdf is not None and idf is not None:
                                combined_df = cdf.combinding_dataframes(hdf, idf)

                    elif broker_name == "zerodha":
                        broker_info = next((b for b in selected_brokers if b['name'] == broker_key), None)
                        if broker_info:
                            kite = zr.kite_connect_from_credentials(broker_info['credentials'])
                            hdf = zr.zerodha_historical_data(kite, instrument_key, interval)
                            idf = zr.zerodha_intraday_data(kite, instrument_key, interval)
                            if hdf is not None and idf is not None:
                                combined_df = cdf.combinding_dataframes(hdf, idf)

                    elif broker_name == "angelone":
                        broker_info = next((b for b in selected_brokers if b['name'] == broker_key), None)
                        if broker_info:
                            api_key = broker_info['credentials'].get("api_key")
                            user_id = broker_info['credentials'].get("user_id")
                            pin = broker_info['credentials'].get("pin")
                            totp_secret = broker_info['credentials'].get("totp_secret")
                            session = ar.angelone_get_session(api_key, user_id, pin, totp_secret)
                            auth_token = session["auth_token"]
                            interval = ar.number_to_interval(interval)
                            combined_df = ar.angelone_get_historical_data(api_key, auth_token, session["obj"], "NSE",
                                                                          instrument_key, interval)

                    elif broker_name == "5paisa":
                        broker_info = next((b for b in selected_brokers if b['name'] == broker_key), None)
                        if broker_info:
                            access_token = broker_info['credentials'].get("access_token")
                            combined_df = fp.fivepaisa_historical_data_fetch(access_token, instrument_key, interval, 25)

                except Exception as e:
                    push_log(f"❌ Error fetching data for {symbol}: {e}", "error")
                    continue

                if combined_df is None or combined_df.empty:
                    push_log(f"⚠️ No data returned for {symbol}, skipping.", "warning")
                    continue

                push_log(f"✅ Data ready for {symbol}")
                indicators_df = ind.all_indicators(combined_df, strategy)
                row = indicators_df.tail(1).iloc[0]

                # Logging summary row
                cols = indicators_df.columns.tolist()
                formatted = " | ".join([f"{c}:{row[c]}" for c in cols])
                push_log(f"📊 Indicators: {formatted}")

                # STEP 4: Check trade conditions
                try:
                    creds = next((b["credentials"] for b in selected_brokers if b["name"] == broker_key), None)
                    lots = stock.get("lots")
                    target_pct = stock.get("target_percentage")

                    if broker_name == "upstox":
                        us.upstox_trade_conditions_check(lots, target_pct, indicators_df.tail(1), creds, company,
                                                         symbol, exchange_type, strategy)
                    elif broker_name == "zerodha":
                        zr.zerodha_trade_conditions_check(lots, target_pct, indicators_df.tail(1), creds, symbol,
                                                          strategy)
                    elif broker_name == "angelone":
                        ar.angelone_trade_conditions_check(session["obj"], auth_token, lots, target_pct,
                                                           indicators_df, creds, symbol, strategy)
                    elif broker_name == "5paisa":
                        fp.fivepaisa_trade_conditions_check(lots, target_pct, indicators_df, creds, stock, strategy)

                except Exception as e:
                    push_log(f"❌ Error executing trade for {symbol}: {e}", "error")

                del combined_df
                del indicators_df
                gc.collect()

            push_log(f"✅ Trading cycle completed at {now_interval}")
            push_log(f"⏳ Waiting for next interval at {next_interval}...")
            # No time.sleep — wait naturally until interval updates
            gsleep(1)

    push_log("🏁 All active trades ended. Exiting trading loop.")
    gc.collect()
