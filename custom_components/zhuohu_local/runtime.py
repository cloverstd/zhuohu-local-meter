"""Keep a read-only meter session alive and deliver changes to entities."""

import asyncio
from contextlib import suppress
import logging
from time import monotonic

from . import protocol

LOGGER = logging.getLogger(__name__)
MAX_SAMPLE_AGE = 120
SESSION_ERRORS = (OSError, EOFError, ValueError, TimeoutError, protocol.PairingUnavailable)


async def _open(host):
    async with asyncio.timeout(5):
        return await asyncio.open_connection(host, protocol.PORT)


async def _read_values(reader, profile):
    command, body = await protocol.read(reader)
    payload = protocol.status_payload(command, body)
    if payload is None:
        return {}
    try:
        return profile.decode(payload)
    except ValueError:
        return {}


async def async_validate(host, profile):
    reader, writer = await _open(host)
    try:
        await protocol.authenticate(reader, writer, None)
        protocol.send(writer, protocol.READ_STATE, b"\x12")
        await writer.drain()
        async with asyncio.timeout(5):
            while not await _read_values(reader, profile):
                pass
    finally:
        await protocol.close(writer)


class MeterRuntime:
    def __init__(self, host, profile):
        self.host = host
        self.profile = profile
        self.values = {}
        self.available = False
        self.last_report = 0.0
        self._listeners = set()

    def listen(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _notify(self, keys):
        for listener in tuple(self._listeners):
            listener(keys)

    def _offline(self):
        self.values.clear()
        if self.available:
            self.available = False
            self._notify(None)

    def _apply(self, values):
        self.last_report = monotonic()
        changed = {key for key in values if self.values.get(key) != values[key]}
        self.values.update(values)
        if not self.available:
            self.available = True
            self._notify(None)
        elif changed:
            self._notify(changed)

    async def run(self):
        pairing = None
        retry = 1
        while True:
            writer = None
            keepalive = None
            try:
                reader, writer = await _open(self.host)
                pairing = await protocol.authenticate(reader, writer, pairing)
                self.values.clear()
                keepalive = asyncio.create_task(protocol.maintain_connection(writer))
                while True:
                    async with asyncio.timeout(12):
                        values = await _read_values(reader, self.profile)
                    if keepalive.done():
                        keepalive.result()
                    if values:
                        retry = 1
                        self._apply(values)
                    elif monotonic() - self.last_report >= MAX_SAMPLE_AGE:
                        self._offline()
            except SESSION_ERRORS as error:
                if isinstance(error, protocol.PairingUnavailable):
                    pairing = None
                LOGGER.debug("Local meter %s ended its session: %s", self.host, type(error).__name__)
            finally:
                self._offline()
                if keepalive is not None:
                    keepalive.cancel()
                    with suppress(asyncio.CancelledError, OSError):
                        await keepalive
                if writer is not None:
                    await protocol.close(writer)
            await asyncio.sleep(retry)
            retry = min(2 * retry, 30)
