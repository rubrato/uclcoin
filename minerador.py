from uclcoin import Block
import requests
import json
from collections import namedtuple

r = requests.get('http://piv.azurewebsites.net/block/minable/02f6674448e6ae798037522ce8007e07ad0ba78d74e4545e38acf4ac3bb41553a5')
print(r.text)

last_block = json.loads(r.text)
block = Block.from_dict(last_block["block"])
difficulty = last_block["difficulty"]

while block.current_hash[:difficulty].count('0') < difficulty:
    block.nonce += 1
    block.recalculate_hash()

data = json.dumps(block, default=lambda x: x.__dict__)

r = requests.post('http://piv.azurewebsites.net/block',data,json=True)
print(r.text)
