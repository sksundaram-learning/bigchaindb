import time
from unittest.mock import patch

import rethinkdb as r

from multipipes import Pipe


def test_filter_by_assignee(b, signed_create_tx):
    from bigchaindb.pipelines.block import BlockPipeline

    block_maker = BlockPipeline()

    tx = signed_create_tx.to_dict()
    tx.update({'assignee': b.me})

    # filter_tx has side effects on the `tx` instance by popping 'assignee'
    assert block_maker.filter_tx(tx) == tx

    tx = signed_create_tx.to_dict()
    tx.update({'assignee': 'nobody'})

    assert block_maker.filter_tx(tx) is None


def test_validate_transaction(b, create_tx):
    from bigchaindb.pipelines.block import BlockPipeline

    block_maker = BlockPipeline()

    assert block_maker.validate_tx(create_tx.to_dict()) is None

    valid_tx = create_tx.sign([b.me_private])
    assert block_maker.validate_tx(valid_tx.to_dict()) == valid_tx


def test_create_block(b, user_vk):
    from bigchaindb.models import Transaction
    from bigchaindb.pipelines.block import BlockPipeline

    block_maker = BlockPipeline()

    for i in range(100):
        tx = Transaction.create([b.me], [user_vk])
        tx = tx.sign([b.me_private])
        block_maker.create(tx)

    # force the output triggering a `timeout`
    block_doc = block_maker.create(None, timeout=True)

    assert len(block_doc.transactions) == 100


def test_write_block(b, user_vk):
    from bigchaindb.models import Block, Transaction
    from bigchaindb.pipelines.block import BlockPipeline

    block_maker = BlockPipeline()

    txs = []
    for i in range(100):
        tx = Transaction.create([b.me], [user_vk])
        tx = tx.sign([b.me_private])
        txs.append(tx)

    block_doc = b.create_block(txs)
    block_maker.write(block_doc)
    expected = r.table('bigchain').get(block_doc.id).run(b.conn)
    expected = Block.from_dict(expected)

    assert expected == block_doc


def test_delete_tx(b, user_vk):
    from bigchaindb.models import Transaction
    from bigchaindb.pipelines.block import BlockPipeline

    block_maker = BlockPipeline()

    tx = Transaction.create([b.me], [user_vk])
    tx = tx.sign([b.me_private])
    b.write_transaction(tx)

    backlog_tx = r.table('backlog').get(tx.id).run(b.conn)
    backlog_tx.pop('assignee')
    assert backlog_tx == tx.to_dict()

    returned_tx = block_maker.delete_tx(tx.to_dict())

    assert returned_tx == tx.to_dict()
    assert r.table('backlog').get(tx.id).run(b.conn) is None


def test_prefeed(b, user_vk):
    import random
    from bigchaindb.models import Transaction
    from bigchaindb.pipelines.block import initial

    for i in range(100):
        tx = Transaction.create([b.me], [user_vk], {'msg': random.random()})
        tx = tx.sign([b.me_private])
        b.write_transaction(tx)

    backlog = initial()

    assert len(list(backlog)) == 100


@patch('bigchaindb.pipelines.block.create_pipeline')
def test_start(create_pipeline):
    from bigchaindb.pipelines import block

    pipeline = block.start()
    assert create_pipeline.called
    assert create_pipeline.return_value.setup.called
    assert create_pipeline.return_value.start.called
    assert pipeline == create_pipeline.return_value


def test_full_pipeline(b, user_vk):
    import random
    from bigchaindb.models import Block, Transaction
    from bigchaindb.pipelines.block import create_pipeline, get_changefeed

    outpipe = Pipe()

    count_assigned_to_me = 0
    for i in range(100):
        tx = Transaction.create([b.me], [user_vk], {'msg': random.random()})
        tx = tx.sign([b.me_private]).to_dict()
        assignee = random.choice([b.me, 'aaa', 'bbb', 'ccc'])
        tx['assignee'] = assignee
        if assignee == b.me:
            count_assigned_to_me += 1
        r.table('backlog').insert(tx, durability='hard').run(b.conn)

    assert r.table('backlog').count().run(b.conn) == 100

    pipeline = create_pipeline()
    pipeline.setup(indata=get_changefeed(), outdata=outpipe)
    pipeline.start()

    time.sleep(2)
    pipeline.terminate()

    block_doc = outpipe.get()
    chained_block = r.table('bigchain').get(block_doc.id).run(b.conn)
    chained_block = Block.from_dict(chained_block)

    assert len(block_doc.transactions) == count_assigned_to_me
    assert chained_block == block_doc
    assert r.table('backlog').count().run(b.conn) == 100 - count_assigned_to_me
