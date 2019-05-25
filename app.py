#!/usr/bin/env python
# pylint: disable=C0103,C0111
import gevent.monkey
gevent.monkey.patch_all()
from uclcoin import (Block, BlockChain, BlockchainException, KeyPair,
                     Transaction)
from pymongo import MongoClient
from flask import Flask, jsonify, request

import requests
import grequests
import json
import re
import numpy as np
from hashlib import sha256

uclcoindb = MongoClient('mongodb+srv://pi:pi@cluster0-tdudc.azure.mongodb.net/test?retryWrites=true').uclcoin
blockchain = BlockChain(mongodb=uclcoindb)

peers = set()

app = Flask(__name__)


# TODO
# endpoint to return the node's copy of the chain.
# Our application will be using this endpoint to query
# all the posts to display.
@app.route('/chain', methods=['GET'])
def get_chain():
    # make sure we've the longest chain
    consensus()
    chain_data = []
    for block in blockchain.blocks:
        chain_data.append(block.__dict__)

    for chain in chain_data:
        for i,transaction in enumerate(chain['transactions']):
            tempTrans = chain['transactions'][i]
            jsonTrans = json.dumps(tempTrans.__str__())
            chain['transactions'][i] = jsonTrans.replace("\"","*").replace("'","\"")

    jsonText = json.dumps({"length": len(chain_data),
                       "chain": chain_data,
                       "peers": list(peers)}, sort_keys=True, indent=4)
    return jsonText.replace("\"*","").replace("*\"","").replace("\\\"","\"")


