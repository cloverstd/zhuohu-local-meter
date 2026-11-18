"""Read-only Gizwits LAN connection for a configured meter."""

import asyncio
import struct


VERSION = b"\x00\x00\x00\x03"
BIND = 0x06
LOGIN = 0x08
PING = 0x15
READ_STATE = 0x90
STATUS_REPLY = 0x91
STATUS_REPORT = 0x93
PORT = 12416


class PairingUnavailable(RuntimeError):
    """The meter declined its normal local pairing request."""


def send(writer, command, data=b""):
    body = struct.pack("!BH", 0, command) + data
    writer.write(VERSION + bytes((len(body),)) + body)


async def read(reader):
    if await reader.readexactly(4) != VERSION:
        raise ValueError("Invalid GAgent version")
    size = 0
    for shift in range(0, 28, 7):
        byte = (await reader.readexactly(1))[0]
        size |= (byte & 127) << shift
        if byte < 128:
            break
    else:
        raise ValueError("Invalid GAgent length field")
    if not 3 <= size <= 4096:
        raise ValueError("Invalid GAgent frame size")
    body = await reader.readexactly(size)
    return int.from_bytes(body[1:3], "big"), body[3:]


async def expect(reader, response):
    async with asyncio.timeout(4):
        while True:
            command, body = await read(reader)
            if command == response:
                return body


async def authenticate(reader, writer, pairing):
    if pairing is None:
        send(writer, BIND)
        pairing = await expect(reader, BIND + 1)
        size = int.from_bytes(pairing[:2], "big")
        if not 1 <= size <= 32 or len(pairing) != size + 2:
            raise PairingUnavailable("Normal local pairing unavailable")
    send(writer, LOGIN, pairing)
    if await expect(reader, LOGIN + 1) != b"\x00":
        raise PairingUnavailable("Local login declined")
    return pairing


async def maintain_connection(writer):
    ticks = 0
    while True:
        if ticks % 10 == 0:
            send(writer, READ_STATE, b"\x12")
        if ticks:
            send(writer, PING)
        await writer.drain()
        await asyncio.sleep(3)
        ticks += 1


def status_payload(command, body):
    if command == STATUS_REPLY and body.startswith(b"\x13"):
        return body[1:]
    if command == STATUS_REPORT and len(body) >= 6:
        identity_size = int.from_bytes(body[4:6], "big")
        payload = body[6 + identity_size:]
        if identity_size == 22 and payload.startswith(b"\x14"):
            return payload[1:]
    return None


async def close(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 2)
    except (OSError, TimeoutError):
        pass
