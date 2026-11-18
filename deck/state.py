"""Persistent workspace preferences and history. Sessions live in memory."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock


class State:
    def __init__(self, file: Path, prompts: Path):
        self.file, self.prompts = file, prompts
        self.guard = RLock()
        self.data = (
            json.loads(file.read_text(encoding="utf-8"))
            if file.exists()
            else {"active": None, "workspaces": {}}
        )

    def _save(self):
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.file.parent,
            prefix=self.file.name + ".",
            suffix=".tmp",
            encoding="utf-8",
            delete=False,
        ) as out:
            tmp = Path(out.name)
            try:
                json.dump(self.data, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            except BaseException:
                out.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            os.replace(tmp, self.file)
        finally:
            tmp.unlink(missing_ok=True)

    def open(self, path):
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Choose an existing directory.")
        wid = hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()[:20]
        with self.guard:
            self.data["workspaces"].setdefault(
                wid,
                {
                    "path": str(path),
                    "prefs": {"enter_sends": True, "prompt_library": str(self.prompts)},
                    "history": [],
                },
            )
            self.data["active"] = wid
            self._save()
        return wid

    def entry(self, wid):
        with self.guard:
            try:
                item = self.data["workspaces"][wid]
            except KeyError:
                raise ValueError("Unknown workspace.") from None
            return {
                **{key: value for key, value in item.items() if key != "saved_models"},
                "prefs": dict(item["prefs"]),
                "history": list(item["history"]),
            }

    def preferences(self, wid, values):
        with self.guard:
            self.data["workspaces"][wid]["prefs"].update(values)
            self._save()

    def saved_models(self, wid, seed, action=None, name=None, enabled=None):
        """Store picker visibility independently of Aider's model launch setting."""
        with self.guard:
            entry = self.data["workspaces"][wid]
            original = entry.get("saved_models")
            models = dict(original) if original is not None else dict(seed)
            if action == "save":
                models.setdefault(name, enabled)
            elif action == "remove":
                models.pop(name, None)
            elif action == "visibility":
                if not enabled and name not in models:
                    raise ValueError("This model is no longer in the saved list.")
                models[name] = enabled
            if action and original != models:
                entry["saved_models"] = models
                try:
                    self._save()
                except OSError:
                    if original is None:
                        del entry["saved_models"]
                    else:
                        entry["saved_models"] = original
                    raise
            return models

    def ollama_tags(self, wid, tag=None, *, remove=False):
        with self.guard:
            prefs = self.data["workspaces"][wid]["prefs"]
            old = prefs.get("ollama_tags", [])
            tags = list(old)
            if remove and tag in tags:
                tags.remove(tag)
            elif tag and not remove and tag not in tags:
                tags.append(tag)
            if tags != old:
                prefs["ollama_tags"] = tags
                try:
                    self._save()
                except OSError:
                    prefs["ollama_tags"] = old
                    raise
            return list(tags)

    def remember(self, wid, text):
        with self.guard:
            history = self.data["workspaces"][wid]["history"]
            if history[-1:] != [text]:
                history.append(text)
                del history[:-200]
                self._save()
