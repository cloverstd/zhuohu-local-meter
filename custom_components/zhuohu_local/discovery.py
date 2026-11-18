"""Discover supported meters using the standard Gizwits UDP exchange."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import socket

from .profiles import PRODUCT_FINGERPRINTS
from .protocol import VERSION

DISCOVERY_PORT = 12414
DISCOVERY_REQUEST = VERSION + b"\x03\x00\x00\x03"
LAN_NETWORKS = tuple(
    IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


@dataclass(frozen=True)
class DiscoveredMeter:
    host: str
    mac: str
    kind: str


def lan_ip(value):
    address = IPv4Address(value)
    if not any(address in network for network in LAN_NETWORKS):
        raise ValueError("Expected a private IPv4 address")
    return str(address)


def _parse_response(packet, host):
    if not packet.startswith(VERSION):
        return None
    size = 0
    header_end = len(VERSION)
    for offset in range(4):
        if header_end >= len(packet):
            return None
        byte = packet[header_end]
        header_end += 1
        size |= (byte & 127) << (7 * offset)
        if byte < 128:
            break
    else:
        return None
    if size != len(packet) - header_end or packet[header_end:header_end + 3] != b"\x00\x00\x04":
        return None

    body = memoryview(packet)[header_end + 3:]
    offset = 0

    def next_field():
        nonlocal offset
        if len(body) - offset < 2:
            raise ValueError("Missing discovery field")
        count = int.from_bytes(body[offset:offset + 2], "big")
        offset += 2
        value = body[offset:offset + count]
        if len(value) != count:
            raise ValueError("Truncated discovery field")
        offset += count
        return bytes(value)

    try:
        identifier = next_field()
        mac = next_field()
        product = next_field()
        if len(product) != 32:
            product = next_field()
    except ValueError:
        return None
    if len(identifier) not in (0, 22) or len(mac) != 6 or not any(mac) or mac[0] & 1:
        return None
    if len(product) != 32 or not all(byte in b"0123456789abcdefABCDEF" for byte in product):
        return None
    kind = PRODUCT_FINGERPRINTS.get(sha256(product.lower()).hexdigest())
    return DiscoveredMeter(host, mac.hex(":"), kind) if kind else None


async def _discover(destinations, source, accepts, duration, *, first_only=False):
    loop = asyncio.get_running_loop()
    meters = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.setblocking(False)
        udp.bind((source, 0))

        async def receive():
            async with asyncio.timeout(duration):
                while True:
                    packet, (host, port) = await loop.sock_recvfrom(udp, 2048)
                    if port in (DISCOVERY_PORT, 2415) and accepts(host):
                        if (meter := _parse_response(packet, host)) is not None:
                            meters[meter.mac] = meter
                            if first_only:
                                return

        listener = asyncio.create_task(receive())
        try:
            for index, target in enumerate(destinations):
                try:
                    await loop.sock_sendto(udp, DISCOVERY_REQUEST, (target, DISCOVERY_PORT))
                except OSError:
                    continue
                if first_only:
                    if (await asyncio.wait((listener,), timeout=0.6))[0]:
                        break
                elif index % 32 == 31:
                    await asyncio.sleep(0.01)
            await listener
        except TimeoutError:
            pass
        finally:
            if not listener.done():
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
    return list(meters.values())


async def async_probe(host):
    host = lan_ip(host)
    results = await _discover(
        [host] * 3, "0.0.0.0", lambda peer: peer == host, 2.5, first_only=True
    )
    return results[0] if results else None


async def async_scan(adapters):
    interfaces = {}
    for adapter in adapters:
        if not adapter.get("enabled"):
            continue
        for info in adapter["ipv4"]:
            interface = IPv4Interface(f'{info["address"]}/{info["network_prefix"]}')
            if interface.network.prefixlen >= 31:
                continue
            if any(interface.network.subnet_of(lan) for lan in LAN_NETWORKS):
                interfaces.setdefault(interface.network, interface.ip)

    async def scan_interface(network, local_ip):
        destinations = [str(network.broadcast_address)]
        if network.num_addresses <= 512:
            destinations.extend(str(ip) for ip in network.hosts() if ip != local_ip)
        return await _discover(
            destinations,
            str(local_ip),
            lambda host: IPv4Address(host) in network,
            2.5,
        )

    if not interfaces:
        return await _discover(
            ["255.255.255.255"],
            "0.0.0.0",
            lambda host: any(IPv4Address(host) in network for network in LAN_NETWORKS),
            2.5,
        )

    results = await asyncio.gather(
        *(scan_interface(network, local_ip) for network, local_ip in interfaces.items()),
        return_exceptions=True,
    )
    devices = {
        meter.mac: meter
        for result in results if not isinstance(result, BaseException)
        for meter in result
    }
    return sorted(devices.values(), key=lambda meter: IPv4Address(meter.host))