def extract_values(obj, key):
    """Pull all values of specified key from nested JSON."""
    arr = []

    def extract(obj, arr, key):
        """Recursively search for values of key in JSON tree."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    extract(v, arr, key)
                elif k == key:
                    arr.append(v)
        elif isinstance(obj, list):
            for item in obj:
                extract(item, arr, key)
        return arr

    results = extract(obj, arr, key)
    return results


# Get Nodes
@app.route('/get_nodes', methods=['GET'])
def get_nodes():
    return requests.get('https://dnsblockchainucl.azurewebsites.net/chains').text


# endpoint to add new peers to the network.
@app.route('/register_node', methods=['POST'])
def register_new_peers():
    node_address = request.get_json()["node_address"]
    if not node_address:
        return "Invalid data", 400

    # Add the node to the peer list
    peers.add(node_address)

    # Return the consensus blockchain to the newly registered node
    # so that he can sync
    return get_chain()


@app.route('/register_with', methods=['POST'])
def register_with_existing_node():
    """
    Internally calls the `register_node` endpoint to
    register current node with the node specified in the
    request, and sync the blockchain as well as peer data.
    """
    node_address = json(request.get_json())["node_address"]
    if not node_address:
        return "Invalid data", 400

    data = {"node_address": request.host_url}
    headers = {'Content-Type': "application/json"}

    # Make a request to register with remote node and obtain information
    response = requests.post(node_address + "/register_node",
                             data=json.dumps(data), headers=headers)

    if response.status_code == 200:
        global blockchain
        global peers
        # update chain and the peers
        chain_dump = response.json()['chain']
        blockchain = create_chain_from_dump(chain_dump)
        peers.update(response.json()['peers'])
        return "Registration successful", 200
    else:
        # if something goes wrong, pass it on to the API response
        return response.content, response.status_code


def create_chain_from_dump(chain_dump):
    blockchain = BlockChain(mongodb=uclcoindb)
    for idx, block_data in enumerate(chain_dump):
        block = Block(block_data["index"],
                      block_data["transactions"],
                      block_data["previous_hash"],
                      block_data["timestamp"])
        proof = block_data['hash']
        if idx > 0:
            added = blockchain.add_block(block, proof)
            if not added:
                raise Exception("The chain dump is tampered!!")
        else:  # the block is a genesis block, no verification needed
            blockchain.chain.append(block)
    return blockchain


# endpoint to add a block mined by someone else to
# the node's chain. The block is first verified by the node
# and then added to the chain.
@app.route('/add_block', methods=['POST'])
def verify_and_add_block():
    block_data = request.get_json()
    block = Block(block_data["index"],
                  block_data["transactions"],
                  block_data["previous_hash"],
                  block_data["timestamp"])

    proof = block_data['hash']
    added = blockchain.add_block(block, proof)

    if not added:
        return "The block was discarded by the node", 400

def consensus():
    """
    Our simple consnsus algorithm. If a longer valid chain is
    found, our chain is replaced with it.
    """
    global blockchain

    longest_chain = None
    current_len = blockchain._count_blocks

    for node in peers:
        print('{}/chain'.format(node))
        response = requests.get('{}chain'.format(node))
        print("Content", response.content)
        length = response.json()['length']
        chain = response.json()['chain']
        if length > current_len and blockchain.check_chain_validity(chain):
            current_len = length
            longest_chain = chain

    if longest_chain:
        blockchain = longest_chain
        return True

    return False


def announce_new_block(block):
    """
    A function to announce to the network once a block has been mined.
    Other blocks can simply verify the proof of work and add it to their
    respective chains.
    """
    for peer in peers:
        url = "{}add_block".format(peer)
        requests.post(url, data=json.dumps(block.__dict__, sort_keys=True))


@app.route('/balance/<address>', methods=['GET'])
def get_balance(address):
    if not re.match(r'[\da-f]{66}$', address):
        return jsonify({'message': 'Invalid address'}), 400

    balance = blockchain.get_balance(address)
    return jsonify({'balance': balance}), 200


@app.route('/pending_transactions', methods=['GET'])
def pending_transactions():
    pending_txns = [dict(t) for t in blockchain.pending_transactions]
    return jsonify({'transactions': pending_txns}), 200


@app.route('/block/<index>', methods=['GET'])
def get_block(index):
    block = None
    if index == 'last':
        block = blockchain.get_latest_block()
    elif index.isdigit():
        block = blockchain.get_block_by_index(int(index))
    if not block:
        return jsonify({'message': 'Block not found'}), 404

    return jsonify(dict(block)), 200


@app.route('/block', methods=['POST'])
def add_block():
    try:
        block_json = request.get_json(force=True)
        block = Block.from_dict(block_json)
        rs = (grequests.post(f'{node["address"]}/validate', data=request.data) for node in json.loads(get_nodes()))
        responses = grequests.map(rs)
        validated_chains = 1
        for response in responses:
            if response.status_code == 201:
                validated_chains += 1
                # 2 porque esta já conta como uma
                if validated_chains == 3:
                    break

        if validated_chains == 3:
            blockchain.add_block(block)
            announce_new_block(block)
            return jsonify({'message': f'Block #{block.index} added to the Blockchain'}), 201
        else:
            return jsonify({'message': f'Block rejected: {block}'}), 400
    except (KeyError, TypeError, ValueError):
        return jsonify({'message': f'Invalid block format'}), 400
    except BlockchainException as bce:
        return jsonify({'message': f'Block rejected: {block}'}), 400


@app.route('/block/minable/<address>', methods=['GET'])
def get_minable_block(address):
    if not re.match(r'[\da-f]{66}$', address):
        return jsonify({'message': 'Invalid address'}), 400

    block = blockchain.get_minable_block(address)
    response = {
        'difficulty': blockchain.calculate_hash_difficulty(),
        'block': dict(block)
    }
    return jsonify(response), 200


@app.route('/validate', methods=['POST'])
def validate_block():
    try:
        block = request.get_json(force=True)
        block = Block.from_dict(block)
        return jsonify({'message': f'Block #{block.index} is a valid block!'}), 201
    except (KeyError, TypeError, ValueError):
        return jsonify({'message': f'Invalid block format'}), 400
    except BlockchainException as bce:
        return jsonify({'message': f'Invalid block: {bce}'}), 400


@app.route('/transaction', methods=['POST'])
def add_transaction():
    try:
        transaction = request.get_json(force=True)
        if not re.match(r'[\da-f]{66}$', transaction['destination']):
            return jsonify({'message': 'Invalid address'}), 400
        if transaction['amount'] < 0.00001:
            return jsonify({'message': 'Invalid amount. Minimum allowed amount is 0.00001'}), 400
        if 0 > transaction['fee'] < 0.00001:
            return jsonify({'message': 'Invalid fee. Minimum allowed fee is 0.00001 or zero'}), 400
        transaction = Transaction.from_dict(transaction)
        blockchain.add_transaction(transaction)
        return jsonify({'message': f'Pending transaction {transaction.tx_hash} added to the Blockchain'}), 201
    except (KeyError, TypeError, ValueError):
        return jsonify({'message': f'Invalid transacton format'}), 400
    except BlockchainException as bce:
        return jsonify({'message': f'Transaction rejected: {bce}'}), 400


@app.route('/transaction/<private_key>/<public_key>/<value>', methods=['POST'])
def add_transaction2(private_key, public_key, value):
    try:
        wallet = KeyPair(private_key)
        transaction = wallet.create_transaction(public_key, float(value))
        blockchain.add_transaction(transaction)
        return jsonify({'message': f'Pending transaction {transaction.tx_hash} added to the Blockchain'}), 201
    except BlockchainException as bce:
        return jsonify({'message': f'Transaction rejected: {bce}'}), 400


@app.route('/avgtimes', methods=['GET'])
def get_averages():
    if blockchain._count_blocks() < 101:
        return jsonify({'message': f'Not enough blocks'}), 400
    last_time = blockchain.get_block_by_index(-101).timestamp
    times = []
    for i in range(-100, 0):
        block = blockchain.get_block_by_index(i)
        times.append(block.timestamp - last_time)
        last_time = block.timestamp
    response = {
        'last001': blockchain.get_block_by_index(-1).timestamp - blockchain.get_block_by_index(-2).timestamp,
        'last005': sum(times[-5:]) / 5,
        'last010': sum(times[-10:]) / 10,
        'last050': sum(times[-50:]) / 50,
        'last100': sum(times[-100:]) / 100,
        'lastIndex': blockchain.get_latest_block().index
    }
    return jsonify(response), 200


@app.route('/ranking', methods=['GET'])
def get_ranking():
    ranking = dict()
    blocks = blockchain.blocks
    next(blocks)  # skip genesis block
    for block in blocks:
        cbt = block.transactions[-1]
        ranking[cbt.destination] = ranking.get(cbt.destination, 0) + cbt.amount
    ranking = sorted(ranking.items(), key=lambda x: x[1], reverse=True)
    return jsonify(ranking), 200


if __name__ == '__main__':
    app.run()