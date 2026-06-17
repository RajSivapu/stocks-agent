import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import json, ssl, urllib.request, urllib.parse
from lib import config, db
ctx = ssl.create_default_context(); UA={"User-Agent":"Mozilla/5.0"}
def get(u,h=None,t=15):
    return urllib.request.urlopen(urllib.request.Request(u,headers=h or UA),timeout=t,context=ctx).read()
# NOTE: failure status is the exception *type name* only — never raw message/repr —
# because results are sent to Telegram + printed to cloud logs, and raw exceptions can
# carry secrets (Finnhub token in a URL, Postgres password in the DSN). Run locally for detail.
results = {}
try: db.init_schema(); results["postgres"]="ok"
except Exception as e: results["postgres"]=f"FAIL {type(e).__name__}"
try:
    k=config.secret("finnhub_api_key")
    get("https://finnhub.io/api/v1/quote?symbol=AAPL",{"X-Finnhub-Token":k,**UA}); results["finnhub"]="ok"
except Exception as e: results["finnhub"]=f"FAIL {type(e).__name__}"
try: get("https://query1.finance.yahoo.com/v8/finance/chart/VOO?range=1d&interval=1d"); results["yahoo"]="ok"
except Exception as e: results["yahoo"]=f"FAIL {type(e).__name__}"
try:
    tok=config.secret("telegram_bot_token"); chat=config.secret("telegram_chat_id")
    data=urllib.parse.urlencode({"chat_id":chat,"text":f"healthcheck: {json.dumps(results)}"}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",data=data),timeout=15,context=ctx)
    results["telegram"]="ok"
except Exception as e: results["telegram"]=f"FAIL {type(e).__name__}"
print(json.dumps(results, indent=2))
