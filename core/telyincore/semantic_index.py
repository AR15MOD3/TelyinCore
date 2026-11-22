"""One semantic implementation for native and isolated runtimes.

Only derived vectors are written. Candidate visibility remains the caller's job.
"""
from pathlib import Path
from . import embeddings, search


class SemanticIndex:
    def __init__(self, runtime, *, model_path=embeddings.DEFAULT_MODEL, batch_size=2, memory_home=None):
        self.runtime = Path(runtime)
        self.model_path = str(model_path)
        self.batch_size = max(1, min(8, batch_size))
        self.last_error = ""
        self.memory_home = memory_home
        self._reported = None

    def _report(self, state):
        kind = state['state']
        if kind != self._reported:
            import logging
            message = {'ready': '语义索引已就绪，新增与修改会自动增量补建',
                       'building': '正在后台补建语义索引，字面检索继续可用',
                       'off': '语义检索未就绪：' + state.get('why', ''),
                       'retry': '语义补建失败，将自动重试：' + state.get('why', '')}[kind]
            logging.getLogger('semantic').log(logging.WARNING if kind in ('off', 'retry') else logging.INFO, message)
            self._reported = kind
        return state

    @staticmethod
    def chunks(body):
        # Bound each CPU inference and cover document tails instead of truncating.
        body = str(body)
        return [body[start:start + 2000] for start in range(0, max(1, len(body)), 1800)]

    @property
    def jar(self):
        return self.runtime / "vectors"

    def maintain(self):
        reason = embeddings.availability(self.model_path)
        if reason:
            self.last_error = reason
            return self._report({"state": "off", "why": reason})
        try:
            # Serialize selection + inference so a search cannot duplicate this work.
            with embeddings._inference_lock:
                rows = search.the_pairs(self.runtime / "search")
                if self.memory_home is not None:
                    from . import memory
                    rows += [(where, row.text) for where, row in memory._documents(self.memory_home)
                             if row.role not in ('system', 'developer')]
                had = embeddings.the_jar(self.jar, model_path=self.model_path)
                missing = list(dict.fromkeys(chunk for _, body in rows for chunk in self.chunks(body)
                                            if embeddings.the_key(chunk) not in had))
                chosen = missing[:self.batch_size]
                expected = embeddings._content_identity(self.model_path)
                if chosen:
                    vectors = embeddings.embed(chosen, model_path=self.model_path)
                    embeddings.put_the_jar(self.jar, dict(zip(map(embeddings.the_key, chosen), vectors)),
                                           model_path=self.model_path, expected_content=expected)
                self.last_error = ""
                return self._report({"state": "building" if len(missing) > len(chosen) else "ready",
                        "embedded": len(chosen), "remaining": len(missing) - len(chosen)})
        except Exception as error:
            self.last_error = type(error).__name__ + ": " + str(error)[:160]
            return self._report({"state": "retry", "why": self.last_error})

    def health(self, bodies=None):
        try:
            rows = list(bodies) if bodies is not None else [body for _, body in search.the_pairs(self.runtime / "search")]
            reason = embeddings.availability(self.model_path) or self.last_error
            if reason:
                return dict(state="off", lines=len(rows), behind=0, why=reason)
            behind = embeddings.the_backlog(list(dict.fromkeys(chunk for body in rows for chunk in self.chunks(body))), self.jar, model_path=self.model_path)
            if behind:
                return dict(state="degraded", lines=len(rows), behind=behind,
                            why=f"后台正在补建 {behind} 条语义向量，完成后自动恢复；字面检索可用")
            return dict(state="work" if rows else "off", lines=len(rows), behind=0,
                        why="" if rows else "索引里没有正文（底料为空）")
        except Exception as error:
            return dict(state="off", lines=0, behind=0, why=type(error).__name__ + ": " + str(error)[:120])

    def rank_texts(self, query, bodies):
        try:
            if self.health(bodies)["state"] != "work":
                return search.SemanticUnavailable()
            chunks = [(index, chunk) for index, body in enumerate(bodies) for chunk in self.chunks(body)]
            # All candidates were visibility-filtered before this call. Original
            # memory receipts, provenance and full-text references remain intact.
            got = embeddings.a_road([chunk for _, chunk in chunks], query, model_path=self.model_path,
                                    at_most=len(chunks), jar=self.jar)
            return list(dict.fromkeys(chunks[int(index)][0] for index in got))
        except Exception as error:
            self.last_error = type(error).__name__ + ": " + str(error)[:160]
            return search.SemanticUnavailable()

    def road(self, query, names):
        try:
            allowed = set(names)
            pairs = [(key, body) for key, body in search.the_pairs(self.runtime / "search") if key in allowed]
            if not pairs:
                return search.SemanticUnavailable()
            got = self.rank_texts(query, [body for _, body in pairs])
            if isinstance(got, search.SemanticUnavailable):
                return got
            positions = {name: i for i, name in enumerate(names)}
            return [positions[pairs[index][0]] for index in got]
        except Exception as error:
            self.last_error = type(error).__name__ + ": " + str(error)[:160]
            return search.SemanticUnavailable()
