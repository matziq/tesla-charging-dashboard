#!/usr/bin/env python3
"""Windows per-user encrypted storage for Tesla offline credentials."""

import ctypes
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


MAX_RECORD_BYTES = 64 * 1024
STORE_NAME = "offline-token.bin"


class DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("Offline token storage requires Windows DPAPI")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(DataBlob),
    ]
    operation.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = DataBlob(len(data), source)
    output_blob = DataBlob()
    try:
        if not operation(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob)):
            raise ValueError("Windows could not protect or open offline credentials")
        try:
            return ctypes.string_at(output_blob.data, output_blob.size)
        finally:
            ctypes.memset(output_blob.data, 0, output_blob.size)
            kernel32.LocalFree(output_blob.data)
    finally:
        ctypes.memset(source, 0, len(data))


def _valid_field(value):
    return (isinstance(value, str) and value and len(value) <= 8192 and value.isascii()
            and all(33 <= ord(char) < 127 for char in value))


def default_directory():
    if os.name != "nt":
        raise ValueError("Offline token storage requires Windows")
    location = os.environ.get("LOCALAPPDATA")
    if not location or not Path(location).is_absolute():
        raise ValueError("Windows per-user application data directory is unavailable")
    return Path(location) / "TeslaChargingDashboard"


class TokenStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else default_directory()
        self.path = self.directory / STORE_NAME

    @contextmanager
    def locked(self):
        if os.name != "nt":
            raise ValueError("Offline token storage requires Windows")
        import msvcrt

        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink() or self.path.is_symlink():
                raise ValueError("Offline token location must not be a symbolic link")
            lock_path = self.directory / "offline-token.lock"
            if lock_path.is_symlink():
                raise ValueError("Offline token lock must not be a symbolic link")
            lock = lock_path.open("a+b")
        except OSError:
            raise ValueError("Could not access offline credential storage") from None
        with lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("Another offline authorization or refresh is in progress") from None
            try:
                yield self
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def read(self):
        try:
            with self.path.open("rb") as source:
                encrypted = source.read(MAX_RECORD_BYTES + 1)
        except OSError:
            raise ValueError("Offline credentials are unavailable; complete offline authorization first") from None
        if not encrypted or len(encrypted) > MAX_RECORD_BYTES:
            raise ValueError("Offline credential record has an invalid size")
        try:
            record = json.loads(_dpapi(encrypted, decrypt=True))
        except (UnicodeError, ValueError):
            raise ValueError("Offline credential record could not be opened") from None
        if (not isinstance(record, dict) or set(record) != {"client_id", "refresh_token", "state"}
                or not _valid_field(record["client_id"]) or not _valid_field(record["refresh_token"])
                or record["state"] not in ("ready", "pending")):
            raise ValueError("Offline credential record has an invalid format")
        return record["client_id"], record["refresh_token"], record["state"]

    def write(self, client_id, refresh_token, state="ready"):
        if not _valid_field(client_id) or not _valid_field(refresh_token) or state not in ("ready", "pending"):
            raise ValueError("Offline credentials have an invalid format")
        data = json.dumps({"client_id": client_id, "refresh_token": refresh_token, "state": state}).encode("utf-8")
        encrypted = _dpapi(data)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".offline-", dir=self.directory, delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(encrypted)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            raise ValueError("Could not save rotated offline credentials; check storage before retrying") from None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
