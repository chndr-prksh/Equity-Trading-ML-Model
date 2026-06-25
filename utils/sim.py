import pandas as pd
import mysql.connector
from sqlalchemy import create_engine
from tqdm import tqdm
import pandas_ta as ta
import numpy as np
import logging
import xgboost
import json
import joblib
import uuid
import warnings
warnings.filterwarnings("ignore")

def load_json_file(file_path):
    with open(file_path, 'r') as file:
        data = json.load(file)
    return data

def save_json_file(file_path, data):
    with open(file_path, 'w') as file:
        json.dump(data, file, indent=2)

def update_value(json_data, key, value):
    if key in json_data:
        json_data[key] = value
    else:
        for k, v in json_data.items():
            if isinstance(v, dict):
                update_value(v, key, value)

def edit_json_key(file_path, key, value):
    json_data = load_json_file(file_path)
    update_value(json_data, key, value)
    save_json_file(file_path, json_data)

def get_past_data(today, engine):
    day_data = pd.read_sql("SELECT * from PAST_DATA", engine)
    return day_data
    if not(validate_past_dates(day_data, today)):
        return day_data
    else:
        raise Exception(f"Date {today} already exists in dataset")

def get_current_trades():
    return {}

def add_amount_while_seeling(tick, buy_price, sell_price, n_share, STOCK_AMOUNT_TRACKER):
    current_balance = load_json_file(STOCK_AMOUNT_TRACKER)[tick]
    gain_balance = sell_price  * n_share
    edit_json_key(STOCK_AMOUNT_TRACKER, tick, current_balance + gain_balance)

def get_recommendations(todays_trades, day_data, today,
             OPEN_TRADES, CLOSED_TRADES, STOCK_AMOUNT_TRACKER):
    open_trades = OPEN_TRADES.copy()
    closed_trades_today = []
    for k, o_trades in open_trades.items():
        stock_day_data = day_data[day_data['Ticker'] == o_trades['tick']]
        today_stock_data = stock_day_data[stock_day_data['Date'] == today].reset_index(drop = True)
