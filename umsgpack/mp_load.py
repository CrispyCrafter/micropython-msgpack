# mp_load.py A lightweight MessagePack serializer and deserializer module.

# Adapted for MicroPython by Peter Hinch

# Copyright (c) 2021 Peter Hinch Released under the MIT License see LICENSE

# Original source: https://github.com/vsergeev/u-msgpack-python
# See __init__.py for details of changes made for MicroPython.

import struct
import collections
import uio
from . import *
try:
    from . import umsgpack_ext
except ImportError:
    pass

def _fail():  # Debug code should never be called.
    raise Exception('Logic error')


def _read_except(fp, n):
    if n == 0:
        return b""

    data = fp.read(n)
    if len(data) == 0:
        raise InsufficientDataException()

    while len(data) < n:
        chunk = fp.read(n - len(data))
        if len(chunk) == 0:
            raise InsufficientDataException()

        data += chunk

    return data

def _re0(s, fp, n):
    return struct.unpack(s, _read_except(fp, n))[0]

def _unpack_integer(code, fp):
    ic = ord(code)
    if (ic & 0xe0) == 0xe0:
        return struct.unpack("b", code)[0]
    if (ic & 0x80) == 0x00:
        return struct.unpack("B", code)[0]
    ic -= 0xcc
    off = ic << 1
    try:
        s = "B >H>I>Qb >h>i>q"[off : off + 2]
    except IndexError:
        _fail()
    return _re0(s.strip(), fp, 1 << (ic & 3))


def _unpack_float(code, fp):
    ic = ord(code)
    if ic == 0xca:
        return _re0(">f", fp, 4)
    if ic == 0xcb:
        return _re0(">d", fp, 8)
    _fail()


def _unpack_string(code, fp, options):
    ic = ord(code)
    if (ic & 0xe0) == 0xa0:
        length = ic & ~0xe0
    elif ic == 0xd9:
        length = _re0("B", fp, 1)
    elif ic == 0xda:
        length = _re0(">H", fp, 2)
    elif ic == 0xdb:
        length = _re0(">I", fp, 4)
    else:
        _fail()

    data = _read_except(fp, length)
    try:
        return str(data, 'utf-8')  # Preferred MP way to decode
    except:  # MP does not have UnicodeDecodeError
        if options.get("allow_invalid_utf8"):
            return data  # MP Remove InvalidString class: subclass of built-in class
        raise InvalidStringException("unpacked string is invalid utf-8")


def _unpack_binary(code, fp):
    ic = ord(code)
    if ic == 0xc4:
        length = _re0("B", fp, 1)
    elif ic == 0xc5:
        length = _re0(">H", fp, 2)
    elif ic == 0xc6:
        length = _re0(">I", fp, 4)
    else:
        _fail()

    return _read_except(fp, length)


def _unpack_ext(code, fp, options):
    ic = ord(code)
    n = b'\xd4\xd5\xd6\xd7\xd8'.find(code)
    length = 0 if n < 0 else 1 << n
    if not length:
        if ic == 0xc7:
            length = _re0("B", fp, 1)
        elif ic == 0xc8:
            length = _re0(">H", fp, 2)
        elif ic == 0xc9:
            length = _re0(">I", fp, 4)
        else:
            _fail()

    ext_type = _re0("b", fp, 1)
    ext_data = _read_except(fp, length)

    # Create extension object
    ext = Ext(ext_type, ext_data)

    # Unpack with ext handler, if we have one
    ext_handlers = options.get("ext_handlers")
    if ext_handlers and ext.type in ext_handlers:
        return ext_handlers[ext.type](ext)
    # Unpack with ext classes, if type is registered
    if ext_type in ext_type_to_class:
        try:
            return ext_type_to_class[ext_type].unpackb(ext_data)
        except AttributeError:
            raise NotImplementedError("Ext class {:s} lacks unpackb()".format(repr(ext_type_to_class[ext_type])))

    return ext

def _unpack_array(code, fp, options):
    ic = ord(code)
    if (ic & 0xf0) == 0x90:
        length = (ic & ~0xf0)
    elif ic == 0xdc:
        length = _re0(">H", fp, 2)
    elif ic == 0xdd:
        length = _re0(">I", fp, 4)
    else:
        _fail()
    g = (load(fp, options) for i in range(length))  # generator
    return tuple(g) if options.get('use_tuple') else list(g)


def _deep_list_to_tuple(obj):
    if isinstance(obj, list):
        return tuple([_deep_list_to_tuple(e) for e in obj])
    return obj


def _unpack_map(code, fp, options):
    ic = ord(code)
    if (ic & 0xf0) == 0x80:
        length = (ic & ~0xf0)
    elif ic == 0xde:
        length = _re0(">H", fp, 2)
    elif ic == 0xdf:
        length = _re0(">I", fp, 4)
    else:
        _fail()

    d = {} if not options.get('use_ordered_dict') \
        else collections.OrderedDict()
    for _ in range(length):
        # Unpack key
        k = load(fp, options)

        if isinstance(k, list):
            # Attempt to convert list into a hashable tuple
            k = _deep_list_to_tuple(k)
        try:
            hash(k)
        except:
            raise UnhashableKeyException(
                "unhashable key: \"{:s}\"".format(str(k)))
        if k in d:
            raise DuplicateKeyException(
                "duplicate key: \"{:s}\" ({:s})".format(str(k), str(type(k))))

        # Unpack value
        v = load(fp, options)

        try:
            d[k] = v
        except TypeError:
            raise UnhashableKeyException(
                "unhashable key: \"{:s}\"".format(str(k)))
    return d


def load(fp, options):
    code = _read_except(fp, 1)
    ic = ord(code)
    if (ic <= 0x7f) or (0xcc <= ic <= 0xd3) or (0xe0 <= ic <= 0xff):
        return _unpack_integer(code, fp)
    if ic <= 0xc9:
        if ic <= 0xc3:
            if ic <= 0x8f:
                return _unpack_map(code, fp, options)
            if ic <= 0x9f:
                return _unpack_array(code, fp, options)
            if ic <= 0xbf:
                return _unpack_string(code, fp, options)
            if ic == 0xc1:
                raise ReservedCodeException("got reserved code: 0xc1")
            return (None, 0, False, True)[ic - 0xc0]
        if ic <= 0xc6:
            return _unpack_binary(code, fp)
        return _unpack_ext(code, fp, options)
    if ic <= 0xcb:
        return _unpack_float(code, fp)
    if ic <= 0xd8:
        return _unpack_ext(code, fp, options)
    if ic <= 0xdb:
        return _unpack_string(code, fp, options)
    if ic <= 0xdd:
        return _unpack_array(code, fp, options)
    return _unpack_map(code, fp, options)

# Interface to __init__.py

def loads(s, options):
    if not isinstance(s, (bytes, bytearray)):
        raise TypeError("packed data must be type 'bytes' or 'bytearray'")
    return load(uio.BytesIO(s), options)
