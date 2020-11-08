import json
import time
import signal
import sys
import asyncio
import websockets
import logging
import aiohttp
import os
from dotenv import load_dotenv
from aiohttp_socks import ProxyConnector
from aiostream import stream, pipe
from pathlib import Path
from collections.abc import Mapping
from demo_opts import get_device
from luma.core.render import canvas
from PIL import Image, ImageSequence
from luma.core.sprite_system import framerate_regulator

load_dotenv()

NODE_URL = os.getenv("NODE_URL")
NODE_USERNAME = os.getenv("NODE_USERNAME")
NODE_PASSWORD = os.getenv("NODE_PASSWORD")

logging.getLogger('websockets.client').setLevel(logging.ERROR)
logging.getLogger('websockets.server').setLevel(logging.ERROR)
logging.getLogger('websockets.protocol').setLevel(logging.ERROR)

basePath = Path(__file__).resolve().parent

bitmapCurrencies = {
    'EUR': Image.open(basePath.joinpath('euro.bmp')).convert("1")
}
bitmapDigits = [
    Image.open(basePath.joinpath('digits-0.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-1.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-2.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-3.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-4.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-5.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-6.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-7.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-8.bmp')).convert("1"),
    Image.open(basePath.joinpath('digits-9.bmp')).convert("1"),
]
bitmapDot = Image.open(basePath.joinpath('dot.bmp')).convert("1")
bitmapFees = Image.open(basePath.joinpath('sats-per-vb.bmp')).convert("1")
newBlockAnim = Image.open(basePath.joinpath('new-block.gif'))


def handler(signum, frame):
    sys.exit()


def draw_number(draw, num: int, offset: int = 0):
    if num == 0:
        draw.bitmap((29, 0), bitmapDigits[0], fill="white")

    x = 32 + offset
    count = 0
    while num > 0:
        if count == 3:
            count = 0
            draw.bitmap((x - 1, 0), bitmapDot, fill="white")
            x -= 2
        digit = bitmapDigits[num % 10]
        (digitWidth, _) = digit.size
        num = num // 10
        draw.bitmap((x - digitWidth, 0), digit, fill="white")
        x -= digitWidth + 1
        count += 1


# def show_height(device, height: int):
#     with canvas(device) as draw:
#         draw.point((0, 0), fill="white")
#         draw_number(draw, height)


def show_price(device, price):
    with canvas(device) as draw:
        draw.point((0, 0), fill="white")
        draw.bitmap((2, 0), bitmapCurrencies['EUR'], fill="white")
        draw_number(draw, int(price))


def show_fees(device, fees: float):
    with canvas(device) as draw:
        draw.point((0, 1), fill="white")
        draw.bitmap((2, 0), bitmapFees, fill="white")
        draw_number(draw, int(round(fees)))


def show_loading(device):
    show_price(device, 0)


signal.signal(signal.SIGINT, handler)


def node_rpc(session, method, params=[]):
    url = 'http://' + NODE_USERNAME + ':' + NODE_PASSWORD + '@' + NODE_URL
    data = json.dumps({'method': method, 'params': params, 'id': 'jsonrpc'})
    return session.post(url=url, data=data)


async def price_generator():
    async with websockets.connect("wss://ws.kraken.com") as ws:
        await ws.send(
            json.dumps({
                "event": "subscribe",
                #"event": "ping",
                "pair": ["XBT/EUR"],
                #"subscription": {"name": "ticker"}
                #"subscription": {"name": "spread"}
                "subscription": {
                    "name": "trade"
                }
                #"subscription": {"name": "book", "depth": 10}
                #"subscription": {"name": "ohlc", "interval": 5}
            }))

        while True:
            result = await ws.recv()
            result = json.loads(result)

            if not isinstance(result, Mapping):
                _, details, _, _ = result
                price = float(details[0][0])
                print("Price changed! %s" % f'{int(price):,}')
                yield price


async def safe_price_generator():
    while True:
        try:
            async for price in price_generator():
                yield price
        except GeneratorExit:
            break
        except Exception as error:
            print('ERROR Kraken WS: ' + repr(error))
            await asyncio.sleep(3)


async def height_generator(session):
    yield 10000000  # should be safe for some years
    while True:
        try:
            async with session.get(
                    'https://blockstream.info/api/blocks/tip/height'
            ) as response:
                text = await response.text()
                print('New height: ' + text)
                yield int(text)

                await asyncio.sleep(11)
        except GeneratorExit:
            break
        except Exception as error:
            print('ERROR Height: ' + repr(error))
            await asyncio.sleep(3)


async def node_height_generator(session):
    while True:
        try:
            async with node_rpc(session, 'getblockcount') as response:
                result = await response.json()
                print('New height: %d' % int(result['result']))
                yield int(result['result'])

                await asyncio.sleep(10)
        except GeneratorExit:
            break
        except Exception as error:
            print('ERROR Height: ' + repr(error))
            await asyncio.sleep(3)


async def fees_generator(session):
    while True:
        try:
            async with session.get(
                    'https://blockstream.info/api/fee-estimates') as response:
                text = await response.text()
                result = json.loads(text)
                print('New fees: %f' % result['1'])
                yield result['1']

                await asyncio.sleep(10)
        except GeneratorExit:
            break
        except Exception as error:
            print('ERROR Fees: ' + repr(error))
            await asyncio.sleep(3)


async def node_fees_generator(session):
    while True:
        try:
            async with node_rpc(session, 'estimatesmartfee',
                                [1, 'CONSERVATIVE']) as response:
                result = await response.json()
                print('New fees: %f' % (result['result']['feerate'] * 100000))
                yield result['result']['feerate'] * 100000

                await asyncio.sleep(10)
        except GeneratorExit:
            break
        except Exception as error:
            print('ERROR Fees: ' + repr(error))
            await asyncio.sleep(3)


def play_new_block(device, height: int):
    regulator = framerate_regulator(fps=10)  # 100 ms

    for index, frame in enumerate(ImageSequence.Iterator(newBlockAnim)):
        with regulator:
            with canvas(device) as draw:
                draw.bitmap((0, 0), frame.convert("1"), fill="white")
                if index >= 52:
                    draw_number(draw, height, 28 - (index - 52))

    time.sleep(12)


async def with_previous(gen):
    prev_value = None
    async for value in gen:
        if prev_value != None:
            yield (prev_value, value)
        if prev_value != value:
            prev_value = value
            yield (value, value)


async def main():
    tor_connector = ProxyConnector.from_url('socks5://localhost:9050')

    async with aiohttp.ClientSession(connector=tor_connector) as tor_session:
        card = 0
        last_switch = time.monotonic()
        height_stream = stream.iterate(
            with_previous(node_height_generator(tor_session)))
        price_stream = stream.iterate(safe_price_generator())
        fees_stream = stream.iterate(node_fees_generator(tor_session))
        zip_stream = stream.ziplatest(height_stream,
                                      price_stream,
                                      fees_stream,
                                      partial=False)

        device = get_device()
        show_loading(device)

        async with zip_stream.stream() as streamer:
            async for item in streamer:
                ((prev_height, cur_height), price, fees) = item
                if time.monotonic() - last_switch >= 15:
                    card = (card + 1) % 2
                    print("Showing card %d" % card)
                    last_switch = time.monotonic()

                if cur_height - prev_height > 0:
                    play_new_block(device, cur_height)
                elif card == 0:
                    show_price(device, price)
                elif card == 1:
                    show_fees(device, fees)


asyncio.run(main())
