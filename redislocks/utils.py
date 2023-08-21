"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""


def ensure_bytes(data) -> bytes:
    if isinstance(data, str):
        return data.encode("utf-8")
    elif isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    elif isinstance(data, bytes):
        return data
    elif isinstance(data, (int, float)):
        return str(data).encode()
    else:
        return bytes(data)


def ensure_str(data) -> str:
    if isinstance(data, (bytes, bytearray)):
        return data.decode()
    elif isinstance(data, memoryview):
        return data.tobytes().decode()
    elif isinstance(data, str):
        return data
    else:
        return str(data)
