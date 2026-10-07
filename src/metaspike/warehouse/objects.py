"""Object storage as the warehouse code needs it: four operations and nothing more.

    create   write an object that must not exist yet (immutable data)
    replace  overwrite an object only if it is still the generation that was read
             (the one mutable thing: a pointer)
    get      read one object by its exact name
    list     names under a prefix

Reads by exact name are the default. `list` is used only by an identity that holds a
whole-bucket role: an identity whose grants are conditional on an object-name prefix
is expected to be refused a listing, so nothing a collector or the catalogue job does
may depend on one.

`MemoryObjectStore` is the offline implementation and can be told to behave like such
a restricted identity. `GcsObjectStore` speaks the Cloud Storage JSON API over HTTPS
with a short-lived token from the runtime's metadata server; it holds no key.
"""

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

_GCS = "https://storage.googleapis.com"


class ObjectStoreError(Exception):
    """An object operation failed."""


class ObjectNotFound(ObjectStoreError):
    """No object has that name (or this identity may not read it)."""


class PreconditionFailed(ObjectStoreError):
    """The object was not in the state the write required. Nothing was written."""


class AccessDenied(ObjectStoreError):
    """This identity is not allowed to do that here."""


@dataclass(frozen=True)
class StoredObject:
    name: str
    data: bytes
    # Changes on every write of the name. Opaque: only ever compared for equality.
    generation: int


class ObjectStore(Protocol):
    def create(self, name: str, data: bytes, content_type: str = "application/json") -> int:
        """Write a new object. Raises PreconditionFailed if the name is taken."""
        ...

    def replace(
        self, name: str, data: bytes, if_generation: int, content_type: str = "application/json"
    ) -> int:
        """Overwrite `name` only if it is still at `if_generation`."""
        ...

    def get(self, name: str) -> StoredObject:
        """The object. Raises ObjectNotFound."""
        ...

    def list(self, prefix: str) -> tuple[str, ...]:
        """Every object name that starts with `prefix`, sorted. Raises AccessDenied."""
        ...

    def delete(self, name: str) -> None:
        """Remove one object. Used only for our own scratch objects, never landed data."""
        ...


@dataclass
class _Bucket:
    objects: dict[str, StoredObject]
    writes: int = 0
    clock: int = 0


class MemoryObjectStore:
    """One bucket, in memory. Offline stand-in used by tests and local runs.

    `writable_prefixes` and `readable_prefixes` imitate prefix-conditional grants:
    when given, a write or read outside them is refused and listing is refused
    altogether, which is how such an identity is expected to behave in production.
    """

    def __init__(
        self,
        *,
        writable_prefixes: tuple[str, ...] | None = None,
        readable_prefixes: tuple[str, ...] | None = None,
        can_list: bool = True,
        can_overwrite: bool = True,
        _bucket: _Bucket | None = None,
    ) -> None:
        self._bucket = _Bucket(objects={}) if _bucket is None else _bucket
        self._writable = writable_prefixes
        self._readable = readable_prefixes
        self._can_list = can_list
        self._can_overwrite = can_overwrite

    def view(
        self,
        *,
        writable_prefixes: tuple[str, ...] | None = None,
        readable_prefixes: tuple[str, ...] | None = None,
        can_list: bool = True,
        can_overwrite: bool = True,
    ) -> "MemoryObjectStore":
        """The same bucket as another identity sees it."""
        return MemoryObjectStore(
            writable_prefixes=writable_prefixes,
            readable_prefixes=readable_prefixes,
            can_list=can_list,
            can_overwrite=can_overwrite,
            _bucket=self._bucket,
        )

    @property
    def writes(self) -> int:
        return self._bucket.writes

    def create(self, name: str, data: bytes, content_type: str = "application/json") -> int:
        self._may_write(name)
        if name in self._bucket.objects:
            raise PreconditionFailed(f"{name} already exists")
        return self._put(name, data)

    def replace(
        self, name: str, data: bytes, if_generation: int, content_type: str = "application/json"
    ) -> int:
        self._may_write(name)
        if not self._can_overwrite:
            raise AccessDenied(f"this identity may not overwrite {name}")
        current = self._bucket.objects.get(name)
        if current is None or current.generation != if_generation:
            raise PreconditionFailed(f"{name} is no longer at generation {if_generation}")
        return self._put(name, data)

    def get(self, name: str) -> StoredObject:
        if self._readable is not None and not name.startswith(self._readable):
            raise AccessDenied(f"this identity may not read {name}")
        found = self._bucket.objects.get(name)
        if found is None:
            raise ObjectNotFound(name)
        return found

    def list(self, prefix: str) -> tuple[str, ...]:
        if not self._can_list:
            raise AccessDenied("this identity may not list the bucket")
        return tuple(sorted(name for name in self._bucket.objects if name.startswith(prefix)))

    def delete(self, name: str) -> None:
        self._may_write(name)
        if not self._can_overwrite:
            raise AccessDenied(f"this identity may not delete {name}")
        if name not in self._bucket.objects:
            raise ObjectNotFound(name)
        del self._bucket.objects[name]

    def _may_write(self, name: str) -> None:
        if not name or name.startswith("/") or ".." in name.split("/"):
            raise ValueError(f"unsafe object name: {name!r}")
        if self._writable is not None and not name.startswith(self._writable):
            raise AccessDenied(f"this identity may not write {name}")

    def _put(self, name: str, data: bytes) -> int:
        self._bucket.clock += 1
        self._bucket.writes += 1
        generation = self._bucket.clock
        self._bucket.objects[name] = StoredObject(
            name=name, data=bytes(data), generation=generation
        )
        return generation


