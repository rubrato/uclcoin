from uclcoin import Block
import requests
import json
from collections import namedtuple

r = requests.get('http://127.0.0.1:5000/block/minable/0382499de4b7f5ffd6a86dd71c94364ba5f6682f7434304693f0df9130442dc072')
last_block = json.loads(r.text)
block = Block.from_dict(last_block["block"])
difficulty = last_block["difficulty"]

while block.current_hash[:difficulty].count('0') < difficulty:
    block.nonce += 1
    block.recalculate_hash()

data = json.dumps(block, default=lambda x: x.__dict__)

requests.post('http://127.0.0.1:5000/block',data,json=True)
