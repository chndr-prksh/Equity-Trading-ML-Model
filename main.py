import pandas as pd
import mysql.connector
from sqlalchemy import create_engine
from datetime import date, timedelta
from tqdm import tqdm
import pandas_ta as ta
import numpy as np
import logging
import xgboost
import json
import joblib
import uuid
import warnings
import os
from dotenv import load_dotenv
warnings.filterwarnings("ignore")

load_dotenv()

from utils.sim import *
from utils.ta import *

table_name = 'PAST_DATA'
STOCK_AMOUNT_TRACKER = "tracker_2023.json"
metrics_df = {}

x = [
 'rolling_change_mean_10',
 'rolling_change_mean_15',
 'rolling_change_mean_20',
 'SMA_10',
 'SMA_15',
 'SMA_20',
 'SMA_25',
 'SMA_30',
 'RSI_7',
 'RSI_14',
 'BBL_30_2.0',
 'BBM_30_2.0',
 'BBU_30_2.0',
 'BBB_30_2.0',
 'BBP_30_2.0'
    ]

logging.basicConfig(
     level=logging.INFO, 
     format= '[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
     datefmt='%H:%M:%S'
 )

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "STOCK_LAMBDA")

cnx = mysql.connector.connect(user=DB_USER, password=DB_PASSWORD, host=DB_HOST)
cursor = cnx.cursor()
cursor.execute(f"USE {DB_NAME}")

engine = create_engine(f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")



def get_return(CLOSED_TRADES):
    trades_closed = pd.DataFrame(CLOSED_TRADES).T
    if len(trades_closed) == 0:
        return np.nan, np.nan
    trades_closed = trades_closed.sort_values(by = ['tick', 'sell_date'])
    port_folio_sum = trades_closed.groupby('tick').apply(lambda x : x['balance'].iloc[-1]).sum()
    invested = trades_closed['tick'].nunique() * 100000
    return_perc = ((port_folio_sum - invested) / invested) * 100
    trades_closed['return'] = trades_closed['sell_price'] / trades_closed['buy_price']
    hit = (trades_closed['return'] > 1).mean()
    return return_perc, hit

def run_for_a_day(scoring_date, OPEN_TRADES, CLOSED_TRADES):
    print(f"Starting for date {scoring_date}")
    TODAY = scoring_date
    data = get_past_data(TODAY, engine)
    file_name = TODAY.replace("-", "")
    todays_data = load_data_daily(f"day_by_day_2022/{file_name}_NSE.csv", TODAY)
    todays_data['Date'] = pd.to_datetime(todays_data['Date'])
    data_including_today = pd.concat([data, todays_data])
    data_including_today['Date'] = pd.to_datetime(data_including_today['Date'], dayfirst = True)
    
    # data_including_today = data_including_today[data_including_today['Ticker'] == 'ANGELONE']
    
    feature_engineered = create_features(data_including_today)
    scoring_frame =  feature_engineered[feature_engineered['Date'] == TODAY]
    
    model_dict = joblib.load('models/2023_0.5.pkl')
    model = model_dict['model']
    t_s = model_dict['top_stock']
    predictions = model.predict_proba(scoring_frame[x])
    scoring_frame['predictions'] = predictions[:, 1]
    scoring_frame['buy_indication'] = scoring_frame['predictions'] > 0.5
    
    trade_frame = scoring_frame[['Date', 'Ticker', 'Close', 'predictions', 'buy_indication']]
    trade_frame = trade_frame[trade_frame['buy_indication'] == 1]
    trade_frame = trade_frame[trade_frame['Ticker'].isin(t_s)]
    trade_frame['SL'] = trade_frame['Close'] * 0.9
    trade_frame['TARGET'] = trade_frame['Close'] * 1.03
    
    trade_frame = trade_frame.reset_index(drop = True)
    todays_trades = trade_frame.T.to_dict().values()
    
    
    todays_data.to_sql(table_name, engine, if_exists='append', index=False)
    
    get_recommendations(todays_trades, data_including_today, TODAY, OPEN_TRADES, CLOSED_TRADES, STOCK_AMOUNT_TRACKER)
    
    if len(OPEN_TRADES) >= 1:
        open_trades_df = pd.DataFrame(OPEN_TRADES).T
        open_trades_df_fresh = open_trades_df[open_trades_df['buy_streak'] == 0]
        print(open_trades_df_fresh['buy_date'].value_counts().sort_index())
        ret, hit = get_return(CLOSED_TRADES)
        metrics_df[TODAY] = {
            'ret' : ret,
            'hit' : hit
        }
     
        logging.info(f"Return till {TODAY} is {ret} with a Hit Ratio of {hit}")

def get_recommendation(date, open_trades):
    data = pd.DataFrame(open_trades).T
    data = data[data["buy_date"] == date]
    data.to_csv(f"recomendations_{date}.csv", index = False)

def main():
    DEV = False #True
    OPEN_TRADES = load_json_file("open_trades.json")
    CLOSED_TRADES = load_json_file("closed_trades.json")
    scoring_date =  '2023-06-20' if DEV else date.today().strftime("%Y-%m-%d")
    run_for_a_day(scoring_date, OPEN_TRADES, CLOSED_TRADES)
    get_recommendation(scoring_date, OPEN_TRADES)
    save_json_file("open_trades.json", OPEN_TRADES)
    save_json_file("closed_trades.json", CLOSED_TRADES)
    

if __name__ == "__main__":
    main()