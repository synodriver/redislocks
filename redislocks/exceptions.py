"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""


class NotAvailable(Exception):
    """Raised when unable to acquire the Semaphore in non-blocking mode"""
