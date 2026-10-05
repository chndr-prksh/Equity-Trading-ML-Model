import pandas as pd
import mysql.connector
from sqlalchemy import create_engine
import logging
import json
import joblib
import uuid
import warnings
import os
from dotenv import load_dotenv
from utils.sim import save_json_file
warnings.filterwarnings("ignore")

load_dotenv()

BASE_DATA = 'base_data_23.csv'
table_name = 'PAST_DATA'

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "STOCK_LAMBDA")

nifty_500 = pd.read_csv("ind_nifty500list_new.csv")

cnx = mysql.connector.connect(user=DB_USER, password=DB_PASSWORD, host=DB_HOST)
cursor = cnx.cursor()
cursor.execute(f"USE {DB_NAME}")

engine = create_engine(f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")


day_data = pd.read_csv(BASE_DATA)
day_data = day_data[day_data['Ticker'].isin(nifty_500['Symbol'].unique())].reset_index(drop = True)
day_data['Date']=pd.to_datetime(day_data['Date'], dayfirst = True)
day_data.to_sql(table_name, engine, if_exists='replace', index=False)


STOCK_AMOUNT_TRACKER = "tracker_2023.json"
model = joblib.load("models/2023_0.5.pkl")
top_stock_current_values = {x : 100000 for x in model['top_stock']}
save_json_file(STOCK_AMOUNT_TRACKER, top_stock_current_values)

save_json_file("open_trades.json", {})
save_json_file("closed_trades.json", {})