class GcsObjectStore:
    """One Cloud Storage bucket over the JSON API. No client library, no key file."""

    def __init__(self, client: httpx.Client, bucket: str, token: Callable[[], str]) -> None:
        self._client = client
        self._bucket = bucket
        self._token = token

    def create(self, name: str, data: bytes, content_type: str = "application/json") -> int:
        return self._upload(name, data, content_type, 0)

    def replace(
        self, name: str, data: bytes, if_generation: int, content_type: str = "application/json"
    ) -> int:
        return self._upload(name, data, content_type, if_generation)

    def get(self, name: str) -> StoredObject:
        response = self._client.get(
            f"{_GCS}/storage/v1/b/{self._bucket}/o/{urllib.parse.quote(name, safe='')}",
            params={"alt": "media"},
            headers=self._headers(),
        )
        if response.status_code == 404:
            raise ObjectNotFound(name)
        self._check(response, name)
        return StoredObject(
            name=name, data=response.content, generation=int(response.headers["x-goog-generation"])
        )

    def list(self, prefix: str) -> tuple[str, ...]:
        names: list[str] = []
        page: str | None = None
        while True:
            params = {"prefix": prefix, "fields": "items(name),nextPageToken"}
            if page is not None:
                params["pageToken"] = page
            response = self._client.get(
                f"{_GCS}/storage/v1/b/{self._bucket}/o", params=params, headers=self._headers()
            )
            self._check(response, prefix)
            body = response.json()
            names.extend(item["name"] for item in body.get("items", ()))
            page = body.get("nextPageToken")
            if page is None:
                return tuple(sorted(names))

    def delete(self, name: str) -> None:
        response = self._client.delete(
            f"{_GCS}/storage/v1/b/{self._bucket}/o/{urllib.parse.quote(name, safe='')}",
            headers=self._headers(),
        )
        if response.status_code == 404:
            raise ObjectNotFound(name)
        self._check(response, name)

    def _upload(self, name: str, data: bytes, content_type: str, if_generation: int) -> int:
        response = self._client.post(
            f"{_GCS}/upload/storage/v1/b/{self._bucket}/o",
            params={
                "uploadType": "media",
                "name": name,
                # 0 means "only if no live object has this name".
                "ifGenerationMatch": str(if_generation),
            },
            content=data,
            headers={**self._headers(), "Content-Type": content_type},
        )
        if response.status_code == 412:
            raise PreconditionFailed(f"{name} was not at generation {if_generation}")
        self._check(response, name)
        return int(response.json()["generation"])

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def _check(self, response: httpx.Response, subject: str) -> None:
        # The response body is deliberately not echoed: an error must never carry a
        # request header, and a status is enough to act on.
        if response.status_code in (401, 403):
            raise AccessDenied(f"{self._bucket}/{subject}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ObjectStoreError(f"{self._bucket}/{subject}: HTTP {response.status_code}")
