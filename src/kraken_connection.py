import json
import time
import signal
import sys
import asyncio
import websockets
import logging
from aiostream import stream, pipe
from pathlib import Path
from collections.abc import Mapping
from demo_opts import get_device
from luma.core.render import canvas
from PIL import ImageFont, Image

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

font_path = str(basePath.joinpath('fonts', 'smallest_pixel-7.ttf'))
font = ImageFont.truetype(font_path, 10)


def handler(signum, frame):
    # ws.close()
    sys.exit()


def show_loading():
    show_price(0)


def draw_number(draw, num: int):
    if num == 0:
        draw.bitmap((29, 0), bitmapDigits[0], fill="white")

    x = 32
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


def show_height():
    with canvas(device) as draw:
        draw.point((0, 0), fill="white")
        draw_number(draw, 655286)


def show_price(price):
    with canvas(device) as draw:
        draw.point((0, 1), fill="white")
        draw.bitmap((2, 0), bitmapCurrencies['EUR'], fill="white")
        draw_number(draw, int(price))


def show_fees():
    with canvas(device) as draw:
        draw.point((0, 2), fill="white")
        draw.bitmap((2, 0), bitmapFees, fill="white")
        draw_number(draw, 310)


signal.signal(signal.SIGINT, handler)
device = get_device()
show_loading()


async def price_generator():
    uri = "wss://ws.kraken.com"
    async with websockets.connect(uri) as ws:
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
            try:
                result = await ws.recv()
                result = json.loads(result)

                if not isinstance(result, Mapping):
                    _, details, _, _ = result
                    price = float(details[0][0])
                    print("Price changed! %s" % f'{int(price):,}')

                    if time.monotonic() - last_switch >= 15:
                        card = (card + 1) % 3
                        print("Showing card %d" % card)
                        last_switch = time.monotonic()

                    if card == 0:
                        show_height()
                    elif card == 1:
                        show_price(price)
                    elif card == 2:
                        show_fees()
            except Exception as error:
                print('Caught this error: ' + repr(error))
                time.sleep(3)


async def hello():
    last_switch = time.monotonic()
    card = 0


asyncio.get_event_loop().run_until_complete(hello())
