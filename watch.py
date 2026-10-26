import difflib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class WatchStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.db()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS watches (id TEXT PRIMARY KEY, origin TEXT, owner TEXT, url TEXT, condition TEXT, stamp INTEGER DEFAULT 0, snapshot TEXT DEFAULT '', active INTEGER DEFAULT 1)"
            )

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add(self, ident, origin, owner, url, condition):
        with closing(self.db()) as db, db:
            db.execute(
                "INSERT INTO watches(id,origin,owner,url,condition) VALUES(?,?,?,?,?)",
                (ident, origin, owner, url, condition),
            )

    def list(self, origin=None, owner=None):
        with closing(self.db()) as db:
            if origin is None:
                rows = db.execute("SELECT * FROM watches WHERE active=1").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM watches WHERE origin=? AND owner=? AND active=1",
                    (origin, owner),
                ).fetchall()
        return [dict(row) for row in rows]

    def advance(self, ident, stamp, text):
        with closing(self.db()) as db, db:
            db.execute(
                "UPDATE watches SET stamp=?, snapshot=? WHERE id=?",
                (stamp, text, ident),
            )

    def stop(self, ident, origin, owner):
        with closing(self.db()) as db, db:
            return (
                db.execute(
                    "UPDATE watches SET active=0 WHERE id=? AND origin=? AND owner=?",
                    (ident, origin, owner),
                ).rowcount
                > 0
            )


class ChangeClient:
    def __init__(self, url, key, transport=None):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Configure a valid changedetection.io base URL")
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/api/v1/",
            headers={"x-api-key": key},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    async def request(self, method, path, **kwargs):
        async with self.http.stream(method, path, **kwargs) as response:
            if response.status_code not in {200, 201, 204}:
                raise ValueError(f"changedetection.io HTTP {response.status_code}")
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError(
                        "Snapshot exceeds 2 MiB; refine the watch's CSS filter"
                    )
        return bytes(raw).decode("utf-8")

    async def create(self, url, domains):
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or not any(
                host == d.lower().strip().rstrip(".") for d in domains if d.strip()
            )
        ):
            raise ValueError(
                "URL host must exactly match an administrator-configured allowed domain"
            )
        result = json.loads(
            await self.request(
                "POST", "watch", json={"url": url, "title": "AstrBot evidence watch"}
            )
        )
        return str(result["uuid"])

    async def poll(self, watch, store, judge, send):
        """Process snapshots in timestamp order; commit a cursor only after delivery."""
        from uuid import UUID

        ident = str(UUID(watch["id"]))
        history = json.loads(await self.request("GET", f"watch/{ident}/history"))
        stamps = sorted(int(ts) for ts in history if str(ts).isdigit())
        previous = watch["snapshot"]
        cursor = watch["stamp"]
        for stamp in stamps:
            if stamp <= cursor:
                continue
            text = await self.request("GET", f"watch/{ident}/history/{stamp}")
            if cursor:
                diff = "\n".join(
                    difflib.unified_diff(
                        previous.splitlines(),
                        text.splitlines(),
                        fromfile="before",
                        tofile="after",
                        n=0,
                    )
                )
                if diff:
                    if len(diff) > 20000:
                        raise ValueError(
                            "Diff exceeds the evaluation limit; configure a narrower source filter"
                        )
                    decision = await judge(watch["origin"], watch["condition"], diff)
                    if (
                        not isinstance(decision, dict)
                        or type(decision.get("match")) is not bool
                    ):
                        raise ValueError(
                            "The model did not return a valid match decision"
                        )
                    if decision["match"]:
                        evidence = decision.get("evidence")
                        changed = [
                            line[1:]
                            for line in diff.splitlines()
                            if line.startswith(("+", "-"))
                            and not line.startswith(("+++", "---"))
                        ]
                        if (
                            not isinstance(evidence, list)
                            or not evidence
                            or len(evidence) > 5
                            or any(
                                not isinstance(e, str)
                                or not e.strip()
                                or e not in "\n".join(changed)
                                for e in evidence
                            )
                        ):
                            raise ValueError(
                                "Model evidence is not present in the actual changes"
                            )
                        reason = str(decision.get("reason", ""))[:500]
                        when = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                        message = (
                            f"🔎 条件命中：{watch['condition']}\n来源：{watch['url']}\n快照时间：{when}\n判断：{reason}\n原文证据：\n"
                            + "\n".join(f"> {e[:500]}" for e in evidence)
                            + f"\n变化对比（+新增 / -删除）：\n{diff[:3500]}\n监测ID：{ident}"
                        )
                        if await send(watch["origin"], message) is False:
                            raise ValueError(
                                "Notification could not be delivered; cursor retained"
                            )
            # The first snapshot establishes a baseline without an alert.
            store.advance(ident, stamp, text)
            cursor, previous = stamp, text

    async def close(self):
        await self.http.aclose()
