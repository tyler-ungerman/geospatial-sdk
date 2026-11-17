"""Client is lazy and configurable."""

from __future__ import annotations

from .backends import R2Store

_store = R2Store()

write_json = _store.write_json
read_json = _store.read_json
write_text = _store.write_text
read_text = _store.read_text
exists = _store.exists
list_keys = _store.list_keys
delete = _store.delete
upload_file = _store.upload_file
download_file = _store.download_file
