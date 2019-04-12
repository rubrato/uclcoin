import os
from waitress import serve
from main import app

print("hay")
serve(app,host="0.0.0.0",port=os.environ["PORT"])