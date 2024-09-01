"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
from pathlib import Path

_current_dir = Path(__file__).resolve().parent

lockread_script = (_current_dir / "lockread.lua").read_text(encoding="utf-8")

unlockread_script = (_current_dir / "unlockread.lua").read_text(encoding="utf-8")

lockwrite_script = (_current_dir / "lockwrite.lua").read_text(encoding="utf-8")

unlockwrite_script = (_current_dir / "unlockwrite.lua").read_text(encoding="utf-8")

cancellockwrite_script = (_current_dir / "cancellockwrite.lua").read_text(
    encoding="utf-8"
)

get_state_script = (_current_dir / "get_state.lua").read_text(encoding="utf-8")
