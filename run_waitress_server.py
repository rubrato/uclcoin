import os
from waitress import serve
from index import app

print("hay")
serve(app,host="0.0.0.0",port=5000)