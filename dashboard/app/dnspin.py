"""Name lookups that do not depend on this machine's own resolver.

Why (2026-11-06): on the IV VPS the first resolver in /etc/resolv.conf answers NXDOMAIN for EVERY *.ivinfotech.com name and
`search localhost` then turns that into 127.0.0.1 — so from the dashboard every ivinfotech.com name "is" this very machine.
That happens to work for betas that live here, but a beta on another server is never reached: its check lands on this
machine's own web server, whose certificate is for another name ("the beta address does not answer"). The dashboard
therefore asks public resolvers itself (plain DNS over UDP, standard library only) and connects to the address they give,
still checking the certificate against the real name.

  public_ips(name)  → list of IPv4 strings, [] when the name does not exist, None when nobody answered (cannot tell)
  pinned(host, ip)  → context manager: inside it, THIS thread connects to `host` at `ip` (SNI / certificate use `host`)
"""
import contextlib
import random
import socket
import struct
import threading

RESOLVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
_real_getaddrinfo = socket.getaddrinfo
_local = threading.local()


def _question(name):
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("ascii")
        if not raw or len(raw) > 63:
            raise ValueError("bad label")
        out += bytes([len(raw)]) + raw
    return out + b"\x00" + struct.pack(">HH", 1, 1)            # type A, class IN


def _skip_name(data, pos):
    while True:
        n = data[pos]
        if n == 0:
            return pos + 1
        if n & 0xC0 == 0xC0:                                    # compression pointer: two bytes, the name ends here
            return pos + 2
        pos += 1 + n


def parse(data, qid):
    """(rcode, [ipv4, …]) from a DNS answer packet; ValueError when it is not the answer to our question."""
    if len(data) < 12:
        raise ValueError("short answer")
    rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != qid or not flags & 0x8000:
        raise ValueError("not our answer")
    pos = 12
    for _ in range(qd):
        pos = _skip_name(data, pos) + 4
    ips = []
    for _ in range(an):
        pos = _skip_name(data, pos)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(data[pos:pos + 4]))
        pos += rdlen
    return flags & 0x000F, ips


def _ask(name, server, timeout):
    qid = random.randrange(65536)
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + _question(name)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(4096)
    finally:
        s.close()
    return parse(data, qid)


def public_ips(name, timeout=3.0, resolvers=None):
    """IPv4 addresses public DNS gives for `name`; [] = the name has no address (NXDOMAIN / no A record);
    None = no resolver answered, so nothing is known."""
    for server in resolvers or RESOLVERS:
        try:
            rcode, ips = _ask(name, server, timeout)
        except (OSError, ValueError, IndexError, struct.error):
            continue
        if rcode == 0 and ips:
            return ips
        if rcode in (0, 3):
            return []
    return None


def _getaddrinfo(host, *args, **kwargs):
    pins = getattr(_local, "pins", None)
    if pins and host in pins:
        host = pins[host]
    return _real_getaddrinfo(host, *args, **kwargs)


@contextlib.contextmanager
def pinned(host, ip):
    """Inside the block this thread connects to `host` at `ip`. Other threads and other names are untouched."""
    if socket.getaddrinfo is not _getaddrinfo:
        socket.getaddrinfo = _getaddrinfo
    before = getattr(_local, "pins", None)
    _local.pins = {**(before or {}), host: ip}
    try:
        yield
    finally:
        _local.pins = before
