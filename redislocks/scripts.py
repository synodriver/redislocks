"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
from pathlib import Path

_current_dir = Path(__file__).resolve().parent

with open(_current_dir / "lockread.lua", encoding="utf-8") as lockread_f:
    lockread_script = lockread_f.read()

with open(_current_dir / "unlockread.lua", encoding="utf-8") as unlockread_f:
    unlockread_script = unlockread_f.read()

# with open(_current_dir / "checkcanread.lua", encoding="utf-8") as checkcanread_f:
#     checkcanread_script = checkcanread_f.read()

with open(
    _current_dir / "lockwrite_nowait.lua", encoding="utf-8"
) as lockwrite_nowait_f:
    lockwrite_nowait_script = lockwrite_nowait_f.read()

with open(_current_dir / "unlockwrite.lua", encoding="utf-8") as unlockwrite_f:
    unlockwrite_script = unlockwrite_f.read()


with open(_current_dir / "checklock.lua", encoding="utf-8") as checklock_f:
    checklock_script = checklock_f.read()
