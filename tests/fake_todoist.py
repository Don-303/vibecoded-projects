"""An in-memory stand-in for Todoist's Sync API, served through httpx.MockTransport."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx


class FakeTodoist:
    def __init__(self, token: str = "secret") -> None:
        self.token = token
        self.version = 0
        self.items: dict[str, dict] = {}
        self.projects: dict[str, dict] = {}
        self.next_id = 1000
        self.online = True
        self.requests: list[dict] = []
        self.transport = httpx.MockTransport(self.handle)

    # ── helpers ──
    def _bump(self, obj: dict) -> dict:
        self.version += 1
        obj["_v"] = self.version
        return obj

    def _new_id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def public(self, obj: dict) -> dict:
        return {k: v for k, v in obj.items() if not k.startswith("_")}

    # ── "phone" actions ──
    def add_project(self, name: str) -> str:
        pid = self._new_id()
        self.projects[pid] = self._bump({"id": pid, "name": name, "is_deleted": False})
        return pid

    def phone_add(self, content: str, project: str = "Terminus", **fields) -> str:
        pid = next(p["id"] for p in self.projects.values() if p["name"] == project)
        iid = self._new_id()
        item = {"id": iid, "content": content, "project_id": pid, "labels": [], "checked": False,
                "is_deleted": False, "due": None, "duration": None, "completed_at": None}
        item.update(fields)
        self.items[iid] = self._bump(item)
        return iid

    def phone_edit(self, iid: str, **fields) -> None:
        self.items[iid].update(fields)
        self._bump(self.items[iid])

    def phone_complete(self, iid: str) -> None:
        self.phone_edit(iid, checked=True, completed_at="2026-09-25T08:30:00.000000Z")

    def phone_delete(self, iid: str) -> None:
        self.phone_edit(iid, is_deleted=True)

    def item_named(self, content: str) -> dict:
        return next(i for i in self.items.values() if i["content"] == content)

    # ── Sync endpoint ──
    def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("no route to host", request=request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "Unauthorized"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append(form)
        mapping, status = {}, {}
        for cmd in json.loads(form.get("commands", "[]")):
            try:
                self._run(cmd, mapping)
                status[cmd["uuid"]] = "ok"
            except KeyError:
                status[cmd["uuid"]] = {"error": "Item not found", "error_code": 22}

        token = form.get("sync_token")
        body: dict = {"temp_id_mapping": mapping, "sync_status": status,
                      "sync_token": str(self.version)}
        if token is not None:
            full = token == "*"
            since = 0 if full else int(token)
            types = json.loads(form.get("resource_types", "[]"))
            def pick(objs):
                out = [o for o in objs.values() if o["_v"] > since]
                if full:  # full sync lists only live objects
                    out = [o for o in out if not o.get("is_deleted") and not o.get("checked")]
                return [self.public(o) for o in out]
            body["full_sync"] = full
            if "items" in types:
                body["items"] = pick(self.items)
            if "projects" in types:
                body["projects"] = pick(self.projects)
        return httpx.Response(200, json=body)

    def _run(self, cmd: dict, mapping: dict) -> None:
        args, kind = cmd["args"], cmd["type"]
        if kind == "project_add":
            pid = self.add_project(args["name"])
            mapping[cmd["temp_id"]] = pid
        elif kind == "item_add":
            iid = self._new_id()
            item = {"id": iid, "labels": [], "checked": False, "is_deleted": False,
                    "due": None, "duration": None, "completed_at": None}
            item.update(args)
            self.items[iid] = self._bump(item)
            mapping[cmd["temp_id"]] = iid
        else:
            iid = mapping.get(args["id"], args["id"])  # temp ids resolve within a batch
            item = self.items[iid]
            if item["is_deleted"]:
                raise KeyError(iid)
            if kind == "item_update":
                item.update({k: v for k, v in args.items() if k != "id"})
            elif kind == "item_close":
                item["checked"] = True
                item["completed_at"] = datetime.now(timezone.utc).isoformat()
            elif kind == "item_uncomplete":
                item["checked"] = False
                item["completed_at"] = None
            elif kind == "item_delete":
                item["is_deleted"] = True
            self._bump(item)
