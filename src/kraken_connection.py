import json
import time
import signal
import sys
import asyncio
import websockets
import logging
import aiohttp
import os
import math
from enum import Enum
from collections import namedtuple
from dotenv import load_dotenv
from aiohttp_socks import ProxyConnector
from aiostream import stream, pipe
from pathlib import Path
from collections.abc import Mapping
from demo_opts import get_device
from luma.core.render import canvas
from PIL import Image, ImageSequence
from luma.core.sprite_system import framerate_regulator

FeeRates = namedtuple("FeeRates", "feerate_24h feerate_30min")

load_dotenv()

NODE_URL = os.getenv("NODE_URL")
NODE_USERNAME = os.getenv("NODE_USERNAME")
NODE_PASSWORD = os.getenv("NODE_PASSWORD")

logging.getLogger('websockets.client').setLevel(logging.ERROR)
logging.getLogger('websockets.server').setLevel(logging.ERROR)
logging.getLogger('websockets.protocol').setLevel(logging.ERROR)

basePath = Path(__file__).resolve().parent

bitmaps = {
    '€': Image.open(basePath.joinpath('euro.bmp')).convert("1"),
    '$': Image.open(basePath.joinpath('usd.png')).convert("1"),
    '.': Image.open(basePath.joinpath('dot.bmp')).convert("1"),
    ',': Image.open(basePath.joinpath('dot.bmp')).convert("1"),  # TODO: comma
    '-': Image.open(basePath.joinpath('hyphen.png')).convert("1"),
    '0': Image.open(basePath.joinpath('digits-0.bmp')).convert("1"),
    '1': Image.open(basePath.joinpath('digits-1.bmp')).convert("1"),
    '2': Image.open(basePath.joinpath('digits-2.bmp')).convert("1"),
    '3': Image.open(basePath.joinpath('digits-3.bmp')).convert("1"),
    '4': Image.open(basePath.joinpath('digits-4.bmp')).convert("1"),
    '5': Image.open(basePath.joinpath('digits-5.bmp')).convert("1"),
    '6': Image.open(basePath.joinpath('digits-6.bmp')).convert("1"),
    '7': Image.open(basePath.joinpath('digits-7.bmp')).convert("1"),
    '8': Image.open(basePath.joinpath('digits-8.bmp')).convert("1"),
    '9': Image.open(basePath.joinpath('digits-9.bmp')).convert("1"),
    'B': Image.open(basePath.joinpath('b.png')).convert("1"),
    'F': Image.open(basePath.joinpath('f.png')).convert("1"),
    'M': Image.open(basePath.joinpath('m.png')).convert("1"),
}

bitmapNewBlockHeader = Image.open(
    basePath.joinpath('new-block-header.png')).convert("1")

TextAlignment = Enum('TextAlignment', 'LEFT RIGHT CENTER')


def handler(signum, frame):
    sys.exit()


def measure_text(str: str):
    width = len(str) - 1  # spacing between letters
    for c in str:
        width += 1 if c == ' ' else bitmaps[c].width

    return width


def draw_text(draw,
              str: str,
              align: TextAlignment = TextAlignment.LEFT,
              offset: int = 0):
    width = measure_text(str)
    x = offset
    if align == TextAlignment.RIGHT:
        x += 32 - width
    elif align == TextAlignment.CENTER:
        x += math.ceil((32 - width) / 2)

    for c in str:
        if c == ' ':
            x += 2
        else:
            draw.bitmap((x, 0), bitmaps[c], fill="white")
            x += bitmaps[c].width + 1

    return width


def draw_int(draw,
             num: int,
             align: TextAlignment = TextAlignment.LEFT,
             offset: int = 0):
    return draw_text(draw, '{:,}'.format(num), align=align, offset=offset)


def float_to_str(num: float, digits: int):
    digitsBeforeComma = max(1, math.ceil(math.log10(max(1, num))))
    digitsAfterComma = max(0, digits - digitsBeforeComma)
    formatString = '{:.' + str(digitsAfterComma) + 'f}'

    return formatString.format(num)


def draw_number(draw, num: int, offset: int = 0):
    return draw_text(draw,
                     '{:,}'.format(num),
                     align=TextAlignment.RIGHT,
                     offset=offset)


def draw_number_old(draw, num: int, offset: int = 0):
    if num == 0:
        draw.bitmap((29, 0), bitmaps['0'], fill="white")

    x = 32 + offset
    count = 0
    while num > 0:
        if count == 3:
            count = 0
            draw.bitmap((x - 1, 0), bitmaps['.'], fill="white")
            x -= 2
        digit = bitmaps[str(num % 10)]
        (digitWidth, _) = digit.size
        num = num // 10
        draw.bitmap((x - digitWidth, 0), digit, fill="white")
        x -= digitWidth + 1
        count += 1
    return 32 + offset - x


def show_price_eur(device, price):
    with canvas(device) as draw:
        draw.point((0, 0), fill="white")
        draw_text(draw, '€', offset=2)
        draw_int(draw, int(price), align=TextAlignment.RIGHT)


def show_price_usd(device, price):
    with canvas(device) as draw:
        draw.point((0, 1), fill="white")
        draw_text(draw, '$', offset=2)
        draw_int(draw, int(price), align=TextAlignment.RIGHT)


