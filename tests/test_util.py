# -*- coding: utf-8 -*-
import unittest
from array import array

from redislocks.utils import ensure_bytes, ensure_str


class TestUtil(unittest.TestCase):
    def test_ensure_str(self):
        self.assertEquals(ensure_str("abc"), "abc")
        self.assertEquals(ensure_str(b"abc"), "abc")
        self.assertEquals(ensure_str(bytearray(b"abc")), "abc")
        self.assertEquals(ensure_str(memoryview(b"abc")), "abc")

    def test_ensure_bytes(self):
        self.assertEquals(ensure_bytes(array("b", b"dada")), b"dada")
        self.assertEquals(ensure_bytes("abc"), b"abc")
        self.assertEquals(ensure_bytes(b"abc"), b"abc")
        self.assertEquals(ensure_bytes(bytearray(b"abc")), b"abc")
        self.assertEquals(ensure_bytes(memoryview(b"abc")), b"abc")
        self.assertEquals(ensure_bytes(42), b"42")
        self.assertEquals(ensure_bytes(42.0), b"42.0")
        self.assertEquals(ensure_bytes(42.0), b"42.0")
