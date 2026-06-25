import pandas as pd
from tqdm import tqdm
import pandas_ta as ta
import numpy as np
import logging
import json
import joblib
import uuid
import warnings
warnings.filterwarnings("ignore")

logging.basicConfig(
     level=logging.INFO, 
     format= '[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
     datefmt='%H:%M:%S'
 )

def create_sma_cols(df, roll_past = [10, 15, 20, 25, 30], normalize = True):
    for n in roll_past:
        if normalize:
            df[f"SMA_{n}"] = ta.sma(df["Close"], length = n, ) / df["Close"]
        else:
            df[f"SMA_{n}"] = ta.sma(df["Close"], length = n) 
    return df

def create_rsi(df, roll_past = [7, 14]):
    for n in roll_past:
        df[f"RSI_{n}"]= np.floor(ta.rsi(df["Close"], length = n))
    return df

def create_doji(df, roll_past = [10, 15, 20, 25, 30]):
    for n in roll_past:
        df[f"doji_{n}"]= ta.cdl_doji(df['Open'], df['High'], df['Low'], df['Close'], length= n,
                                     scalar=1) 
    return df


def create_williams_r(df, roll_past=[7, 14, 21, 28]):
    for n in roll_past:
        df[f'will_r_{n}'] = ta.willr(df["High"], df["Low"], df["Close"], n, )
    return df

def create_atr(df, roll_past=[7, 14, 21, 28], normalize=True):
    for n in roll_past:
        df[f'atr_{n}'] = ta.atr(df["High"], df["Low"], df["Close"], n, percent = True)
    return df


def create_keltner(df, roll_past = [7, 14, 21, 28]):
    kc_cols = []
    for length in roll_past:
        t = ta.kc(df["High"], df["Low"], df["Close"], length)
        for c in [f'KCLe_{length}_2', f'KCBe_{length}_2', f'KCUe_{length}_2']:
            t[c] = t[c] / df["Close"]
        kc_cols.append(t)
    kc_cols = pd.concat(kc_cols, axis = 1)
    df = pd.concat([df, kc_cols], axis = 1)
    return df

def create_donchian(df, roll_past = [7, 21]):
    dc_cols = []
    for length in roll_past:
        t = ta.donchian(df['High'], df['Low'], lower_length=length, upper_length=length)
        for c in [f'DCL_{length}_{length}', f'DCM_{length}_{length}', f'DCU_{length}_{length}']:
            t[c] = t[c] / df["Close"]
        dc_cols.append(t)
    dc_cols = pd.concat(dc_cols, axis = 1)
    df = pd.concat([df, dc_cols], axis = 1)
    return df

def create_stoch(df, configs = [(7, 3, 3), (14, 3, 3), (21, 3, 3), (28, 3, 3)], normalize = True):
    stch_cols = []
    for k, d, s_k in configs:
        t = ta.stoch(df['High'], df["Low"], df["Close"], k, d, s_k)
        c = t.columns
        ind = t.index
        stch_cols.append(t)
    stch = pd.concat(stch_cols, axis = 1)
    df = pd.concat([df, stch], axis = 1)
    return df

def create_bb_cols(df, roll_past = [5, 10, 15, 20, 25, 30]):
    bb_cols = []
    for length in roll_past:
        t = ta.bbands(df["Close"], length)
        for c in [f'BBL_{length}_2.0', f'BBM_{length}_2.0', f'BBU_{length}_2.0', f'BBB_{length}_2.0']:
            t[c] = t[c] / df["Close"]
        bb_cols.append(t)
    bb_cols = pd.concat(bb_cols, axis = 1)
    df = pd.concat([df, bb_cols], axis = 1)
    return df

def calculate_technicals(df):
    df["rolling_change_mean_10"] = (1 - (df["Close"] / df["Open"])).rolling(10).mean() 
    df["rolling_change_mean_15"] = (1 - (df["Close"] / df["Open"])).rolling(15).mean() 
    df["rolling_change_mean_20"] = (1 - (df["Close"] / df["Open"])).rolling(20).mean() 
    df = create_sma_cols(df)
    df = create_rsi(df)
    df = create_bb_cols(df)
    df = df.dropna()
    return df
        
def validate_past_dates(data, today):
    if today in data['Date'].unique():
        return True
    return False

def create_features(data):
    ooss_data = []
    for tick in tqdm(data['Ticker'].unique()):
        try:
            temp_ = data[data['Ticker'] == tick]
            temp_['Date'] = pd.to_datetime(temp_['Date'])
            temp_ = temp_.sort_values(by = 'Date')
            temp_ = temp_.reset_index(drop = True)
            temp_ = calculate_technicals(temp_)
            ooss_data.append(temp_)
        except:
            logging.info(f"Not processing for stock : {tick}")
    return pd.concat(ooss_data)