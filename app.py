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

server = MongoClient('mongodb+srv://root:root@cluster0-axobn.mongodb.net/test?retryWrites=true&w=majority')
uclcoindb = server.uclcoin
blockchain = BlockChain(mongodb=uclcoindb)
domain = 'https://piv.azurewebsites.net' #Insert your domain
consecutives_invalids_blocks = 0

app = Flask(__name__)

@app.route('/consensus', methods=['GET'])
def get_consensus():
    local_consensus = consensus()
    if local_consensus:
        return jsonify({'message': f'Consensus updated'}), 201
    return jsonify({'message': f'Consensus already updated'}), 400


# endpoint to return the node's copy of the chain.
# Our application will be using this endpoint to query
# all the posts to display.
@app.route('/chain', methods=['GET'])
def get_chain():
    # make sure we've the longest chain
    chain_data = []
    for block in blockchain.blocks:
       chain_data.append(block.__dict__)

    for chain in chain_data:
        for i,transaction in enumerate(chain['transactions']):
            tempTrans = chain['transactions'][i]
            jsonTrans = json.dumps(tempTrans.__str__())
            chain['transactions'][i] = jsonTrans.replace("\"","*").replace("'","\"")

    jsonText = json.dumps(chain_data, sort_keys=True, indent=4)
    return jsonText.replace("\"*","").replace("*\"","").replace("\\\"","\"")

# Get Nodes
@app.route('/get_nodes', methods=['GET'])
def get_nodes():
    return requests.get('https://dnsblockchainucl.azurewebsites.net/chains').text

# endpoint to add a block mined by someone else to
# the node's chain. The block is first verified by the node
# and then added to the chain.
@app.route('/add_block', methods=['POST'])
def verify_and_add_block():
    block_data = request.get_json()
    block = Block.from_dict(block_data)
    blockchain.add_block(block)

    return "The block was added", 200

def consensus():
    """
    Our simple consnsus algorithm. If a longer valid chain is
    found, our chain is replaced with it.
    """
    global blockchain

    result = False
    current_len = blockchain._blocks.count()
    rs = (grequests.get(f'{node["address"]}/chain') for node in json.loads(get_nodes()))
    responses = grequests.map(rs)
    for response in responses:
        if response != None and response.status_code == 200:
            blocks = response.json()
            if len(blocks) > current_len:    
                blockchain.clear()    
                for block in blocks:
                    temp_block = Block.from_dict(block)
                    blockchain.add_block(temp_block)
                result = True

    return result

def announce_new_block(block):
    """
    A function to announce to the network once a block has been mined.
    Other blocks can simply verify the proof of work and add it to their
    respective chains.
    """ 
    for node in json.loads(get_nodes()):
        address = node['address']
        if address != domain:
            url = "{}/add_block".format(address)
            requests.post(url, json=block)


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
        blockchain.validate_block(block)
        rs = (grequests.post(f'{node["address"]}/validate', data=request.data) for node in json.loads(get_nodes()))
        responses = grequests.map(rs)
        validated_chains = 1
        unvalidated_chains = 0
        total_valids = 2
        total_unvalids = 1
        for response in responses:
            if response != None:
                if response.status_code == 201:
                    validated_chains += 1
                elif response.status_code == 400:
                    unvalidated_chains += 1
                    if unvalidated_chains == total_unvalids:
                        break
                if validated_chains == total_valids:
                    break      
        if validated_chains == total_valids:
            blockchain.add_block(block)
            announce_new_block(block_json)
            return jsonify({'message': f'Block #{block.index} added to the Blockchain'}), 201
        elif unvalidated_chains == total_unvalids:    
            consensus()
            return jsonify({'message': 'Blockchain was Outdated'}), 400
        else:
            return jsonify({'message': f'Block rejected: {block}'}), 400
    except (KeyError, TypeError, ValueError):
        return jsonify({'message': f'Invalid block format'}), 400
    except BlockchainException as bce:
        consecutives_invalids_blocks += 1
        if consecutives_invalids_blocks == 3:
            consensus()
            consecutives_invalids_blocks = 0
            return jsonify({'message': 'Blockchain was Outdated'}), 400
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
        BlockChain.validate_block(block)
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

@app.route('/keypair', methods=['GET'])
def generate_key():
    wallet = KeyPair()
    rs =  [{'private_key':f'{wallet.private_key}'},{'public_key':f'{wallet.public_key}'}]
    return jsonify(rs), 200

if __name__ == '__main__':
    app.run()
