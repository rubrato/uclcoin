import os
from waitress import serve
from index import app

print("WORKEEEEEEEEEEEEEEDDDDDDDDDDDDDDDDDDDDDDDD")
serve(app,host="0.0.0.0",port=os.environ["PORT"])