#         print(today_stock_data['Open'], today_stock_data['High'], today_stock_data['Low'], today_stock_data['Close'])
        today_stock_data = today_stock_data.T.to_dict()[0]
        cond1 = today_stock_data['High'] >= o_trades['target']
        cond2 = (stock_day_data['Date'] > o_trades['buy_date']).sum() < 5
        if today_stock_data['Open'] <= o_trades['stop_loss']:
            # print("sell due to SL hit")
            o_trades['sell_date'] = today
            o_trades['sell_price'] = today_stock_data['Open']
            add_amount_while_seeling(o_trades['tick'], o_trades['buy_price'], o_trades['sell_price'], o_trades['n_share'], STOCK_AMOUNT_TRACKER)
            o_trades['type'] = "CLOSED_TARGET_GapDownOpening" if o_trades['type'] == 'HOLD_TARGET_SL' else "CLOSED_SL_GapDownOpening"
            o_trades['balance'] = load_json_file(STOCK_AMOUNT_TRACKER)[o_trades['tick']]
            o_trades['sell_day_close'] = today_stock_data['Close']
            o_trades['sell_day_open'] = today_stock_data['Open']
            CLOSED_TRADES[k] = o_trades
            del OPEN_TRADES[k]
            
        elif cond1 & cond2:
            OPEN_TRADES[k]['stop_loss'] = o_trades['target']
            o_trades['type'] = "HOLD_TARGET_SL"
        
        elif today_stock_data['Low'] <= o_trades['stop_loss']:
            # print("sell due to SL hit 2")
            o_trades['sell_date'] = today
            o_trades['sell_price'] = o_trades['stop_loss']
            add_amount_while_seeling(o_trades['tick'], o_trades['buy_price'], o_trades['sell_price'], o_trades['n_share'], STOCK_AMOUNT_TRACKER)
            o_trades['type'] = "CLOSED_TARGET" if o_trades['type'] == 'HOLD_TARGET_SL' else "CLOSED_SL"
            o_trades['balance'] = load_json_file(STOCK_AMOUNT_TRACKER)[o_trades['tick']]
            o_trades['sell_day_close'] = today_stock_data['Close']
            o_trades['sell_day_open'] = today_stock_data['Open']
            CLOSED_TRADES[k] = o_trades
            del OPEN_TRADES[k]
        
        elif (stock_day_data['Date'] > o_trades['buy_date']).sum() >= 5: 
            # print("Entering")
            if o_trades['tick'] in [x['Ticker'] for x in todays_trades]:
                target_trade = [x for x in todays_trades if x['Ticker'] == o_trades['tick']][0]
                o_trades['target'] = target_trade['TARGET']
                o_trades['buy_streak'] += 1
                o_trades['buy_date'] = target_trade['Date'].strftime("%Y-%m-%d")
                o_trades['type'] = "OPEN_Streaked"
            else:
                o_trades['sell_date'] = today
                o_trades['sell_price'] = today_stock_data['Close']
                add_amount_while_seeling(o_trades['tick'], o_trades['buy_price'], o_trades['sell_price'], o_trades['n_share'], STOCK_AMOUNT_TRACKER)
                o_trades['type'] = "CLOSED_HOLDING_PERIOD_OFF"
                o_trades['balance'] = load_json_file(STOCK_AMOUNT_TRACKER)[o_trades['tick']]
                CLOSED_TRADES[k] = o_trades
                del OPEN_TRADES[k]       
        else:
            continue
    trades_to_be_executed = {}
    open_trades_list = [x['tick'] for x in open_trades.values()]
    for trades in todays_trades:
        if trades['Ticker'] in open_trades_list:
            continue
        else:
            available_amount = load_json_file(STOCK_AMOUNT_TRACKER)[trades['Ticker']]
            incoming_trade = {}
            incoming_trade['tick'] = trades['Ticker']
            incoming_trade['buy_price'] = trades['Close']
            incoming_trade['stop_loss'] = trades['SL']
            incoming_trade['target'] = trades['TARGET']
            incoming_trade['buy_date'] = trades['Date'].strftime("%Y-%m-%d")
            incoming_trade['o_buy_date'] = trades['Date'].strftime("%Y-%m-%d")
            incoming_trade['n_share'] = int(available_amount / incoming_trade['buy_price'])
            incoming_trade['buy_streak'] = 1
            rounding_left_amount = available_amount - incoming_trade['buy_price'] * incoming_trade['n_share']
            edit_json_key(STOCK_AMOUNT_TRACKER, trades['Ticker'], rounding_left_amount)
            incoming_trade['type'] = "OPEN"
#             logging.info(f"Buy {incoming_trade['tick']} at {incoming_trade['buy_price']}")
            trades_to_be_executed[str(uuid.uuid4())] = incoming_trade

    OPEN_TRADES.update(trades_to_be_executed)
    
    return len(OPEN_TRADES)

def load_data_daily(DAILY_DATA_PATH, date):
    nifty_500 = pd.read_csv("ind_nifty500list_new.csv")
    data = pd.read_csv(DAILY_DATA_PATH)
    data = data.rename(lambda x : x.lower(), axis = 1)
    if 'date' in data.columns:
        data = data[['symbol', 'date', 'open', 'high', 'low', 'close']]
        data.columns = ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close']
        data = data[data['Ticker'].isin(nifty_500['Symbol'].unique())].reset_index(drop = True)
        data['Date'] = date
    else:
        data = data[['symbol', 'series', 'open', 'high', 'low', 'close']]
        data.columns = ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close']
        data = data[data['Ticker'].isin(nifty_500['Symbol'].unique())].reset_index(drop = True)
#         data['Date'] = pd.to_datetime(data['Date'], format = "%d-%m-%Y").astype('str')
        data['Date'] = date
    return data