def show_fees(device, rates: FeeRates):
    with canvas(device) as draw:
        rates_str = '{:d}-{:d}'.format(round(rates.feerate_24h),
                                       round(rates.feerate_30min))
        draw.point((0, 2), fill="white")
        draw_text(draw, 'F', offset=2)
        draw_text(draw, rates_str, align=TextAlignment.RIGHT)


def show_mempool(device, size: float):
    with canvas(device) as draw:
        draw.point((0, 3), fill="white")
        draw_text(draw, 'M', offset=2)
        draw_text(draw,
                  float_to_str(size, 3) + 'MB',
                  align=TextAlignment.RIGHT)


def show_loading(device):
    show_price_eur(device, 0)


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
                "pair": ["XBT/EUR", "XBT/USD"],
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
                _, details, _, pair = result
                price = float(details[0][0])
                if pair == 'XBT/EUR':
                    print("EUR Price changed! %s" % f'{int(price):,}')
                    yield ('EUR', price)
                else:
                    print("USD Price changed! %s" % f'{int(price):,}')
                    yield ('USD', price)


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
                    'https://whatthefee.io/data.json') as response:
                text = await response.text()
                result = json.loads(text)
                feerate_30min = math.exp(result['data'][0][4] / 100)
                feerate_24h = math.exp(result['data'][10][4] / 100)
                print('New fees: %.2f - %.2f' % (feerate_24h, feerate_30min))
                yield FeeRates(feerate_24h, feerate_30min)

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


async def node_mempool_generator(session):
    while True:
        try:
            async with node_rpc(session, 'getmempoolinfo') as response:
                result = await response.json()
                print('New mempool size: %.2f MB' %
                      (result['result']['bytes'] / 1000000))
                yield result['result']['bytes'] / 1000000

                await asyncio.sleep(10.5)
        except GeneratorExit:
            break
        except Exception as error:
            print('ERROR Mempool: ' + repr(error))
            await asyncio.sleep(3)


def play_new_block(device, height: int):
    regulator = framerate_regulator(fps=10)  # 100 ms
    slow_regulator = framerate_regulator(fps=5)  # 200 ms

    # scroll from below
    for top in range(8, 0, -1):
        with regulator:
            with canvas(device) as draw:
                draw.bitmap((0, top), bitmapNewBlockHeader, fill='white')
        top -= 1

    # flash 3 times
    for frame in range(7):
        with slow_regulator:
            with canvas(device) as draw:
                if frame % 2 == 0:
                    draw.bitmap((0, 0), bitmapNewBlockHeader, fill='white')

    # scroll to left and reveal block height
    for left in range(0, -33, -1):
        with regulator:
            with canvas(device) as draw:
                draw.bitmap((left, 0), bitmapNewBlockHeader, fill='white')
                digits_count = max(1, math.ceil(math.log10(max(1, height))))
                pixel_size = (digits_count * 3) + (digits_count - 1) + (
                    2 if digits_count > 3 else 0)
                draw_number(draw, height,
                            math.ceil(left + 16 + (pixel_size / 2)))

    time.sleep(7)


async def when_changed(gen):
    prev_value = None
    async for value in gen:
        if prev_value != None and prev_value != value:
            yield value
        prev_value = value


async def main():
    tor_connector = ProxyConnector.from_url('socks5://localhost:9050')

    async with aiohttp.ClientSession() as session:
        async with aiohttp.ClientSession(
                connector=tor_connector) as tor_session:
            card = 0
            price_eur = None
            price_usd = None
            fees = None
            mempool_size = None
            last_switch = time.monotonic()
            height_stream = stream.map(
                stream.iterate(when_changed(
                    node_height_generator(tor_session))), lambda height:
                ('height', height))
            price_stream = stream.map(stream.iterate(safe_price_generator()),
                                      lambda price: ('price', price))
            fees_stream = stream.map(stream.iterate(fees_generator(session)),
                                     lambda fees: ('fees', fees))
            mempool_stream = stream.map(
                stream.iterate(node_mempool_generator(tor_session)),
                lambda mempool: ('mempool', mempool))
            merge_stream = stream.merge(
                height_stream,
                price_stream,
                fees_stream,
                mempool_stream,
            )

            device = get_device()
            show_loading(device)

            async with merge_stream.stream() as streamer:
                async for item in streamer:
                    (label, value) = item
                    if label == 'height':
                        play_new_block(device, value)
                        continue

                    if label == 'price':
                        (currency, price) = value
                        if currency == 'EUR':
                            price_eur = price
                        else:
                            price_usd = price
                    elif label == 'fees':
                        fees = value
                    elif label == 'mempool':
                        mempool_size = value

                    if price_eur == None or price_usd == None or fees == None or mempool_size == None:
                        last_switch = time.monotonic()
                        continue

                    if time.monotonic() - last_switch >= 15:
                        card = (card + 1) % 4
                        print("Showing card %d" % card)
                        last_switch = time.monotonic()

                    if card == 0:
                        show_price_eur(device, price_eur)
                    elif card == 1:
                        show_price_usd(device, price_usd)
                    elif card == 2:
                        show_fees(device, fees)
                    elif card == 3:
                        show_mempool(device, mempool_size)


asyncio.run(main())
