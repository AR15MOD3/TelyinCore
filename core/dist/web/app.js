// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/status-json.ts
var StatusHttpError = class extends Error {
  constructor(url, status) {
    super(`${url} returned ${status}`);
    this.url = url;
    this.status = status;
    this.name = "StatusHttpError";
  }
  url;
  status;
};
var StatusTimeoutError = class extends Error {
  constructor(url, timeoutMs) {
    super(`${url} timed out after ${timeoutMs}ms`);
    this.url = url;
    this.timeoutMs = timeoutMs;
    this.name = "StatusTimeoutError";
  }
  url;
  timeoutMs;
};
var StaleStatusError = class extends Error {
  constructor(url) {
    super(`${url} response was invalidated`);
    this.url = url;
    this.name = "StaleStatusError";
  }
  url;
};
var inFlight = /* @__PURE__ */ new Map();
function invalidateStatus(url) {
  for (const [key2, entry] of inFlight) if (entry.url === url) inFlight.delete(key2);
}
function invalidateStatuses(urls) {
  for (const url of urls) invalidateStatus(url);
}
function snapshot(url, init, timeoutMs) {
  const headers = typeof Headers === "function" ? Array.from(new Headers(init.headers).entries()).sort() : init.headers ?? {};
  const key2 = JSON.stringify([url, headers, init.credentials ?? "same-origin"]);
  const shared = inFlight.get(key2);
  if (shared) return shared.promise;
  let promise;
  promise = (async () => {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    let timer;
    try {
      return await Promise.race([
        (async () => {
          const response = await fetch(url, {
            ...init,
            cache: "no-store",
            signal: controller?.signal
          });
          const body = typeof response.text === "function" ? await response.text() : JSON.stringify(await response.json());
          if (inFlight.get(key2)?.promise !== promise) throw new StaleStatusError(url);
          return { status: response.status ?? (response.ok ? 200 : 500), ok: response.ok, body };
        })(),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            controller?.abort();
            reject(new StatusTimeoutError(url, timeoutMs));
          }, timeoutMs);
        })
      ]);
    } finally {
      if (timer !== void 0) clearTimeout(timer);
      if (inFlight.get(key2)?.promise === promise) inFlight.delete(key2);
    }
  })();
  inFlight.set(key2, { url, promise });
  return promise;
}
async function pageFetch(url, init = {}, timeoutMs = 8e3) {
  if ((init.method ?? "GET").toUpperCase() !== "GET") {
    inFlight.clear();
    try {
      return await fetch(url, init);
    } finally {
      inFlight.clear();
    }
  }
  const result = await snapshot(url, init, timeoutMs);
  return {
    ok: result.ok,
    status: result.status,
    text: async () => result.body,
    json: async () => JSON.parse(result.body)
  };
}
async function getStatusJson(url, timeoutMs = 8e3) {
  const response = await pageFetch(url, {}, timeoutMs);
  if (!response.ok) throw new StatusHttpError(url, response.status);
  return await response.json();
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/display-storage.ts
var localKeys = /* @__PURE__ */ new Set([
  "ui-language",
  "telyincore-look-mode",
  "telyincore-look-preset",
  "telyincore-look-resolved-v1",
  "telyincore.rail-width",
  "plugin-nav-order",
  "plugin-update-muted"
]);
var sessionKeys = /* @__PURE__ */ new Set(["plugin-nav", "engine-drafts"]);
var localEdits = /* @__PURE__ */ new Map();
var sessionEdits = /* @__PURE__ */ new Map();
var prefix = null;
var loading = null;
var listeners = /* @__PURE__ */ new Set();
function displayStoragePrefix(value) {
  if (!value || typeof value !== "object") return null;
  const row2 = value;
  if (typeof row2.coreRoot !== "string" || !/^(?:[A-Za-z]:[\\/]|\/)/.test(row2.coreRoot) || /[\x00-\x1f]/.test(row2.coreRoot) || row2.coreRoot.length > 4096 || typeof row2.active !== "string" || row2.active.length === 0 || row2.active.length > 64 || row2.active.trim() !== row2.active || /[\\/\x00-\x1f]/.test(row2.active) || !Array.isArray(row2.bots) || !row2.bots.some((bot) => bot && typeof bot === "object" && bot.id === row2.active)) return null;
  const root = row2.coreRoot.replace(/\\/g, "/").replace(/\/+$/, "");
  return `telyincore.display.v1:${encodeURIComponent(root)}:${encodeURIComponent(row2.active)}:`;
}
function get(key2, session) {
  if (!(session ? sessionKeys : localKeys).has(key2)) return null;
  const edits = session ? sessionEdits : localEdits;
  if (prefix === null) return edits.get(key2) ?? null;
  try {
    const storage = session ? window.sessionStorage : window.localStorage;
    if (edits.has(key2)) {
      storage.setItem(prefix + key2, edits.get(key2));
      edits.delete(key2);
    }
    return storage.getItem(prefix + key2);
  } catch {
    return edits.get(key2) ?? null;
  }
}
function put(key2, value, session) {
  if (!(session ? sessionKeys : localKeys).has(key2)) return;
  (session ? sessionEdits : localEdits).set(key2, value);
  if (prefix === null) return;
  try {
    (session ? window.sessionStorage : window.localStorage).setItem(prefix + key2, value);
    (session ? sessionEdits : localEdits).delete(key2);
  } catch {
  }
}
var readDisplayLocal = (key2) => get(key2, false);
var writeDisplayLocal = (key2, value) => put(key2, value, false);
var readDisplaySession = (key2) => get(key2, true);
var writeDisplaySession = (key2, value) => put(key2, value, true);
function onDisplayScopeReady(listener) {
  if (prefix !== null) listener(prefix);
  else listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
function initializeDisplayScope() {
  if (loading === null) loading = (async () => {
    try {
      prefix = displayStoragePrefix(await getStatusJson("/infinity/bots", 5e3));
      if (prefix === null) return false;
      for (const [key2, value] of localEdits) put(key2, value, false);
      for (const [key2, value] of sessionEdits) put(key2, value, true);
      for (const listener of listeners) {
        try {
          listener(prefix);
        } catch {
        }
      }
      listeners.clear();
      return true;
    } catch {
      return false;
    }
  })();
  return loading;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-navigation-order.ts
var orderKey = "plugin-nav-order";
var units = (list) => Array.from(list.children);
function readOrder() {
  try {
    const value = JSON.parse(readDisplayLocal(orderKey) || "[]");
    return Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}
function button(label, className, text2) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.title = label;
  b.setAttribute("aria-label", label);
  if (text2) b.textContent = text2;
  return b;
}
function glide(list, change, exclude) {
  const siblings = units(list).filter((el6) => el6 !== exclude);
  const before = siblings.map((el6) => el6.getBoundingClientRect().top);
  change();
  siblings.forEach((el6, i) => {
    el6.getAnimations?.().forEach((a) => a.cancel());
    const delta = before[i] - el6.getBoundingClientRect().top;
    if (Math.abs(delta) > 1 && !window.matchMedia("(prefers-reduced-motion: reduce)").matches)
      el6.animate?.([{ transform: "translateY(" + delta + "px)" }, { transform: "none" }], {
        duration: 165,
        easing: "cubic-bezier(.2,.75,.25,1)"
      });
  });
}
function pluginNavigationOrder(rail, refresh2) {
  let editing = false;
  function mount(entries, rememberNew = false) {
    const overview = rail.querySelector('[data-page="infinity"]');
    const tree = overview?.parentElement?.querySelector(".rail-sublist");
    const changed = () => rail.dispatchEvent(new Event("plugin-navigation-order"));
    if (!overview || !tree) {
      changed();
      return;
    }
    tree.classList.add("plugin-nav-tree");
    const order = readOrder();
    const rank = new Map(order.map((id, i) => [id, i]));
    const parents = new Map(entries.map((item) => [item.id, item.navigationParent]));
    const nodes = /* @__PURE__ */ new Map();
    const lists = /* @__PURE__ */ new Map();
    for (const link of Array.from(tree.querySelectorAll("a[data-page]"))) {
      const unit = document.createElement("div");
      unit.className = "plugin-nav-unit";
      const id = link.dataset.page;
      unit.dataset.navId = id;
      const row2 = document.createElement("div");
      row2.className = "plugin-nav-row";
      row2.append(link);
      const grip = button("拖动" + link.textContent + "，也可用上下方向键排序", "plugin-nav-grip");
      row2.append(grip);
      unit.append(row2);
      nodes.set(id, unit);
    }
    function parentOf(id) {
      const parent = parents.get(id);
      if (!parent || !nodes.has(parent)) return;
      const visited = /* @__PURE__ */ new Set([id]);
      let next = parent;
      while (next && nodes.has(next)) {
        if (visited.has(next)) return;
        visited.add(next);
        next = parents.get(next);
      }
      return parent;
    }
    const ids = [...nodes.keys()].sort(
      (a, b) => (rank.get(a) ?? order.length) - (rank.get(b) ?? order.length)
    );
    for (const id of ids) {
      const parent = parentOf(id);
      let list = tree;
      if (parent) {
        if (!lists.has(parent)) {
          const children = document.createElement("div");
          children.className = "plugin-nav-children";
          nodes.get(parent).append(children);
          lists.set(parent, children);
        }
        list = lists.get(parent);
      }
      list.append(nodes.get(id));
    }
    if (rememberNew && ids.some((id) => !rank.has(id))) {
      writeDisplayLocal(orderKey, JSON.stringify([...order, ...ids.filter((id) => !rank.has(id))]));
    }
    const handles = [...nodes.values()].map((unit) => {
      const handle = unit.querySelector(":scope > .plugin-nav-row > .plugin-nav-grip");
      handle.hidden = unit.parentElement.children.length < 2;
      return handle;
    });
    if (handles.every((handle) => handle.hidden)) {
      changed();
      return;
    }
    const toolbar = document.createElement("div");
    toolbar.className = "plugin-nav-toolbar";
    overview.before(toolbar);
    toolbar.append(overview);
    const edit = button("调整插件顺序", "plugin-nav-edit");
    edit.innerHTML = '<svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><path d="M2 4h10M2 8h7M2 12h4m7-5v8m-3-3 3 3 3-3"/></svg>';
    toolbar.append(edit);
    const controls = document.createElement("div");
    controls.className = "plugin-nav-actions";
    controls.hidden = true;
    const cancel = button("取消排序", "plugin-nav-action", "放弃更改");
    const done = button("保存插件顺序", "plugin-nav-action is-primary", "完成");
    const sortedIds = () => Array.from(tree.querySelectorAll("[data-nav-id]"), (el6) => el6.dataset.navId);
    const initial = sortedIds();
    function reflectDraft() {
      cancel.hidden = sortedIds().every((id, index) => id === initial[index]);
    }
    const check2 = document.createElement("span");
    check2.className = "plugin-nav-check";
    check2.setAttribute("aria-hidden", "true");
    done.prepend(check2);
    cancel.title = "放弃本次调整，恢复进入排序前的顺序";
    controls.append(done, cancel);
    toolbar.append(controls);
    const live2 = document.createElement("span");
    live2.className = "plugin-nav-live";
    live2.setAttribute("role", "status");
    toolbar.append(live2);
    function close2(save) {
      endDrag(false);
      if (save)
        writeDisplayLocal(
          orderKey,
          JSON.stringify(
            Array.from(
              tree.querySelectorAll("[data-nav-id]"),
              (el6) => el6.dataset.navId
            )
          )
        );
      editing = false;
      refresh2();
      rail.querySelector(".plugin-nav-edit")?.focus();
    }
    edit.onclick = () => {
      toolbar.classList.add("is-editing");
      reflectDraft();
      editing = true;
      tree.classList.add("is-sorting");
      controls.hidden = false;
      edit.hidden = true;
      changed();
      tree.querySelector(".plugin-nav-grip:not([hidden])")?.focus({ preventScroll: true });
    };
    controls.onkeydown = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close2(false);
      }
    };
    cancel.onclick = () => close2(false);
    done.onclick = () => close2(true);
    let drag = null;
    function endDrag(commit) {
      if (!drag) return;
      const d = drag;
      drag = null;
      cancelAnimationFrame(d.raf);
      if (!commit) glide(d.list, () => d.list.insertBefore(d.unit, d.start));
      d.unit.classList.remove("is-placeholder");
      const destination = d.unit.getBoundingClientRect();
      const offset = d.ghost.getBoundingClientRect().top - destination.top;
      d.ghost.remove();
      if (commit && !window.matchMedia("(prefers-reduced-motion: reduce)").matches)
        d.unit.animate?.(
          [
            { transform: "translateY(" + offset + "px)", backgroundColor: "var(--workspace-nav)" },
            { transform: "none", backgroundColor: "transparent" }
          ],
          { duration: 160, easing: "cubic-bezier(.2,.78,.28,1)" }
        );
      if (tree.hasPointerCapture(d.pointer)) tree.releasePointerCapture(d.pointer);
      d.handle.focus({ preventScroll: true });
      reflectDraft();
      changed();
    }
    function withinList(d) {
      const rect = d.list.getBoundingClientRect();
      return d.x >= rect.left - 12 && d.x <= rect.right + 4 && d.y >= rect.top - 4 && d.y <= rect.bottom + 4;
    }
    function frame() {
      if (!drag) return;
      const d = drag, bounds = rail.getBoundingClientRect();
      const inside = d.x >= bounds.left && d.x <= bounds.right;
      if (inside && d.y >= bounds.top && d.y <= bounds.bottom) {
        if (d.y < bounds.top + 32) rail.scrollTop -= 7;
        if (d.y > bounds.bottom - 32) rail.scrollTop += 7;
        const peers = withinList(d) ? units(d.list) : [];
        const position = peers.indexOf(d.unit);
        let target = position;
        for (let i = 0; i < peers.length; i++) {
          if (i === position) continue;
          const el6 = peers[i], transform = getComputedStyle(el6).transform;
          const shift = transform === "none" ? 0 : new DOMMatrixReadOnly(transform).m42;
          const midpoint = el6.getBoundingClientRect().top - shift + el6.offsetHeight / 2;
          if (i > position && d.y > midpoint) target = i;
          if (i < position && d.y < midpoint) {
            target = i;
            break;
          }
        }
        if (target !== position) {
          glide(
            d.list,
            () => d.list.insertBefore(
              d.unit,
              target < position ? peers[target] : peers[target].nextSibling
            ),
            d.unit
          );
          reflectDraft();
          changed();
        }
      }
      d.ghost.style.transform = "translateY(" + (d.y - d.offset) + "px)";
      d.raf = requestAnimationFrame(frame);
    }
    tree.addEventListener("pointerdown", (e) => {
      const handle = e.target.closest(".plugin-nav-grip");
      if (!handle || !editing || drag || e.button !== 0) return;
      e.preventDefault();
      const unit = handle.closest("[data-nav-id]"), rect = unit.getBoundingClientRect();
      const ghost = unit.cloneNode(true);
      ghost.className = "plugin-nav-ghost";
      ghost.setAttribute("aria-hidden", "true");
      ghost.inert = true;
      ghost.setAttribute("popover", "manual");
      Object.assign(ghost.style, {
        left: rect.left + "px",
        width: rect.width + "px",
        transform: "translateY(" + rect.top + "px)"
      });
      rail.append(ghost);
      ghost.showPopover?.();
      unit.classList.add("is-placeholder");
      tree.setPointerCapture(e.pointerId);
      drag = {
        unit,
        handle,
        list: unit.parentElement,
        ghost,
        pointer: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        offset: e.clientY - rect.top,
        start: unit.nextElementSibling,
        raf: 0
      };
      frame();
    });
    tree.addEventListener("pointermove", (e) => {
      if (drag?.pointer === e.pointerId) {
        drag.x = e.clientX;
        drag.y = e.clientY;
      }
    });
    tree.addEventListener("pointerup", (e) => {
      if (drag?.pointer !== e.pointerId) return;
      drag.x = e.clientX;
      drag.y = e.clientY;
      cancelAnimationFrame(drag.raf);
      frame();
      const r = rail.getBoundingClientRect();
      endDrag(
        withinList(drag) && e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom
      );
    });
    for (const type of ["pointercancel", "lostpointercapture"])
      tree.addEventListener(type, (e) => {
        if (drag?.pointer === e.pointerId) endDrag(false);
      });
    tree.addEventListener("click", (e) => {
      if (!editing) return;
      const row2 = e.target.closest(".plugin-nav-row");
      if (row2) {
        e.preventDefault();
        e.stopPropagation();
        row2.querySelector(".plugin-nav-grip")?.focus();
      }
    });
    tree.addEventListener("keydown", (e) => {
      if (!editing) return;
      if (e.key === "Escape") {
        e.preventDefault();
        if (drag) endDrag(false);
        else close2(false);
        return;
      }
      const handle = e.target.closest(".plugin-nav-grip");
      if (!handle || drag || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
      e.preventDefault();
      const unit = handle.closest("[data-nav-id]"), list = unit.parentElement;
      const up = e.key === "ArrowUp", sibling = up ? unit.previousElementSibling : unit.nextElementSibling;
      if (sibling) {
        glide(list, () => list.insertBefore(unit, up ? sibling : sibling.nextSibling));
        handle.focus();
        reflectDraft();
        changed();
        live2.textContent = "当前位置 " + (units(list).indexOf(unit) + 1) + " / " + list.children.length;
      }
    });
    changed();
  }
  return {
    mount,
    get editing() {
      return editing;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-restart.ts
var pending = [];
var request;
var requested;
var stale = false;
function acceptPluginRuntime(rows) {
  pending = rows.filter((row2) => row2.installed !== false && row2.restart_required === true);
  stale = false;
  paint();
}
function takePluginRestartRequest() {
  const value = requested;
  requested = void 0;
  return value;
}
function openPluginRestart(name) {
  requested = name || pending[0]?.name;
  if (location.hash !== "#/infinity") location.hash = "#/infinity";
  else window.dispatchEvent(new Event("plugin-restart-open"));
}
function paint() {
  const button4 = document.querySelector(".topbar-plugin-restart");
  if (!button4) return;
  button4.hidden = !pending.length;
  button4.textContent = `插件待重启 ${pending.length}${stale ? " · 待确认" : ""}`;
  button4.title = stale ? "状态刷新失败，显示上次确认的待生效插件" : pending.map((p) => p.displayName || p.name).join("、") + "：重启当前 Bot 的 Core 服务后生效";
}
async function read() {
  if (request) return request;
  request = (async () => {
    try {
      const response = await pageFetch("/plugins", { cache: "no-store" });
      if (!response.ok) throw Error();
      const data = await response.json();
      if (!data.wired || !Array.isArray(data.plugins)) throw Error();
      acceptPluginRuntime(data.plugins);
    } catch {
      stale = true;
      paint();
    }
  })().finally(() => {
    request = void 0;
  });
  return request;
}
function startPluginRestart() {
  const row2 = document.querySelector(".topbar-status");
  if (!row2 || row2.querySelector(".topbar-plugin-restart")) return;
  const button4 = document.createElement("button");
  button4.type = "button";
  button4.className = "topbar-update topbar-plugin-restart";
  button4.hidden = true;
  button4.onclick = () => openPluginRestart();
  row2.append(button4);
  void read();
  window.setInterval(() => {
    if (!document.hidden) void read();
  }, 6e4);
  window.addEventListener("plugins-changed", () => {
    void read();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void read();
  });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-updates.ts
var state = { rows: [], checking: false };
var pending2;
var failure = "";
var started = false;
var loaded = false;
var updateFor = (name) => state.rows.find((row2) => row2.name === name);
var pluginUpdateSnapshot = () => ({ ...state, error: failure });
var muteKey = "plugin-update-muted";
function mutedNames() {
  try {
    const names = JSON.parse(readDisplayLocal(muteKey) || "[]");
    return Array.isArray(names) ? names.filter((n) => typeof n === "string") : [];
  } catch {
    return [];
  }
}
var updateMuted = (name) => mutedNames().includes(name);
function setUpdateMuted(name, mute) {
  const names = new Set(mutedNames());
  if (mute) names.add(name);
  else names.delete(name);
  writeDisplayLocal(muteKey, JSON.stringify([...names]));
  paint2();
}
var available = () => state.rows.filter((row2) => row2.status === "available" && !updateMuted(row2.name));
var el = (tag, text2 = "", cls = "") => {
  const node6 = document.createElement(tag);
  node6.textContent = text2;
  node6.className = cls;
  return node6;
};
function updateText(row2) {
  if (row2.status === "available") return `${row2.current} → ${row2.latest}${row2.stale ? " · 上次发现，待复查" : " · 可更新"}`;
  if (row2.stale && row2.status !== "pending") return row2.error || "检查已过期，等待复查";
  if (row2.status === "current") return `${row2.current} · 已是最新`;
  if (row2.status === "ahead") return `${row2.current} · 高于公开版本 ${row2.latest}`;
  if (row2.status === "prerelease") return `${row2.latest} 为预发布版本`;
  if (row2.status === "pending") return "等待检查";
  return row2.message || row2.error || "暂时无法确认";
}
function summary() {
  if (failure) return failure;
  if (state.checking) return "正在后台检查插件更新…";
  const count4 = available().length;
  const unknown = state.rows.filter((row2) => row2.status === "pending" || row2.status === "unavailable" || row2.stale).length;
  const unsupported = state.rows.filter((row2) => row2.status === "unsupported").length;
  const checked = state.rows.filter((row2) => ["current", "ahead", "prerelease", "unreleased"].includes(row2.status) && !row2.stale).length;
  return count4 ? `${count4} 个插件发现新版本${unknown ? `，${unknown} 项待复查` : ""}` : unknown ? `${unknown} 个插件尚未确认版本` : checked ? `已检查 ${checked} 个插件，暂无更新` : state.rows.some((row2) => row2.status === "available") ? "暂无需要提示的插件更新" : unsupported ? `${unsupported} 个插件无法自动检查，请查看来源` : state.rows.length ? "内置插件随 Core 更新" : loaded ? "暂无已安装插件" : "等待读取插件更新状态";
}
function paint2() {
  document.querySelectorAll("[data-plugin-update-summary]").forEach((node6) => node6.textContent = summary());
  document.querySelectorAll("[data-plugin-update-name]").forEach((node6) => {
    const row2 = updateFor(node6.dataset.pluginUpdateName);
    node6.hidden = !row2 || row2.status !== "available" || updateMuted(row2.name);
    const text2 = node6.querySelector("[data-plugin-update-text]");
    if (text2 && row2) text2.textContent = updateText(row2);
    node6.classList.toggle("has-update", row2?.status === "available");
  });
  const top = document.querySelector(".topbar-plugin-updates");
  if (top) {
    top.textContent = available().length ? `插件更新 ${available().length}` : "插件更新";
    top.hidden = available().length === 0;
    top.title = summary();
    top.classList.toggle("has-update", available().length > 0);
  }
  window.dispatchEvent(new Event("plugin-updates-changed"));
}
var manual;
async function readUpdates(force) {
  const response = await pageFetch("/plugin-updates" + (force ? "?refresh=1" : ""), { cache: "no-store" });
  if (!response.ok) throw Error(response.status === 503 || response.status === 404 ? "此 Core 尚未启用插件更新检查" : "暂时无法读取插件更新");
  const next = await response.json();
  if (!Array.isArray(next.rows) || typeof next.checking !== "boolean") throw Error("更新状态格式异常");
  state = next;
  loaded = true;
  failure = "";
  paint2();
}
async function observeUpdates(refresh2) {
  try {
    const deadline = Date.now() + 9e4;
    await readUpdates(refresh2);
    let verifyQueued = refresh2;
    while (verifyQueued || state.checking) {
      verifyQueued = false;
      if (Date.now() >= deadline) throw Error("检查耗时较长，可稍后再查。");
      await new Promise((resolve) => setTimeout(resolve, 1250));
      await readUpdates(false);
    }
  } catch (error) {
    state = { ...state, checking: false, rows: state.rows.map((row2) => ({ ...row2, stale: true })) };
    failure = error instanceof Error && error.name !== "StatusTimeoutError" ? error.message : "读取更新状态超时，可重新检查。";
    paint2();
  }
}
function checkPluginUpdates(refresh2 = false) {
  if (manual) return manual;
  if (refresh2) {
    manual = Promise.resolve(pending2).then(() => observeUpdates(true)).finally(() => {
      manual = void 0;
    });
    return manual;
  }
  return pending2 ??= observeUpdates(false).finally(() => {
    pending2 = void 0;
  });
}
function pluginUpdateNotice(name) {
  const box = el("section", "", "plugin-update-strip");
  const text2 = el("span");
  if (name) {
    box.dataset.pluginUpdateName = name;
    text2.dataset.pluginUpdateText = "";
    box.hidden = true;
  } else {
    text2.dataset.pluginUpdateSummary = "";
    text2.textContent = summary();
  }
  const button4 = el("button", name ? "查看更新 →" : "查看更新 →", "infinity-action");
  button4.type = "button";
  button4.addEventListener("click", () => openPluginUpdates(name, button4));
  box.append(text2, button4);
  if (name) {
    const row2 = updateFor(name);
    if (row2 && row2.status === "available" && !updateMuted(row2.name)) {
      box.hidden = false;
      text2.textContent = updateText(row2);
    }
  }
  return box;
}
var requested2 = null;
function takePluginUpdateRequest() {
  const value = requested2;
  requested2 = null;
  return value;
}
function openPluginUpdates(name, _opener) {
  const updates = available();
  requested2 = { name: name || (updates.length === 1 ? updates[0].name : void 0) };
  if (location.hash !== "#/infinity") location.hash = "#/infinity";
  else window.dispatchEvent(new Event("plugin-updates-open"));
}
function startPluginUpdates() {
  if (started) return;
  started = true;
  onDisplayScopeReady((prefix2) => {
    paint2();
    window.addEventListener("storage", (event) => {
      if (event.key === prefix2 + muteKey || event.key === null) paint2();
    });
  });
  const row2 = document.querySelector(".topbar-status");
  if (row2) {
    const button4 = el("button", "插件更新", "topbar-update topbar-plugin-updates");
    button4.type = "button";
    button4.hidden = true;
    button4.onclick = () => openPluginUpdates(void 0, button4);
    row2.append(button4);
  }
  void checkPluginUpdates();
  window.setInterval(() => {
    if (!document.hidden) void checkPluginUpdates();
  }, 6e4);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void checkPluginUpdates();
  });
  window.addEventListener("plugins-changed", () => {
    void checkPluginUpdates();
  });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/ui-phrases.ts
var traditional = {
  "国内镜像始终直连。切换到官方源时，按下方用途使用代理。": "國內鏡像一律直連。切換到官方來源時，依下方用途使用代理。",
  "模型 API": "模型 API",
  "其他用途": "其他用途",
  "插件市场（GitHub 搜索与下载）": "外掛市集（GitHub 搜尋與下載）",
  "下载（模型、运行环境和 Core 更新）": "下載（模型、執行環境和 Core 更新）",
  "正在读取网络代理设置…": "正在讀取網路代理設定…",
  "启用代理前请填写代理地址。": "啟用代理前請填寫代理位址。",
  "缺少 CosyVoice3 运行环境。推荐模型下载只安装权重，请先安装运行环境。": "缺少 CosyVoice3 執行環境。推薦模型下載只安裝權重，請先安裝執行環境。",
  "缺少 CosyVoice3 官方通用参考音频，请补齐运行目录中的 repo/asset/zero_shot_prompt.wav。": "缺少 CosyVoice3 官方通用參考音訊，請補齊執行目錄中的 repo/asset/zero_shot_prompt.wav。",
  "缺少自定义音色 Telyin.wav，请补齐参考音频或改选默认音色。": "缺少自訂音色 Telyin.wav，請補齊參考音訊或改選預設音色。",
  "请从本机可用音色中选择。": "請從本機可用音色中選擇。",
  "暂无主动请求。": "暫無主動請求。",
  "暂无未完成事项。": "暫無未完成事項。",
  "暂无留下的记录。": "暫無留下的記錄。",
  "暂无待处理事项。": "暫無待處理事項。",
  "页面状态": "頁面狀態",
  "档案身份尚未确认，暂不允许清理。": "檔案身分尚未確認，暫不允許清理。",
  "插件列表读取失败；仍可查看本页已知的显示缓存。": "插件清單讀取失敗；仍可查看本頁已知的顯示緩存。",
  "用量仅含已登记的临时显示状态，不代表插件文件大小。": "用量僅含已登記的暫時顯示狀態，不代表插件檔案大小。",
  "仅清理页签选择等临时显示状态，不重载表单。配置、聊天记录、记忆和未保存内容均不受影响。": "僅清理分頁選擇等暫時顯示狀態，不重新載入表單。設定、聊天記錄、記憶和未儲存內容均不受影響。",
  " 项": " 項",
  "正在读取…": "正在讀取…",
  "没有匹配的插件页面缓存。": "沒有符合的插件頁面緩存。",
  "未清理：缓存不可用或有内容需要保护。": "未清理：緩存無法使用或有內容需要保護。",
  "已清理显示缓存；配置和未保存内容保持不变。": "已清理顯示緩存；設定和未儲存內容保持不變。",
  "无法访问浏览器缓存，未清理任何数据。": "無法存取瀏覽器緩存，未清理任何資料。",
  "有未保存内容，暂不清理": "有未儲存內容，暫不清理",
  "清理": "清理",
  "页签状态": "分頁狀態",
  "无缓存": "無緩存",
  "有缓存": "有緩存",
  "名称、关键词或缩写": "名稱、關鍵詞或縮寫",
  "搜索插件页面缓存": "搜尋插件頁面緩存",
  "当前档案 · 当前浏览器标签页": "目前檔案 · 目前瀏覽器分頁",
  "插件页面缓存": "插件頁面緩存",
  "存储": "儲存",
  "配色实时同步": "配色實時同步",
  "此安装包由所有档案共享。确认后将先在各档案停用并卸载插件，但不会删除配置和缓存。保留的数据不会自动清理。": "此安裝包由所有檔案共享。確認後將先在各檔案停用並卸載插件，但不會刪除配置和緩存。保留的數據不會自動清理。",
  "展开查看 · 配色实时同步": "展開查看 · 配色實時同步",
  "聊天预览": "聊天預覽",
  "此安装包由所有档案共享。确认后将先在各档案停用并卸载插件，但不会删除配置和缓存。缓存清除入口尚未开放，保留的数据不会自动清理。": "此安裝包由所有檔案共享。確認後將先在各檔案停用並卸載插件，但不會刪除配置和緩存。緩存清除入口尚未開放，保留的數據不會自動清理。",
  "确认停用": "確認停用",
  "确认启用": "確認啓用",
  "卸载插件": "卸載插件",
  " token，已压到它": " token，已壓到它",
  " · 不代表真实收发验收": " · 不代表真實收發驗收",
  " · 不读取 Codex 或浏览器的登录文件": " · 不讀取 Codex 或瀏覽器的登錄文件",
  " · 名单只作用于这个 Bot。": " · 名單隻作用於這個 Bot。",
  " · 已认可 ": " · 已認可 ",
  " · 已连接，接入任务收尾中": " · 已連接，接入任務收尾中",
  " · 库已满，暂停收集；旧素材保留": " · 庫已滿，暫停收集；舊素材保留",
  " · 无法打开文件夹": " · 無法打開文件夾",
  " · 无法连接本机服务": " · 無法連接本機服務",
  " · 最后写于 ": " · 最後寫於 ",
  " · 盘上原来没有这一份 ⇒ 没留下旧版": " · 盤上原來沒有這一份 ⇒ 沒留下舊版",
  " · 管理端验证可选，不影响当前 QQ 连接": " · 管理端驗證可選，不影響當前 QQ 連接",
  " · 记过 ": " · 記過 ",
  " —— 后续动作已经办完": " —— 後續動作已經辦完",
  " —— 后续投递结果待核对": " —— 後續投遞結果待覈對",
  " —— 文件写了，后续动作还欠着": " —— 文件寫了，後續動作還欠着",
  " —— 点一下跳到那一块": " —— 點一下跳到那一塊",
  " 下一步：导入接入配置、保存会话名单并启用插件；当前尚未完成消息收发验收。": " 下一步：導入接入配置、保存會話名單並啓用插件；當前尚未完成消息收發驗收。",
  " 与 ": " 與 ",
  " 个人": " 個人",
  " 个内置插件未安装": " 個內置插件未安裝",
  " 个账号 · ": " 個賬號 · ",
  " 人已保存登录凭证": " 人已保存登錄憑證",
  " 人已绑定 · ": " 人已綁定 · ",
  " 子项": " 子項",
  " 字节": " 字節",
  " 收集待评审语音": " 收集待評審語音",
  " 条": " 條",
  " 条 —— ": " 條 —— ",
  " 条。": " 條。",
  " 条回执": " 條回執",
  " 条她记下的话": " 條她記下的話",
  " 条，共 ": " 條，共 ",
  " 条；完整记录保留在文件中。": " 條；完整記錄保留在文件中。",
  " 模型文件夹。": " 模型文件夾。",
  " 次调用 · ": " 次調用 · ",
  " 段 · 渲染后约 ": " 段 · 渲染後約 ",
  " 段未保存 · Ctrl+S 保存当前章节": " 段未保存 · Ctrl+S 保存當前章節",
  " 的一次加载确认…": " 的一次加載確認…",
  " 的页面": " 的頁面",
  " 的鸣潮账号 ": " 的鳴潮賬號 ",
  " 笔停在半路）": " 筆停在半路）",
  " 笔）": " 筆）",
  " 这本已经到顶了 —— 再写就会压掉最旧的。": " 這本已經到頂了 —— 再寫就會壓掉最舊的。",
  " 里已有一把 —— 输入新值会替换它": " 裏已有一把 —— 輸入新值會替換它",
  " 页": " 頁",
  " 项失败": " 項失敗",
  " ｜ 并 ": " ｜ 並 ",
  "+ 新建实例": "+ 新建實例",
  "0 段 · 渲染后约 0 tokens": "0 段 · 渲染後約 0 tokens",
  "1.0 · 当前固定": "1.0 · 當前固定",
  "12:32（写法只有一条纪律：**观测、推测、事实分开写**）": "12:32（寫法只有一條紀律：**觀測、推測、事實分開寫**）",
  "6/6 不代表真实收发已经通过。": "6/6 不代表真實收發已經通過。",
  "API 地址或协议已变更，请填入该服务的 API 密钥后测试。": "API 地址或協議已變更，請填入該服務的 API 密鑰後測試。",
  "API 密钥": "API 密鑰",
  "API 密钥认证": "API 密鑰認證",
  "API 设置": "API 設置",
  "ASR · 语音识别": "ASR · 語音識別",
  "ASR 模型下载进度": "ASR 模型下載進度",
  "Bot 信息暂时不可用。": "Bot 信息暫時不可用。",
  "Bot 显示名": "Bot 顯示名",
  "ChatGPT 订阅": "ChatGPT 訂閱",
  "Fun-CosyVoice3-0.5B-2512，约 5.43 GB。校验后安装，不含运行环境或私有音色。": "Fun-CosyVoice3-0.5B-2512，約 5.43 GB。校驗後安裝，不含運行環境或私有音色。",
  "GLM / Z.ai Coding Plan 仍需套餐密钥；不是 ChatGPT 账号登录": "GLM / Z.ai Coding Plan 仍需套餐密鑰；不是 ChatGPT 賬號登錄",
  "OneBot 接入：核对 HTTP、正向 WebSocket 与鉴权配置，并填写允许的会话名单。": "OneBot 接入：覈對 HTTP、正向 WebSocket 與鑑權配置，並填寫允許的會話名單。",
  "OneBot 鉴权未通过": "OneBot 鑑權未通過",
  "OpenAI 包含 ChatGPT 登录和模型调用。代理失败时会报错，不会自动改走直连。": "OpenAI 包含 ChatGPT 登錄和模型調用。代理失敗時會報錯，不會自動改走直連。",
  "QQ 协议报告未就绪": "QQ 協議報告未就緒",
  "QQ 接入与消息收发会立即停止；SnowLuma 如果由外部启动，不会被此操作关闭。再次启用后会按当前配置重新加载。": "QQ 接入與消息收發會立即停止；SnowLuma 如果由外部啓動，不會被此操作關閉。再次啓用後會按當前配置重新加載。",
  "QQ 接入与消息设置": "QQ 接入與消息設置",
  "QQ 插件 · 已启用": "QQ 插件 · 已啓用",
  "QQ 插件 · 未启用": "QQ 插件 · 未啓用",
  "QQ 接入：填写目标 QQ 号并登录 QQ，手动点击验证接入；Core 启动不会自动恢复。": "QQ 接入：填寫目標 QQ 號並登錄 QQ，手動點擊驗證接入；Core 啓動不會自動恢復。",
  "QQ 接入：目标账号在线；协议状态自动只读检查，不需要重复管理登录或注入。": "QQ 接入：目標賬號在線；協議狀態自動只讀檢查，不需要重複管理登錄或注入。",
  "QQ 接入：管理在线采样已过期（30 秒），不代表 QQ 掉线；点击验证接入重新采样。": "QQ 接入：管理在線採樣已過期（30 秒），不代表 QQ 掉線；點擊驗證接入重新採樣。",
  "QQ 插件环境声明文本": "QQ 插件環境聲明文本",
  "QQ 状态 · 读取中…": "QQ 狀態 · 讀取中…",
  "QQ 状态 · 读取失败 · 自动重试": "QQ 狀態 · 讀取失敗 · 自動重試",
  "QQ 状态更新暂不可用 · 自动重试": "QQ 狀態更新暫不可用 · 自動重試",
  "QQ 状态读取失败": "QQ 狀態讀取失敗",
  "QQ 状态读取失败 · 自动重试": "QQ 狀態讀取失敗 · 自動重試",
  "QQ 状态读取失败，正在自动重试；数据就绪前不能保存。": "QQ 狀態讀取失敗，正在自動重試；數據就緒前不能保存。",
  "QQ 用户": "QQ 用戶",
  "QQ 程序路径 · 可选": "QQ 程序路徑 · 可選",
  "QQ 通道 · 环境提示词": "QQ 通道 · 環境提示詞",
  "Qwen3.5-0.8B Q4_K_M + F16 视觉投影，约 703 MB。校验大小和 SHA256 后安装；不含 llama.cpp 运行时。": "Qwen3.5-0.8B Q4_K_M + F16 視覺投影，約 703 MB。校驗大小和 SHA256 後安裝；不含 llama.cpp 運行時。",
  "SenseVoiceSmall INT8，约 230 MB。下载后校验大小和 SHA256，再安装。": "SenseVoiceSmall INT8，約 230 MB。下載後校驗大小和 SHA256，再安裝。",
  "SnowLuma · QQ 在线 · 插件 WS 已连接": "SnowLuma · QQ 在線 · 插件 WS 已連接",
  "SnowLuma · QQ 在线 · 插件 WS 未连接": "SnowLuma · QQ 在線 · 插件 WS 未連接",
  "SnowLuma · WS 已核验 · ": "SnowLuma · WS 已覈驗 · ",
  "SnowLuma · WS 断开": "SnowLuma · WS 斷開",
  "SnowLuma · 状态未核验": "SnowLuma · 狀態未覈驗",
  "SnowLuma · 管理采样在线 · WS 未核验": "SnowLuma · 管理採樣在線 · WS 未覈驗",
  "SnowLuma · 运行日志": "SnowLuma · 運行日誌",
  "SnowLuma · 连接状态": "SnowLuma · 連接狀態",
  "SnowLuma 已停止；下次点击启动将自动恢复 QQ 接入。": "SnowLuma 已停止；下次點擊啓動將自動恢復 QQ 接入。",
  "SnowLuma 管理密码 · 不是 QQ 密码": "SnowLuma 管理密碼 · 不是 QQ 密碼",
  "SnowLuma 管理用户名": "SnowLuma 管理用戶名",
  "SnowLuma 节点弹窗的“保存修改”只更新草稿；还需点击外层“OneBot 协议端点”的“保存”应用配置。": "SnowLuma 節點彈窗的“保存修改”只更新草稿；還需點擊外層“OneBot 協議端點”的“保存”應用配置。",
  "SnowLuma 运行日志": "SnowLuma 運行日誌",
  "SnowLuma 首次接入检查": "SnowLuma 首次接入檢查",
  "TTS · 语音合成": "TTS · 語音合成",
  "TTS 模型下载进度": "TTS 模型下載進度",
  "TTS 正在启动，模型加载可能需要一些时间。": "TTS 正在啓動，模型加載可能需要一些時間。",
  "[来源未记录]": "[來源未記錄]",
  "[终端]": "[終端]",
  "console:local-owner / qq:账号 / wechat:账号": "console:local-owner / qq:賬號 / wechat:賬號",
  "refresh 抛在：": "refresh 拋在：",
  "vLLM ／ Ollama ／ LM Studio 默认说 `chat/completions`，而 `Local` 那条（NInfer）两套都会说": "vLLM ／ Ollama ／ LM Studio 默認說 `chat/completions`，而 `Local` 那條（NInfer）兩套都會說",
  "web.py::_lead_comment() → owner_of() ／ 边界.md:1（按部署可覆盖）": "web.py::_lead_comment() → owner_of() ／ 邊界.md:1（按部署可覆蓋）",
  '{"query":"相关记录"}': '{"query":"相關記錄"}',
  "· 已经记满（再记就会压掉最旧的）": "· 已經記滿（再記就會壓掉最舊的）",
  "× 关闭": "× 關閉",
  "—— 那不等于已鉴权：这一台机器上的谁都改得动它。": "—— 那不等於已鑑權：這一臺機器上的誰都改得動它。",
  "… · 账号须人工核对后绑定。": "… · 賬號須人工覈對後綁定。",
  "── stderr · 错误输出 ──": "── stderr · 錯誤輸出 ──",
  "● 当前": "● 當前",
  "✓ 已就绪": "✓ 已就緒",
  "。其余关联保留。": "。其餘關聯保留。",
  "。旧内容保留、关联读取；不开放其他群聊历史，不把私聊带进群里。": "。舊內容保留、關聯讀取；不開放其他羣聊歷史，不把私聊帶進羣裏。",
  "。服务未返回原因。": "。服務未返回原因。",
  "。版本兼容要求以所下载 SnowLuma 发行说明为准。": "。版本兼容要求以所下載 SnowLuma 發行說明爲準。",
  "。请检查各目录的 package.json；安装位置见上方。": "。請檢查各目錄的 package.json；安裝位置見上方。",
  "。账号：": "。賬號：",
  "」属于同一人。": "」屬於同一人。",
  "」没对上哪一件。": "」沒對上哪一件。",
  "」没对上哪一份。": "」沒對上哪一份。",
  "一次最多 8 张": "一次最多 8 張",
  "上一批用时": "上一批用時",
  "上一批输入估算 / 当前配置窗口": "上一批輸入估算 / 當前配置窗口",
  "上一次失败：": "上一次失敗：",
  "上一轮：思考 ／ 输出各花了多久（点一下看明细）": "上一輪：思考 ／ 輸出各花了多久（點一下看明細）",
  "上一页": "上一頁",
  "上下文估算暂不可用": "上下文估算暫不可用",
  "上下文占用": "上下文佔用",
  "上下文用了多少 —— 点一下看它占在哪几块": "上下文用了多少 —— 點一下看它佔在哪幾塊",
  "上次文档整理未完成": "上次文檔整理未完成",
  "上游报了会自动开；也可以自己扳": "上游報了會自動開；也可以自己扳",
  "上游未上报完整缓存用量": "上游未上報完整緩存用量",
  "上游未报告图片能力": "上游未報告圖片能力",
  "上游没说这一条 —— 可以自己扳": "上游沒說這一條 —— 可以自己扳",
  "上游说这一条最多 ": "上游說這一條最多 ",
  "上游默认": "上游默認",
  "下一步：下载官方发行包，解压到安装目录根部，再检查入口文件。": "下一步：下載官方發行包，解壓到安裝目錄根部，再檢查入口文件。",
  "下一步：保存本 Bot 的目标 QQ 号；目标已保存不等于已导入 OneBot 连接。": "下一步：保存本 Bot 的目標 QQ 號；目標已保存不等於已導入 OneBot 連接。",
  "下一步：到插件总览启用 QQ 插件，再检测 OneBot 接入。": "下一步：到插件總覽啓用 QQ 插件，再檢測 OneBot 接入。",
  "下一步：在管理页完成首次登录，在本页保存管理凭据并验证；仅保存不代表管理初始化已核验。": "下一步：在管理頁完成首次登錄，在本頁保存管理憑據並驗證；僅保存不代表管理初始化已覈驗。",
  "下一步：安装 Node.js，重新打开 Core 启动窗口后再检查。": "下一步：安裝 Node.js，重新打開 Core 啓動窗口後再檢查。",
  "下一步：导入当前账号连接，前往会话路由填写对端联系人或群号并保存。": "下一步：導入當前賬號連接，前往會話路由填寫對端聯繫人或羣號並保存。",
  "下一步：确认目标 QQ 已登录，点击验证接入；需要扫码或二次验证时请手动完成。": "下一步：確認目標 QQ 已登錄，點擊驗證接入；需要掃碼或二次驗證時請手動完成。",
  "下一页": "下一頁",
  "下载 Windows 官方发行包，解压到项目旁的 SnowLuma 目录；需兼容的 Node.js。": "下載 Windows 官方發行包，解壓到項目旁的 SnowLuma 目錄；需兼容的 Node.js。",
  "下载推荐 ": "下載推薦 ",
  "下载推荐模型 ↓": "下載推薦模型 ↓",
  "下载源": "下載源",
  "下载源 ": "下載源 ",
  "下载诊断 ZIP，包含最近对话和工具回执，转发前请核对内容。": "下載診斷 ZIP，包含最近對話和工具回執，轉發前請覈對內容。",
  "下载诊断包": "下載診斷包",
  "下载请求失败": "下載請求失敗",
  "下载通用模型权重": "下載通用模型權重",
  "不可读取": "不可讀取",
  "不要这一张": "不要這一張",
  "世界认知": "世界認知",
  "世界里的事": "世界裏的事",
  "业务已核验": "業務已覈驗",
  "业务未核验": "業務未覈驗",
  "丢掉这张草稿": "丟掉這張草稿",
  "个可装": "個可裝",
  "个已启用": "個已啓用",
  "个已安装，共": "個已安裝，共",
  "中国镜像": "中國鏡像",
  "中国镜像 · HF-Mirror（推荐）": "中國鏡像 · HF-Mirror（推薦）",
  "中性小标": "中性小標",
  "中断于 ": "中斷於 ",
  "主动分享只发送给主人，收件人不可在这里更改": "主動分享只發送給主人，收件人不可在這裏更改",
  "主动消息仅发送给主人": "主動消息僅發送給主人",
  "主动消息收件人 · 主人（启用分享时锁定）": "主動消息收件人 · 主人（啓用分享時鎖定）",
  "主动目标已移出私聊名单，已取消主动投递；正在保存。": "主動目標已移出私聊名單，已取消主動投遞；正在保存。",
  "主动请求": "主動請求",
  "主强调": "主強調",
  "主题与配色": "主題與配色",
  "交互与状态": "交互與狀態",
  "交接失败，请导出诊断": "交接失敗，請導出診斷",
  "交接已在进行中": "交接已在進行中",
  "交接已完成，整理任务已安排": "交接已完成，整理任務已安排",
  "交接状态未确认，请检查连接": "交接狀態未確認，請檢查連接",
  "交接笔记": "交接筆記",
  "交接笔记 · 点击查看 ": "交接筆記 · 點擊查看 ",
  "交接进行中，请稍候": "交接進行中，請稍候",
  "人设": "人設",
  "人设 · PERSONA": "人設 · PERSONA",
  "人设与记忆": "人設與記憶",
  "人设章节": "人設章節",
  "仅保存目标 QQ 不会导入连接；每个 Bot 须使用独立端口和自己的令牌。": "僅保存目標 QQ 不會導入連接；每個 Bot 須使用獨立端口和自己的令牌。",
  "仅在插件页": "僅在插件頁",
  "仅显示已安装 QQ 插件的声明；不会读取其他插件或终端环境作为替代。": "僅顯示已安裝 QQ 插件的聲明；不會讀取其他插件或終端環境作爲替代。",
  "仅有回执": "僅有回執",
  "仅绑定 UID，未保存登录凭证": "僅綁定 UID，未保存登錄憑證",
  "仅表示页面连接状态；当前模型未做探活。": "僅表示頁面連接狀態；當前模型未做探活。",
  "仅退出此模型档案，后续订阅请求将停止。其他模型和当前配置不受影响。": "僅退出此模型檔案，後續訂閱請求將停止。其他模型和當前配置不受影響。",
  "今天没有浮出什么；只在有话说的时候才出现。": "今天沒有浮出什麼；只在有話說的時候纔出現。",
  "仓库": "倉庫",
  "代理设置响应不完整，未覆盖。": "代理設置響應不完整，未覆蓋。",
  "休止请求已接受，正在等待收尾": "休止請求已接受，正在等待收尾",
  "休止：结束当前服务": "休止：結束當前服務",
  "优先使用上游容量；未知或查询失败时按 128K（131072）规划交接。回退值不代表上游承诺支持，可手动填写已确认的容量。": "優先使用上游容量；未知或查詢失敗時按 128K（131072）規劃交接。回退值不代表上游承諾支持，可手動填寫已確認的容量。",
  "会话名单与语音设置保留；已启用的 QQ 插件将重载。": "會話名單與語音設置保留；已啓用的 QQ 插件將重載。",
  "会话路由": "會話路由",
  "会话路由已保存；请验证目标接入并导入当前账号的 OneBot 连接，之后再启用插件。": "會話路由已保存；請驗證目標接入並導入當前賬號的 OneBot 連接，之後再啓用插件。",
  "体积": "體積",
  "你好，这是一段语音试听。让我们听听当前音色的效果。": "你好，這是一段語音試聽。讓我們聽聽當前音色的效果。",
  "使用合并稿（尚未保存）": "使用合併稿（尚未保存）",
  "使用本地 ONNX int8；3 线程档来自基准测试，": "使用本地 ONNX int8；3 線程檔來自基準測試，",
  "使用符合条件的 ChatGPT 订阅，不需要 API Key。模型和额度以账号授权为准；此入口不会自动切换到付费 API。": "使用符合條件的 ChatGPT 訂閱，不需要 API Key。模型和額度以賬號授權爲準；此入口不會自動切換到付費 API。",
  "例如：工作账号、备用API（可随时改名）": "例如：工作賬號、備用API（可隨時改名）",
  "保存代理设置": "保存代理設置",
  "保存会话路由": "保存會話路由",
  "保存回执无效，结果未确认": "保存回執無效，結果未確認",
  "保存失败": "保存失敗",
  "保存失败。": "保存失敗。",
  "保存失败，请检查服务后重试。": "保存失敗，請檢查服務後重試。",
  "保存失败：": "保存失敗：",
  "保存头像": "保存頭像",
  "保存当前章节，保留其他未保存草稿": "保存當前章節，保留其他未保存草稿",
  "保存时写进 data/engine.env（变量名 ": "保存時寫進 data/engine.env（變量名 ",
  "保存未确认，请重新读取核对。": "保存未確認，請重新讀取覈對。",
  "保存未确认，请重新读取核对；未自动重试。": "保存未確認，請重新讀取覈對；未自動重試。",
  "保存结果未确认；请刷新核对后再决定，未自动重试。": "保存結果未確認；請刷新覈對後再決定，未自動重試。",
  "保存设置": "保存設置",
  "保存语音设置": "保存語音設置",
  "保留手动推理强度；上游尚未确认此档位，实际支持以上游为准": "保留手動推理強度；上游尚未確認此檔位，實際支持以上游爲準",
  "保留这份画像的原内容，并移到独立身份。随它分开的账号：": "保留這份畫像的原內容，並移到獨立身份。隨它分開的賬號：",
  "修改后保存 · Ctrl+S": "修改後保存 · Ctrl+S",
  "修改显示名": "修改顯示名",
  "做法与技能": "做法與技能",
  "做法读取失败：": "做法讀取失敗：",
  "做法这一格读不到（服务没答）。": "做法這一格讀不到（服務沒答）。",
  "停止 SnowLuma 服务": "停止 SnowLuma 服務",
  "停止当前处理": "停止當前處理",
  "停止整个 SnowLuma 服务会断开使用它的所有 QQ 账号。": "停止整個 SnowLuma 服務會斷開使用它的所有 QQ 賬號。",
  "停止服务": "停止服務",
  "停止此安装的 SnowLuma": "停止此安裝的 SnowLuma",
  "停止状态未确认，请检查连接": "停止狀態未確認，請檢查連接",
  "停用插件没走完：": "停用插件沒走完：",
  "允许接收哪些消息": "允許接收哪些消息",
  "允许的私聊 QQ 号": "允許的私聊 QQ 號",
  "允许的群号": "允許的羣號",
  "先填稳定平台账号。": "先填穩定平臺賬號。",
  "全部类别": "全部類別",
  "全部输出 · 分流显示": "全部輸出 · 分流顯示",
  "全都摊开": "全都攤開",
  "全链路": "全鏈路",
  "全链路 · 本次交接周期": "全鏈路 · 本次交接週期",
  "共 0 个人": "共 0 個人",
  "共0个可装。": "共0個可裝。",
  "共0个可装（ 筛过之后一个都没有）。": "共0個可裝（ 篩過之後一個都沒有）。",
  "共0个可装（ 这是上次问到的，正在重问）。": "共0個可裝（ 這是上次問到的，正在重問）。",
  "共0个已安装，共0个已启用。": "共0個已安裝，共0個已啓用。",
  "共享 SnowLuma 运行中": "共享 SnowLuma 運行中",
  "共享安装": "共享安裝",
  "共享服务由其他实例管理；本页可恢复自己的 QQ，不允许停止其他 Bot 的协议服务。": "共享服務由其他實例管理；本頁可恢復自己的 QQ，不允許停止其他 Bot 的協議服務。",
  "共用服务": "共用服務",
  "共用管理端不等于共用账号；保存本 Bot 的目标 QQ 和管理凭据后，可验证独立端口再接入。": "共用管理端不等於共用賬號；保存本 Bot 的目標 QQ 和管理憑據後，可驗證獨立端口再接入。",
  "关": "關",
  "关联信息已变化，请刷新后重试。": "關聯信息已變化，請刷新後重試。",
  "关联后在下一次请求读取已确认的关联画像与私人入口；原文件和来源不变。": "關聯後在下一次請求讀取已確認的關聯畫像與私人入口；原文件和來源不變。",
  "关联知识": "關聯知識",
  "关联资料：": "關聯資料：",
  "关键字": "關鍵字",
  "关键词或日期…": "關鍵詞或日期…",
  "关闭": "關閉",
  "内置": "內置",
  "内置源目录": "內置源目錄",
  "再点一下确认删除": "再點一下確認刪除",
  "写入画像": "寫入畫像",
  "写入那条路：这一页能改她是谁 —— 「只听回环」不等于已鉴权。": "寫入那條路：這一頁能改她是誰 —— 「只聽迴環」不等於已鑑權。",
  "写入那条路：这一页能改她是谁。边界照旧是「只听回环」": "寫入那條路：這一頁能改她是誰。邊界照舊是「只聽迴環」",
  "写机器那几个数": "寫機器那幾個數",
  "写的是 `/v1/…` 的后半段。「本地服务」不是一种协议 —— 它们各有各的说法：": "寫的是 `/v1/…` 的後半段。「本地服務」不是一種協議 —— 它們各有各的說法：",
  "写顶栏那几格": "寫頂欄那幾格",
  "冷启动或 QQ 离线时，使用顶部“连接或恢复此 QQ”。Core 启动不会拉起 SnowLuma；": "冷啓動或 QQ 離線時，使用頂部“連接或恢復此 QQ”。Core 啓動不會拉起 SnowLuma；",
  "SnowLuma 管理台尚未就绪。若随 Bot 启动失败，请查看启动器的 QQ 提示。": "SnowLuma 管理台尚未就緒。若隨 Bot 啓動失敗，請查看啓動器的 QQ 提示。",
  "等待确认 QQ 接入状态。": "等待確認 QQ 接入狀態。",
  "凭证仅保存在本机 · ": "憑證僅保存在本機 · ",
  "分区选择失败": "分區選擇失敗",
  "分开这份画像": "分開這份畫像",
  "分隔线": "分隔線",
  "列表刷新失败，当前显示可能不是最新。": "列表刷新失敗，當前顯示可能不是最新。",
  "删除": "刪除",
  "删除 ": "刪除 ",
  "删除仅移除此 Bot 保存的绑定及登录信息，不注销游戏账号。请先到插件总览停用鸣潮插件，待服务退出后刷新名单，再执行删除。": "刪除僅移除此 Bot 保存的綁定及登錄信息，不註銷遊戲賬號。請先到插件總覽停用鳴潮插件，待服務退出後刷新名單，再執行刪除。",
  "删除结果未确认，请刷新核对；不要重复提交。": "刪除結果未確認，請刷新覈對；不要重複提交。",
  "刷新名单": "刷新名單",
  "刷新日志": "刷新日誌",
  "刷新状态": "刷新狀態",
  "刷新连接状态": "刷新連接狀態",
  "前往会话路由填写允许名单 →": "前往會話路由填寫允許名單 →",
  "前往插件市场 →": "前往插件市場 →",
  "前往插件总览 →": "前往插件總覽 →",
  "加载确认已提交；正在核验目标进程与实际账号。": "加載確認已提交；正在覈驗目標進程與實際賬號。",
  "加载试听": "加載試聽",
  "协议": "協議",
  "协议服务": "協議服務",
  "协议状态暂不可确认": "協議狀態暫不可確認",
  "单次请求时间；首段可能是思考或正文，等待包含网络与服务端处理。": "單次請求時間；首段可能是思考或正文，等待包含網絡與服務端處理。",
  "危险／失败": "危險／失敗",
  "卸载": "卸載",
  "历史 ": "歷史 ",
  "历史记录暂不可用；重新进入可重试。": "歷史記錄暫不可用；重新進入可重試。",
  "原档案保存已完成；当前编辑保留，请刷新核对。": "原檔案保存已完成；當前編輯保留，請刷新覈對。",
  "原草稿又有新输入，未覆盖。请重新读取新版后合并。": "原草稿又有新輸入，未覆蓋。請重新讀取新版後合併。",
  "去掉一两张图，或把它们裁小一点。": "去掉一兩張圖，或把它們裁小一點。",
  "双击修改显示名（不更改文件夹名）": "雙擊修改顯示名（不更改文件夾名）",
  "发出去的一张图": "發出去的一張圖",
  "发送中，尚未确认": "發送中，尚未確認",
  "发送失败": "發送失敗",
  "发送消息": "發送消息",
  "发送消息 · Enter（Shift+Enter 换行）": "發送消息 · Enter（Shift+Enter 換行）",
  "发送状态未确认": "發送狀態未確認",
  "发送状态未确认，请检查连接后再试。": "發送狀態未確認，請檢查連接後再試。",
  "取消合并（保留原草稿）": "取消合併（保留原草稿）",
  "取消登录": "取消登錄",
  "只为这份画像建立身份记录。不会自动合并其他画像，也不会迁移旧消息。": "只爲這份畫像建立身份記錄。不會自動合併其他畫像，也不會遷移舊消息。",
  "只修改当前 Bot 的 QQ 场景。": "只修改當前 Bot 的 QQ 場景。",
  "只修改当前 Bot 的显示名，不修改文件夹或人设。": "只修改當前 Bot 的顯示名，不修改文件夾或人設。",
  "只在这边回头看的时候才会写；不是每天都有。": "只在這邊回頭看的時候纔會寫；不是每天都有。",
  "只收图片（jpeg ／ png ／ webp ／ gif）": "只收圖片（jpeg ／ png ／ webp ／ gif）",
  "只收集本 Bot 成功提交发送的合成语音。默认关闭，本地保存；不收用户录音、不共享、不自动训练或删除。认可不等于已经适合训练。": "只收集本 Bot 成功提交發送的合成語音。默認關閉，本地保存；不收用戶錄音、不共享、不自動訓練或刪除。認可不等於已經適合訓練。",
  "只确认当前画像的账号（不选其他画像）": "只確認當前畫像的賬號（不選其他畫像）",
  "只读声明": "只讀聲明",
  "只读日志": "只讀日誌",
  "可以先保存允许名单；连接配置齐全时才重载 QQ 插件。不会改动账号和令牌。": "可以先保存允許名單；連接配置齊全時才重載 QQ 插件。不會改動賬號和令牌。",
  "可安装": "可安裝",
  "可编辑": "可編輯",
  "可选 · 未验证": "可選 · 未驗證",
  "合并稿已放回编辑区，尚未保存。请核对后再保存。": "合併稿已放回編輯區，尚未保存。請覈對後再保存。",
  "同一个人的另一份画像": "同一個人的另一份畫像",
  "同一秒里的第二次保存": "同一秒裏的第二次保存",
  "同步协议配置": "同步協議配置",
  "同步此 QQ 的协议配置": "同步此 QQ 的協議配置",
  "后台整理": "後臺整理",
  "后端仍是旧版本，请重启 Core 后刷新此页。": "後端仍是舊版本，請重啓 Core 後刷新此頁。",
  "后端尚未加载订阅登录功能，请先重启 Core，再保存": "後端尚未加載訂閱登錄功能，請先重啓 Core，再保存",
  "后端尚未加载订阅登录功能，请重启 Core 后刷新此页。": "後端尚未加載訂閱登錄功能，請重啓 Core 後刷新此頁。",
  "向左拖到底收成图标栏，向右拖开；双击恢复默认；方向键微调": "向左拖到底收成圖標欄，向右拖開；雙擊恢復默認；方向鍵微調",
  "启动 SnowLuma": "啓動 SnowLuma",
  "启动 SnowLuma 并尝试恢复当前账号；不会跳过扫码或二次验证。": "啓動 SnowLuma 並嘗試恢復當前賬號；不會跳過掃碼或二次驗證。",
  "启动 TTS": "啓動 TTS",
  "启用": "啓用",
  "启用前请填写代理地址并选择供应商。": "啓用前請填寫代理地址並選擇供應商。",
  "启用模型代理": "啓用模型代理",
  "启用语音发送": "啓用語音發送",
  "启用（未加载）": "啓用（未加載）",
  "和她说句话试试。": "和她說句話試試。",
  "唤醒 / 静候：恢复处理或暂停新请求": "喚醒 / 靜候：恢復處理或暫停新請求",
  "唤醒：恢复处理": "喚醒：恢復處理",
  "唤醒：恢复处理。静候期间未发送 ": "喚醒：恢復處理。靜候期間未發送 ",
  "回复与主动发送": "回覆與主動發送",
  "回复用时": "回覆用時",
  "回执未提供正文。": "回執未提供正文。",
  "国内模型、本地服务及自定义中转保持直连。": "國內模型、本地服務及自定義中轉保持直連。",
  "图标": "圖標",
  "图片不能超过 20 MB。": "圖片不能超過 20 MB。",
  "图片尺寸过大，请先缩小图片。": "圖片尺寸過大，請先縮小圖片。",
  "图片无法打开，请换一张图片。": "圖片無法打開，請換一張圖片。",
  "图片输入": "圖片輸入",
  "图片输入：关": "圖片輸入：關",
  "图片输入：开": "圖片輸入：開",
  "圆角": "圓角",
  "在插件市场查看 ": "在插件市場查看 ",
  "在文件夹里打开": "在文件夾裏打開",
  "均衡 · CPU 2 线程": "均衡 · CPU 2 線程",
  "填入系统代理": "填入系統代理",
  "声明来源": "聲明來源",
  "处理中…": "處理中…",
  "处理设定修订": "處理設定修訂",
  "复制": "複製",
  "复制失败": "複製失敗",
  "复制失败，请手动选择消息文字": "複製失敗，請手動選擇消息文字",
  "复制这条消息": "複製這條消息",
  "外观": "外觀",
  "外观未保存；当前仅为临时显示，请重新保存。": "外觀未保存；當前僅爲臨時顯示，請重新保存。",
  "外观预设": "外觀預設",
  "失败": "失敗",
  "头像无法更新": "頭像無法更新",
  "头像缩放": "頭像縮放",
  "头像裁剪区域，使用方向键调整位置": "頭像裁剪區域，使用方向鍵調整位置",
  "头像设置": "頭像設置",
  "契约": "契約",
  "契约不兼容": "契約不兼容",
  "她写的那两份": "她寫的那兩份",
  "她还没记过谁 —— 她记下来的时候，这里就有第一条。": "她還沒記過誰 —— 她記下來的時候，這裏就有第一條。",
  "字体与尺寸": "字體與尺寸",
  "它只把此 QQ 的 HTTP／WebSocket 地址与令牌保存到当前 Bot，不会改动会话名单。": "它只把此 QQ 的 HTTP／WebSocket 地址與令牌保存到當前 Bot，不會改動會話名單。",
  "安装": "安裝",
  "安装与服务管理": "安裝與服務管理",
  "安装位置": "安裝位置",
  "安装位置：": "安裝位置：",
  "安装依赖检查需重启 Core 后启用。": "安裝依賴檢查需重啓 Core 後啓用。",
  "安装入口：": "安裝入口：",
  "安装包由多个档案共享，当前档案不能单独卸载。请在插件列表中停用；其他档案不受影响。": "安裝包由多個檔案共享，當前檔案不能單獨卸載。請在插件列表中停用；其他檔案不受影響。",
  "安装包由多个档案共享；当前档案可停用，不能单独卸载。": "安裝包由多個檔案共享；當前檔案可停用，不能單獨卸載。",
  "安装文件：需要项目旁的 SnowLuma/index.mjs；未安装时先按项目部署说明安装。": "安裝文件：需要項目旁的 SnowLuma/index.mjs；未安裝時先按項目部署說明安裝。",
  "安装说明与目录": "安裝說明與目錄",
  "官方下载 →": "官方下載 →",
  "实例名称": "實例名稱",
  "实时更新": "實時更新",
  "实时预览": "實時預覽",
  "实际缓存命中": "實際緩存命中",
  "容量尚未查询": "容量尚未查詢",
  "密钥写入结果未确认": "密鑰寫入結果未確認",
  "对方": "對方",
  "对话 · 往来": "對話 · 往來",
  "对话与交接后续": "對話與交接後續",
  "导入失败，原配置已保留。": "導入失敗，原配置已保留。",
  "导入完成。": "導入完成。",
  "导入结果未确认，请检测接入后再操作。": "導入結果未確認，請檢測接入後再操作。",
  "导出诊断": "導出診斷",
  "导出诊断失败，请重试。": "導出診斷失敗，請重試。",
  "导航遮罩": "導航遮罩",
  "将停止当前项目安装目录的协议进程，QQ 连接会断开，但 QQ 程序不会关闭。": "將停止當前項目安裝目錄的協議進程，QQ 連接會斷開，但 QQ 程序不會關閉。",
  "将关联画像：": "將關聯畫像：",
  "小标": "小標",
  "小标上的字": "小標上的字",
  "小点": "小點",
  "尚无整理记录": "尚無整理記錄",
  "尚无样本": "尚無樣本",
  "尚未在真实直播负载下验收。VAD 留待后续。": "尚未在真實直播負載下驗收。VAD 留待後續。",
  "尚未声明": "尚未聲明",
  "尚未收到回执。": "尚未收到回執。",
  "尚未登录": "尚未登錄",
  "尚未落定 · 请在对话中交流，由她决定修改、保留或暂缓。": "尚未落定 · 請在對話中交流，由她決定修改、保留或暫緩。",
  "尚未记录": "尚未記錄",
  "尚未选择登录 QQ；可先保存会话名单，再到 SnowLuma 页完成账号接入。": "尚未選擇登錄 QQ；可先保存會話名單，再到 SnowLuma 頁完成賬號接入。",
  "展开会话号": "展開會話號",
  "展开候选": "展開候選",
  "展开系统事件详情": "展開系統事件詳情",
  "工具声明 · 插件": "工具聲明 · 插件",
  "工具用法与回执": "工具用法與回執",
  "工具详情": "工具詳情",
  "工具详情内容": "工具詳情內容",
  "工具详情正文，可滚动查看": "工具詳情正文，可滾動查看",
  "左栏头像": "左欄頭像",
  "左栏背景": "左欄背景",
  "已从本机载入登录": "已從本機載入登錄",
  "已保存 · 历史版本已保留": "已保存 · 歷史版本已保留",
  "已保存库街区登录凭证": "已保存庫街區登錄憑證",
  "已保存抽卡登录凭证": "已保存抽卡登錄憑證",
  "已保存抽卡登录（未在线验证）": "已保存抽卡登錄（未在線驗證）",
  "已保存登录凭证（详细状态待核对）": "已保存登錄憑證（詳細狀態待覈對）",
  "已保存登录（未在线验证）": "已保存登錄（未在線驗證）",
  "已保存账号": "已保存賬號",
  "已保存，下一轮匹配的 QQ 场景会读取新版。": "已保存，下一輪匹配的 QQ 場景會讀取新版。",
  "已保存，下次请求生效。": "已保存，下次請求生效。",
  "已停止 · 用时 $1 秒": "已停止 · 用時 $1 秒",
  "已关联画像：": "已關聯畫像：",
  "已删除。": "已刪除。",
  "已取消合并；原草稿和读时版本未变，尚未保存。": "已取消合併；原草稿和讀時版本未變，尚未保存。",
  "已取消，没有执行操作。": "已取消，沒有執行操作。",
  "已启动": "已啓動",
  "已启用": "已啓用",
  "已启用 · 未加载": "已啓用 · 未加載",
  "已启用，尚未加载": "已啓用，尚未加載",
  "已唤醒": "已喚醒",
  "已填默认值 —— 填完按「保存」": "已填默認值 —— 填完按「保存」",
  "已复制": "已複製",
  "已安装": "已安裝",
  "已安装 · INSTALLED": "已安裝 · INSTALLED",
  "已并入（历史）": "已併入（歷史）",
  "已打开 ": "已打開 ",
  "已打开本机音色目录（references）。": "已打開本機音色目錄（references）。",
  "已找到 3 条相关记录。": "已找到 3 條相關記錄。",
  "已显示全部记录": "已顯示全部記錄",
  "已暂停显示 · 可手动刷新": "已暫停顯示 · 可手動刷新",
  "已暂停跟随": "已暫停跟隨",
  "已登录": "已登錄",
  "已确认的账号归属": "已確認的賬號歸屬",
  "已绑定": "已綁定",
  "已结束 · 不足 1 秒": "已結束 · 不足 1 秒",
  "已认可": "已認可",
  "已设置；留空保持原值": "已設置；留空保持原值",
  "已进入静候": "已進入靜候",
  "已连接": "已連接",
  "已连接，正在收尾…": "已連接，正在收尾…",
  "已连接，等待接入任务收尾…": "已連接，等待接入任務收尾…",
  "已配置 · 未运行": "已配置 · 未運行",
  "已配置 · 运行中": "已配置 · 運行中",
  "已配置——上游默认": "已配置——上游默認",
  "已配置——输入新 API 密钥可替换": "已配置——輸入新 API 密鑰可替換",
  "市场那一格没答上来。": "市場那一格沒答上來。",
  "帮我查一下之前的相关记录。": "幫我查一下之前的相關記錄。",
  "常规配置": "常規配置",
  "序列化字节，非 token": "序列化字節，非 token",
  "底层服务在线 · 外部启动": "底層服務在線 · 外部啓動",
  "底层服务在线 · 页面管理": "底層服務在線 · 頁面管理",
  "建立独立身份": "建立獨立身份",
  "建议填写；多个 QQ 进程时只连接此账号": "建議填寫；多個 QQ 進程時只連接此賬號",
  "开": "開",
  "开始下载": "開始下載",
  "强调": "強調",
  "强调小标": "強調小標",
  "强调淡底": "強調淡底",
  "强调色上文字": "強調色上文字",
  "归 ": "歸 ",
  "归档链接": "歸檔鏈接",
  "当前 Bot · 环境/QQ.md": "當前 Bot · 環境/QQ.md",
  "当前 Bot 全链路": "當前 Bot 全鏈路",
  "当前 Bot 无法确认，请刷新后重试。": "當前 Bot 無法確認，請刷新後重試。",
  "当前 Bot 的 QQ 环境提示词": "當前 Bot 的 QQ 環境提示詞",
  "当前 Bot 的 QQ：": "當前 Bot 的 QQ：",
  "当前 Bot、当前提供商与模型的留存累计；跨交接，不代表本次上线增量。": "當前 Bot、當前提供商與模型的留存累計；跨交接，不代表本次上線增量。",
  "当前 Core 未提供环境声明读取接口。": "當前 Core 未提供環境聲明讀取接口。",
  "当前协议及服务尚未适配思考开关": "當前協議及服務尚未適配思考開關",
  "当前协议及服务尚未适配推理强度": "當前協議及服務尚未適配推理強度",
  "当前处于静候，请唤醒后发送": "當前處於靜候，請喚醒後發送",
  "当前控制台": "當前控制台",
  "当前控制台 · 当前模型累计": "當前控制台 · 當前模型累計",
  "当前控制台 · 当前模型累计输入用量与缓存命中": "當前控制台 · 當前模型累計輸入用量與緩存命中",
  "当前显示上次缓存的插件列表。": "當前顯示上次緩存的插件列表。",
  "当前服务尚不支持账号管理。": "當前服務尚不支持賬號管理。",
  "当前本地服务无需 API 密钥": "當前本地服務無需 API 密鑰",
  "当前模型": "當前模型",
  "当前没有可显示的 QQ 通道环境声明。": "當前沒有可顯示的 QQ 通道環境聲明。",
  "待分类": "待分類",
  "待复核": "待複覈",
  "待恢复": "待恢復",
  "待机基础上下文估算": "待機基礎上下文估算",
  "待确认进程已过期或失效，尚未加载 QQ。请重新获取候选，然后核对并确认；无需重启服务。": "待確認進程已過期或失效，尚未加載 QQ。請重新獲取候選，然後覈對並確認；無需重啓服務。",
  "待评审": "待評審",
  "待评审 ": "待評審 ",
  "待配置 API 密钥": "待配置 API 密鑰",
  "待重试": "待重試",
  "心里悬着的": "心裏懸着的",
  "心里没有悬着的事。": "心裏沒有懸着的事。",
  "忘记管理凭据": "忘記管理憑據",
  "思维链": "思維鏈",
  "思考预算": "思考預算",
  "性能 · CPU 3 线程": "性能 · CPU 3 線程",
  "性能档": "性能檔",
  "总输出上限必须小于上下文容量": "總輸出上限必須小於上下文容量",
  "恢复检测已提交；检测完成后请核对候选进程，尚不代表已加载。": "恢復檢測已提交；檢測完成後請覈對候選進程，尚不代表已加載。",
  "或者换一格版本名（slot）再存。盘上原来那一版一个字都没动。": "或者換一格版本名（slot）再存。盤上原來那一版一個字都沒動。",
  "手写": "手寫",
  "手动合并稿（请对照新版编辑）": "手動合併稿（請對照新版編輯）",
  "打开 ": "打開 ",
  "打开 ASR 本地模型文件夹": "打開 ASR 本地模型文件夾",
  "打开 SnowLuma 管理页 →": "打開 SnowLuma 管理頁 →",
  "打开 TTS 本地模型文件夹": "打開 TTS 本地模型文件夾",
  "打开 TTS 音色文件夹": "打開 TTS 音色文件夾",
  "打开安装目录 →": "打開安裝目錄 →",
  "打开插件页面 →": "打開插件頁面 →",
  "打开文件夹": "打開文件夾",
  "打开本机 SnowLuma 管理页": "打開本機 SnowLuma 管理頁",
  "打开本机自定义音色目录（references），用于管理参考音频": "打開本機自定義音色目錄（references），用於管理參考音頻",
  "执行回执": "執行回執",
  "执行失败": "執行失敗",
  "扩展状态尚未接入。": "擴展狀態尚未接入。",
  "扩展状态暂时读不到，正在重试…": "擴展狀態暫時讀不到，正在重試…",
  "扫码、二次验证和条款确认仍由你完成。": "掃碼、二次驗證和條款確認仍由你完成。",
  "扫码与二次验证仍需在 QQ 中完成。": "掃碼與二次驗證仍需在 QQ 中完成。",
  "找到了 3 条相关记录，我来帮你整理。": "找到了 3 條相關記錄，我來幫你整理。",
  "抛在「": "拋在「",
  "报的是只看文本": "報的是隻看文本",
  "拖动调整位置，滚轮或滑杆缩放；方向键也可移动。": "拖動調整位置，滾輪或滑桿縮放；方向鍵也可移動。",
  "按 1KiB 连续前缀保守匹配，完整相同请求精确匹配。仅本次进程留存，重启清零；与同分区最近 4 次请求比较，非上游缓存有效期。": "按 1KiB 連續前綴保守匹配，完整相同請求精確匹配。僅本次進程留存，重啓清零；與同分區最近 4 次請求比較，非上游緩存有效期。",
  "按名称筛选…": "按名稱篩選…",
  "按用户名筛选…": "按用戶名篩選…",
  "按画像与关系的最近记录时间排列": "按畫像與關係的最近記錄時間排列",
  "按需发送 · 仅工具调用": "按需發送 · 僅工具調用",
  "按需发送仅供 Agent 工具调用；回复附语音会先送文字，语音失败不撤回文字。": "按需發送僅供 Agent 工具調用；回覆附語音會先送文字，語音失敗不撤回文字。",
  "授权地址校验失败，未打开": "授權地址校驗失敗，未打開",
  "已登录，可以测试连通。": "已登入，可以測試連通。",
  "接入已完成：目标 QQ 在线、插件 WS 已连接。真实消息收发仍待验收。": "接入已完成：目標 QQ 在線、插件 WS 已連接。真實消息收發仍待驗收。",
  "接入检查": "接入檢查",
  "接入账号不匹配": "接入賬號不匹配",
  "接入配置清单 · ": "接入配置清單 · ",
  "接收语音时转写": "接收語音時轉寫",
  "控制台 · 本机入口": "控制台 · 本機入口",
  "控制状态读取失败：": "控制狀態讀取失敗：",
  "控制通道不可用，尚未提交加载；请先检查服务状态。": "控制通道不可用，尚未提交加載；請先檢查服務狀態。",
  "推理强度": "推理強度",
  "推荐 Fun-CosyVoice3-0.5B-2512，约 5.43 GB；仅下载权重，不安装运行环境。": "推薦 Fun-CosyVoice3-0.5B-2512，約 5.43 GB；僅下載權重，不安裝運行環境。",
  "推荐 SenseVoiceSmall INT8，约 230 MB；点击后下载并校验安装。": "推薦 SenseVoiceSmall INT8，約 230 MB；點擊後下載並校驗安裝。",
  "推荐模型已安装，不重复下载": "推薦模型已安裝，不重複下載",
  "推荐模型已安装；运行环境需单独配置，不含私有音色。": "推薦模型已安裝；運行環境需單獨配置，不含私有音色。",
  "提交的那一版已保存；在途新输入仍是未保存草稿。": "提交的那一版已保存；在途新輸入仍是未保存草稿。",
  "提出设定修订": "提出設定修訂",
  "插件会立即停止运行；它提供的事件监听与工具将暂时不可用。再次启用后会按当前配置重新加载。": "插件會立即停止運行；它提供的事件監聽與工具將暫時不可用。再次啓用後會按當前配置重新加載。",
  "插件列表刷新失败 · 自动重试": "插件列表刷新失敗 · 自動重試",
  "插件列表读取失败 · 自动重试": "插件列表讀取失敗 · 自動重試",
  "插件已启用但未加载": "插件已啓用但未加載",
  "插件市场": "插件市場",
  "插件市场 · MARKETPLACE": "插件市場 · MARKETPLACE",
  "插件开发规范 ": "插件開發規範 ",
  "插件总览": "插件總覽",
  "插件总览 · INFINITY": "插件總覽 · INFINITY",
  "插件提供的 QQ 通道环境说明。": "插件提供的 QQ 通道環境說明。",
  "插件操作没走到服务：": "插件操作沒走到服務：",
  "插件操作没走完：": "插件操作沒走完：",
  "插件状态": "插件狀態",
  "插件状态读取失败": "插件狀態讀取失敗",
  "插件目录读取失败：": "插件目錄讀取失敗：",
  "插件自定义配置页": "插件自定義配置頁",
  "插件运行中": "插件運行中",
  "插件运行：启用 QQ 插件后，用允许名单内的账号发送测试消息。": "插件運行：啓用 QQ 插件後，用允許名單內的賬號發送測試消息。",
  "插件页面": "插件頁面",
  "搜索做法与技能的标题或正文": "搜索做法與技能的標題或正文",
  "搜索标题或正文": "搜索標題或正文",
  "操作失败。": "操作失敗。",
  "操作失败，请重试。": "操作失敗，請重試。",
  "操作失败，身份绑定未确认。": "操作失敗，身份綁定未確認。",
  "操作未完成，请刷新后核对。": "操作未完成，請刷新後覈對。",
  "操作未完成，请查看详情。": "操作未完成，請查看詳情。",
  "操作没有完成。": "操作沒有完成。",
  "操作状态未确认，请检查连接。": "操作狀態未確認，請檢查連接。",
  "操作账（最近 ": "操作賬（最近 ",
  "支持 HTTP(S) 代理，不支持带账号密码的地址。": "支持 HTTP(S) 代理，不支持帶賬號密碼的地址。",
  "收到真实回复才算收发验收完成。": "收到真實回覆纔算收發驗收完成。",
  "收可选值时那一份读不到：": "收可選值時那一份讀不到：",
  "收起会话号": "收起會話號",
  "收起工具详情": "收起工具詳情",
  "放弃未保存草稿？": "放棄未保存草稿？",
  "放弃草稿并重载": "放棄草稿並重載",
  "数字与标点规范化": "數字與標點規範化",
  "整理态是它的副产品（她会自己动手写那两份）": "整理態是它的副產品（她會自己動手寫那兩份）",
  "整理状态：": "整理狀態：",
  "文件版本冲突或格式校验失败，草稿已保留。": "文件版本衝突或格式校驗失敗，草稿已保留。",
  "文字与边界": "文字與邊界",
  "文档": "文檔",
  "文档结构保护拒绝了本次改写；已保存的内容保留，草稿待复核。": "文檔結構保護拒絕了本次改寫；已保存的內容保留，草稿待複覈。",
  "断开": "斷開",
  "断线补读 · 0—30 分钟": "斷線補讀 · 0—30 分鐘",
  "新增语音选项需重启 Core 后启用，试听还需重启 TTS 服务。": "新增語音選項需重啓 Core 後啓用，試聽還需重啓 TTS 服務。",
  "新版没有可用 revision，请检查服务版本": "新版沒有可用 revision，請檢查服務版本",
  "无": "無",
  "无法开始下载，请检查 Core 服务。": "無法開始下載，請檢查 Core 服務。",
  "无法打开安装目录": "無法打開安裝目錄",
  "无法打开模型文件夹。": "無法打開模型文件夾。",
  "无法打开目录，请检查 Core 服务。": "無法打開目錄，請檢查 Core 服務。",
  "无法打开音色文件夹。": "無法打開音色文件夾。",
  "无法生成裁剪图片。": "無法生成裁剪圖片。",
  "无法读取代理设置；请确认后端版本并重试。未用默认值覆盖。": "無法讀取代理設置；請確認後端版本並重試。未用默認值覆蓋。",
  "无法读取插件配置": "無法讀取插件配置",
  "无法读取登录状态": "無法讀取登錄狀態",
  "无需 API 密钥": "無需 API 密鑰",
  "无需参数。": "無需參數。",
  "日常在线不需要重复同步；不同 QQ 使用独立端口和自己的令牌。": "日常在線不需要重複同步；不同 QQ 使用獨立端口和自己的令牌。",
  "日志来源": "日誌來源",
  "显示名无法更新": "顯示名無法更新",
  "显示更多": "顯示更多",
  "显示最近 ": "顯示最近 ",
  "普通 QQ 回复附带语音": "普通 QQ 回覆附帶語音",
  "暂不选择": "暫不選擇",
  "暂无": "暫無",
  "暂无日志；服务启动并产生输出后会显示在这里。": "暫無日誌；服務啓動併產生輸出後會顯示在這裏。",
  "暂无正文": "暫無正文",
  "暂时读不到扩展状态。": "暫時讀不到擴展狀態。",
  "暂未保存鸣潮账号": "暫未保存鳴潮賬號",
  "更新于": "更新於",
  "曾经部分提交：": "曾經部分提交：",
  "最后写于 ": "最後寫於 ",
  "最大输出 token": "最大輸出 token",
  "最近一次模型请求": "最近一次模型請求",
  "最近写下的那一句": "最近寫下的那一句",
  "最近片段中没有错误流输出。": "最近片段中沒有錯誤流輸出。",
  "最近的画像": "最近的畫像",
  "有必填项未填，请检查": "有必填項未填，請檢查",
  "有未保存的修改；请先保存，或离开本页后重新进入。": "有未保存的修改；請先保存，或離開本頁後重新進入。",
  "服务已休止": "服務已休止",
  "服务已启动；QQ 离线时使用“连接或恢复此 QQ”，正常在线无需重复接入。": "服務已啓動；QQ 離線時使用“連接或恢復此 QQ”，正常在線無需重複接入。",
  "服务未启动": "服務未啓動",
  "未保存登录凭证": "未保存登錄憑證",
  "未启动": "未啓動",
  "未启用": "未啓用",
  "未启用主人分享": "未啓用主人分享",
  "未命名实例": "未命名實例",
  "未安装": "未安裝",
  "未找到匹配条目，请换个关键词或类别。": "未找到匹配條目，請換個關鍵詞或類別。",
  "未检测到可用版本": "未檢測到可用版本",
  "未登录": "未登錄",
  "未绑定": "未綁定",
  "未缓存输入": "未緩存輸入",
  "未记录对应调用参数。": "未記錄對應調用參數。",
  "未记录调用参数。": "未記錄調用參數。",
  "未设置": "未設置",
  "未选模型时打开 ASR 模型根目录": "未選模型時打開 ASR 模型根目錄",
  "未选模型时打开 TTS 模型根目录": "未選模型時打開 TTS 模型根目錄",
  "本地前缀复用": "本地前綴複用",
  "本地前缀复用（估算）": "本地前綴複用（估算）",
  "本地模型在线": "本地模型在線",
  "本地模型未响应；QQ 消息仍可入站，但 Agent 暂时无法生成回复。": "本地模型未響應；QQ 消息仍可入站，但 Agent 暫時無法生成回覆。",
  "本地模型离线": "本地模型離線",
  "本地模型离线，回复请求会失败": "本地模型離線，回覆請求會失敗",
  "本地请求前缀复用估算，按序列化 UTF-8 字节加权；不是上游实际缓存命中或计费 token。": "本地請求前綴複用估算，按序列化 UTF-8 字節加權；不是上游實際緩存命中或計費 token。",
  "本机控制台仅代表入口，请核对实际使用者。": "本機控制台僅代表入口，請覈對實際使用者。",
  "本机终端入口键：console:local-owner。它只标记入口，不证明操作者身份；确认由你本人使用后再绑定。": "本機終端入口鍵：console:local-owner。它只標記入口，不證明操作者身份；確認由你本人使用後再綁定。",
  "本次使用字面检索，语义检索暂不可用。": "本次使用字面檢索，語義檢索暫不可用。",
  "本次保存已完成，后续修改仍未保存。": "本次保存已完成，後續修改仍未保存。",
  "本轮已结束": "本輪已結束",
  "本轮已结束，请等待状态更新": "本輪已結束，請等待狀態更新",
  "条": "條",
  "来源": "來源",
  "来源不明": "來源不明",
  "来话昵称仅供界面辨认，不会自动绑定或合并身份。要向 BOT 介绍某个人，请在对话里说明。": "來話暱稱僅供界面辨認，不會自動綁定或合併身份。要向 BOT 介紹某個人，請在對話裏說明。",
  "来话显示名（仅界面） · 画像键 ": "來話顯示名（僅界面） · 畫像鍵 ",
  "查看 QQ 连接状态 →": "查看 QQ 連接狀態 →",
  "查看全部画像 →": "查看全部畫像 →",
  "查看关系": "查看關係",
  "查看协议服务的最近输出；不是 Core 命令终端，不执行命令。共享服务的日志可能包含其他账号。": "查看協議服務的最近輸出；不是 Core 命令終端，不執行命令。共享服務的日誌可能包含其他賬號。",
  "查看当前 Bot 的鸣潮账号与本机登录凭证。插件启停请在插件总览中操作。": "查看當前 Bot 的鳴潮賬號與本機登錄憑證。插件啓停請在插件總覽中操作。",
  "查看技能目录": "查看技能目錄",
  "查阅经历": "查閱經歷",
  "标准流 → 错误流，非时间排序": "標準流 → 錯誤流，非時間排序",
  "标准输出": "標準輸出",
  "档案保存已返回，但权威绑定回执未确认；未写入密钥，请刷新核对。": "檔案保存已返回，但權威綁定回執未確認；未寫入密鑰，請刷新覈對。",
  "检测暂不可用。": "檢測暫不可用。",
  "检测请求失败，请稍后重试。": "檢測請求失敗，請稍後重試。",
  "检索「": "檢索「",
  "检索知识": "檢索知識",
  "检索记忆": "檢索記憶",
  "模型仅支持文本": "模型僅支持文本",
  "模型供应商": "模型供應商",
  "模型加载中…": "模型加載中…",
  "模型名称": "模型名稱",
  "模型支持图片输入": "模型支持圖片輸入",
  "模型档案": "模型檔案",
  "模型离线": "模型離線",
  "模型设置": "模型設置",
  "模型请求合计": "模型請求合計",
  "模型配置尚未保存，当前没有可用的模型配置。": "模型配置尚未保存，當前沒有可用的模型配置。",
  "模型配置尚未保存，本次仍使用当前已保存的配置。": "模型配置尚未保存，本次仍使用當前已保存的配置。",
  "次级文字": "次級文字",
  "正在保存…取消等待不会撤回服务器已经完成的保存。": "正在保存…取消等待不會撤回服務器已經完成的保存。",
  "正在保存，请稍候": "正在保存，請稍候",
  "正在停止，请稍候": "正在停止，請稍候",
  "正在停止，请稍候…": "正在停止，請稍候…",
  "正在删除…": "正在刪除…",
  "正在发送…": "正在發送…",
  "正在只读检查协议状态": "正在只讀檢查協議狀態",
  "正在合成试听音频…": "正在合成試聽音頻…",
  "正在后台刷新，请稍后点击刷新查看结果。": "正在後臺刷新，請稍後點擊刷新查看結果。",
  "正在唤醒": "正在喚醒",
  "正在唤醒…": "正在喚醒…",
  "正在唤醒……": "正在喚醒……",
  "正在唤醒：本地服务就绪后恢复处理。": "正在喚醒：本地服務就緒後恢復處理。",
  "正在处理…": "正在處理…",
  "正在开始下载…": "正在開始下載…",
  "正在恢复接入，请等待本页实时结果，不要重复点击。": "正在恢復接入，請等待本頁實時結果，不要重複點擊。",
  "正在打开模型文件夹…": "正在打開模型文件夾…",
  "正在打开音色文件夹…": "正在打開音色文件夾…",
  "正在检测已保存的接入设置…": "正在檢測已保存的接入設置…",
  "正在测试…（只发一次请求，请等待）": "正在測試…（只發一次請求，請等待）",
  "正在测试…（最多等待45秒，只发一次请求）": "正在測試…（最多等待45秒，只發一次請求）",
  "正在自动只读检查目标账号与协议状态；不会登录、注入 QQ 或发送测试消息。": "正在自動只讀檢查目標賬號與協議狀態；不會登錄、注入 QQ 或發送測試消息。",
  "正在读…": "正在讀…",
  "正在读取 QQ 状态…": "正在讀取 QQ 狀態…",
  "正在读取 QQ 配置": "正在讀取 QQ 配置",
  "正在读取 QQ 配置；数据就绪前不能保存、试听或启停进程。": "正在讀取 QQ 配置；數據就緒前不能保存、試聽或啓停進程。",
  "正在读取插件列表…": "正在讀取插件列表…",
  "正在读取插件状态…": "正在讀取插件狀態…",
  "正在读取日志…": "正在讀取日誌…",
  "正在读取模型代理设置…": "正在讀取模型代理設置…",
  "正在读取状态…": "正在讀取狀態…",
  "正在读取环境声明…": "正在讀取環境聲明…",
  "正在读取登录状态…": "正在讀取登錄狀態…",
  "正在读取目标账号配置…": "正在讀取目標賬號配置…",
  "正在读取配置，请稍候再操作。": "正在讀取配置，請稍候再操作。",
  "正在读那十段…": "正在讀那十段…",
  "正在进入静候……": "正在進入靜候……",
  "正在连接账号…": "正在連接賬號…",
  "正在验证并接入…": "正在驗證並接入…",
  "正文字体": "正文字體",
  "正文未提供，请更新服务后重试。": "正文未提供，請更新服務後重試。",
  "正文（旧名）": "正文（舊名）",
  "此功能需重启 Core 后启用": "此功能需重啓 Core 後啓用",
  "此处保存管理凭据与目标 QQ，不会导入 OneBot 连接或启用插件。": "此處保存管理憑據與目標 QQ，不會導入 OneBot 連接或啓用插件。",
  "此套餐仍需 API Key；已锁定专用地址。仅限厂商允许的编码场景，不是通用聊天订阅。": "此套餐仍需 API Key；已鎖定專用地址。僅限廠商允許的編碼場景，不是通用聊天訂閱。",
  "此插件暂未提供网页配置项。": "此插件暫未提供網頁配置項。",
  "此插件未声明配置字段，也没有 config.json 或 config.example.json；请由作者提供声明。": "此插件未聲明配置字段，也沒有 config.json 或 config.example.json；請由作者提供聲明。",
  "此检查不代表鉴权已验证；不要关闭鉴权或对外开放端口。": "此檢查不代表鑑權已驗證；不要關閉鑑權或對外開放端口。",
  "此类别尚未记录。": "此類別尚未記錄。",
  "此项记录历史完成状态，不代表当前鉴权或 QQ 在线；清除或变更管理凭据后需重新验证。": "此項記錄歷史完成狀態，不代表當前鑑權或 QQ 在線；清除或變更管理憑據後需重新驗證。",
  "每秒读取最近片段，非完整历史。向上滚动会保留当前画面；错误流可能来自较早运行，不代表当前连接状态。": "每秒讀取最近片段，非完整歷史。向上滾動會保留當前畫面；錯誤流可能來自較早運行，不代表當前連接狀態。",
  "每行一个 QQ 号，也可用逗号分隔": "每行一個 QQ 號，也可用逗號分隔",
  "每行一个群号，也可用逗号分隔": "每行一個羣號，也可用逗號分隔",
  "每页显示": "每頁顯示",
  "比率按有效命中 / 输入 token 总和加权，非各次百分比平均。": "比率按有效命中 / 輸入 token 總和加權，非各次百分比平均。",
  "比较单位": "比較單位",
  "气泡": "氣泡",
  "沟通做法": "溝通做法",
  "没有匹配项，可直接输入": "沒有匹配項，可直接輸入",
  "没有对上的。": "沒有對上的。",
  "没有改动 —— 这一下没碰盘（要读盘上现在那一份，按「重载」）": "沒有改動 —— 這一下沒碰盤（要讀盤上現在那一份，按「重載」）",
  "没有模型的档案不许设为当前": "沒有模型的檔案不許設爲當前",
  "没有需要保存的修改": "沒有需要保存的修改",
  "浅色": "淺色",
  "测试失败: ": "測試失敗: ",
  "测试成功": "測試成功",
  "测试连通": "測試連通",
  "浏览器无法预览图片。": "瀏覽器無法預覽圖片。",
  "浏览器阻止了授权窗口，请允许弹出窗口后重新登录。": "瀏覽器阻止了授權窗口，請允許彈出窗口後重新登錄。",
  "浮现": "浮現",
  "消息反应窗口 · 0—10 秒": "消息反應窗口 · 0—10 秒",
  "消息已接收，等待处理…": "消息已接收，等待處理…",
  "消息输入": "消息輸入",
  "清空筛选": "清空篩選",
  "温度": "溫度",
  "温度由上游控制；输出值为本地预算。": "溫度由上游控制；輸出值爲本地預算。",
  "源代码仓库": "源代碼倉庫",
  "点击空白处也可收起 · ": "點擊空白處也可收起 · ",
  "父级控制通道不可用；配置已保留，请先从原启动器正常收尾。": "父級控制通道不可用；配置已保留，請先從原啓動器正常收尾。",
  "版本冲突：草稿已保留。读取新版手动合并，或确认重载后放弃草稿。": "版本衝突：草稿已保留。讀取新版手動合併，或確認重載後放棄草稿。",
  "状态": "狀態",
  "状态待确认，请检查连接": "狀態待確認，請檢查連接",
  "状态更新失败；保留上次读取内容，不代表当前进程已经加载此声明。": "狀態更新失敗；保留上次讀取內容，不代表當前進程已經加載此聲明。",
  "环境": "環境",
  "环境/QQ.md": "環境/QQ.md",
  "用户": "用戶",
  "用时 $1 秒": "用時 $1 秒",
  "由模型决定": "由模型決定",
  "画像": "畫像",
  "画像 · PROFILE": "畫像 · PROFILE",
  "画像读取失败：": "畫像讀取失敗：",
  "画像这一层没接上读法。": "畫像這一層沒接上讀法。",
  "画那一页": "畫那一頁",
  "界面语言": "界面語言",
  "留空自动查找；找不到时填写 QQ.exe 完整路径": "留空自動查找；找不到時填寫 QQ.exe 完整路徑",
  "登录信息已标记失效": "登錄信息已標記失效",
  "登录凭证": "登錄憑證",
  "登录凭证已标记失效": "登錄憑證已標記失效",
  "登录操作未完成，请刷新状态后重试。": "登錄操作未完成，請刷新狀態後重試。",
  "的经历": "的經歷",
  "盘上新版": "盤上新版",
  "目前仅支持简体中文": "目前僅支持簡體中文",
  "目前没有扩展。": "目前沒有擴展。",
  "目标 QQ 与插件 WS 已连接；接入任务仍在收尾，完成前保持操作锁，不重复加载。": "目標 QQ 與插件 WS 已連接；接入任務仍在收尾，完成前保持操作鎖，不重複加載。",
  "目标 QQ 号": "目標 QQ 號",
  "目标 QQ 在线，插件 WS 已连接；状态自动只读检查，无需重复验证接入。真实消息收发仍需单独验收。": "目標 QQ 在線，插件 WS 已連接；狀態自動只讀檢查，無需重複驗證接入。真實消息收發仍需單獨驗收。",
  "目标 QQ 在线；插件 WS 尚未连接，请检查插件状态。无需重复管理登录或注入 QQ。": "目標 QQ 在線；插件 WS 尚未連接，請檢查插件狀態。無需重複管理登錄或注入 QQ。",
  "知识库": "知識庫",
  "知识库 · KNOWLEDGE": "知識庫 · KNOWLEDGE",
  "知识库读取失败：": "知識庫讀取失敗：",
  "知识库这一层没接上读法。": "知識庫這一層沒接上讀法。",
  "知识库里还没有东西 —— 定下来的事写进那儿，这里就看得见。": "知識庫裏還沒有東西 —— 定下來的事寫進那兒，這裏就看得見。",
  "确认": "確認",
  "确认停止": "確認停止",
  "确认关联": "確認關聯",
  "确认分开": "確認分開",
  "确认卸载": "確認卸載",
  "确认同步": "確認同步",
  "确认后，下一次私人请求才会使用这条关联。": "確認後，下一次私人請求才會使用這條關聯。",
  "确认安装": "確認安裝",
  "确认已过期或进程列表已变化，尚未提交加载。请点击“重新获取待确认进程”后重新核对。": "確認已過期或進程列表已變化，尚未提交加載。請點擊“重新獲取待確認進程”後重新覈對。",
  "确认并加载一次": "確認並加載一次",
  "确认建立": "確認建立",
  "确认是同一个人": "確認是同一個人",
  "确认目标 QQ 进程": "確認目標 QQ 進程",
  "确认绑定": "確認綁定",
  "确认解绑": "確認解綁",
  "确认账号归属": "確認賬號歸屬",
  "稳定平台账号": "穩定平臺賬號",
  "空闲": "空閒",
  "端口可达": "端口可達",
  "端点": "端點",
  "等宽字体": "等寬字體",
  "等宽（旧名）": "等寬（舊名）",
  "等待回执": "等待回執",
  "等待授权": "等待授權",
  "等待读取日志": "等待讀取日誌",
  "等待首段输出": "等待首段輸出",
  "筛过之后一个都没有。": "篩過之後一個都沒有。",
  "筛选": "篩選",
  "筛选已安装插件": "篩選已安裝插件",
  "筛选技能类别": "篩選技能類別",
  "筛选插件市场": "篩選插件市場",
  "筛选：插件名、描述、作者": "篩選：插件名、描述、作者",
  "简体中文": "簡體中文",
  "管理凭据已用 Windows 当前用户加密保存；不会回传密码。QQ 扫码和二次验证仍须手动完成。": "管理憑據已用 Windows 當前用戶加密保存；不會回傳密碼。QQ 掃碼和二次驗證仍須手動完成。",
  "管理在线采样已过期（30 秒），不代表 QQ 掉线；点击验证接入重新采样，无需重填账号或重复登录。": "管理在線採樣已過期（30 秒），不代表 QQ 掉線；點擊驗證接入重新採樣，無需重填賬號或重複登錄。",
  "管理端口可达；QQ 会话与 WS 状态未核验，请验证目标接入。": "管理端口可達；QQ 會話與 WS 狀態未覈驗，請驗證目標接入。",
  "管理端尚未启动，请先点击启动 SnowLuma": "管理端尚未啓動，請先點擊啓動 SnowLuma",
  "管理端验证（可选）：QQ 已在线；管理验证记录单独保存，不影响当前 QQ 在线状态。需要通过管理端恢复接入时再验证管理凭据。": "管理端驗證（可選）：QQ 已在線；管理驗證記錄單獨保存，不影響當前 QQ 在線狀態。需要通過管理端恢復接入時再驗證管理憑據。",
  "系统 · ": "系統 · ",
  "系统消息 · 人设": "系統消息 · 人設",
  "素材库不可用；更新后需重启 Core。": "素材庫不可用；更新後需重啓 Core。",
  "素材库操作失败": "素材庫操作失敗",
  "素材评审状态": "素材評審狀態",
  "素材页响应已过期": "素材頁響應已過期",
  "素材页已离开": "素材頁已離開",
  "索引状态：": "索引狀態：",
  "累计": "累計",
  "累计仅限当前控制台、模型与上下文分区，不代表本次上线增量。": "累計僅限當前控制台、模型與上下文分區，不代表本次上線增量。",
  "累计批": "累計批",
  "累计输入用量": "累計輸入用量",
  "纯白": "純白",
  "组件试色": "組件試色",
  "细节调整": "細節調整",
  "细边界": "細邊界",
  "终端": "終端",
  "终端 · TERMINAL": "終端 · TERMINAL",
  "经历": "經歷",
  "经历 · EXPERIENCE": "經歷 · EXPERIENCE",
  "经历读取失败：": "經歷讀取失敗：",
  "经历这一层没接上读法。": "經歷這一層沒接上讀法。",
  "绑定": "綁定",
  "绑定账号": "綁定賬號",
  "结束本轮": "結束本輪",
  "结构 · 基座": "結構 · 基座",
  "统计不完整，仅含有效上报用量": "統計不完整，僅含有效上報用量",
  "统计单位：HTTP 尝试；不回退旧混合累计": "統計單位：HTTP 嘗試；不回退舊混合累計",
  "续读": "續讀",
  "维护记录 · ": "維護記錄 · ",
  "缓存命中": "緩存命中",
  "缓存命中 —": "緩存命中 —",
  "缓存观测不可用或版本不兼容": "緩存觀測不可用或版本不兼容",
  "缓存读取": "緩存讀取",
  "编辑 ": "編輯 ",
  "编辑当前 Bot 在 QQ 场景中的提示词。": "編輯當前 Bot 在 QQ 場景中的提示詞。",
  "缩放 ": "縮放 ",
  "缺少同快照身份修订号，请刷新后再操作。": "缺少同快照身份修訂號，請刷新後再操作。",
  "缺少读时 revision，未保存。请保留草稿并重新加载新版后合并。": "缺少讀時 revision，未保存。請保留草稿並重新加載新版後合併。",
  "网址": "網址",
  "群成员各自绑定、登录自己的账号。此处仅展示本机保存情况，不代表 QQ 在线状态或库街区凭证实时有效；刷新名单不会联网验证凭证。": "羣成員各自綁定、登錄自己的賬號。此處僅展示本機保存情況，不代表 QQ 在線狀態或庫街區憑證實時有效；刷新名單不會聯網驗證憑證。",
  "群聊历史仍按场景隔离；无归属旧记录不会自动认领。": "羣聊歷史仍按場景隔離；無歸屬舊記錄不會自動認領。",
  "聊天头像": "聊天頭像",
  "聊天气泡": "聊天氣泡",
  "背景层级": "背景層級",
  "胶囊": "膠囊",
  "自动查询；未知时使用 128K（131072）": "自動查詢；未知時使用 128K（131072）",
  "自动识别": "自動識別",
  "自动跟随上游（当前 ": "自動跟隨上游（當前 ",
  "自我记录": "自我記錄",
  "英语": "英語",
  "获取模型列表": "獲取模型列表",
  "菜单": "菜單",
  "补充自述": "補充自述",
  "裁剪头像": "裁剪頭像",
  "观测异常，统计可能不完整": "觀測異常，統計可能不完整",
  "视觉": "視覺",
  "解绑": "解綁",
  "解绑账号": "解綁賬號",
  "解除这个账号与当前身份的关联。已有画像和历史消息不会被改写。": "解除這個賬號與當前身份的關聯。已有畫像和歷史消息不會被改寫。",
  "订阅供应商请从套餐列表选择；未实现的账号登录不会冒充支持": "訂閱供應商請從套餐列表選擇；未實現的賬號登錄不會冒充支持",
  "订阅套餐": "訂閱套餐",
  "订阅接口不发送此参数": "訂閱接口不發送此參數",
  "订阅接口不发送输出上限；此值仍作为 Core 本地输出保护预算": "訂閱接口不發送輸出上限；此值仍作爲 Core 本地輸出保護預算",
  "订阅接口不支持调整": "訂閱接口不支持調整",
  "订阅登录无需 API Key": "訂閱登錄無需 API Key",
  "模型测试成功": "模型測試成功",
  "服务已连通，可以获取模型列表。": "服務已連通，可以取得模型列表。",
  "此实例已有不同的认证方式或地址。请新建 ChatGPT 订阅实例，再在其中登录。": "此實例已有不同的認證方式或位址。請新建 ChatGPT 訂閱實例，再在其中登入。",
  "认可": "認可",
  "认可素材库 · 当前 Bot": "認可素材庫 · 當前 Bot",
  "认证方式": "認證方式",
  "讨论稿\n": "討論稿\n",
  "记下": "記下",
  "记下的事": "記下的事",
  "记下：": "記下：",
  "记录": "記錄",
  "记录 · ": "記錄 · ",
  "记录「": "記錄「",
  "记录于 ": "記錄於 ",
  "记录保存了通道标识，但对应插件尚未提供显示规则。": "記錄保存了通道標識，但對應插件尚未提供顯示規則。",
  "记录做法": "記錄做法",
  "记忆": "記憶",
  "记忆 · MEMORY": "記憶 · MEMORY",
  "记忆读取失败：": "記憶讀取失敗：",
  "记忆这一层没接上读法。": "記憶這一層沒接上讀法。",
  "记过": "記過",
  "许可证": "許可證",
  "设为当前": "設爲當前",
  "设置": "設置",
  "高级设置": "進階設定",
  "设置 · SETTINGS": "設置 · SETTINGS",
  "设置 —— 主题 ／ 颜色 ／ 预设": "設置 —— 主題 ／ 顏色 ／ 預設",
  "识别模型": "識別模型",
  "识别语言": "識別語言",
  "诊断": "診斷",
  "诊断包已生成；未自动下载可点击 ": "診斷包已生成；未自動下載可點擊 ",
  "诊断生成超时，请重试。": "診斷生成超時，請重試。",
  "试听失败。": "試聽失敗。",
  "试听已生成；可在卡片内播放。": "試聽已生成；可在卡片內播放。",
  "试听当前音色": "試聽當前音色",
  "试听文本": "試聽文本",
  "详情": "詳情",
  "语速": "語速",
  "语速暂固定为 1.0。检测灯反映已保存设置，修改后请保存。": "語速暫固定爲 1.0。檢測燈反映已保存設置，修改後請保存。",
  "语音发送方式": "語音發送方式",
  "语音模型": "語音模型",
  "说": "說",
  "说一句": "說一句",
  "说：": "說：",
  "请先启动 SnowLuma，再打开管理页。": "請先啓動 SnowLuma，再打開管理頁。",
  "请先安装本机 TTS 环境": "請先安裝本機 TTS 環境",
  "请先选择 TTS 合成模型，再点击启动。": "請先選擇 TTS 合成模型，再點擊啓動。",
  "请刷新后再解绑。": "請刷新後再解綁。",
  "请在本页核对 QQ 主进程": "請在本頁覈對 QQ 主進程",
  "请它交接一次 —— 把这一段收一收，换一段干净的上下文接着走；": "請它交接一次 —— 把這一段收一收，換一段乾淨的上下文接着走；",
  "请将官方发行包解压到此目录，使 index.mjs 位于目录根部。": "請將官方發行包解壓到此目錄，使 index.mjs 位於目錄根部。",
  "请查看接入结果提示。": "請查看接入結果提示。",
  "请核对这个账号与「": "請覈對這個賬號與「",
  "请求": "請求",
  "请输入 1—200 字试听文字。": "請輸入 1—200 字試聽文字。",
  "请选中需要保存的未保存章节；其他草稿会保留。": "請選中需要保存的未保存章節；其他草稿會保留。",
  "请通过下方账号授权登录；不使用 API Key": "請通過下方賬號授權登錄；不使用 API Key",
  "读 /state": "讀 /state",
  "读不到插件那一格。": "讀不到插件那一格。",
  "读不到画像那一格。": "讀不到畫像那一格。",
  "读不到知识库那一格。": "讀不到知識庫那一格。",
  "读不到经历那一格。": "讀不到經歷那一格。",
  "读不到记忆那一格。": "讀不到記憶那一格。",
  "读不到（": "讀不到（",
  "读不到（推的那条通道另说）": "讀不到（推的那條通道另說）",
  "读取": "讀取",
  "读取失败 · 保留上次内容": "讀取失敗 · 保留上次內容",
  "读取失败，旧读数已隐藏": "讀取失敗，舊讀數已隱藏",
  "读取失败，未覆盖。": "讀取失敗，未覆蓋。",
  "读取失败：": "讀取失敗：",
  "读取待议记录": "讀取待議記錄",
  "读取新版供手动合并": "讀取新版供手動合併",
  "读取暂不可用": "讀取暫不可用",
  "读取此 QQ 的 HTTP / WebSocket 地址与令牌，保存到当前 Bot；不改变会话名单。": "讀取此 QQ 的 HTTP / WebSocket 地址與令牌，保存到當前 Bot；不改變會話名單。",
  "读取画像": "讀取畫像",
  "读时正文": "讀時正文",
  "调整侧边栏宽度": "調整側邊欄寬度",
  "调用参数": "調用參數",
  "调用工具": "調用工具",
  "调色盘的名单读不到，退回那六行：": "調色盤的名單讀不到，退回那六行：",
  "账号名单尚未读取": "賬號名單尚未讀取",
  "账号名单暂不可用": "賬號名單暫不可用",
  "账号连接": "賬號連接",
  "跟随最新输出": "跟隨最新輸出",
  "跟随系统": "跟隨系統",
  "身份关联已变化，请手动刷新并重新确认；没有自动重试。": "身份關聯已變化，請手動刷新並重新確認；沒有自動重試。",
  "身份绑定操作失败：": "身份綁定操作失敗：",
  "身份绑定记录暂时读不到，已停止绑定操作；画像仍可查看。": "身份綁定記錄暫時讀不到，已停止綁定操作；畫像仍可查看。",
  "载入图片（也可以拖进来 ／ 粘贴）—— 随这一行一起发；回车是发送，Shift+回车换行": "載入圖片（也可以拖進來 ／ 粘貼）—— 隨這一行一起發；回車是發送，Shift+回車換行",
  "辅助请求": "輔助請求",
  "输入 1—200 字试听文字": "輸入 1—200 字試聽文字",
  "输入 API Key": "輸入 API Key",
  "输入此套餐的 API Key": "輸入此套餐的 API Key",
  "输入用量与缓存命中": "輸入用量與緩存命中",
  "过程": "過程",
  "运行中": "運行中",
  "还差一个模型": "還差一個模型",
  "还是空的": "還是空的",
  "还是空的 —— 定下来的事落到这儿，名字就出现在这儿。": "還是空的 —— 定下來的事落到這兒，名字就出現在這兒。",
  "还是空的 —— 这边还没写下过第一句。": "還是空的 —— 這邊還沒寫下過第一句。",
  "还有 ": "還有 ",
  "还没做": "還沒做",
  "还没放下的": "還沒放下的",
  "还没有主动开过口 —— 她主动发起的私聊一封都没有。": "還沒有主動開過口 —— 她主動發起的私聊一封都沒有。",
  "还没有别人 —— 她记下一个人的时候，名字会出现在这儿。": "還沒有別人 —— 她記下一個人的時候，名字會出現在這兒。",
  "还没有经历记下来 —— 说过的话、写过的一段，都会落到这儿。": "還沒有經歷記下來 —— 說過的話、寫過的一段，都會落到這兒。",
  "还没有装任何插件。": "還沒有裝任何插件。",
  "这一份人不能改": "這一份人不能改",
  "这一台没有接做法读口。": "這一臺沒有接做法讀口。",
  "这一台浏览器画不了图": "這一臺瀏覽器畫不了圖",
  "这一套外观没存下来：": "這一套外觀沒存下來：",
  "这一张太大了（缩到上限还是超过 6 MB）": "這一張太大了（縮到上限還是超過 6 MB）",
  "这一张要发出去": "這一張要發出去",
  "这一张解不开（不是图，或文件坏了）": "這一張解不開（不是圖，或文件壞了）",
  "这一张读不出来": "這一張讀不出來",
  "这一张转不出来": "這一張轉不出來",
  "这一条能收图（上游或公开目录报的）": "這一條能收圖（上游或公開目錄報的）",
  "这一格摆的是这边写给下一轮的自己的话 —— 还没写过的时候，这里是空的。": "這一格擺的是這邊寫給下一輪的自己的話 —— 還沒寫過的時候，這裏是空的。",
  "这一格摆的是这边还没划掉的事 —— 一件都没有的时候，这里是空的。": "這一格擺的是這邊還沒劃掉的事 —— 一件都沒有的時候，這裏是空的。",
  "这一格摆的是这边还没落地的事 —— 一件都没落下的时候，这里是空的。": "這一格擺的是這邊還沒落地的事 —— 一件都沒落下的時候，這裏是空的。",
  "这一格还没接上。": "這一格還沒接上。",
  "这一格还没有东西。": "這一格還沒有東西。",
  "这一版没写成：同一秒里已经存过一版了 —— 等一秒再按一次「保存」，": "這一版沒寫成：同一秒裏已經存過一版了 —— 等一秒再按一次「保存」，",
  "这一页还没有素材。": "這一頁還沒有素材。",
  "这份插件尚未安装。": "這份插件尚未安裝。",
  "这份画像尚未连接稳定身份。相同昵称不会自动归并。": "這份畫像尚未連接穩定身份。相同暱稱不會自動歸併。",
  "这会停止整个协议服务，使用它的其他 Bot 与 QQ 账号也会断开连接；QQ 程序不会关闭。": "這會停止整個協議服務，使用它的其他 Bot 與 QQ 賬號也會斷開連接；QQ 程序不會關閉。",
  "这会向所选进程加载一次 SnowLuma；若实际账号不符，将停止接入，不会自动切换其他 QQ。": "這會向所選進程加載一次 SnowLuma；若實際賬號不符，將停止接入，不會自動切換其他 QQ。",
  "这是对端联系人和群的允许名单，不是登录机器人的 QQ 号。": "這是對端聯繫人和羣的允許名單，不是登錄機器人的 QQ 號。",
  "这条旧记录未保存通道信息，无法可靠确认来自终端还是外部平台。": "這條舊記錄未保存通道信息，無法可靠確認來自終端還是外部平臺。",
  "这次没拉到 GitHub。": "這次沒拉到 GitHub。",
  "连接": "連接",
  "连接中": "連接中",
  "连接中…": "連接中…",
  "连接或恢复此 QQ": "連接或恢復此 QQ",
  "连接或恢复这个 QQ": "連接或恢復這個 QQ",
  "退出 ChatGPT 登录": "退出 ChatGPT 登錄",
  "退出登录": "退出登錄",
  "适用范围": "適用範圍",
  "选中淡底": "選中淡底",
  "选中边": "選中邊",
  "选另一份画像，确认是同一个人的不同账号；从哪一边操作，关联结果相同。": "選另一份畫像，確認是同一個人的不同賬號；從哪一邊操作，關聯結果相同。",
  "选择 QQ 与保存管理登录": "選擇 QQ 與保存管理登錄",
  "选择图片并裁剪头像": "選擇圖片並裁剪頭像",
  "选择或搜索模型": "選擇或搜索模型",
  "选择或输入模型": "選擇或輸入模型",
  "那一套没换上来：": "那一套沒換上來：",
  "那一套读不到：": "那一套讀不到：",
  "那一版已经在盘上了": "那一版已經在盤上了",
  "配置已保留；请从原启动器正常退出后重启。": "配置已保留；請從原啓動器正常退出後重啓。",
  "配置按当前 Bot 独立保存；保存会重载已启用的插件，停用的插件不会自动启动。": "配置按當前 Bot 獨立保存；保存會重載已啓用的插件，停用的插件不會自動啓動。",
  "配置检查已就绪；请检测 OneBot，再从允许名单内的账号发消息验收。": "配置檢查已就緒；請檢測 OneBot，再從允許名單內的賬號發消息驗收。",
  "重启 Core 后可打开本机音色文件夹": "重啓 Core 後可打開本機音色文件夾",
  "重新获取待确认进程": "重新獲取待確認進程",
  "重新评审": "重新評審",
  "重新读取": "重新讀取",
  "重新读取将放弃尚未保存的代理草稿，继续吗？": "重新讀取將放棄尚未保存的代理草稿，繼續嗎？",
  "重新读取已保存的人设": "重新讀取已保存的人設",
  "重新读取配置": "重新讀取配置",
  "重试": "重試",
  "重载": "重載",
  "重载会放弃所有未保存草稿，并读取盘上新版。": "重載會放棄所有未保存草稿，並讀取盤上新版。",
  "错误输出": "錯誤輸出",
  "镜像为第三方服务；两种来源使用相同版本和校验值。失败后可切换重试。": "鏡像爲第三方服務；兩種來源使用相同版本和校驗值。失敗後可切換重試。",
  "阅读历史 · 已暂停跟随": "閱讀歷史 · 已暫停跟隨",
  "阅读历史 · 有新日志": "閱讀歷史 · 有新日誌",
  "阅读知识": "閱讀知識",
  "隐藏已安装": "隱藏已安裝",
  "静候中": "靜候中",
  "静候预览，不含本轮动态信息": "靜候預覽，不含本輪動態信息",
  "静候：暂停新请求": "靜候：暫停新請求",
  "静候：暂停新请求。当前本地模型未响应。": "靜候：暫停新請求。當前本地模型未響應。",
  "静候：暂停新请求；已开始的处理不受影响。": "靜候：暫停新請求；已開始的處理不受影響。",
  "音频已加载，请点击播放。": "音頻已加載，請點擊播放。",
  "页面操作失败，结果未确认；请刷新核对，不要自动重试。": "頁面操作失敗，結果未確認；請刷新覈對，不要自動重試。",
  "页面断开": "頁面斷開",
  "页面没接上": "頁面沒接上",
  "页面背景": "頁面背景",
  "页面请求无效、配置已变化或已有操作进行中；未执行新操作。": "頁面請求無效、配置已變化或已有操作進行中；未執行新操作。",
  "预算未知": "預算未知",
  "预设清单读不到：": "預設清單讀不到：",
  "预设读不到（照旧用表里那些）：": "預設讀不到（照舊用表裏那些）：",
  "颜色": "顏色",
  "颜色方案": "顏色方案",
  "首次可保存一次管理凭据。使用 Windows 当前用户加密，不随项目分发；不关闭鉴权。": "首次可保存一次管理憑據。使用 Windows 當前用戶加密，不隨項目分發；不關閉鑑權。",
  "首次填写；留空保留已保存凭据": "首次填寫；留空保留已保存憑據",
  "首次接入与账号设置": "首次接入與賬號設置",
  "首次接入或在 SnowLuma 改过协议端口、令牌后，点一次“同步协议配置”。": "首次接入或在 SnowLuma 改過協議端口、令牌後，點一次“同步協議配置”。",
  "首次条款确认与登录由你完成。": "首次條款確認與登錄由你完成。",
  "首次登录（管理初始化）：在独立管理页完成初始改密或验证，再在本页保存新的管理密码并验证。": "首次登錄（管理初始化）：在獨立管理頁完成初始改密或驗證，再在本頁保存新的管理密碼並驗證。",
  "首次管理登录的历史完成状态与当前账号在线状态分别检查。": "首次管理登錄的歷史完成狀態與當前賬號在線狀態分別檢查。",
  "高级": "高級",
  "高级外观": "高級外觀",
  "高级：手动账号与解绑": "高級：手動賬號與解綁",
  "鸣潮 UID": "鳴潮 UID",
  "鸣潮插件尚未安装": "鳴潮插件尚未安裝",
  "鸣潮查询": "鳴潮查詢",
  "鸣潮查询 · 配置": "鳴潮查詢 · 配置",
  "默认 3 秒，按首条消息计时；窗口内撤回则取消，不叠加聚合等待。": "默認 3 秒，按首條消息計時；窗口內撤回則取消，不疊加聚合等待。",
  "默认音色 · 通用": "默認音色 · 通用",
  "默认音色使用通用参考声音；私有音色仅在本机安装后可选。": "默認音色使用通用參考聲音；私有音色僅在本機安裝後可選。",
  "（ 这是上次问到的，正在重问）。": "（ 這是上次問到的，正在重問）。",
  "（它选 `responses` —— 那是选择，不是限制）。 只有 `responses` 拿得到「思考过程」。": "（它選 `responses` —— 那是選擇，不是限制）。 只有 `responses` 拿得到「思考過程」。",
  "（实测 7 档全过 ，嵌在 `reasoning.effort` 里发 ）": "（實測 7 檔全過 ，嵌在 `reasoning.effort` 裏發 ）",
  "（已下载 ": "（已下載 ",
  "（有没保存的改动）": "（有沒保存的改動）",
  "（未选择）": "（未選擇）",
  "（本页显示 ": "（本頁顯示 ",
  "（没写契约号）": "（沒寫契約號）",
  "（没写版本）": "（沒寫版本）",
  "（没写）": "（沒寫）",
  "（没有时刻）": "（沒有時刻）",
  "（没有落下的文件）": "（沒有落下的文件）",
  "（没选模型）": "（沒選模型）",
  "（点一下看明细）": "（點一下看明細）",
  "（还没给过说法）": "（還沒給過說法）",
  "（这一份没写简介）": "（這一份沒寫簡介）",
  "（这一份还没有正文）": "（這一份還沒有正文）",
  "，查看参数和回执": "，查看參數和回執",
  "：草稿保留，未自动刷新。": "：草稿保留，未自動刷新。",
  "；本 Bot 已启用语音发送": "；本 Bot 已啓用語音發送",
  "；本 Bot 未启用语音发送": "；本 Bot 未啓用語音發送",
  "；语音设置已保存，后续动作未确认。请刷新检查后再决定；未自动回滚或重试。": "；語音設置已保存，後續動作未確認。請刷新檢查後再決定；未自動回滾或重試。",
  "🗑 确认删除": "🗑 確認刪除",
  "画像加载失败，请重试。": "畫像加載失敗，請重試。",
  "当前服务暂不支持查看画像。": "當前服務暫不支持查看畫像。",
  "暂无画像记录。": "暫無畫像記錄。",
  "暂无人物。": "暫無人物。",
  "搜索姓名或账号": "搜索姓名或賬號",
  "已达记录上限，新增记录会替换最旧记录": "已達記錄上限，新增記錄會替換最舊記錄",
  " 条记录": " 條記錄",
  "展开全部": "展開全部",
  "账号信息不完整，请刷新后再操作。": "賬號信息不完整，請刷新後再操作。",
  "尚未设置账号关联。": "尚未設置賬號關聯。",
  "请填写平台账号。": "請填寫平臺賬號。",
  "账号关联已变化，请刷新后重新确认。": "賬號關聯已變化，請刷新後重新確認。",
  "经历加载失败，请重试。": "經歷加載失敗，請重試。",
  "当前服务暂不支持查看经历。": "當前服務暫不支持查看經歷。",
  "暂无经历。保存的对话和记录会显示在这里。": "暫無經歷。保存的對話和記錄會顯示在這裏。",
  " 条 · ": " 條 · ",
  "日期未知": "日期未知",
  "知识库加载失败，请重试。": "知識庫加載失敗，請重試。",
  "当前服务暂不支持查看知识库。": "當前服務暫不支持查看知識庫。",
  "暂无知识库内容。": "暫無知識庫內容。",
  "暂无文档。": "暫無文檔。",
  "没有匹配结果。": "沒有匹配結果。",
  "暂无正文。": "暫無正文。",
  "搜索文档名称": "搜索文檔名稱",
  "记忆加载失败，请重试。": "記憶加載失敗，請重試。",
  "当前服务暂不支持查看记忆。": "當前服務暫不支持查看記憶。",
  "待讨论": "待討論",
  "未完成事项": "未完成事項",
  "暂无自我记录。": "暫無自我記錄。",
  "暂无记录。": "暫無記錄。",
  "最近的自我记录": "最近的自我記錄",
  "Bot 在整理记忆时更新。": "Bot 在整理記憶時更新。",
  "暂无内容": "暫無內容",
  "暂无主动记录。": "暫無主動記錄。",
  "记忆记录": "記憶記錄",
  "待讨论。可在对话中确认修改、保留或暂缓。": "待討論。可在對話中確認修改、保留或暫緩。",
  "当前服务暂不支持。": "當前服務暫不支持。",
  "技能加载失败，请重试。": "技能加載失敗，請重試。",
  "当前服务暂不支持查看技能。": "當前服務暫不支持查看技能。",
  "无已保存文件": "無已保存文件",
  "此前已保存：": "此前已保存：",
  " · 后续操作已完成": " · 後續操作已完成",
  " · 文件已保存，后续操作未完成": " · 文件已保存，後續操作未完成",
  "操作记录（最近 ": "操作記錄（最近 ",
  "图片读取失败": "圖片讀取失敗",
  "当前浏览器无法处理图片": "當前瀏覽器無法處理圖片",
  "图片转换失败": "圖片轉換失敗",
  "请选择 JPEG、PNG、WebP 或 GIF 图片": "請選擇 JPEG、PNG、WebP 或 GIF 圖片",
  "无法打开图片，文件格式不支持或已损坏": "無法打開圖片，文件格式不支持或已损壞",
  "图片压缩后仍超过 6 MB，请先缩小图片": "圖片壓縮後仍超過 6 MB，請先縮小圖片",
  "输入消息…": "輸入消息…",
  "已发送图片": "已發送圖片",
  "待发送图片": "待發送圖片",
  "移除图片": "移除圖片",
  "查看上下文用量明细": "查看上下文用量明細",
  "整理当前对话摘要，并在新的上下文中继续。": "整理當前對話摘要，並在新的上下文中繼續。",
  "相关记忆会在后台整理。": "相關記憶會在後臺整理。",
  "执行结果": "執行結果",
  "工具参数与结果": "工具參數與結果",
  "等待结果": "等待結果",
  "仅有结果": "僅有結果",
  "未返回结果正文。": "未返回結果正文。",
  "尚未收到结果。": "尚未收到結果。",
  " 条结果": " 條結果",
  "，查看参数和结果": "，查看參數和結果",
  "该模型的输出上限为 ": "該模型的輸出上限爲 ",
  " token，已自动调整": " token，已自動調整",
  "服务商或模型目录声明支持图片输入": "服務商或模型目錄聲明支持圖片輸入",
  "服务商声明仅支持文本输入": "服務商聲明僅支持文本輸入",
  "尚未确认图片能力，可手动设置": "尚未確認圖片能力，可手動設置",
  "根据模型信息自动设置，也可手动调整": "根據模型信息自動設置，也可手動調整",
  "未选择模型": "未選擇模型",
  "删除草稿": "刪除草稿",
  "已填入默认值，请检查后保存。": "已填入默認值，請檢查後保存。",
  " 已保存密钥，输入新值可替换": " 已保存密鑰，輸入新值可替換",
  "请先选择模型，再设为当前": "請先選擇模型，再設爲當前",
  "加载中…": "加載中…",
  "正在加载人设…": "正在加載人設…",
  "此内容为只读": "此內容爲只讀",
  "没有需要保存的修改。可点击“重载”获取最新内容。": "沒有需要保存的修改。可點擊“重載”獲取最新內容。",
  " · 点击查看章节": " · 點擊查看章節",
  "（未保存）": "（未保存）",
  "内容背景": "內容背景",
  "标签文字": "標頁文字",
  "普通标签": "普通標頁",
  "强调标签": "強調標頁",
  "暂无已安装插件。": "暫無已安裝插件。",
  "暂时无法连接 GitHub。": "暫時無法連接 GitHub。",
  "未声明版本": "未聲明版本",
  "暂无简介": "暫無簡介",
  "未声明接口版本": "未聲明接口版本",
  "未声明": "未聲明",
  "（缓存结果，正在刷新）": "（緩存結果，正在刷新）",
  "接口不兼容": "接口不兼容",
  "页面未连接": "頁面未連接"
};

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/ui-language.ts
var bindings = /* @__PURE__ */ new Set();
var owned = /* @__PURE__ */ new WeakMap();
var listeners2 = /* @__PURE__ */ new Set();
var language = "zh-Hans";
var uiLanguage = () => language;
var corePhrase = (uiPhrase) => ({ uiPhrase });
function render(part, locale = language) {
  if (Array.isArray(part)) return part.map((item) => render(item, locale)).join("");
  if (part && typeof part === "object" && "uiPhrase" in part) {
    const source = String(part.uiPhrase);
    return locale === "zh-Hant" ? traditional[source] ?? source : source;
  }
  return String(part ?? "");
}
function read2(node6, key2, attribute) {
  return String(attribute ? node6.getAttribute(key2) ?? "" : node6[key2] ?? "");
}
function write(node6, key2, value, attribute) {
  if (attribute) node6.setAttribute(key2, value);
  else node6[key2] = value;
}
function bind(node6, key2, parts, attribute) {
  let map = owned.get(node6);
  if (!map) {
    map = /* @__PURE__ */ new Map();
    owned.set(node6, map);
  }
  const id = (attribute ? "@" : "") + key2;
  const old = map.get(id);
  if (old) bindings.delete(old);
  const last2 = render(parts);
  const binding = { node: new WeakRef(node6), key: key2, attribute, parts, last: last2 };
  map.set(id, binding);
  bindings.add(binding);
  write(node6, key2, last2, attribute);
  if (bindings.size % 256 === 0) {
    for (const item of bindings) if (!item.node.deref()) bindings.delete(item);
  }
  return last2;
}
var coreSetUIText = (node6, key2, parts) => bind(node6, key2, parts, false);
var coreSetUIAttribute = (node6, key2, parts) => bind(node6, key2, parts, true);
function coreUITextNode(parts) {
  const node6 = document.createTextNode("");
  bind(node6, "textContent", parts, false);
  return node6;
}
var uiLabel = (node6, text2, key2 = "textContent") => bind(node6, key2, corePhrase(text2), false);
function uiSource(node6, key2 = "textContent") {
  const binding = owned.get(node6)?.get(key2);
  return binding && read2(node6, key2, false) === binding.last ? render(binding.parts, "zh-Hans") : read2(node6, key2, false);
}
function setUILanguage(value, persist = true) {
  language = value === "zh-Hant" ? "zh-Hant" : "zh-Hans";
  if (persist) writeDisplayLocal("ui-language", language);
  if (typeof document !== "undefined") document.documentElement.lang = language;
  for (const item of bindings) {
    const node6 = item.node.deref();
    if (!node6 || item.key === "textContent" && node6.nodeType === 1 && Array.from(node6.childNodes).some((child) => child.nodeType !== 3) || read2(node6, item.key, item.attribute) !== item.last) {
      bindings.delete(item);
      continue;
    }
    item.last = render(item.parts);
    write(node6, item.key, item.last, item.attribute);
  }
  for (const listener of listeners2) listener(language);
}
function onUILanguage(listener) {
  listeners2.add(listener);
  return () => listeners2.delete(listener);
}
onDisplayScopeReady(() => setUILanguage(readDisplayLocal("ui-language") === "zh-Hant" ? "zh-Hant" : "zh-Hans", false));

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/text-detail.ts
function showTextDetail(title, text2) {
  const opener = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "avatar-crop-dialog text-detail-dialog";
  dialog.setAttribute("aria-label", title);
  const heading2 = document.createElement("h2");
  heading2.textContent = title;
  const body = document.createElement("div");
  body.className = "text-detail-body";
  body.textContent = text2;
  const actions = document.createElement("div");
  actions.className = "avatar-crop-actions";
  const close2 = document.createElement("button");
  close2.type = "button";
  coreSetUIText(close2, "textContent", corePhrase("关闭"));
  close2.onclick = () => dialog.close();
  actions.append(close2);
  dialog.append(heading2, body, actions);
  document.body.append(dialog);
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (opener?.isConnected) opener.focus();
  }, { once: true });
  dialog.showModal();
  close2.focus();
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/package-updates.ts
var last;
var pending3;
var installing = false;
var preflightError = "";
function paint3(card, state3) {
  if (installing) return;
  const current2 = card.querySelector("[data-version]");
  const notice = card.querySelector("[data-update-message]");
  card.dataset.updateState = state3.status;
  const platform = { win: "Windows", linux: "Linux" }[state3.platform] ?? "";
  const version = (value) => value.replace(/^beta\./, "");
  const target = state3.pendingVersion || (state3.status === "available" ? state3.latest : void 0);
  card.querySelector("h3").textContent = state3.pendingVersion ? "更新已就绪" : state3.status === "available" ? "发现新版本" : state3.status === "current" ? "已是最新版本" : state3.status === "unavailable" ? "检查更新失败" : "版本更新";
  current2.textContent = target && state3.current ? version(state3.current) + " → " + version(target) : state3.current ? "v" + version(state3.current) : "版本未识别";
  card.querySelector("[data-update-platform]").textContent = [platform, state3.current, state3.localPreview ? "本机验收源" : state3.channel === "acceptance" ? "验收更新" : ""].filter(Boolean).join(" · ");
  const links = card.querySelector("[data-update-links]");
  links.querySelectorAll("a").forEach((link) => link.remove());
  const history = card.querySelector("[data-update-history]");
  const receipt = state3.lastInstall;
  history.hidden = !receipt;
  if (receipt) {
    const source = { mirror: "GitHub 镜像", official: "GitHub 官方源", cache: "本地缓存", offline: "离线更新包", preview: "本机验收源" }[receipt.source];
    const route2 = receipt.route === "proxy" ? " · 代理" : receipt.route === "direct" ? " · 直连" : "";
    const previous = receipt.base ? receipt.base.replace(/^beta\./, "") + " → " + receipt.version.replace(/^beta\./, "") + "　" : "";
    history.textContent = previous + "上次安装来源　" + source + route2;
  }
  const install = card.querySelector("[data-install]");
  install.hidden = !(state3.pendingVersion || state3.installable && state3.status === "available");
  install.textContent = "更新并重启";
  if (state3.pendingVersion) {
    notice.textContent = "已下载并校验。点击更新时会先确认最新版，再重启 Core。";
  } else if (state3.status === "available") {
    const tag = state3.platform + (state3.channel === "acceptance" ? "-acceptance-" : "-") + state3.latest;
    const file = `Telyincore_${state3.platform}_${state3.current}_to_${state3.latest}_update.zip`;
    const base = state3.releaseUrl?.split("/releases/tag/")[0];
    const direct = base?.startsWith("https://github.com/") && state3.releaseUrl === base + "/releases/tag/" + tag && state3.downloadUrl === base + "/releases/download/" + tag + "/" + file;
    notice.textContent = state3.required ? "请先更新再唤醒 Bot。安装会重启 Core。" : state3.installable ? "安装会重启 Core，短暂中断会话。" : "此版本需要手动下载安装。";
    if (direct && state3.downloadUrl && !state3.installable) {
      const download = document.createElement("a");
      download.href = state3.downloadUrl;
      download.download = file;
      download.className = "package-download";
      download.textContent = "下载更新包";
      links.insertBefore(download, links.lastElementChild);
    } else if (!state3.installable) {
      notice.textContent = state3.required ? "请先更新再唤醒 Bot。请从发布说明获取更新包。" : "请从发布说明获取适用的更新包。";
    }
    if (state3.releaseUrl?.startsWith("https://github.com/")) {
      const notes = document.createElement("a");
      notes.href = state3.releaseUrl;
      notes.target = "_blank";
      notes.rel = "noopener noreferrer";
      notes.textContent = "查看发布说明";
      links.insertBefore(notes, links.lastElementChild);
    }
  } else if (state3.status === "current") {
    notice.textContent = "当前无需更新。";
  } else if (state3.status === "ahead") {
    notice.textContent = "本机版本高于当前公开版本 " + state3.latest + "。";
  } else {
    notice.textContent = state3.status === "unavailable" ? "暂时无法确认新版本，请稍后重试。" : state3.message || "暂时无法检查更新。";
  }
  if (preflightError) {
    card.querySelector("h3").textContent = "更新已暂停";
    notice.textContent = preflightError;
  }
  const details = card.querySelector("[data-update-details]");
  const detailText = state3.status === "unavailable" ? state3.message : state3.checkRoute;
  details.querySelector("[data-update-connection]").textContent = detailText || "";
  const timing = card.querySelector("[data-update-timing]");
  const time = (stamp) => new Date(stamp * 1e3).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  timing.textContent = [
    state3.checkedAt ? "最近检查 " + time(state3.checkedAt) : "",
    state3.status === "unavailable" ? state3.nextCheckAt ? "服务运行期间将于 " + time(state3.nextCheckAt) + " 左右自动重试，也可立即重试。" : "可立即重试；服务运行期间也会自动检查。" : ""
  ].filter(Boolean).join(" · ");
  timing.hidden = !timing.textContent;
  card.querySelector("[data-check-update]").textContent = state3.status === "unavailable" ? "重新检查" : "检查更新";
  card.querySelector("[data-update-inspect]").disabled = false;
}
function paintTopbar(checking = false) {
  const button4 = document.querySelector(".topbar-update");
  if (!button4) return;
  const available2 = last?.status === "available" && !!last.latest;
  const failed = last?.status === "unavailable";
  const version = last?.current ? "v" + last.current.replace(/^beta\./, "") : "版本";
  button4.classList.toggle("has-update", !checking && available2);
  button4.classList.toggle("check-failed", !checking && failed);
  button4.setAttribute("aria-busy", String(checking));
  button4.querySelector(".topbar-version-label").textContent = checking ? version + " · 检查中" : available2 ? "新版 " + last.latest.replace(/^beta\./, "") : failed ? version + " · 检查失败" : last?.status === "unconfigured" ? version + " · 未配置更新源" : last?.status === "unknown" ? "版本未识别" : version;
  button4.title = checking ? "正在检查更新" : failed ? "暂时无法确认是否有新版本，点击查看原因并重试" : available2 ? "发现新版本 " + last.latest + "，查看版本更新" : last?.status === "current" ? "已确认是最新发布版本，点击查看详情" : "版本更新 · 查看详情";
  button4.setAttribute("aria-label", button4.title);
}
async function check(force = false) {
  if (pending3) {
    if (!force) return pending3;
    await pending3;
    if (pending3) return pending3;
  }
  if (force || !last) paintTopbar(true);
  pending3 = (async () => {
    let state3;
    try {
      const reply = await fetch(
        "/package-update" + (force ? "?refresh=1" : ""),
        { cache: "no-store", signal: AbortSignal.timeout(2e4) }
      );
      if (!reply.ok) throw new Error();
      state3 = await reply.json();
    } catch {
      state3 = {
        platform: last?.platform ?? "",
        current: last?.current ?? null,
        status: "unavailable",
        message: "暂时无法连接 Core 检查更新。"
      };
    }
    last = state3;
    document.querySelectorAll(".package-update-card").forEach((card) => paint3(card, state3));
    paintTopbar();
  })().finally(() => {
    pending3 = void 0;
  });
  return pending3;
}
function openCoreUpdateDialog(opener) {
  const dialog = document.createElement("dialog");
  dialog.className = "avatar-crop-dialog package-update-dialog";
  dialog.setAttribute("aria-label", "版本更新");
  const close2 = document.createElement("button");
  close2.type = "button";
  close2.textContent = "关闭";
  close2.addEventListener("click", () => dialog.close());
  close2.className = "package-update-close";
  close2.textContent = "×";
  close2.setAttribute("aria-label", "关闭");
  close2.title = "关闭";
  dialog.append(close2, packageUpdateCard());
  dialog.addEventListener("close", () => {
    dialog.remove();
    opener.focus();
  }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
}
function startPackageUpdates() {
  const row2 = document.querySelector(".topbar-status");
  if (row2 && !row2.querySelector(".topbar-update")) {
    const button4 = document.createElement("button");
    button4.type = "button";
    button4.className = "topbar-update";
    const mark = document.createElement("span");
    mark.className = "topbar-version-mark";
    mark.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.className = "topbar-version-label";
    label.textContent = "版本";
    const caption = document.createElement("span");
    caption.className = "topbar-update-caption";
    caption.textContent = "检查更新";
    button4.append(caption, mark, label);
    button4.title = "版本更新 · 检查更新";
    button4.setAttribute("aria-haspopup", "dialog");
    button4.setAttribute("aria-label", button4.title);
    button4.addEventListener("click", () => openCoreUpdateDialog(button4));
    row2.append(button4);
  }
  void check();
  window.setInterval(() => {
    if (!document.hidden) void check();
  }, 60 * 1e3);
  window.addEventListener("core-control-refresh", () => {
    if (!document.hidden) void check();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void check();
  });
}
function packageUpdateCard() {
  const card = document.createElement("section");
  card.className = "settings-card package-update-card";
  const title = document.createElement("h3");
  title.textContent = "版本更新";
  const current2 = document.createElement("p");
  current2.dataset.version = "";
  const notice = document.createElement("p");
  notice.dataset.updateMessage = "";
  notice.setAttribute("role", "status");
  const button4 = document.createElement("button");
  button4.type = "button";
  button4.dataset.checkUpdate = "";
  button4.className = "settings-language-button";
  button4.textContent = "检查更新";
  button4.addEventListener("click", async () => {
    preflightError = "";
    offline.hidden = true;
    button4.disabled = true;
    notice.textContent = "正在检查…";
    try {
      await check(true);
    } finally {
      button4.disabled = false;
    }
  });
  const install = document.createElement("button");
  install.type = "button";
  install.className = "settings-language-button";
  install.dataset.install = "";
  install.hidden = true;
  const offline = document.createElement("button");
  offline.type = "button";
  offline.hidden = true;
  async function applyUpdate(cachedVersion) {
    if (installing) return;
    const baseVersion = last?.current;
    const displayedVersion = last?.pendingVersion || last?.latest;
    let targetVersion = cachedVersion || displayedVersion;
    const displayTarget = (actual) => {
      targetVersion = actual;
      if (baseVersion) current2.textContent = baseVersion.replace(/^beta\./, "") + " → " + actual.replace(/^beta\./, "");
    };
    if (!targetVersion) return;
    preflightError = "";
    installing = true;
    install.disabled = true;
    offline.hidden = true;
    notice.textContent = cachedVersion ? "正在核对已下载的更新包…" : "正在确认最新版…";
    let waitingForTransfer = true;
    const showProgress = window.setInterval(async () => {
      try {
        const response = await fetch("/package-update", { cache: "no-store", signal: AbortSignal.timeout(2e3) });
        if (!response.ok) return;
        const state3 = await response.json();
        if (waitingForTransfer && state3.installProgress) {
          if (state3.installTarget) displayTarget(state3.installTarget);
          const newer = state3.installTarget && state3.installTarget !== displayedVersion ? "目标已更新至 " + state3.installTarget.replace(/^beta\./, "") + " · " : "";
          notice.textContent = newer + state3.installProgress;
        }
      } catch {
      }
    }, 2e3);
    try {
      const response = await fetch(cachedVersion ? "/package-update/restart" : "/package-update/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cachedVersion })
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        if (detail.code === "update_preflight_blocked") {
          preflightError = detail.error;
          title.textContent = "更新已暂停";
        }
        if (typeof detail.cachedVersion === "string") {
          offline.textContent = "安装已下载的 " + detail.cachedVersion.replace(/^beta\./, "");
          offline.onclick = () => {
            void applyUpdate(detail.cachedVersion);
          };
          offline.hidden = false;
        }
        throw new Error(detail.error || "更新未完成，请重试");
      }
      waitingForTransfer = false;
      window.clearInterval(showProgress);
      const accepted = await response.json();
      if (typeof accepted.targetVersion === "string") displayTarget(accepted.targetVersion);
      notice.textContent = "正在重启，等待 " + targetVersion.replace(/^beta\./, "") + " 就绪…";
      const deadline = Date.now() + 12e4;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        try {
          const state3 = await fetch("/package-update", { cache: "no-store", signal: AbortSignal.timeout(4e3) });
          if (state3.ok && (await state3.json()).current === targetVersion) {
            window.location.reload();
            return;
          }
        } catch {
        }
      }
      notice.textContent = "尚未收到新版 Core 的就绪回应。请查看启动器状态。";
    } catch (error) {
      notice.textContent = error instanceof Error ? error.message : "更新失败，请重试。";
    } finally {
      waitingForTransfer = false;
      window.clearInterval(showProgress);
      installing = false;
      install.disabled = false;
    }
  }
  install.onclick = () => {
    void applyUpdate();
  };
  const history = document.createElement("p");
  history.dataset.updateHistory = "";
  history.hidden = true;
  const timing = document.createElement("p");
  timing.dataset.updateTiming = "";
  timing.hidden = true;
  const details = document.createElement("section");
  details.dataset.updateDetails = "";
  details.hidden = true;
  const summary2 = document.createElement("h3");
  summary2.textContent = "更新详情";
  const platform = document.createElement("p");
  platform.dataset.updatePlatform = "";
  const connection = document.createElement("p");
  connection.dataset.updateConnection = "";
  details.append(summary2, platform, connection, timing, history);
  details.hidden = true;
  const inspect = document.createElement("button");
  inspect.type = "button";
  inspect.textContent = "查看更新详情";
  inspect.dataset.updateInspect = "";
  inspect.disabled = true;
  inspect.onclick = () => showTextDetail(
    "更新详情",
    [platform, connection, timing, history].filter((e) => !e.hidden).map((e) => e.textContent).filter(Boolean).join("\n\n") || notice.textContent || "版本信息正在读取，请稍后重试。"
  );
  const links = document.createElement("div");
  links.dataset.updateLinks = "";
  links.append(inspect);
  const actions = document.createElement("div");
  actions.className = "package-update-actions";
  actions.append(button4, offline, install);
  card.append(title, current2, notice, links, details, actions);
  if (last) paint3(card, last);
  else notice.textContent = "正在读取版本信息…";
  void check();
  return card;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/quick-search.ts
function installQuickSearch(rail) {
  const nav = rail.querySelector(".rail-nav");
  if (!nav) return;
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "rail-search";
  trigger.append(icon("search"), document.createTextNode("搜索"));
  const key2 = document.createElement("kbd");
  key2.textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ K" : "Ctrl K";
  trigger.append(key2);
  trigger.setAttribute("aria-label", "搜索页面和插件");
  trigger.setAttribute("aria-controls", "rail-search-panel");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-keyshortcuts", "Control+K Meta+K");
  rail.querySelector(".rail-brand")?.after(trigger);
  const panel2 = document.createElement("section");
  panel2.id = "rail-search-panel";
  panel2.className = "rail-search-panel";
  panel2.hidden = true;
  panel2.setAttribute("aria-label", "搜索页面和插件");
  const header = document.createElement("div");
  header.className = "rail-search-field";
  const input2 = document.createElement("input");
  input2.type = "search";
  input2.placeholder = "搜索页面或插件";
  input2.setAttribute("role", "combobox");
  input2.setAttribute("aria-label", "搜索页面和插件");
  input2.setAttribute("aria-expanded", "false");
  input2.setAttribute("aria-controls", "quick-search-results");
  input2.setAttribute("aria-autocomplete", "list");
  const close2 = document.createElement("button");
  close2.type = "button";
  close2.append(icon("close"));
  close2.setAttribute("aria-label", "关闭搜索");
  close2.title = "关闭搜索（Esc）";
  const count4 = document.createElement("p");
  count4.className = "rail-search-count";
  const list = document.createElement("div");
  list.id = "quick-search-results";
  list.className = "rail-search-results";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "搜索结果");
  const note = document.createElement("p");
  note.className = "rail-search-note";
  note.setAttribute("role", "status");
  const footer = document.createElement("p");
  footer.className = "rail-search-help";
  footer.textContent = "↑ ↓ 选择 · Enter 打开 · Esc 返回";
  header.append(icon("search"), input2, close2);
  panel2.append(header, count4, list, note, footer);
  trigger.after(panel2);
  let selected = 0, matches = [], plugins = [], epoch = 0;
  let previous = null;
  const words = {
    live: "聊天 控制台 console chat",
    engine: "模型 API 接口 密钥 model",
    persona: "人设 角色 persona",
    memory: "记忆 memory",
    people: "画像 人物 QQ 账号关联 profile",
    experience: "经历 对话历史 history",
    knowledge: "知识库 knowledge",
    plugin: "插件市场 安装 market",
    infinity: "插件总览 已安装 更新 plugins"
  };
  const catalog = () => {
    const map = /* @__PURE__ */ new Map();
    for (const link of nav.querySelectorAll("a.rail-item[href]")) {
      const href = link.getAttribute("href");
      const id = href.replace(/^#\//, "");
      map.set(href, {
        title: link.querySelector(".rail-label")?.textContent || link.textContent || id,
        kind: id.startsWith("infinity/") ? "插件配置" : "页面",
        words: words[id] || id,
        href
      });
    }
    for (const hit2 of plugins) if (!map.has(hit2.href)) map.set(hit2.href, hit2);
    map.set("#/settings", { title: "高级设置", kind: "页面", words: "外观 主题 配色 存储 网络 代理 settings", href: "#/settings" });
    return [...map.values()];
  };
  const dismiss = (restoreFocus = true) => {
    if (panel2.hidden) return;
    epoch++;
    panel2.hidden = true;
    trigger.hidden = false;
    nav.hidden = false;
    document.body.classList.remove("rail-searching");
    trigger.setAttribute("aria-expanded", "false");
    input2.setAttribute("aria-expanded", "false");
    if (restoreFocus) {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
      else trigger.focus();
    }
  };
  const choose = (hit2) => {
    dismiss();
    location.hash = hit2.href;
    if (document.body.classList.contains("compact-menu-open"))
      document.querySelector(".compact-menu-toggle")?.click();
  };
  const paint4 = () => {
    const query = input2.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    matches = catalog().filter((hit2) => query.every((q) => (hit2.title + " " + hit2.words).toLowerCase().includes(q)));
    selected = Math.min(selected, Math.max(0, matches.length - 1));
    list.replaceChildren();
    count4.textContent = query.length ? `找到 ${matches.length} 项` : "页面与插件";
    matches.forEach((hit2, i) => {
      const row2 = document.createElement("div");
      row2.className = "rail-search-hit";
      row2.id = "quick-search-hit-" + i;
      row2.setAttribute("role", "option");
      row2.setAttribute("aria-selected", String(i === selected));
      const label = document.createElement("span");
      label.textContent = hit2.title;
      const kind = document.createElement("small");
      kind.textContent = hit2.kind;
      row2.append(label, kind);
      row2.onclick = () => choose(hit2);
      list.append(row2);
    });
    if (matches.length) input2.setAttribute("aria-activedescendant", "quick-search-hit-" + selected);
    else {
      input2.removeAttribute("aria-activedescendant");
      const empty = document.createElement("p");
      empty.className = "rail-search-empty";
      empty.textContent = "没有找到页面或插件，试试其他关键词。";
      list.append(empty);
    }
  };
  const open = async () => {
    if (document.querySelector('dialog[open], [role="alertdialog"]')) return;
    if (window.matchMedia("(max-width: 720px)").matches && !document.body.classList.contains("compact-menu-open"))
      document.querySelector(".compact-menu-toggle")?.click();
    if (!panel2.hidden) {
      input2.focus();
      return;
    }
    previous = document.activeElement;
    trigger.hidden = true;
    nav.hidden = true;
    panel2.hidden = false;
    document.body.classList.add("rail-searching");
    trigger.setAttribute("aria-expanded", "true");
    input2.setAttribute("aria-expanded", "true");
    input2.value = "";
    selected = 0;
    plugins = [];
    note.textContent = "正在读取插件…";
    paint4();
    input2.focus();
    const mine = ++epoch;
    try {
      const data = await getStatusJson("/plugins");
      if (mine !== epoch || panel2.hidden) return;
      plugins = (data.plugins || []).filter((p) => p.installed !== false).map((p) => ({
        title: p.displayName || p.name,
        kind: "插件配置",
        words: p.name,
        href: "#/infinity/" + encodeURIComponent(p.name)
      }));
      note.textContent = "";
      paint4();
    } catch {
      if (mine === epoch && !panel2.hidden) note.textContent = "插件暂不可用，仍可搜索页面。";
    }
  };
  trigger.onclick = () => {
    void open();
  };
  close2.onclick = () => dismiss();
  input2.oninput = () => {
    selected = 0;
    paint4();
    list.scrollTop = 0;
  };
  panel2.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      dismiss();
    }
  });
  input2.addEventListener("keydown", (e) => {
    if (e.isComposing || !matches.length) return;
    if (e.key === "Enter") {
      e.preventDefault();
      choose(matches[selected]);
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      selected = (selected + (e.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length;
      paint4();
      document.getElementById("quick-search-hit-" + selected)?.scrollIntoView({ block: "nearest" });
    }
  });
  document.addEventListener("click", (event) => {
    if (panel2.hidden) return;
    const path = event.composedPath();
    if (path.includes(trigger) || path.includes(header) || path.some((node6) => node6 instanceof Element && node6.classList.contains("rail-search-hit"))) return;
    if (event.target === list) {
      const box = list.getBoundingClientRect();
      if (event.clientX >= box.left + list.clientLeft + list.clientWidth) return;
    }
    dismiss(false);
  }, true);
  window.addEventListener("hashchange", () => dismiss(false));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (!e.repeat) void open();
    }
  });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/rail-resize.ts
var WIDTH_KEY = "telyincore.rail-width";
var DEFAULT_WIDTH = 280;
function railWidthBounds(viewport) {
  return { min: 68, max: viewport < 560 ? 68 : Math.min(480, viewport - 360) };
}
function clampRailWidth(width, viewport) {
  const { min, max } = railWidthBounds(viewport);
  const bounded = Math.round(Math.max(min, Math.min(
    max,
    Number.isFinite(width) ? width : DEFAULT_WIDTH
  )));
  return bounded < 200 ? min : bounded;
}
function dragRailWidth(width, viewport) {
  const { min, max } = railWidthBounds(viewport);
  return Math.round(Math.max(min, Math.min(max, width)));
}
function installRailResize(root, rail) {
  const handle = document.createElement("div");
  handle.className = "rail-resizer";
  handle.tabIndex = 0;
  handle.setAttribute("role", "separator");
  coreSetUIAttribute(handle, "aria-label", corePhrase("调整侧边栏宽度"));
  handle.setAttribute("aria-orientation", "vertical");
  handle.setAttribute("aria-controls", rail.id);
  handle.setAttribute("aria-description", "向左拖到底收成图标栏，向右拖开；双击恢复默认；方向键微调");
  let edited = false;
  let preferred = window.innerWidth <= 900 ? 68 : DEFAULT_WIDTH;
  try {
    const stored = readDisplayLocal(WIDTH_KEY);
    if (stored !== null && stored.trim() !== "" && Number.isFinite(Number(stored))) {
      preferred = Number(stored);
    }
  } catch {
  }
  let width = DEFAULT_WIDTH;
  let compact2 = false;
  let initialized = false;
  const animations = /* @__PURE__ */ new Set();
  const apply = (dragging = false) => {
    width = dragging ? dragRailWidth(preferred, window.innerWidth) : clampRailWidth(preferred, window.innerWidth);
    const nextCompact = width < 200;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
    const changing = initialized && compact2 !== nextCompact && !reduced;
    if (changing) {
      for (const animation of animations) animation.cancel();
      animations.clear();
    }
    const movers = changing ? Array.from(rail.querySelectorAll?.(
      ".rail-item > .icon, .rail-search > .icon, .rail-action, .rail-mark"
    ) ?? []) : [];
    const before = movers.map((element3) => element3.getBoundingClientRect());
    if (changing && nextCompact && !dragging) {
      for (const label of rail.querySelectorAll?.(
        ".rail-label, .rail-brandname, .rail-name"
      ) ?? []) {
        const rect = label.getBoundingClientRect();
        if (!rect.width || !rect.height) continue;
        const ghost = document.createElement("span");
        ghost.className = "rail-label-ghost";
        ghost.textContent = label.textContent;
        ghost.setAttribute("aria-hidden", "true");
        const style = getComputedStyle(label);
        Object.assign(ghost.style, {
          left: `${rect.left}px`,
          top: `${rect.top}px`,
          width: `${rect.width}px`,
          height: `${rect.height}px`,
          font: style.font,
          color: style.color
        });
        rail.append(ghost);
        const animation = ghost.animate(
          [{ opacity: 1 }, {
            opacity: 0,
            transform: "translateX(-6px)"
          }],
          { duration: 120, easing: "ease-out" }
        );
        animation.onfinish = () => {
          ghost.remove();
          animations.delete(animation);
        };
        animation.oncancel = () => {
          ghost.remove();
          animations.delete(animation);
        };
        animations.add(animation);
      }
    }
    root.style.setProperty("--rail-w", `${width}px`);
    root.classList.toggle("rail-compact", nextCompact);
    document.documentElement?.removeAttribute("data-core-rail-compact");
    if (changing) {
      movers.forEach((element3, index) => {
        const after = element3.getBoundingClientRect();
        const from = before[index];
        const animation = element3.animate([
          { transform: `translate(${from.left - after.left}px, ${from.top - after.top}px)` },
          { transform: "translate(0, 0)" }
        ], { duration: dragging ? 120 : 260, easing: "cubic-bezier(.22, 1, .36, 1)" });
        animations.add(animation);
        animation.onfinish = () => animations.delete(animation);
        animation.oncancel = () => animations.delete(animation);
      });
      if (!nextCompact && !dragging) {
        for (const label of rail.querySelectorAll?.(
          ".rail-label, .rail-brandname, .rail-name"
        ) ?? []) {
          const animation = label.animate(
            [{ opacity: 0 }, { opacity: 1 }],
            { duration: 200, delay: 60, fill: "backwards", easing: "ease-out" }
          );
          animations.add(animation);
          animation.onfinish = () => animations.delete(animation);
          animation.oncancel = () => animations.delete(animation);
        }
      }
    }
    compact2 = nextCompact;
    initialized = true;
    const { min, max } = railWidthBounds(window.innerWidth);
    handle.setAttribute("aria-valuemin", String(min));
    handle.setAttribute("aria-valuemax", String(max));
    handle.setAttribute("aria-valuenow", String(width));
  };
  const save = () => {
    edited = true;
    try {
      writeDisplayLocal(WIDTH_KEY, String(preferred));
    } catch {
    }
  };
  let drag = null;
  const finish2 = (cancel = false) => {
    if (drag === null) return;
    const id = drag.id;
    if (cancel) preferred = drag.previous;
    drag = null;
    root.classList.remove("rail-resizing");
    if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
    apply();
    if (!cancel) save();
  };
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || drag !== null) return;
    event.preventDefault();
    handle.focus();
    handle.setPointerCapture(event.pointerId);
    drag = { id: event.pointerId, x: event.clientX, width, previous: preferred };
    root.classList.add("rail-resizing");
  });
  handle.addEventListener("pointermove", (event) => {
    if (drag === null || drag.id !== event.pointerId) return;
    preferred = dragRailWidth(drag.width + event.clientX - drag.x, window.innerWidth);
    apply(true);
  });
  handle.addEventListener("pointerup", (event) => {
    if (event.pointerId === drag?.id) finish2();
  });
  handle.addEventListener("pointercancel", (event) => {
    if (event.pointerId === drag?.id) finish2(true);
  });
  handle.addEventListener("lostpointercapture", () => finish2(true));
  window.addEventListener("blur", () => finish2(true));
  window.addEventListener("resize", () => apply());
  handle.addEventListener("dblclick", () => {
    preferred = DEFAULT_WIDTH;
    apply();
    save();
  });
  handle.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drag !== null) {
      event.preventDefault();
      finish2(true);
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || drag !== null) return;
    event.preventDefault();
    const { min, max } = railWidthBounds(window.innerWidth);
    preferred = event.key === "Home" ? min : event.key === "End" ? max : clampRailWidth(
      width === 68 && event.key === "ArrowRight" ? 200 : width + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 40 : 10),
      window.innerWidth
    );
    apply();
    save();
  });
  root.append(handle);
  apply();
  onDisplayScopeReady(() => {
    if (edited || drag !== null) return;
    const stored = readDisplayLocal(WIDTH_KEY);
    if (stored !== null && stored.trim() !== "" && Number.isFinite(Number(stored))) {
      preferred = Number(stored);
      apply();
    }
  });
  window.requestAnimationFrame?.(() => root.classList.add("rail-motion-ready"));
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/brand.ts
var CORE_MARK_T = "M4 5H28L22 11H18V25L12 29V11H4Z";
var CORE_MARK_CORE = "M24 17L29 22L24 27L19 22Z";
function coreBrandMark() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "rail-mark core-brand-icon");
  for (const [shape, opacity] of [[CORE_MARK_T, "1"], [CORE_MARK_CORE, ".48"]]) {
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", shape);
    path.setAttribute("fill", "currentColor");
    path.setAttribute("opacity", opacity);
    svg.append(path);
  }
  return svg;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/confirm-dialog.ts
var nextDialogId = 0;
async function confirmAction(heading2, description, action, notice = false, options = {}) {
  const opener = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "avatar-crop-dialog";
  if (options.className) dialog.classList.add(options.className);
  const title = document.createElement("h2");
  uiLabel(title, heading2);
  title.id = `local-confirm-title-${++nextDialogId}`;
  const detail = document.createElement("p");
  detail.className = "avatar-crop-hint";
  uiLabel(detail, description);
  detail.id = `local-confirm-detail-${nextDialogId}`;
  dialog.setAttribute("aria-labelledby", title.id);
  dialog.setAttribute("aria-describedby", detail.id);
  const actions = document.createElement("div");
  actions.className = "avatar-crop-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  coreSetUIText(cancel, "textContent", corePhrase("取消"));
  cancel.hidden = notice;
  const proceed = document.createElement("button");
  proceed.type = "button";
  proceed.className = "primary";
  uiLabel(proceed, action);
  let accepted = false;
  let finish2 = () => {
  };
  const closed = new Promise((resolve) => {
    finish2 = resolve;
    dialog.addEventListener("close", () => resolve(), { once: true });
  });
  cancel.onclick = () => {
    dialog.close();
    finish2();
  };
  proceed.onclick = () => {
    accepted = true;
    dialog.close();
    finish2();
  };
  actions.append(cancel, proceed);
  dialog.append(title);
  if (options.detail) {
    const subject = document.createElement("p");
    subject.className = "plugin-uninstall-subject";
    subject.textContent = options.detail;
    dialog.append(subject);
  }
  dialog.append(detail, actions);
  document.body.append(dialog);
  try {
    dialog.showModal();
    (notice ? proceed : cancel).focus();
    await closed;
    return accepted;
  } finally {
    dialog.remove();
    if (opener?.isConnected) opener.focus();
  }
}
async function notifyAction(heading2, description) {
  await confirmAction(heading2, description, "知道了", true);
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/asset-url.ts
function assetUrl(name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name)) throw new Error("Invalid asset name");
  return new URL(name, import.meta.url).href;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/look.ts
var THE_LOOK_ROWS = [
  ["--color-ink", "正文"],
  ["--color-dim", "次要文字"],
  ["--color-canvas", "底色"],
  ["--color-line", "分隔线"],
  ["--color-mint", "强调"],
  ["--radius-card", "圆角"]
];
var theLook = {};
function readLookAsset(file) {
  return globalThis.fetch(assetUrl(file), { signal: AbortSignal.timeout(8e3) });
}
var lookListeners = /* @__PURE__ */ new Set();
function onLookChanged(listener) {
  lookListeners.add(listener);
  return () => {
    lookListeners.delete(listener);
  };
}
var lookEpoch = 0;
var resolvedBase = "";
var displayEdited = false;
function writeTheLook(automatic = false, editedName = "") {
  if (!automatic) displayEdited = true;
  if (!automatic && editedName.startsWith("--color-") && theMode() === "auto") {
    writeDisplayLocal(MODE_KEY, window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  lookEpoch++;
  for (const [name, value] of Object.entries(theLook)) {
    document.documentElement.style.setProperty(name, value);
  }
  try {
    writeDisplayLocal("telyincore-look-resolved-v1", JSON.stringify({
      version: 1,
      mode: readDisplayLocal("telyincore-look-mode") || "auto",
      pick: readDisplayLocal("telyincore-look-preset") || "",
      dark: window.matchMedia?.("(prefers-color-scheme: dark)").matches === true,
      base: resolvedBase,
      values: theLook
    }));
    const mode2 = readDisplayLocal("telyincore-look-mode") || "auto";
    document.documentElement.style.colorScheme = mode2 === "dark" || mode2 === "auto" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && theLook["--color-canvas"]) meta.setAttribute("content", theLook["--color-canvas"]);
  } catch {
  }
  for (const listener of lookListeners) listener();
}
var MODE_KEY = "telyincore-look-mode";
var PICK_KEY = "telyincore-look-preset";
function theMode() {
  const raw = readDisplayLocal(MODE_KEY);
  return raw === "light" || raw === "dark" ? raw : "auto";
}
function thePick() {
  return readDisplayLocal(PICK_KEY) ?? "";
}
async function setTheMode(mode2) {
  displayEdited = true;
  const epoch = ++lookEpoch;
  const fine = mode2 === "light" || mode2 === "dark" ? mode2 : "auto";
  writeDisplayLocal(MODE_KEY, fine);
  if (fine === "auto") {
    await loadLook();
    return;
  }
  const file = await theBaseFor(fine);
  if (epoch === lookEpoch) await putOn(file);
}
var watching = false;
function watchTheSystem() {
  if (watching) return;
  watching = true;
  const ask2 = window.matchMedia("(prefers-color-scheme: dark)");
  ask2.addEventListener("change", () => {
    if (theMode() === "auto") void loadLook();
  });
}
async function theBaseFor(mode2) {
  const want = mode2 === "auto" ? window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light" : mode2;
  const all = await thePresets();
  const pick2 = all.find((one) => one.scheme === want);
  return pick2 === void 0 ? all[0]?.file ?? "default.json" : pick2.file;
}
async function loadLook() {
  const epoch = ++lookEpoch;
  const next = { ...theLook };
  try {
    watchTheSystem();
    const mode2 = theMode();
    const want = thePick();
    const base = mode2 === "auto" ? await theBaseFor(mode2) : want !== "" ? want : await theBaseFor(mode2);
    const got = await readLookAsset(base);
    if (!got.ok) return;
    const box = await got.json();
    const baseSnapshot = JSON.stringify(box);
    for (const [name, value] of Object.entries(box)) {
      if (name.startsWith("__")) continue;
      if (typeof value === "string" && value !== "") next[name] = value;
    }
    await saveTail;
    if (epoch !== lookEpoch) return;
    const mine = await pageFetch("/look");
    if (mine.ok) {
      const box2 = await mine.json();
      for (const [name, value] of Object.entries(box2)) {
        if (name.startsWith("__")) continue;
        if (mode2 === "auto" && name.startsWith("--color-")) continue;
        if (typeof value === "string" && value !== "") next[name] = value;
      }
    }
    if (epoch !== lookEpoch) return;
    resolvedBase = baseSnapshot;
    writeDisplayLocal(PICK_KEY, base);
    Object.assign(theLook, next);
    writeTheLook(true);
  } catch (why) {
    console.error("预设读不到（照旧用表里那些）：", why);
  }
}
var saveTail = Promise.resolve();
var saveEpoch = 0;
function keepTheLook() {
  const body = JSON.stringify(theLook);
  const epoch = ++saveEpoch;
  const task = saveTail.then(() => persistLook(body, epoch));
  saveTail = task.catch(() => {
  });
  return task;
}
async function persistLook(body, epoch) {
  try {
    const response = await pageFetch("/look", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (epoch !== saveEpoch) return;
    document.getElementById("look-save-failure")?.remove();
    document.documentElement.dataset.lookSave = "saved";
  } catch (why) {
    console.error("这一套外观没存下来：", why);
    if (epoch !== saveEpoch) return;
    document.documentElement.dataset.lookSave = "failed";
    let warning = document.getElementById("look-save-failure");
    if (!warning) {
      warning = document.createElement("p");
      warning.id = "look-save-failure";
      warning.setAttribute("role", "status");
      document.body.append(warning);
    }
    coreSetUIText(warning, "textContent", corePhrase("外观未保存；当前仅为临时显示，请重新保存。"));
  }
}
async function thePresets() {
  try {
    const got = await readLookAsset("presets.json");
    if (!got.ok) return [];
    const box = await got.json();
    return box.filter((one) => typeof one.file === "string").map((one) => ({
      file: String(one.file),
      name: String(one.name ?? one.file),
      scheme: typeof one.scheme === "string" ? one.scheme : void 0
    }));
  } catch (why) {
    console.error("预设清单读不到：", why);
    return [];
  }
}
async function putOn(file, keepMode = false) {
  const epoch = ++lookEpoch;
  try {
    const got = await readLookAsset(file);
    if (!got.ok) return;
    const box = await got.json();
    if (epoch !== lookEpoch) return;
    resolvedBase = JSON.stringify(box);
    writeDisplayLocal(PICK_KEY, file);
    const scheme = typeof box.__scheme === "string" ? box.__scheme : "";
    if (!keepMode && (scheme === "light" || scheme === "dark")) {
      writeDisplayLocal(MODE_KEY, scheme);
    }
    for (const [name, value] of Object.entries(box)) {
      if (name.startsWith("__")) continue;
      if (typeof value === "string" && value !== "") theLook[name] = value;
    }
    writeTheLook();
    void keepTheLook();
  } catch (why) {
    console.error("那一套没换上来：", why);
  }
}
onDisplayScopeReady((prefix2) => {
  if (displayEdited) return;
  const apply = window.__coreApplyScopedTheme;
  apply?.(prefix2);
  void loadLook();
});

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/avatar-crop.ts
function cropBounds(width, height, zoom, x, y) {
  const side = Math.min(width, height) / Math.max(1, zoom);
  return {
    side,
    sx: Math.max(0, Math.min(width - side, x - side / 2)),
    sy: Math.max(0, Math.min(height - side, y - side / 2))
  };
}
async function cropAvatar(file, save) {
  if (file.size > 20 * 1024 * 1024) throw new Error("图片不能超过 20 MB。");
  const image = await createImageBitmap(file);
  if (image.width * image.height > 4e7) {
    image.close();
    throw new Error("图片尺寸过大，请先缩小图片。");
  }
  const previous = document.activeElement;
  const dialog = document.createElement("dialog");
  coreSetUIAttribute(dialog, "aria-label", corePhrase("裁剪头像"));
  dialog.className = "avatar-crop-dialog";
  const title = document.createElement("h2");
  coreSetUIText(title, "textContent", corePhrase("裁剪头像"));
  const hint = document.createElement("p");
  coreSetUIText(hint, "textContent", corePhrase("拖动调整位置，滚轮或滑杆缩放；方向键也可移动。"));
  hint.className = "avatar-crop-hint";
  const canvas = document.createElement("canvas");
  canvas.width = 300;
  canvas.height = 300;
  canvas.tabIndex = 0;
  coreSetUIAttribute(canvas, "aria-label", corePhrase("头像裁剪区域，使用方向键调整位置"));
  canvas.className = "avatar-crop-canvas";
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    image.close();
    throw new Error("浏览器无法预览图片。");
  }
  let x = image.width / 2;
  let y = image.height / 2;
  let zoom = 1;
  let busy = false;
  let closed = false;
  let upload = null;
  let drag = null;
  const draw = () => {
    const box = cropBounds(image.width, image.height, zoom, x, y);
    x = box.sx + box.side / 2;
    y = box.sy + box.side / 2;
    ctx.clearRect(0, 0, 300, 300);
    ctx.drawImage(image, box.sx, box.sy, box.side, box.side, 0, 0, 300, 300);
  };
  const label = document.createElement("label");
  coreSetUIText(label, "textContent", corePhrase("缩放 "));
  label.className = "avatar-crop-zoom";
  const range = document.createElement("input");
  range.type = "range";
  range.min = "1";
  range.max = "4";
  range.step = "any";
  range.value = "1";
  coreSetUIAttribute(range, "aria-label", corePhrase("头像缩放"));
  label.append(range);
  let targetZoom = zoom;
  let zoomFrame = 0;
  let previousFrame = 0;
  const stopZoom = () => {
    cancelAnimationFrame(zoomFrame);
    zoomFrame = 0;
    previousFrame = 0;
    targetZoom = zoom;
  };
  const animateZoom = (time) => {
    const elapsed = previousFrame ? Math.min(64, time - previousFrame) : 16;
    previousFrame = time;
    zoom += (targetZoom - zoom) * (1 - Math.exp(-elapsed / 75));
    if (Math.abs(targetZoom - zoom) < 1e-4) zoom = targetZoom;
    range.value = String(zoom);
    draw();
    if (zoom !== targetZoom) zoomFrame = requestAnimationFrame(animateZoom);
    else {
      zoomFrame = 0;
      previousFrame = 0;
    }
  };
  range.addEventListener("input", () => {
    stopZoom();
    zoom = Number(range.value);
    targetZoom = zoom;
    draw();
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (busy) return;
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1);
    targetZoom = Math.max(1, Math.min(4, targetZoom * Math.exp(
      -Math.max(-120, Math.min(120, delta)) * 8e-4
    )));
    if (!zoomFrame) zoomFrame = requestAnimationFrame(animateZoom);
  }, { passive: false });
  canvas.addEventListener("pointerdown", (event) => {
    if (busy) return;
    stopZoom();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag || drag.id !== event.pointerId || busy) return;
    const factor = Math.min(image.width, image.height) / zoom / canvas.getBoundingClientRect().width;
    x -= (event.clientX - drag.x) * factor;
    y -= (event.clientY - drag.y) * factor;
    drag.x = event.clientX;
    drag.y = event.clientY;
    draw();
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) {
    canvas.addEventListener(event, () => {
      drag = null;
    });
  }
  canvas.addEventListener("keydown", (event) => {
    if (busy) return;
    const direction = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1]
    }[event.key];
    if (!direction) return;
    event.preventDefault();
    const step = Math.min(image.width, image.height) / zoom / 50;
    x += direction[0] * step;
    y += direction[1] * step;
    draw();
  });
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  const actions = document.createElement("div");
  actions.className = "avatar-crop-actions";
  const button4 = (text2) => {
    const result = document.createElement("button");
    result.type = "button";
    result.textContent = text2;
    actions.append(result);
    return result;
  };
  const reset = button4("重置");
  const cancel = button4("取消");
  const confirm = button4("保存头像");
  confirm.className = "primary";
  const kicker = document.createElement("span");
  kicker.className = "avatar-crop-kicker";
  coreSetUIText(kicker, "textContent", corePhrase("头像设置"));
  reset.addEventListener("click", () => {
    stopZoom();
    targetZoom = 1;
    x = image.width / 2;
    y = image.height / 2;
    zoom = 1;
    range.value = "1";
    draw();
  });
  cancel.addEventListener("click", () => {
    upload?.abort();
    dialog.close();
  });
  dialog.addEventListener("cancel", () => {
    upload?.abort();
  });
  confirm.addEventListener("click", () => {
    if (busy) return;
    stopZoom();
    busy = true;
    range.disabled = reset.disabled = confirm.disabled = true;
    upload = new AbortController();
    const signal = upload.signal;
    coreSetUIText(cancel, "textContent", corePhrase("取消等待"));
    coreSetUIText(status, "textContent", corePhrase("正在保存…取消等待不会撤回服务器已经完成的保存。"));
    void (async () => {
      try {
        const output = document.createElement("canvas");
        output.width = output.height = 256;
        const target = output.getContext("2d");
        if (!target) throw new Error("无法生成裁剪图片。");
        const box = cropBounds(image.width, image.height, zoom, x, y);
        target.drawImage(image, box.sx, box.sy, box.side, box.side, 0, 0, 256, 256);
        const blob = await new Promise((resolve) => output.toBlob(resolve, "image/png"));
        if (closed || signal.aborted) return;
        if (!blob || !await save(blob, signal)) throw new Error("保存失败，请检查服务后重试。");
        if (closed || signal.aborted) return;
        dialog.close();
      } catch (error) {
        if (!closed && !signal.aborted) coreSetUIText(status, "textContent", error instanceof Error ? error.message : corePhrase("保存失败。"));
      } finally {
        if (!closed) {
          busy = false;
          range.disabled = reset.disabled = confirm.disabled = false;
          coreSetUIText(cancel, "textContent", corePhrase("取消"));
        }
      }
    })();
  });
  dialog.append(kicker, title, hint, canvas, label, status, actions);
  document.body.append(dialog);
  try {
    draw();
    dialog.showModal();
    canvas.focus();
    await new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(), { once: true });
    });
  } finally {
    closed = true;
    upload?.abort();
    stopZoom();
    image.close();
    dialog.remove();
    previous?.focus();
  }
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/speaker.ts
var NEUTRAL = "BOT";
var NAME_LINE = /^[ \t]*-[ \t]*\*\*名字\*\*[ \t]*[：:][ \t]*([^。，,.;；!！?？\n（(]+)/m;
function bodies_of(report) {
  const pick2 = (list, get2) => {
    if (!Array.isArray(list)) return [];
    const out = [];
    for (const one of list) {
      if (one === null || typeof one !== "object") continue;
      out.push(get2(one));
    }
    return out;
  };
  const into_rows = (one) => {
    const infos = Array.isArray(one.file_infos) ? one.file_infos : [];
    return infos.map((info) => String(info.body ?? "")).join("\n");
  };
  const source = report.segments;
  const rows = report.rows;
  return [...pick2(source, (one) => String(one.body ?? "")), ...pick2(rows, into_rows)].join("\n");
}
function name_in(report) {
  const hit2 = NAME_LINE.exec(bodies_of(report));
  if (hit2 === null) return NEUTRAL;
  const name = hit2[1].trim();
  return name === "" ? NEUTRAL : name;
}
var current = NEUTRAL;
function speaker() {
  return current;
}
var ready = false;
function speakerReady() {
  return ready;
}
var loading2 = null;
var nameRead = 0;
var activeIdentity = "";
function loadSpeaker(force = false) {
  if (force) loading2 = null;
  if (loading2 === null) {
    const read4 = ++nameRead;
    loading2 = (async () => {
      let next = current;
      let identity = activeIdentity;
      let personaName = null;
      const botsRead = pageFetch("/infinity/bots", { cache: "no-store" });
      void botsRead.catch(() => {
      });
      try {
        const got = await pageFetch("/persona", { cache: "no-store" });
        if (got.ok) {
          const parsed = name_in(await got.json());
          personaName = parsed;
          if (parsed !== NEUTRAL) next = parsed;
        }
      } catch {
      }
      try {
        const bots = await botsRead;
        if (bots.ok) {
          const state3 = await bots.json();
          const active2 = state3.bots?.find((one) => one.id === state3.active);
          if (active2 && state3.active !== activeIdentity) {
            next = personaName && personaName !== NEUTRAL ? personaName : active2.id;
          }
          identity = state3.active;
          if (active2?.displayName) next = active2.displayName;
          else if (next === NEUTRAL && active2?.id) next = active2.id;
        }
      } catch {
      }
      if (read4 === nameRead) {
        current = next;
        activeIdentity = identity;
        ready = true;
      }
      return current;
    })();
  }
  return loading2;
}
var localUser = "用户";
var labelRequest = null;
var labelEpoch = 0;
function userLabel() {
  return localUser;
}
function loadUserLabel(force = false) {
  if (force) {
    labelEpoch += 1;
    localUser = "用户";
    labelRequest = null;
  }
  if (labelRequest) return labelRequest;
  const epoch = labelEpoch;
  labelRequest = pageFetch("/conversation/labels").then(async (response) => {
    if (response.ok) {
      const labels = await response.json();
      if (epoch === labelEpoch) localUser = String(labels.user || "用户");
    }
    return localUser;
  }).catch(() => localUser).finally(() => {
    if (epoch === labelEpoch) labelRequest = null;
  });
  return labelRequest;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/bot-manager.ts
async function botDialog(shownName = speaker()) {
  const opener = document.activeElement;
  const response = await pageFetch("/infinity/bots", { cache: "no-store" });
  if (!response.ok) throw new Error("Bot 信息暂时不可用。");
  const state3 = await response.json();
  const dialog = document.createElement("dialog");
  dialog.className = "avatar-crop-dialog";
  coreSetUIAttribute(dialog, "aria-label", corePhrase("修改显示名"));
  const title = document.createElement("h2");
  coreSetUIText(title, "textContent", corePhrase("修改显示名"));
  const hint = document.createElement("p");
  hint.className = "avatar-crop-hint";
  coreSetUIText(hint, "textContent", corePhrase("只修改当前 Bot 的显示名，不修改文件夹或人设。"));
  const input2 = document.createElement("input");
  input2.maxLength = 80;
  coreSetUIAttribute(input2, "aria-label", corePhrase("Bot 显示名"));
  input2.value = state3.bots.find((one) => one.id === state3.active)?.displayName || shownName;
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  const actions = document.createElement("div");
  actions.className = "avatar-crop-actions";
  const cancel = document.createElement("button");
  coreSetUIText(cancel, "textContent", corePhrase("取消"));
  const save = document.createElement("button");
  coreSetUIText(save, "textContent", corePhrase("保存"));
  save.className = "primary";
  let busy = false;
  cancel.onclick = () => {
    if (!busy) dialog.close();
  };
  dialog.addEventListener("cancel", (event) => {
    if (busy) event.preventDefault();
  });
  save.onclick = () => {
    if (busy) return;
    busy = true;
    save.disabled = cancel.disabled = true;
    void (async () => {
      try {
        const answer = await pageFetch("/infinity/bots/rename", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active: state3.active, displayName: input2.value.trim() })
        });
        if (!answer.ok) {
          const error = await answer.json();
          throw new Error(error.said || "保存失败");
        }
        window.dispatchEvent(new Event("bot-display-changed"));
        dialog.close();
      } catch (error) {
        status.textContent = String(error);
      } finally {
        busy = false;
        save.disabled = cancel.disabled = false;
      }
    })();
  };
  actions.append(cancel, save);
  dialog.append(title, hint, input2, status, actions);
  document.body.append(dialog);
  try {
    dialog.showModal();
    input2.focus();
    await new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(), { once: true });
    });
  } finally {
    dialog.remove();
    opener?.focus();
  }
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/shell.ts
var STROKE = {
  fill: "none",
  stroke: "currentColor",
  "stroke-width": "1.6",
  "stroke-linecap": "round",
  "stroke-linejoin": "round"
};
var ICONS = {
  chat: "M3 4h14v10H8l-4 3v-3H3zM6 8h8M6 11h5",
  eye: "M2 10s3-5.5 8-5.5 8 5.5 8 5.5-3 5.5-8 5.5S2 10 2 10zM12.5 10a2.5 2.5 0 1 0-5 0 2.5 2.5 0 1 0 5 0",
  store: "M3 8v9h14V8M2 7l2-4h12l2 4M2 7v1a2 2 0 0 0 4 0V7m0 0v1a2 2 0 0 0 4 0V7m0 0v1a2 2 0 0 0 4 0V7m0 0v1a2 2 0 0 0 4 0V7M8 17v-5h4v5",
  grid: "M3 3h5v5H3zM12 3h5v5h-5zM3 12h5v5H3zM12 12h5v5h-5z",
  contacts: "M4 3h12v14H4zM2 6h3M2 10h3M2 14h3M12 7a2 2 0 1 0-4 0 2 2 0 1 0 4 0M7 14c.2-4 5.8-4 6 0",
  book: "M10 5v12M10 5C8 3 5 3 2 4v12c3-1 6-1 8 1 2-2 5-2 8-1V4c-3-1-6-1-8 1",
  history: "M3 6a7 7 0 1 1-1 6M3 2v4h4M10 6v4l3 2",
  chip: "M5 5h10v10H5zM8 8h4v4H8zM7 2v3M13 2v3M7 15v3M13 15v3M2 7h3M2 13h3M15 7h3M15 13h3",
  power: "M10 2.5v6.8M5.2 5.2a6.8 6.8 0 1 0 9.6 0",
  wake: "M6.5 3.8 16 10l-9.5 6.2z",
  wait: "M6.8 4v12M13.2 4v12",
  settings: "M8.3 2.5h3.4l.5 2 1.4.8 2-.6 1.7 2.9-1.5 1.4v1.8l1.5 1.4-1.7 2.9-2-.6-1.4.8-.5 2H8.3l-.5-2-1.4-.8-2 .6-1.7-2.9 1.5-1.4V9l-1.5-1.4 1.7-2.9 2 .6 1.4-.8zM12.5 10a2.5 2.5 0 1 0-5 0 2.5 2.5 0 1 0 5 0",
  terminal: "M4 4.5l4.2 4.2-4.2 4.2M10.5 13.5h6.2",
  pulse: "M2.6 10.5h3.6l2.2-6.4 3.2 12 2.1-5.6h4.3",
  chart: "M3 16.4V9.2M8.5 16.4V4.4M14 16.4v-5.1M2 19.2h17",
  plug: "M7.4 3v3.6M12.6 3v3.6M5.2 6.6h9.6v2.2a4.8 4.8 0 0 1-9.6 0zM10 13.6v3.6",
  puzzle: "M4 4.4h4.6v2.2a1.8 1.8 0 1 0 3.6 0V4.4H17v4.4h-2.2a1.8 1.8 0 1 0 0 3.6H17v4.4H4z",
  person: "M10 4.2a2.9 2.9 0 1 0 0 5.8 2.9 2.9 0 0 0 0-5.8zM4.4 16.4c.9-2.6 3-3.9 5.6-3.9s4.7 1.3 5.6 3.9",
  memory: "M6.2 3.2h7.6l2.4 2.4v11.2H6.2zM8.6 7h4.2M8.6 10.4h4.2M8.6 13.8h2.4",
  note: "M5.2 3.2h6.4l3.2 3.2v10.4H5.2zM11.4 3.2v3.4h3.4M7.6 10.2h4.8M7.6 13.2h4.8",
  layers: "M10 3.2l6.4 3.3-6.4 3.3-6.4-3.3zM3.6 10.2l6.4 3.3 6.4-3.3M3.6 13.4l6.4 3.3 6.4-3.3",
  search: "M8.5 3.5a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 12l4.5 4.5",
  close: "M5.5 5.5l9 9M14.5 5.5l-9 9",
  edit: "M11.8 4.2l4 4M3.5 16.5l4.5-1 9-9a1.5 1.5 0 0 0 0-2.1l-1.4-1.4a1.5 1.5 0 0 0-2.1 0l-9 9z"
};
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  for (const [key2, value] of Object.entries(STROKE)) svg.setAttribute(key2, value);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] ?? "");
  svg.append(path);
  return svg;
}
var COUNTER_LABELS = {
  tokens: "tok",
  messages: "条",
  batches: "累计批",
  paused: "状态"
};
function searchBox(placeholder) {
  const form = document.createElement("form");
  form.className = "search-box";
  form.setAttribute("role", "search");
  form.setAttribute("aria-label", placeholder);
  const field = document.createElement("div");
  field.className = "search-field";
  const input2 = document.createElement("input");
  input2.type = "search";
  input2.name = "q";
  input2.className = "find";
  input2.placeholder = placeholder;
  input2.setAttribute("aria-label", placeholder);
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "search-clear";
  clear.hidden = true;
  coreSetUIText(clear, "title", corePhrase("清空筛选"));
  coreSetUIAttribute(clear, "aria-label", corePhrase("清空筛选"));
  clear.append(icon("close"));
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "search-submit";
  coreSetUIText(submit, "textContent", corePhrase("筛选"));
  field.append(icon("search"), input2, clear);
  form.append(field, submit);
  let ask2 = () => {
  };
  let lastAsked = "";
  const go = () => {
    lastAsked = input2.value.trim();
    ask2(lastAsked);
  };
  input2.addEventListener("input", () => {
    clear.hidden = input2.value === "";
  });
  input2.addEventListener("blur", (ev) => {
    if (ev.relatedTarget instanceof Node && form.contains(ev.relatedTarget)) return;
    if (input2.value.trim() !== lastAsked) go();
  });
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    go();
  });
  clear.addEventListener("click", () => {
    input2.value = "";
    clear.hidden = true;
    go();
    input2.focus();
  });
  return { element: form, input: input2, onAsk: (what) => {
    ask2 = what;
  } };
}
var EMPTY_CHIPS = ["tokens", "messages", "batches", "paused"];
function node(tag, cls, text2) {
  const el6 = document.createElement(tag);
  if (cls !== void 0) el6.className = cls;
  if (text2 !== void 0) {
    if (typeof text2 === "string") el6.textContent = text2;
    else coreSetUIText(el6, "textContent", text2);
  }
  return el6;
}
function groupEl(index, label, items, soon = []) {
  const box = node("div", "rail-group");
  box.dataset.index = String(index);
  const groupLabel = node("div", "rail-grouplabel");
  uiLabel(groupLabel, label);
  box.append(groupLabel);
  let sublist = null;
  for (const item of items) {
    const link = document.createElement("a");
    link.className = item.child ? "rail-item rail-child" : "rail-item";
    link.href = `#/${item.id}`;
    link.title = item.label;
    link.setAttribute("aria-label", item.label);
    link.dataset.page = item.id;
    if (item.icon !== void 0) link.append(icon(item.icon));
    const navLabel = node("span", "rail-label", item.label);
    if (!item.id.startsWith("infinity/")) {
      uiLabel(navLabel, item.label);
      uiLabel(link, item.label, "title");
      coreSetUIAttribute(link, "aria-label", corePhrase(item.label));
    }
    link.append(navLabel);
    if (item.dot === true) link.append(node("span", "rail-dot"));
    if (item.signals?.length) {
      const indicators = node("span", "rail-signals");
      const description = item.signals.map((one) => one.label).join("；");
      indicators.setAttribute("aria-label", description);
      link.title = `${item.label} · ${description}`;
      link.setAttribute("aria-label", `${item.label}；${description}`);
      const summary2 = node("span", "rail-signal rail-signal-summary");
      summary2.dataset.state = item.signals.some((one) => one.state === "warn") ? "warn" : item.signals.some((one) => one.state === "on") ? "on" : "off";
      summary2.setAttribute("aria-hidden", "true");
      link.append(summary2);
      for (const signal of item.signals) {
        const dot = node("span", "rail-signal");
        dot.dataset.state = signal.state;
        dot.title = signal.label;
        indicators.append(dot);
      }
      link.append(indicators);
    }
    if (item.child) {
      if (sublist === null) {
        sublist = node("div", "rail-sublist");
        sublist.setAttribute("role", "group");
        coreSetUIAttribute(sublist, "aria-label", [corePhrase(""), label, corePhrase(" 子项")]);
        box.append(sublist);
      }
      sublist.append(link);
    } else {
      sublist = null;
      box.append(link);
    }
  }
  for (const one of soon) {
    const row2 = node("div", "rail-item rail-soon");
    row2.dataset.soon = one.id;
    if (one.icon !== void 0) row2.append(icon(one.icon));
    row2.append(node("span", "rail-label", one.label));
    row2.append(node("span", "rail-soontag", corePhrase("还没做")));
    row2.title = one.why;
    box.append(row2);
  }
  return box;
}
function gateIcon(button4) {
  if (button4.querySelector(".gate-icons")) return;
  const group = node("span", "gate-icons");
  for (const name of ["wake", "wait"]) {
    const layer = node("span", "gate-" + name);
    layer.append(icon(name));
    group.append(layer);
  }
  button4.replaceChildren(group);
}
function gateBusy(button4) {
  const busy = button4.dataset.busy === "true" || button4.dataset.starting === "yes";
  button4.classList.toggle("busy", busy);
  button4.setAttribute("aria-busy", String(busy));
  button4.setAttribute("aria-disabled", String(busy));
}
var gateRevisions = /* @__PURE__ */ new WeakMap();
function paintTheGate(button4, machine) {
  if (typeof machine.stopped_by_hand !== "boolean") return false;
  button4.dataset.stateKnown = "yes";
  const stopped = machine.stopped_by_hand === true;
  const dropped = Number(machine.dropped ?? 0);
  const local = String(machine.profile ?? "") === "Local";
  button4.dataset.stopped = stopped ? "yes" : "no";
  button4.dataset.path = local ? "b" : "a";
  gateIcon(button4);
  coreSetUIAttribute(button4, "aria-label", stopped ? corePhrase("唤醒：恢复处理") : corePhrase("静候：暂停新请求"));
  const note = String(machine.gate_note ?? "");
  if (machine.starting === true) {
    button4.dataset.starting = "yes";
    gateBusy(button4);
    coreSetUIText(button4, "title", corePhrase("正在唤醒：本地服务就绪后恢复处理。"));
    if (button4.dataset.polling !== "yes") {
      button4.dataset.polling = "yes";
      window.setTimeout(() => {
        button4.dataset.polling = "no";
        if (button4.isConnected) void loadTheGate(button4);
      }, 2e3);
    }
    return true;
  }
  button4.dataset.starting = "no";
  button4.dataset.polling = "no";
  gateBusy(button4);
  coreSetUIText(button4, "title", stopped ? [[corePhrase("唤醒：恢复处理。静候期间未发送 "), String(dropped)], corePhrase(" 条。")] : machine.model_available === false ? corePhrase("静候：暂停新请求。当前本地模型未响应。") : corePhrase("静候：暂停新请求；已开始的处理不受影响。"));
  if (note !== "") button4.title = button4.title + " ｜ " + note;
  return true;
}
function syncGateState(root, machine) {
  const button4 = root.querySelector(".rail-run");
  if (!button4 || button4.dataset.busy === "true" || !machine || typeof machine !== "object") return;
  gateRevisions.set(button4, (gateRevisions.get(button4) ?? 0) + 1);
  paintTheGate(button4, machine);
}
async function loadTheGate(button4) {
  const revision = (gateRevisions.get(button4) ?? 0) + 1;
  gateRevisions.set(button4, revision);
  try {
    const got = await pageFetch("/state");
    if (!got.ok) return false;
    const box = await got.json();
    if (gateRevisions.get(button4) !== revision) return button4.dataset.stateKnown === "yes";
    return paintTheGate(button4, box.machine ?? {});
  } catch (why) {
    console.error("控制状态读取失败：", why);
    return false;
  }
}
async function pressTheGate(button4) {
  if (button4.dataset.stateKnown !== "yes" || button4.dataset.busy === "true" || button4.dataset.starting === "yes") return;
  gateRevisions.set(button4, (gateRevisions.get(button4) ?? 0) + 1);
  const stopped = button4.dataset.stopped === "yes";
  const want = stopped ? "/run" : "/pause";
  button4.dataset.busy = "true";
  gateBusy(button4);
  coreSetUIText(button4, "title", stopped ? corePhrase("正在唤醒……") : corePhrase("正在进入静候……"));
  try {
    const got = await pageFetch(want, { method: "POST" });
    if (!got.ok) {
      window.dispatchEvent(new CustomEvent("core-control-feedback", { detail: { text: "操作失败，请重试。" } }));
    } else {
      const confirmed = await loadTheGate(button4);
      const text2 = !confirmed ? "状态待确认，请检查连接" : button4.dataset.starting === "yes" ? "正在唤醒…" : button4.dataset.stopped === "yes" ? "已进入静候" : "已唤醒";
      window.dispatchEvent(new CustomEvent("core-control-feedback", { detail: { text: text2 } }));
      window.dispatchEvent(new Event("core-control-refresh"));
    }
  } catch (why) {
    window.dispatchEvent(new CustomEvent("core-control-feedback", { detail: { text: "操作状态未确认，请检查连接。" } }));
  } finally {
    button4.dataset.busy = "false";
    gateBusy(button4);
  }
}
var RAIL_ACTIONS = [
  { key: "run", glyph: "▶", title: "唤醒 / 静候：恢复处理或暂停新请求" },
  { key: "settings", glyph: "⚙", title: "高级设置" },
  {
    key: "shutdown",
    glyph: "⏻",
    title: "休止：结束当前服务"
  }
];
async function shutItDown(button4) {
  if (button4.dataset.busy === "true") return;
  button4.dataset.busy = "true";
  coreSetUIText(button4, "title", corePhrase("正在休止……"));
  try {
    const got = await pageFetch("/shutdown", { method: "POST" });
    if (!got.ok) {
      button4.dataset.busy = "false";
      coreSetUIText(button4, "title", [[corePhrase("休止未完成（HTTP "), got.status], corePhrase("）")]);
      return;
    }
    button4.classList.add("done");
    coreSetUIText(button4, "title", got.status === 202 ? corePhrase("休止请求已接受，正在等待收尾") : corePhrase("服务已休止"));
    window.close();
  } catch (why) {
    button4.dataset.busy = "false";
    coreSetUIText(button4, "title", [corePhrase("休止未完成："), String(why)]);
    console.error("休止未完成：", why);
  }
}
var avatarUrl = null;
function theAvatarUrl() {
  return avatarUrl;
}
function thePictures() {
  return [...document.querySelectorAll(".rail-avatar, .msg-avatar")];
}
function paintAvatars() {
  for (const one of thePictures()) {
    if (avatarUrl === null) {
      one.classList.remove("has-pic");
      one.style.backgroundImage = "";
    } else {
      one.classList.add("has-pic");
      one.style.backgroundImage = `url(${avatarUrl})`;
    }
  }
}
var avatarRead = 0;
async function loadAvatar() {
  const read4 = ++avatarRead;
  try {
    const answer = await fetch("/avatar", { cache: "no-store" });
    if (read4 !== avatarRead) return;
    if (answer.status === 404) {
      if (avatarUrl !== null) URL.revokeObjectURL(avatarUrl);
      avatarUrl = null;
      paintAvatars();
      return;
    }
    if (!answer.ok) return;
    const blob = await answer.blob();
    if (read4 !== avatarRead) return;
    if (avatarUrl !== null) {
      URL.revokeObjectURL(avatarUrl);
    }
    avatarUrl = URL.createObjectURL(blob);
    paintAvatars();
  } catch {
  } finally {
    if (read4 === avatarRead) {
      for (const face of thePictures()) face.removeAttribute("data-avatar-loading");
    }
  }
}
async function sendAvatar(file) {
  await cropAvatar(file, uploadAvatar);
  return true;
}
async function uploadAvatar(blob, signal) {
  const answer = await fetch("/avatar", {
    method: "POST",
    headers: { "Content-Type": "image/png" },
    body: blob,
    signal
  });
  if (!answer.ok || signal?.aborted) {
    return false;
  }
  await loadAvatar();
  return true;
}
function botIdentity() {
  const who = node("div", "rail-who bot-context");
  const face = node("span", "rail-avatar", speaker().slice(0, 1));
  face.setAttribute("data-avatar-loading", "true");
  coreSetUIText(face, "title", corePhrase("选择图片并裁剪头像"));
  face.tabIndex = 0;
  face.setAttribute("role", "button");
  coreSetUIAttribute(face, "aria-label", corePhrase("选择图片并裁剪头像"));
  face.style.cursor = "pointer";
  const picker = node("input", "rail-pick");
  picker.type = "file";
  picker.accept = "image/png,image/jpeg,image/webp,image/gif";
  picker.hidden = true;
  face.addEventListener("click", () => {
    picker.click();
  });
  face.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      picker.click();
    }
  });
  picker.addEventListener("change", () => {
    const chosen = picker.files === null ? void 0 : picker.files[0];
    if (chosen !== void 0) {
      void sendAvatar(chosen).catch((error) => {
        void notifyAction("头像无法更新", error instanceof Error ? error.message : "图片无法打开，请换一张图片。");
      });
    }
    picker.value = "";
  });
  who.append(face, picker);
  const name = node("button", "rail-name", speaker());
  coreSetUIText(name, "title", corePhrase("双击修改显示名（不更改文件夹名）"));
  const editName = () => {
    void botDialog(name.textContent || speaker()).catch((error) => {
      void notifyAction("显示名无法更新", String(error));
    });
  };
  name.addEventListener("dblclick", editName);
  const copy = node("div", "bot-context-copy");
  const caption = node("span", "bot-context-caption");
  coreSetUIText(caption, "textContent", corePhrase("当前 Bot"));
  copy.append(caption, name);
  who.append(copy);
  void loadAvatar();
  void loadLook();
  return who;
}
function railFoot() {
  const foot = node("div", "rail-foot");
  const secondary = node("div", "rail-secondary");
  for (const { key: key2, title, glyph } of RAIL_ACTIONS) {
    const button4 = node("button", `rail-action rail-${key2}`, glyph);
    button4.title = title;
    button4.setAttribute("aria-label", title);
    if (key2 === "shutdown") button4.replaceChildren(icon("power"));
    if (key2 === "settings") button4.replaceChildren(icon("settings"));
    if (key2 === "run") gateIcon(button4);
    if (key2 === "run") {
      button4.addEventListener("click", () => {
        void pressTheGate(button4);
      });
      button4.setAttribute("data-initial-loading", "true");
      void loadTheGate(button4).finally(() => button4.removeAttribute("data-initial-loading"));
    }
    if (key2 === "settings") {
      button4.addEventListener("click", () => {
        location.hash = "#/settings";
      });
    }
    if (key2 === "shutdown") {
      button4.addEventListener("click", () => {
        void shutItDown(button4);
      });
    }
    if (key2 === "settings") {
      const label = node("span", "rail-action-label");
      uiLabel(label, "高级设置");
      button4.append(label);
      secondary.prepend(button4);
    } else secondary.append(button4);
  }
  foot.prepend(secondary);
  return foot;
}
function topbar(identity) {
  const bar2 = node("header", "topbar");
  const summary2 = node("div", "topbar-summary");
  const right = node("div", "topbar-right");
  const modelLink = node("a", "topbar-model", "—");
  modelLink.href = "#/engine";
  modelLink.setAttribute("aria-label", "打开模型配置");
  right.append(node("span", "topbar-providerlabel", corePhrase("当前模型")), modelLink, providerChip());
  summary2.append(node("div", "topbar-chips"), right);
  const row2 = node("div", "topbar-status");
  row2.append(identity);
  bar2.append(summary2, row2);
  return bar2;
}
function providerChip() {
  return node("span", "topbar-provider", corePhrase("连接中…"));
}
function createShell(root) {
  const rail = node("aside", "rail");
  rail.id = "rail";
  const brand = node("div", "rail-brand");
  const mark = coreBrandMark();
  brand.title = "TelyinCore";
  brand.append(mark, node("span", "rail-brandname", "TelyinCore"));
  const nav = node("nav", "rail-nav");
  const identity = botIdentity();
  const foot = railFoot();
  rail.append(brand, nav, foot);
  const bar2 = topbar(identity);
  bar2.id = "topbar";
  const content = node("main", "content");
  content.id = "content";
  const bottom = node("footer", "bottom");
  bottom.id = "bottom";
  const column = node("div", "console");
  column.append(bar2, content, bottom);
  const menuToggle = node("button", "compact-menu-toggle", corePhrase("菜单"));
  menuToggle.type = "button";
  menuToggle.setAttribute("aria-controls", "rail");
  menuToggle.setAttribute("aria-expanded", "false");
  const closeMenu = () => {
    root.classList.remove("compact-menu-open");
    menuToggle.setAttribute("aria-expanded", "false");
  };
  menuToggle.addEventListener("click", () => {
    const open = root.classList.toggle("compact-menu-open");
    menuToggle.setAttribute("aria-expanded", String(open));
  });
  rail.addEventListener("click", (event) => {
    if (event.target.closest(".rail-item")) closeMenu();
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && root.classList.contains("compact-menu-open")) {
      closeMenu();
      menuToggle.focus();
    }
  });
  root.replaceChildren(rail, column, menuToggle);
  installRailResize(root, rail);
  installQuickSearch(rail);
  const query = (cls) => bar2.querySelector(cls);
  const face = identity.querySelector(".rail-avatar");
  const railName = identity.querySelector(".rail-name");
  const chips = query(".topbar-chips");
  const counters = (pairs) => {
    chips.replaceChildren();
    for (const [key2, value] of pairs) {
      const chip = node("span", "chip");
      chip.dataset.key = key2;
      const label = node("span");
      coreSetUIText(label, "textContent", [corePhrase(COUNTER_LABELS[key2] ?? key2), " "]);
      chip.append(label, node("b", void 0, value));
      chips.append(chip);
    }
  };
  counters(EMPTY_CHIPS.map((key2) => [key2, "—"]));
  const shell2 = {
    root,
    rail,
    railNav: nav,
    railFoot: foot,
    topbar: bar2,
    topbarSummary: query(".topbar-summary"),
    topbarStatus: query(".topbar-status"),
    content,
    bottom,
    bar: {
      chips,
      right: query(".topbar-right"),
      model: query(".topbar-model"),
      provider: query(".topbar-provider")
    },
    counters,
    /** 第 **i** 组的落点（页表那一列里第 i 个组 —— 壳只认**位次**）。
     * **本刀多收一格**：`soon`（"还没做"的那几项）—— **缺省是空**，
     *     于是既有那几处调用一个字都不用改（`place(0, 'CORE', [...])` 照旧）。 */
    place(index, label, items, soon = []) {
      const next = groupEl(index, label, items, soon);
      const previous = nav.querySelector(`.rail-group[data-index="${index}"]`);
      if (previous) previous.replaceWith(next);
      else nav.append(next);
    },
    /** Refresh the current Bot identity from the shared display-name source. */
    setSpeaker(name) {
      face.textContent = name.slice(0, 1);
      railName.textContent = name;
      railName.setAttribute("data-name-loading", String(!speakerReady()));
    }
  };
  return { shell: shell2, topbar: shell2.bar };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/model-display.ts
function modelDisplayName(model) {
  const path = model.trim();
  const isFilePath = /^(?:[a-z]:[\\/]|\/|\\\\)/i.test(path);
  const full = isFilePath ? path.replace(/\\/g, "/").split("/").pop() || path : path;
  if (/^qwen[ -]*3\.8[ _-]*flash[ _-]*next\b/i.test(full)) {
    const quant = full.match(/\bI?Q\d(?:_[A-Z0-9]+)+\b/i)?.[0];
    return `Qwen3.8 FN${quant ? ` · ${quant.toUpperCase()}` : ""}`;
  }
  if (!isFilePath) return model;
  const filename = path.replace(/\\/g, "/").split("/").pop();
  if (!filename) return model;
  return filename.replace(/\.gguf$/i, "");
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/engine-events.ts
var ENGINE_CHANGED = "engine-changed";
function notifyEngineChanged() {
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new Event(ENGINE_CHANGED));
  }
}
function onEngineChanged(refresh2) {
  window.addEventListener(ENGINE_CHANGED, refresh2);
  return () => window.removeEventListener(ENGINE_CHANGED, refresh2);
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/qq-toolbox.ts
var QQ = "core-infinity-qq";
var guide = [
  ["消息与语音", "qq_send_text", "发送文字"],
  ["消息与语音", "qq_send_reply", "引用并回复消息"],
  ["消息与语音", "qq_send_voice", "合成并发送语音"],
  ["消息与语音", "qq_read_group_history", "回查共同群聊记录"],
  ["图片与表情", "qq_send_image", "发送插件生成的图片"],
  ["图片与表情", "qq_look_image", "查看收到的图片"],
  ["图片与表情", "qq_send_face", "发送 QQ 原生表情"],
  ["图片与表情", "qq_list_custom_faces", "列出收藏表情"],
  ["图片与表情", "qq_inspect_custom_face", "识别表情包内容"],
  ["图片与表情", "qq_collect_sticker", "收藏表情包"],
  ["图片与表情", "qq_correct_sticker", "按用户纠正更新表情资料"],
  ["图片与表情", "qq_send_custom_face", "发送收藏表情"],
  ["图片与表情", "qq_send_saved_image", "发回收到的图片或 GIF"],
  ["主动分享", "qq_sharing_preview", "审核拟分享的内容"],
  ["主动分享", "qq_share_to_owner", "向主人分享"]
];
function el2(tag, cls = "", text2 = "") {
  const item = document.createElement(tag);
  item.className = cls;
  item.textContent = text2;
  return item;
}
function qqToolbox() {
  const root = el2("div", "infinity-pane infinity-pane-single");
  const card = el2("section", "infinity-voice-card qq-diagnostic-card qq-tools-card");
  const header = el2("div", "qq-diagnostic-heading");
  const status = el2("span", "qq-diagnostic-badge");
  status.setAttribute("role", "status");
  const reload = el2("button", "btn ghost", "刷新");
  reload.type = "button";
  header.append(el2("h3", "", "QQ 工具集"), status, reload);
  const search = el2("input", "qq-tools-search");
  search.type = "search";
  search.placeholder = "搜索用途或工具名";
  search.setAttribute("aria-label", search.placeholder);
  const list = el2("div", "qq-tools-groups");
  card.append(header, el2("p", "qq-diagnostic-description", "已注册的工具仍受当前会话、账号权限和服务配置限制。下方列出各项说明和参数。"), search, list);
  root.append(card);
  let data;
  let state3 = "正在读取";
  let serial2 = 0;
  function render2() {
    const live2 = data?.tools.filter((tool) => tool.provider === QQ) || [];
    const index = new Map(live2.map((tool) => [tool.name, tool]));
    const labels = data?.guide?.length ? data.guide : guide.map(([group, name, title]) => ({ group, name, title }));
    const rows = labels.map((row2) => ({ ...row2, tool: index.get(row2.name) }));
    for (const tool of live2) {
      if (rows.some((row2) => row2.name === tool.name)) continue;
      rows.push({ group: "其他 QQ 工具", name: tool.name, title: tool.name, tool });
    }
    const query = search.value.trim().toLowerCase();
    const filtered = rows.filter((row2) => (row2.title + " " + row2.name + " " + row2.group).toLowerCase().includes(query));
    list.replaceChildren();
    status.textContent = state3;
    for (const group of new Set(filtered.map((row2) => row2.group))) {
      const section = el2("section", "qq-tools-section");
      section.append(el2("h4", "", group));
      for (const row2 of filtered.filter((row3) => row3.group === group)) {
        const item = el2("section", "qq-tool-item");
        const line = el2("h3");
        const label = el2("span", "qq-tool-label");
        label.append(el2("span", "", row2.title), el2("code", "", row2.name));
        line.append(label, el2("small", row2.tool ? "qq-tool-on" : "", row2.tool ? "已注册" : data ? "未注册" : "待核实"));
        item.append(line);
        const description = row2.tool?.description || "官方 QQ 插件的功能说明。当前注册状态以本 Bot 加载的插件和配置为准。";
        item.append(el2("p", "qq-tool-description", description));
        const more = el2("button", "compact-more", "显示全部");
        more.type = "button";
        more.setAttribute("aria-label", "显示全部：" + row2.title);
        more.onclick = () => showTextDetail(row2.title, [
          row2.name,
          description,
          row2.tool?.parameters.length ? "参数 · " + row2.tool.parameters.map((p) => p + (row2.tool.required.includes(p) ? "（必填）" : "")).join("、") : ""
        ].filter(Boolean).join("\n\n"));
        item.append(more);
        section.append(item);
      }
      list.append(section);
    }
    if (!filtered.length) list.append(el2("p", "qq-diagnostic-note", "没有匹配的工具。"));
  }
  async function refresh2() {
    const id = ++serial2;
    reload.disabled = true;
    try {
      const response = await pageFetch("/infinity/qq/tools", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const result = await response.json();
      if (!result.plugins || !Array.isArray(result.tools)) throw new Error("响应格式无效");
      if (id !== serial2) return;
      data = result;
      const plugin = result.plugins[QQ];
      state3 = !plugin ? "QQ 未安装" : !plugin.enabled ? "QQ 已停用" : !plugin.loaded ? "QQ 尚未加载" : "QQ 已注册 " + result.tools.filter((t) => t.provider === QQ).length + " 项";
    } catch {
      if (id !== serial2) return;
      data = void 0;
      state3 = "注册状态暂不可用";
    } finally {
      if (id === serial2) {
        reload.disabled = false;
        render2();
      }
    }
  }
  search.addEventListener("input", render2);
  reload.addEventListener("click", () => void refresh2());
  render2();
  return { root, refresh: refresh2 };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/qq-stickers.ts
function qqStickers() {
  function element3(tag, text2 = "") {
    const e = document.createElement(tag);
    if (text2) e.textContent = text2;
    return e;
  }
  function button4(text2, click) {
    const b = element3("button", text2);
    b.type = "button";
    b.className = "infinity-action";
    b.addEventListener("click", click);
    return b;
  }
  const root = element3("section");
  root.className = "infinity-pane infinity-pane-single qq-stickers";
  const library = element3("div");
  library.className = "infinity-settings-card qq-sticker-library";
  const heading2 = element3("div");
  heading2.className = "qq-sticker-heading";
  const count4 = element3("span");
  count4.className = "qq-sticker-count";
  heading2.append(element3("h3", "表情包"), count4);
  const notice = element3("p");
  notice.className = "infinity-hint";
  notice.setAttribute("role", "status");
  const toolbar = element3("div");
  toolbar.className = "qq-sticker-toolbar";
  const collection = element3("div");
  collection.className = "qq-sticker-grid";
  const editor = element3("form");
  editor.hidden = true;
  editor.className = "qq-sticker-editor";
  const title = element3("h3");
  const file = element3("input");
  file.type = "file";
  file.accept = "image/gif,image/png,image/jpeg,image/webp";
  const desc = element3("textarea");
  desc.maxLength = 300;
  desc.rows = 2;
  const tags = element3("input");
  tags.placeholder = "用逗号分隔";
  const reason = element3("textarea");
  reason.rows = 2;
  reason.maxLength = 300;
  function field(name, input2) {
    const label = element3("label", name);
    label.className = "infinity-field";
    label.append(input2);
    return label;
  }
  const upload = element3("label");
  upload.className = "qq-sticker-upload";
  const image = element3("img");
  image.hidden = true;
  image.alt = "待添加的表情包";
  const pick2 = element3("strong", "选择图片");
  const meta = element3("small", "也可以把图片拖到这里");
  const choose = element3("span");
  choose.className = "qq-sticker-file-action";
  choose.textContent = "浏览文件";
  const fileInfo = element3("div");
  fileInfo.className = "qq-sticker-file-info";
  fileInfo.append(pick2, meta);
  upload.append(file, image, fileInfo, choose);
  const format = element3("p", "PNG、JPEG、GIF 或 WebP，最大 8 MiB。描述、标签和使用想法由 Bot 生成。");
  format.className = "infinity-hint";
  const uploadArea = element3("div");
  uploadArea.append(upload, format);
  const textFields = element3("div");
  textFields.className = "qq-sticker-edit-fields";
  textFields.append(field("描述", desc), field("标签", tags), field("收藏理由或备注", reason));
  let selectedFile = null, previewURL = "";
  function releaseFile() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = "";
    selectedFile = null;
    file.value = "";
    image.removeAttribute("src");
    image.hidden = true;
    pick2.textContent = "选择图片";
    meta.textContent = "也可以把图片拖到这里";
    choose.textContent = "浏览文件";
  }
  function selectFile(value) {
    if (!value) return;
    releaseFile();
    notice.textContent = "";
    if (!value.size || value.size > 8 * 1024 * 1024 || value.type && !file.accept.split(",").includes(value.type)) {
      notice.textContent = "请选择支持格式的图片，大小须在 8 MiB 以内";
      return;
    }
    selectedFile = value;
    previewURL = URL.createObjectURL(value);
    image.src = previewURL;
    image.hidden = false;
    pick2.textContent = value.name;
    meta.textContent = Math.max(1, Math.round(value.size / 1024)) + " KiB";
    choose.textContent = "更换图片";
  }
  file.addEventListener("change", () => selectFile(file.files?.[0]));
  upload.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (!busy) upload.classList.add("is-over");
  });
  upload.addEventListener("dragleave", () => upload.classList.remove("is-over"));
  upload.addEventListener("drop", (e) => {
    e.preventDefault();
    upload.classList.remove("is-over");
    if (!busy) selectFile(e.dataTransfer?.files[0]);
  });
  let current2, bot = "", account = "", faces = [], busy = false;
  let loaded2 = false;
  function edit(face) {
    current2 = face;
    releaseFile();
    notice.textContent = "";
    uploadArea.hidden = !!face;
    textFields.hidden = !face;
    title.textContent = face ? "编辑表情包" : "添加表情包";
    save.textContent = face ? "保存修改" : "识别并添加";
    desc.value = face?.description || "";
    tags.value = face?.tags.join("，") || "";
    reason.value = face?.reason || "";
    editor.hidden = false;
    (face ? desc : file).focus();
  }
  async function api(body) {
    const r = await fetch("/infinity/qq/stickers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bot, account, ...body }),
      cache: "no-store"
    });
    const value = await r.json();
    if (!r.ok || !value.ok) throw new Error(value.said || "收藏操作失败");
    return value;
  }
  async function run(task) {
    if (busy) return;
    busy = true;
    root.setAttribute("aria-busy", "true");
    const controls = Array.from(root.querySelectorAll("button,input,textarea"));
    controls.forEach((c) => c.disabled = true);
    try {
      await task();
    } catch (e) {
      notice.textContent = e instanceof Error ? e.message : "收藏操作失败";
    } finally {
      busy = false;
      root.removeAttribute("aria-busy");
      controls.forEach((c) => c.disabled = false);
      auto.disabled = savedAuto === void 0;
    }
  }
  const preference = element3("label");
  preference.className = "infinity-toggle qq-sticker-preference";
  const auto = element3("input");
  auto.type = "checkbox";
  auto.disabled = true;
  preference.append(auto, element3("span", "允许 Bot 自主收藏表情包"));
  let savedAuto;
  auto.addEventListener("change", () => {
    const enabled = auto.checked;
    void run(async () => {
      try {
        const updated = await api({ action: "setAutoCollect", enabled });
        savedAuto = updated.autoCollect;
        notice.textContent = enabled ? "已开启自主收藏" : "已关闭自主收藏";
      } finally {
        auto.checked = savedAuto === true;
      }
    });
  });
  const search = element3("input");
  search.type = "search";
  search.placeholder = "搜索描述、标签或收藏理由";
  search.setAttribute("aria-label", search.placeholder);
  function render2() {
    collection.replaceChildren();
    const term = search.value.trim().toLocaleLowerCase();
    for (const face of faces) {
      if (term && ![face.description, ...face.tags, face.reason].join(" ").toLocaleLowerCase().includes(term)) continue;
      const card = element3("article");
      card.className = "qq-sticker-card";
      const preview = element3("div");
      preview.className = "qq-sticker-preview";
      if (face.url) {
        const img = element3("img");
        img.loading = "lazy";
        img.referrerPolicy = "no-referrer";
        img.alt = face.tags.slice(0, 2).join("、") || "表情包";
        img.src = face.url;
        preview.append(img);
      } else {
        preview.textContent = "暂无预览";
      }
      const summary2 = element3("p", face.description || "暂无描述");
      summary2.className = "qq-sticker-summary";
      const chips = element3("div");
      chips.className = "qq-sticker-tags";
      for (const tag of face.tags.slice(0, 3)) chips.append(element3("span", tag));
      if (face.tags.length > 3) chips.append(element3("small", "+" + (face.tags.length - 3)));
      const body = element3("div");
      body.className = "qq-sticker-body";
      body.append(summary2, chips);
      const top = element3("div");
      top.className = "qq-sticker-overview";
      top.append(preview, body);
      const details = element3("button", "查看详情");
      details.className = "qq-sticker-details";
      details.type = "button";
      const fields = element3("dl");
      for (const [label, value] of [
        ["添加来源", face.addedBy || "账号原有·来源未记录"],
        ["完整描述", face.description || "暂无描述"],
        ["标签", face.tags.join(" · ") || "暂无标签"],
        [face.editedReason ? "手动备注" : face.addedBy === "Bot 自主收藏" ? "收藏理由" : "使用想法", face.reason || "尚未记录"]
      ]) fields.append(element3("dt", label), element3("dd", value));
      details.onclick = () => showTextDetail("表情详情", Array.from(fields.children).map((e) => e.textContent || "").join("\n"));
      const actions = element3("div");
      actions.className = "qq-sticker-actions";
      actions.append(button4("编辑", () => edit(face)), button4("删除", () => {
        if (!window.confirm("从当前 QQ 账号删除这张收藏表情？")) return;
        void run(async () => {
          await api({ action: "delete", id: face.id });
          editor.hidden = true;
          await load();
        });
      }));
      const footer = element3("div");
      footer.className = "qq-sticker-footer";
      footer.append(details, actions);
      card.append(top, footer);
      collection.append(card);
    }
    count4.textContent = term ? collection.childElementCount + " / " + faces.length + " 张" : faces.length + " 张";
    if (!collection.childElementCount) collection.append(element3("p", term ? "没有匹配的表情" : "暂无收藏表情"));
  }
  async function load() {
    const pref = await api({ action: "preferences" });
    if (bot && bot !== pref.bot) {
      releaseFile();
      editor.hidden = true;
    }
    bot = pref.bot;
    savedAuto = pref.autoCollect;
    auto.checked = pref.autoCollect;
    const data = await api({ action: "list" });
    if (account && (account !== data.account || bot !== data.bot)) {
      releaseFile();
      editor.hidden = true;
    }
    bot = data.bot;
    account = data.account;
    faces = data.items;
    loaded2 = true;
    notice.textContent = "";
    render2();
  }
  function readFile(f) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("图片读取失败"));
      reader.readAsDataURL(f);
    });
  }
  const save = element3("button", "保存");
  save.className = "infinity-action";
  save.type = "submit";
  editor.addEventListener("submit", (e) => {
    e.preventDefault();
    void run(async () => {
      let duplicate = false;
      if (current2) await api({
        action: "edit",
        id: current2.id,
        description: desc.value.trim(),
        tags: tags.value.split(/[，,、]/).map((v) => v.trim()).filter(Boolean),
        reason: reason.value.trim()
      });
      else {
        const f = selectedFile;
        if (!f || f.size > 8 * 1024 * 1024) throw new Error("请选择 8 MiB 以内的图片");
        notice.textContent = "Bot 正在看图并整理标签…";
        save.textContent = "正在识别…";
        try {
          duplicate = (await api({ action: "add", file: await readFile(f) })).duplicate;
        } finally {
          save.textContent = "识别并添加";
        }
      }
      releaseFile();
      editor.hidden = true;
      await load();
      notice.textContent = duplicate ? "这张图已经收藏过了" : current2 ? "已保存修改" : "已添加表情包";
    });
  });
  const formActions = element3("div");
  formActions.className = "qq-sticker-actions";
  formActions.append(button4("取消", () => {
    releaseFile();
    editor.hidden = true;
    notice.textContent = "";
  }), save);
  save.classList.add("qq-primary-action");
  editor.append(title, uploadArea, textFields, formActions);
  search.addEventListener("input", render2);
  const add = button4("添加表情包", () => {
    if (loaded2) edit();
    else void run(async () => {
      await load();
      edit();
    });
  });
  add.classList.add("qq-primary-action");
  toolbar.append(search, button4("刷新", () => {
    void run(load);
  }), add);
  heading2.append(preference);
  library.append(heading2, toolbar, notice, editor, collection);
  root.append(library);
  return { root, open: () => {
    if (editor.hidden) void run(load);
  } };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/qq-voice-setup.ts
function voiceSetup(kind, selection) {
  const section = document.createElement("details");
  section.className = "qq-voice-setup compact-disclosure";
  section.setAttribute("aria-label", kind === "tts" ? "TTS 首次配置" : "ASR 首次配置");
  const summary2 = document.createElement("summary");
  summary2.textContent = "首次配置";
  const checklist = document.createElement("ol");
  const steps = kind === "tts" ? [
    "点击「下载推荐模型」自动安装模型、运行环境和默认参考音频；已有模型时点击「补全运行环境」。",
    "安装时按硬件准备推理依赖，预热参考音频并验证性能配置。",
    "合成自检通过后，选择模型和音色，保存语音设置即可启用。"
  ] : [
    "点击「下载推荐模型」自动安装模型、词表、VAD 和识别依赖；已有模型时点击「补全运行环境」。",
    "安装完成后自动使用随包语音进行转写自检。",
    "也可点击「运行自检」重新检查，或选择自己的 WAV（15 秒以内）。"
  ];
  for (const step of steps) {
    const item = document.createElement("li");
    coreSetUIText(item, "textContent", corePhrase(step));
    checklist.append(item);
  }
  if (kind === "tts") {
    const guide2 = document.createElement("a");
    guide2.href = "https://github.com/FunAudioLLM/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/README.md#install";
    guide2.target = "_blank";
    guide2.rel = "noopener noreferrer";
    guide2.textContent = "CosyVoice 安装说明 ↗";
    guide2.className = "qq-voice-install-guide";
    checklist.children[1].append(guide2);
  }
  const state3 = document.createElement("p");
  state3.className = "infinity-hint";
  const result = document.createElement("p");
  result.className = "infinity-hint";
  result.setAttribute("role", "status");
  const file = document.createElement("input");
  file.type = "file";
  file.accept = ".wav,audio/wav";
  file.setAttribute("aria-label", "自选 WAV（可选，默认使用随包语音）");
  const check2 = document.createElement("button");
  check2.type = "button";
  check2.className = "infinity-action";
  check2.textContent = "运行自检";
  const actions = document.createElement("div");
  actions.className = "qq-voice-setup-actions";
  let bundled;
  const bundledFile = () => {
    if (!bundled) {
      bundled = fetch(assetUrl("asr-self-check.wav")).then(async (response) => {
        if (!response.ok) throw new Error("无法读取随包自检音频，请检查 Core 文件或选择自己的 WAV。");
        return new File([await response.blob()], "asr-self-check.wav", { type: "audio/wav" });
      }).catch((error) => {
        bundled = void 0;
        throw error;
      });
    }
    return bundled;
  };
  if (kind === "asr") {
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "infinity-action";
    restore.textContent = "恢复默认音频";
    restore.hidden = true;
    const selectDefault = async () => {
      try {
        const sample = await bundledFile();
        if (file.files?.length) return;
        const transfer = new DataTransfer();
        transfer.items.add(sample);
        file.files = transfer.files;
        restore.hidden = true;
      } catch {
        result.textContent = "未能预选随包音频，可重试或选择自己的 WAV。";
      }
    };
    file.addEventListener("change", () => {
      restore.hidden = !file.files?.length;
      if (restore.hidden) void selectDefault();
    });
    restore.addEventListener("click", () => {
      file.value = "";
      void selectDefault();
    });
    actions.append(file, restore);
  }
  actions.append(check2);
  section.append(summary2, checklist, state3, actions, result);
  let passed = "";
  let pending5 = false;
  let blocked = false;
  const key2 = () => JSON.stringify(selection());
  check2.addEventListener("click", async () => {
    if (pending5 || blocked || section.closest("fieldset")?.disabled) return;
    const selected = selection();
    const chosen = file.files?.[0];
    if (!selected.model || kind === "asr" && chosen && chosen.size > 15e5) {
      result.textContent = !selected.model ? "请先选择模型。" : "请选择不超过 1.5 MB 的短 WAV。";
      return;
    }
    pending5 = check2.disabled = true;
    passed = "";
    result.textContent = kind === "tts" ? "正在加载模型并合成测试语音…" : "正在检测人声并转写…";
    try {
      const sample = chosen ?? (kind === "asr" ? await bundledFile() : void 0);
      const audio = sample ? await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
        reader.onerror = reject;
        reader.readAsDataURL(sample);
      }) : void 0;
      const response = await fetch("/infinity/qq/voice-setup-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, ...selected, audio })
      });
      const answer = await response.json();
      if (!response.ok || !answer.ok) throw new Error(answer.said || "语音自检失败。");
      passed = JSON.stringify(selected);
      result.textContent = passed !== key2() ? "原选择已通过，请对当前选择重新自检。" : answer.transcript ? "转写通过 · " + answer.transcript : "合成自检通过。";
    } catch (error) {
      result.textContent = error instanceof Error ? error.message : "语音自检失败。";
    } finally {
      pending5 = false;
      check2.disabled = blocked;
    }
  });
  return {
    section,
    ready: () => passed === key2(),
    require: () => {
      section.open = true;
      section.scrollIntoView({ block: "nearest" });
      result.textContent = "首次启用前请完成自检。";
      check2.focus();
    },
    status(issue, unavailable = false) {
      blocked = unavailable;
      check2.disabled = pending5 || blocked;
      state3.hidden = !issue;
      state3.textContent = issue;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-display-cache.ts
var scope = null;
onDisplayScopeReady((prefix2) => {
  scope = prefix2 + "plugin-page-display.v1:";
});
var validName = (name) => /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(name);
var guards = /* @__PURE__ */ new Map();
function protectPluginDisplay(name, unsafe) {
  guards.set(name, unsafe);
  return () => {
    if (guards.get(name) === unsafe) guards.delete(name);
  };
}
function pluginDisplayProtected(name) {
  try {
    return guards.get(name)?.() ?? false;
  } catch {
    return true;
  }
}
var readyPluginDisplay = () => initializeDisplayScope();
function key(name) {
  return scope && validName(name) ? scope + name : null;
}
var displayCacheCapability = { version: 1, slots: ["view-tab", "list-page", "details-open"] };
function validValue(slot, value) {
  if (slot === "details-open") return typeof value === "boolean";
  const max = slot === "host-tab" ? 9 : slot === "view-tab" ? 31 : slot === "list-page" ? 1e5 : -1;
  return Number.isInteger(value) && Number(value) >= 0 && Number(value) <= max;
}
function readPluginDisplay(name, slot) {
  const k = key(name);
  if (!k) return null;
  try {
    const raw = sessionStorage.getItem(k + ":" + slot);
    if (raw === null) return null;
    const value = JSON.parse(raw);
    return validValue(slot, value) ? value : null;
  } catch {
    return null;
  }
}
function writePluginDisplay(name, slot, value) {
  const k = key(name);
  if (!k || !validValue(slot, value)) return false;
  try {
    sessionStorage.setItem(k + ":" + slot, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
var readPluginTab = (name) => readPluginDisplay(name, "host-tab");
var writePluginTab = (name, tab) => {
  writePluginDisplay(name, "host-tab", tab);
};
var slots = ["host-tab", "view-tab", "list-page", "details-open"];
function pluginDisplayEntries() {
  const rows = /* @__PURE__ */ new Map();
  if (!scope) return [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i);
    if (!k?.startsWith(scope)) continue;
    const parts = k.slice(scope.length).split(":");
    const [name, slot] = parts;
    if (parts.length !== 2 || !validName(name) || !slots.includes(slot) || readPluginDisplay(name, slot) === null) continue;
    const value = sessionStorage.getItem(k);
    const row2 = rows.get(name) ?? { bytes: 0, records: 0, valueBytes: 0 };
    row2.bytes += new TextEncoder().encode(k + value).length;
    row2.valueBytes += new TextEncoder().encode(value).length;
    row2.records++;
    rows.set(name, row2);
  }
  return Array.from(rows, ([name, entry]) => ({ name, ...entry }));
}
function clearPluginDisplay(name) {
  const k = key(name);
  if (!k || pluginDisplayProtected(name)) return false;
  try {
    const targets = slots.filter((slot) => readPluginDisplay(name, slot) !== null).map((slot) => k + ":" + slot);
    if (!targets.length) return false;
    for (const target of targets) sessionStorage.removeItem(target);
    return targets.every((target) => sessionStorage.getItem(target) === null);
  } catch {
    return false;
  }
}
function pluginDisplayBridge(options) {
  return (event) => {
    if (event.source !== options.frame.contentWindow || !options.active()) return;
    const request2 = event.data;
    if (!request2 || request2.type !== "telyincore:display:request" || typeof request2.id !== "string" || !/^[0-9]{1,12}$/.test(request2.id)) return;
    const frame = options.frame.contentWindow;
    const epoch = options.epoch();
    void (async () => {
      const ready2 = await readyPluginDisplay();
      if (!options.active() || epoch !== options.epoch() || frame !== options.frame.contentWindow) return;
      const valid = ready2 && displayCacheCapability.slots.includes(request2.slot);
      let ok = false;
      let value = null;
      if (valid && request2.action === "read") {
        value = readPluginDisplay(options.name, request2.slot);
        ok = true;
      } else if (valid && request2.action === "write") {
        ok = writePluginDisplay(options.name, request2.slot, request2.value);
        if (ok) value = readPluginDisplay(options.name, request2.slot);
      }
      frame?.postMessage({ type: "telyincore:display:result", id: request2.id, ok, value }, "*");
    })();
  };
}
function fuzzyPluginMatch(name, id, query) {
  const normalize = (s) => s.normalize("NFKC").toLowerCase().replace(/[\s._-]+/g, "");
  const aliases = {
    "core-infinity-qq": "QQ 接入 jieru xiaoxi",
    "core-infinity-wavesuid": "WavesUID waves",
    "jingjing-town": "jingjing town 晶晶 小镇 xiaozhen"
  };
  const words = [name, id, ...(name + " " + id + " " + (aliases[id] ?? "")).split(/\s+/)].map(normalize);
  return query.slice(0, 128).trim().split(/\s+/).filter(Boolean).every((token) => {
    const q = normalize(token);
    return words.some((c) => {
      if (c.includes(q)) return true;
      if (q.length >= 2 && c.length <= q.length * 5) {
        let n = 0;
        for (const char of c) if (char === q[n]) n++;
        if (n === q.length) return true;
      }
      if (q.length < 4 || Math.abs(c.length - q.length) > 2) return false;
      let row2 = Array.from({ length: c.length + 1 }, (_, i) => i);
      for (let i = 0; i < q.length; i++) {
        const next = [i + 1];
        for (let j = 0; j < c.length; j++) next[j + 1] = Math.min(next[j] + 1, row2[j + 1] + 1, row2[j] + (q[i] === c[j] ? 0 : 1));
        row2 = next;
      }
      return row2[c.length] <= Math.min(2, Math.floor(q.length / 4));
    });
  });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/builtin/infinity/core-infinity-qq/web/environment-editor.js
function environmentEditor({ read: read4, write: write2, botName }) {
  function element3(tag, className, text2 = "") {
    const node6 = document.createElement(tag);
    node6.className = className;
    node6.textContent = text2;
    return node6;
  }
  const root = element3("section", "qq-scene-card infinity-voice-card");
  const body = element3("div", "qq-scene-body");
  const heading2 = element3("header", "qq-scene-title");
  const name = element3("span", "qq-scene-bot");
  const title = element3("h3", "", "QQ 环境提示词");
  heading2.append(title, name);
  const editor = element3("textarea", "qq-scene-field");
  editor.setAttribute("aria-label", "QQ 环境提示词");
  editor.rows = 10;
  const footer = element3("div", "qq-scene-footer");
  const status = element3("span", "qq-scene-status");
  status.setAttribute("role", "status");
  const actions = element3("div", "qq-scene-actions");
  const undo = element3("button", "btn ghost", "撤销修改");
  const retry = element3("button", "btn ghost", "重新读取");
  const save = element3("button", "btn qq-scene-save", "保存修改");
  for (const button4 of [undo, retry, save]) button4.type = "button";
  actions.append(undo, retry, save);
  footer.append(status, actions);
  body.append(heading2, editor, footer);
  const details = element3("details", "qq-scene-declaration");
  const summary2 = element3("summary", "", "查看 QQ 通道声明");
  const documentText = element3("p", "qq-scene-declaration-text", "正在读取通道声明…");
  const note = element3("small", "qq-scene-declaration-note");
  details.append(summary2, documentText, note);
  root.append(body, details);
  let saved = "", revision = "", busy = false, needsRead = false, lastDeclaration = "";
  function render2(message = "") {
    const dirty = editor.value !== saved;
    editor.disabled = busy || !revision;
    editor.hidden = !revision;
    undo.hidden = save.hidden = !dirty;
    undo.disabled = save.disabled = busy;
    retry.hidden = !needsRead;
    retry.disabled = busy;
    status.textContent = message || (dirty ? "有未保存的修改" : "");
    status.dataset.dirty = String(dirty && !message);
    footer.hidden = !dirty && !needsRead && !status.textContent;
    const label = botName();
    name.textContent = label || "";
    name.hidden = !label;
  }
  async function load() {
    if (busy) return;
    busy = true;
    render2("正在读取提示词…");
    let message = "";
    try {
      const info = await read4();
      if (typeof info?.text !== "string" || !info.revision) throw new Error("提示词或版本信息缺失");
      revision = info.revision;
      editor.value = saved = info.text;
      needsRead = false;
    } catch (error) {
      needsRead = true;
      message = "读取失败：" + error.message;
    } finally {
      busy = false;
      render2(message);
    }
  }
  async function persist() {
    if (busy || !revision || editor.value === saved) return;
    const text2 = editor.value;
    busy = true;
    render2("正在保存…");
    let message = "";
    try {
      const info = await write2(text2, revision);
      if (!info?.revision) throw new Error("未收到保存回执，请重新读取核对");
      revision = info.revision;
      saved = text2;
      needsRead = false;
      message = "已保存，下次 QQ 会话生效。";
    } catch (error) {
      message = error.message + "。草稿已保留。";
      if (error.status === 409) needsRead = true;
    } finally {
      busy = false;
      render2(message);
    }
  }
  editor.addEventListener("input", () => render2());
  editor.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      void persist();
    }
  });
  undo.onclick = () => {
    editor.value = saved;
    render2();
    editor.focus();
  };
  retry.onclick = () => {
    if (editor.value === saved || window.confirm("重新读取会放弃当前草稿，继续吗？")) void load();
  };
  save.onclick = () => void persist();
  render2();
  return {
    root,
    async open() {
      if (!revision) await load();
    },
    update(declaration) {
      const available2 = declaration?.status === "available";
      lastDeclaration = available2 ? declaration.text : "";
      documentText.textContent = lastDeclaration || "当前没有可显示的 QQ 通道声明。";
      note.textContent = "";
      name.textContent = botName() || "";
      name.hidden = !name.textContent;
    },
    unavailable() {
      if (!lastDeclaration) documentText.textContent = "暂时无法读取 QQ 通道声明。";
      note.textContent = lastDeclaration ? "状态更新失败，显示上次读取的声明。" : "";
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/qq-diagnostics.ts
function element(tag, cls, text2) {
  const item = document.createElement(tag);
  item.className = cls;
  if (text2 !== void 0) item.textContent = text2;
  return item;
}
function splitSnowLog(content) {
  const clean = content.slice(-131072).replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "").replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").replace(/\r\n?/g, "\n").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
  let stream = "stdout";
  const lines = [];
  for (const text2 of clean.split("\n")) {
    if (text2.trim() === "── stderr · 错误输出 ──") {
      stream = "stderr";
      continue;
    }
    lines.push({ text: text2, stream });
  }
  return ["stdout", "stderr"].flatMap((kind) => {
    const rows = lines.filter((row2) => row2.stream === kind);
    while (rows.length && !rows[0].text.trim()) rows.shift();
    while (rows.length && !rows[rows.length - 1].text.trim()) rows.pop();
    return rows.slice(-160);
  });
}
function snowLogLevel(line) {
  if (/\b(error|fatal|failed|unauthorized)\b/i.test(line) || /异常|失败|错误/.test(line)) return "error";
  if (/\b(warn|warning|retry)\b/i.test(line) || /重试|警告/.test(line)) return "warn";
  if (/\b(ready|connected|online|listening)\b/i.test(line) || /成功|在线/.test(line)) return "ok";
  return "normal";
}
function heading(title, badge) {
  const head = element("div", "qq-diagnostic-heading");
  head.append(element("h3", "", title), badge);
  return head;
}
function createQQTerminal(refresh2) {
  const root = element("section", "infinity-voice-card qq-diagnostic-card qq-terminal-card");
  const badge = element("span", "qq-diagnostic-badge", "只读日志");
  root.append(
    heading("SnowLuma · 运行日志", badge),
    element("p", "qq-diagnostic-description", "查看 SnowLuma 最近的运行日志。")
  );
  const toolbar = element("div", "qq-diagnostic-toolbar");
  const sourceLabel = element("label", "qq-log-source");
  const source = element("select", "");
  coreSetUIAttribute(source, "aria-label", corePhrase("日志来源"));
  source.append(
    new Option("标准输出", "stdout"),
    new Option("错误输出", "stderr"),
    new Option("全部输出 · 分流显示", "all")
  );
  source.value = "stdout";
  sourceLabel.append(element("span", "", "日志来源"), source);
  const controls = element("div", "qq-diagnostic-actions");
  const liveLabel = element("label", "infinity-log-follow");
  const live2 = element("input", "");
  live2.type = "checkbox";
  live2.checked = true;
  liveLabel.append(live2, element("span", "", "实时更新"));
  const latestButton = element("button", "infinity-action", "回到最新");
  latestButton.type = "button";
  const refreshButton = element("button", "infinity-action", "刷新日志");
  refreshButton.type = "button";
  controls.append(liveLabel, latestButton, refreshButton);
  toolbar.append(sourceLabel, controls);
  const log = element("div", "infinity-log qq-log-viewport");
  log.setAttribute("role", "log");
  coreSetUIAttribute(log, "aria-label", corePhrase("SnowLuma 运行日志"));
  log.setAttribute("aria-live", "off");
  log.setAttribute("tabindex", "0");
  const footer = element("div", "qq-diagnostic-footer");
  const status = element("span", "qq-log-status", "等待读取日志");
  status.setAttribute("role", "status");
  const count4 = element("span", "qq-log-count");
  footer.append(status, count4);
  root.append(
    toolbar,
    log,
    footer,
    element("p", "qq-diagnostic-note", "向上滚动可暂停跟随。日志可能包含上一次运行的记录。")
  );
  let snapshot2;
  let shown = "";
  let renderedKey;
  let visible = false;
  let following = true;
  let failed = false;
  let busy = false;
  const sync = () => {
    coreSetUIText(status, "textContent", busy ? corePhrase("正在读取日志…") : failed ? corePhrase("读取失败 · 保留上次内容") : !live2.checked ? corePhrase("已暂停显示 · 可手动刷新") : !following ? snapshot2 !== shown ? corePhrase("阅读历史 · 有新日志") : corePhrase("阅读历史 · 已暂停跟随") : snapshot2 === void 0 ? corePhrase("等待读取日志") : corePhrase("跟随最新输出"));
    coreSetUIText(badge, "textContent", failed ? corePhrase("读取暂不可用") : !live2.checked || !following ? corePhrase("已暂停跟随") : corePhrase("只读日志"));
    badge.dataset.state = failed ? "warn" : "neutral";
    latestButton.disabled = snapshot2 === void 0 || following && live2.checked && snapshot2 === shown;
    refreshButton.disabled = busy;
  };
  const render2 = (scrollToLatest = false) => {
    if (snapshot2 === void 0) {
      sync();
      return;
    }
    const key2 = source.value + "\n" + snapshot2;
    const top = log.scrollTop;
    if (key2 !== renderedKey) {
      renderedKey = key2;
      shown = snapshot2;
      const lines = splitSnowLog(snapshot2).filter((row2) => source.value === "all" || row2.stream === source.value);
      log.replaceChildren();
      if (!lines.length) {
        log.append(element("p", "qq-log-empty", source.value === "stderr" ? "最近片段中没有错误流输出。" : "暂无日志；服务启动并产生输出后会显示在这里。"));
      }
      lines.forEach((line, index) => {
        const row2 = element("div", "qq-log-row");
        row2.dataset.level = snowLogLevel(line.text);
        row2.append(
          element("span", "qq-log-number", String(index + 1)),
          element("span", "qq-log-stream", line.stream === "stderr" ? "ERR" : "OUT"),
          element("span", "qq-log-text", line.text || " ")
        );
        log.append(row2);
      });
      coreSetUIText(count4, "textContent", [corePhrase(""), lines.length, corePhrase(" 行 · "), source.value === "all" ? "标准流 → 错误流，非时间排序" : source.value === "stderr" ? "stderr" : "stdout", corePhrase("")]);
    }
    if (scrollToLatest || following) log.scrollTop = log.scrollHeight;
    else log.scrollTop = top;
    sync();
  };
  log.addEventListener("scroll", () => {
    if (!visible || log.clientHeight <= 0) return;
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 32;
    if (nearBottom === following) return;
    following = nearBottom;
    if (following && live2.checked) render2(true);
    else sync();
  });
  live2.addEventListener("change", () => {
    if (live2.checked) {
      following = true;
      render2(true);
    } else sync();
  });
  latestButton.addEventListener("click", () => {
    following = true;
    live2.checked = true;
    render2(true);
  });
  source.addEventListener("change", () => {
    following = true;
    render2(true);
  });
  refreshButton.addEventListener("click", () => {
    if (busy || refreshButton.disabled) return;
    busy = true;
    sync();
    refresh2();
  });
  return {
    root,
    activate() {
      visible = true;
      if (live2.checked && following) render2(true);
      else sync();
    },
    deactivate() {
      visible = false;
    },
    update(content, manual2 = false) {
      snapshot2 = content;
      failed = false;
      busy = false;
      if (visible && (manual2 || live2.checked && following)) render2();
      else sync();
    },
    unavailable() {
      failed = true;
      busy = false;
      sync();
    }
  };
}
function createQQEnvironment() {
  return environmentEditor({
    botName: () => speaker() === NEUTRAL ? "" : speaker(),
    async read() {
      const response = await fetch("/persona/qq-environment", { cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 404 ? "当前 Bot 没有可编辑的 QQ 场景" : "HTTP " + response.status);
      return response.json();
    },
    async write(text2, revision) {
      const response = await fetch("/persona", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "环境/QQ.md",
          text: text2,
          expect: revision,
          stamp: (/* @__PURE__ */ new Date()).toISOString().slice(0, 19).replace(/[-:]/g, "").replace("T", "-")
        })
      });
      if (!response.ok) throw Object.assign(new Error(response.status === 409 ? "版本冲突或格式校验失败" : "保存失败：HTTP " + response.status), { status: response.status });
      return response.json();
    }
  });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/voice-corpus-panel.ts
function voiceCorpusPanel() {
  const root = document.createElement("section");
  root.className = "voice-corpus";
  const title = document.createElement("h3");
  coreSetUIText(title, "textContent", corePhrase("认可素材库 · 当前 Bot"));
  const hint = document.createElement("p");
  hint.className = "infinity-hint";
  coreSetUIText(hint, "textContent", corePhrase("开启后，收藏当前 Bot 经 QQ 确认发送的合成语音，供你试听和评审。试听样本和收到的录音不会入库。"));
  const enableLabel = document.createElement("label");
  const enabled = document.createElement("input");
  enabled.type = "checkbox";
  enabled.disabled = true;
  enableLabel.append(enabled, coreUITextNode(corePhrase(" 收集合成语音")));
  const toolbar = document.createElement("div");
  toolbar.className = "voice-corpus-toolbar";
  const filter = document.createElement("select");
  coreSetUIAttribute(filter, "aria-label", corePhrase("素材评审状态"));
  for (const [value, label] of [["pending", "待评审"], ["approved", "已认可"], ["rejected", "已排除"]]) {
    filter.append(new Option(label, value));
  }
  const button4 = (text2) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "infinity-action";
    b.textContent = text2;
    return b;
  };
  const refresh2 = button4("刷新");
  const previous = button4("上一页");
  const next = button4("下一页");
  const notice = document.createElement("p");
  notice.className = "infinity-hint";
  notice.setAttribute("role", "status");
  const counts = document.createElement("p");
  counts.className = "infinity-hint";
  const list = document.createElement("div");
  list.className = "voice-corpus-list";
  toolbar.append(filter, refresh2, previous, next);
  root.append(title, hint, enableLabel, toolbar, counts, notice, list);
  let busy = false, offset = 0, knownEnabled = false, ready2 = false;
  let active2 = true, epoch = 0;
  const requests = /* @__PURE__ */ new Set();
  const api = async (body) => {
    if (!active2) throw new Error("素材页已离开");
    const generation = epoch;
    const controller = new AbortController();
    requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), 1e4);
    try {
      const response = await fetch("/infinity/qq/voice-corpus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal
      });
      const result = await response.json();
      if (!active2 || generation !== epoch) throw new Error("素材页响应已过期");
      if (!response.ok || result.ok !== true) throw new Error(result.said || result.error || "素材库不可用；更新后需重启 Core。");
      return result;
    } finally {
      clearTimeout(timeout);
      requests.delete(controller);
    }
  };
  const lock = (on) => {
    busy = on;
    for (const item of root.querySelectorAll("button,select,input")) {
      item.disabled = on;
    }
  };
  let hasNext = false;
  const show = async () => {
    const state3 = filter.value;
    let data = await api({ action: "list", state: state3, offset });
    if (!data.samples.length && offset > 0) {
      offset = Math.max(0, Math.floor((data.counts[state3] - 1) / 20) * 20);
      data = await api({ action: "list", state: state3, offset });
    }
    ready2 = true;
    knownEnabled = data.enabled;
    enabled.checked = knownEnabled;
    coreSetUIText(counts, "textContent", [
      corePhrase("待评审 "),
      data.counts.pending,
      corePhrase(" · 已认可 "),
      data.counts.approved,
      corePhrase(" · 已排除 "),
      data.counts.rejected,
      " · ",
      (data.bytes / 1048576).toFixed(1),
      " / ",
      (data.maxBytes / 1048576).toFixed(0),
      " MiB",
      data.full ? corePhrase(" · 库已满，暂停收集") : ""
    ]);
    for (const audio of list.querySelectorAll("audio")) {
      audio.pause();
      audio.removeAttribute("src");
    }
    list.replaceChildren();
    hasNext = offset + data.samples.length < data.counts[state3];
    if (!data.samples.length) {
      const empty = document.createElement("p");
      empty.className = "infinity-hint";
      coreSetUIText(empty, "textContent", corePhrase("当前筛选下没有素材。"));
      list.append(empty);
    }
    for (const sample of data.samples) {
      const card = document.createElement("article");
      card.className = "voice-corpus-item";
      const text2 = document.createElement("p");
      text2.textContent = sample.text;
      const meta = document.createElement("small");
      meta.textContent = `${sample.voice} · ${new Date(sample.created * 1e3).toLocaleString()}`;
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.hidden = true;
      const actions = document.createElement("div");
      actions.className = "voice-corpus-toolbar";
      const play = button4("加载试听");
      play.addEventListener("click", () => {
        void run(async () => {
          const data2 = await api({ action: "audio", id: sample.id });
          audio.src = `data:audio/wav;base64,${data2.audio}`;
          audio.hidden = false;
          coreSetUIText(notice, "textContent", corePhrase("音频已加载，请点击播放。"));
        });
      });
      actions.append(play);
      for (const [state4, label] of [["approved", "认可"], ["rejected", "排除"], ["pending", "重新评审"]]) {
        if (state4 === sample.state) continue;
        const review = button4(label);
        review.addEventListener("click", () => {
          void run(async () => {
            await api({ action: "review", id: sample.id, state: state4 });
            await show();
          });
        });
        actions.append(review);
      }
      card.append(text2, meta, audio, actions);
      list.append(card);
    }
  };
  const run = async (work) => {
    if (busy || !active2) return;
    const generation = epoch;
    lock(true);
    notice.textContent = "";
    try {
      await work();
    } catch (error) {
      if (!active2 || generation !== epoch) return;
      enabled.checked = knownEnabled;
      coreSetUIText(notice, "textContent", error instanceof Error ? error.message : corePhrase("素材库操作失败"));
    } finally {
      if (active2 && generation === epoch) {
        lock(false);
        enabled.disabled = !ready2;
        previous.disabled = offset === 0;
        next.disabled = !hasNext;
      }
    }
  };
  enabled.addEventListener("change", () => {
    const desired = enabled.checked;
    void run(async () => {
      const result = await api({ action: "configure", enabled: desired });
      knownEnabled = result.enabled;
      await show();
    });
  });
  filter.addEventListener("change", () => {
    offset = 0;
    void run(show);
  });
  refresh2.addEventListener("click", () => {
    void run(show);
  });
  previous.addEventListener("click", () => {
    offset = Math.max(0, offset - 20);
    void run(show);
  });
  next.addEventListener("click", () => {
    offset += 20;
    void run(show);
  });
  return Object.assign(root, {
    start() {
      active2 = true;
      void run(show);
    },
    stop() {
      active2 = false;
      epoch += 1;
      busy = false;
      for (const controller of requests) controller.abort();
      for (const audio of list.querySelectorAll("audio")) {
        audio.pause();
        audio.removeAttribute("src");
      }
    }
  });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/config-presentation.ts
function configPresentation(root, state3) {
  root.setAttribute("data-config-state", state3);
  root.setAttribute("aria-busy", String(state3 === "loading"));
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/qq-signals.ts
function serviceSignal(name, configured, running) {
  return {
    state: !configured ? "warn" : running ? "on" : "off",
    label: `${name} · ${!configured ? "未配置" : running ? "已配置 · 运行中" : "已配置 · 未运行"}`
  };
}
function freshSnowSample(snow) {
  const r = snow?.recovery;
  return snow?.healthSchema === "snow-health-v2" && r?.healthSchema === "snow-health-v2" && r.managementAuthenticated === true && r.healthStatus === "online" && r.accountOnline === true && !!r.targetUin && r.targetUin === r.healthTarget && typeof r.revision === "string" && r.revision.length > 0 && typeof r.healthSampleAgeSeconds === "number" && Number.isFinite(r.healthSampleAgeSeconds) && r.healthSampleAgeSeconds >= 0 && r.healthSampleAgeSeconds < 30;
}
function freshProtocolSample(snow) {
  const p = snow?.protocolHealth;
  return p?.schema === "onebot-readonly-v1" && p.fresh === true && typeof p.sampleAgeSeconds === "number" && Number.isFinite(p.sampleAgeSeconds) && p.sampleAgeSeconds >= 0 && p.sampleAgeSeconds < 30 && !!p.targetUin && p.targetUin === snow?.recovery?.targetUin && p.status === "online" && p.identityVerified === true && p.online === true && p.good === true;
}
function snowSignal(snow) {
  if (snow && (snow.localAccountOnline !== void 0 || snow.healthSchema === "snow-health-v2" && typeof snow.webuiRunning === "boolean")) {
    if (snow.localAccountOnline || freshProtocolSample(snow) || freshSnowSample(snow)) return { state: "on", label: "SnowLuma · QQ 在线" };
    if (snow.webuiRunning) return { state: "warn", label: "SnowLuma · 管理服务已启动，QQ 在线状态待确认" };
    return { state: "off", label: "SnowLuma · 未启动" };
  }
  const protocol = snow?.protocolHealth;
  const running = protocol?.schema === "onebot-readonly-v1" ? protocol.pluginConnected === true : freshSnowSample(snow) && snow?.websocketVerified === true;
  return serviceSignal("SnowLuma", snow?.configured === true, running);
}
function qqSignals(value, loaded2 = false) {
  const marker = serviceSignal;
  return [
    marker(
      "QQ 插件",
      value?.plugin?.configured ?? value?.wired === true,
      value?.plugin?.running ?? loaded2
    ),
    snowSignal(value?.snowluma),
    marker(
      "TTS",
      !!value?.tts?.model,
      value?.tts?.enabled !== false && value?.tts?.running === true
    ),
    marker(
      "ASR",
      !!value?.asr?.model,
      value?.asr?.enabled !== false && value?.asr?.running === true
    )
  ];
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/download-dialog.ts
async function chooseASRSource(kind = "ASR", preferredSource) {
  const opener = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className = "avatar-crop-dialog";
  const title = document.createElement("h2");
  coreSetUIText(title, "textContent", [corePhrase("下载推荐 "), kind, corePhrase(" 模型")]);
  const text2 = document.createElement("p");
  text2.className = "avatar-crop-hint";
  coreSetUIText(text2, "textContent", kind === "ASR" ? corePhrase("SenseVoiceSmall INT8，约 241 MB，包含识别模型、词表、VAD 和运行依赖，完成后自动转写自检。") : kind === "视觉" ? corePhrase("Qwen3.5-0.8B Q4_K_M + F16 视觉投影，约 703 MB。校验大小和 SHA256 后安装；不含 llama.cpp 运行时。") : corePhrase("Fun-CosyVoice3-0.5B-2512，约 5.43 GB。自动准备运行环境、默认参考音频和性能配置，并执行合成自检。首次环境安装还需下载数 GB 依赖。"));
  const label = document.createElement("label");
  coreSetUIText(label, "textContent", corePhrase("下载源 "));
  const select2 = document.createElement("select");
  coreSetUIAttribute(select2, "aria-label", corePhrase("下载源"));
  select2.append(
    new Option("国内镜像 · HF-Mirror（直连）", "mirror"),
    new Option("官方源 · Hugging Face（按下载代理设置）", "official")
  );
  select2.value = preferredSource === "official" ? "official" : "mirror";
  label.append(select2);
  const notice = document.createElement("p");
  notice.className = "avatar-crop-hint";
  coreSetUIText(notice, "textContent", corePhrase("国内镜像始终直连。官方源遵循设置中的下载代理。TTS 和 ASR 暂停后可换源续传，两种来源校验同一份模型。"));
  const actions = document.createElement("div");
  actions.className = "avatar-crop-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  coreSetUIText(cancel, "textContent", corePhrase("取消"));
  const start = document.createElement("button");
  start.type = "button";
  start.className = "primary";
  coreSetUIText(start, "textContent", corePhrase("开始下载"));
  actions.append(cancel, start);
  dialog.append(title, text2, label, notice, actions);
  coreSetUIAttribute(dialog, "aria-label", [corePhrase("下载推荐 "), kind, corePhrase(" 模型")]);
  let result = null;
  cancel.addEventListener("click", () => dialog.close());
  start.addEventListener("click", () => {
    result = select2.value;
    dialog.close();
  });
  document.body.append(dialog);
  try {
    dialog.showModal();
    select2.focus();
    await new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(), { once: true });
    });
    return result;
  } finally {
    dialog.remove();
    opener?.focus();
  }
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/infinity-controls.ts
function infinityField(label, input2) {
  const box = document.createElement("label");
  box.className = "infinity-field";
  const name = document.createElement("span");
  name.textContent = label;
  box.append(name, input2);
  return box;
}
function infinityToggle(label, input2) {
  const box = document.createElement("label");
  box.className = "infinity-toggle";
  const name = document.createElement("span");
  name.textContent = label;
  box.append(input2, name);
  return box;
}
function infinityModelPicker(label, input2, button4) {
  const row2 = document.createElement("div");
  row2.className = "infinity-model-picker";
  row2.append(infinityField(label, input2), button4);
  return row2;
}
function infinityVoiceHeading(title, download) {
  const row2 = document.createElement("div");
  row2.className = "infinity-voice-heading";
  const heading2 = document.createElement("h3");
  heading2.textContent = title;
  row2.append(heading2, download);
  return row2;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/infinity-page.ts
function node2(tag, className, text2) {
  const element3 = document.createElement(tag);
  element3.className = className;
  if (text2 !== void 0) element3.textContent = text2;
  return element3;
}
function stateOf(plugin) {
  if (plugin.installed === false) return "未安装";
  if (plugin.contract) return "接口不兼容";
  if (plugin.loaded) return "运行中";
  if (plugin.enabled) return "已启用 · 未加载";
  return "已停用";
}
function infinityPage(shell2, pluginName = "") {
  const knownConfig = pluginName === "core-infinity-qq";
  const root = node2("section", "page-infinity page-world");
  if (pluginName === "core-infinity-qq") root.classList.add("page-qq-settings");
  const head = knownConfig ? node2("header", "core-page-head") : node2("div", "head");
  const heading2 = node2(
    "h1",
    knownConfig ? "plugins-title" : "",
    knownConfig ? "QQ 连接 · 配置" : pluginName ? "插件配置" : "插件总览 · INFINITY"
  );
  head.append(heading2);
  if (knownConfig) {
    const intro = node2("p", "qq-page-intro");
    coreSetUIText(
      intro,
      "textContent",
      corePhrase("通过 OneBot v11 协议端收发 QQ 私聊、群聊文字、表情和语音。")
    );
    head.append(intro);
  }
  const liveNotice = knownConfig ? node2("p", "plugins-note") : node2("span", "infinity-hint", "正在读取状态…");
  liveNotice.setAttribute("role", "status");
  head.append(liveNotice);
  const summary2 = node2("div", "infinity-summary");
  const cards = node2("div", "infinity-cards");
  const message = node2("p", "infinity-message");
  const qqPanel = node2("section", "infinity-qq-panel");
  const panelScroll = node2("div", "page-scroll qq-panel-scroll");
  const health = node2("div", "infinity-health");
  const tabs = node2("div", "infinity-tabs");
  tabs.setAttribute("role", "tablist");
  const voiceGrid = node2("div", "infinity-voice-grid");
  const environmentPane = node2("div", "infinity-pane infinity-environment-pane");
  const environmentView = createQQEnvironment();
  const toolbox = qqToolbox();
  environmentPane.append(environmentView.root);
  const routePane = node2("div", "infinity-pane");
  const voicePane = node2("div", "infinity-pane");
  const snowPane = node2("div", "infinity-pane");
  const terminalPane = node2("div", "infinity-pane infinity-terminal-pane");
  const routingCard = node2("div", "infinity-voice-card infinity-routing-card");
  const asrCard = node2("div", "infinity-voice-card");
  const ttsCard = node2("div", "infinity-voice-card");
  const ttsDownload = node2(
    "button",
    "infinity-text-link asr-download-link",
    "下载推荐模型 ↓"
  );
  ttsDownload.type = "button";
  const ttsDownloadState = node2("p", "infinity-hint");
  const ttsProgress = document.createElement("progress");
  ttsProgress.className = "infinity-download-progress";
  ttsProgress.max = 1;
  coreSetUIAttribute(ttsProgress, "aria-label", corePhrase("TTS 模型下载进度"));
  ttsProgress.hidden = true;
  const snowCard = node2("div", "infinity-voice-card infinity-snow-card");
  const asrSwitch = document.createElement("input");
  asrSwitch.type = "checkbox";
  const asrModel = document.createElement("select");
  const asrDownload = node2(
    "button",
    "infinity-text-link asr-download-link",
    "下载推荐模型 ↓"
  );
  asrDownload.type = "button";
  const asrDownloadState = node2("p", "infinity-hint");
  const asrState = node2("p", "infinity-service-state");
  const asrProgress = document.createElement("progress");
  asrProgress.className = "infinity-download-progress";
  asrProgress.max = 1;
  coreSetUIAttribute(asrProgress, "aria-label", corePhrase("ASR 模型下载进度"));
  asrProgress.hidden = true;
  const asrLanguage = document.createElement("select");
  asrLanguage.append(
    new Option("自动识别", "auto"),
    new Option("中文", "zh"),
    new Option("英语", "en")
  );
  const asrItn = document.createElement("input");
  asrItn.type = "checkbox";
  const asrThreads = document.createElement("select");
  asrThreads.append(
    new Option("均衡 · CPU 2 线程", "2"),
    new Option("性能 · CPU 3 线程", "3")
  );
  const asrFolder = node2("button", "infinity-action", "打开文件夹");
  asrFolder.type = "button";
  coreSetUIAttribute(asrFolder, "aria-label", corePhrase("打开 ASR 本地模型文件夹"));
  coreSetUIText(asrFolder, "title", corePhrase("未选模型时打开 ASR 模型根目录"));
  const ttsSwitch = document.createElement("input");
  ttsSwitch.type = "checkbox";
  const ttsModel = document.createElement("select");
  const ttsVoice = document.createElement("select");
  const ttsVoiceFolder = node2("button", "infinity-action", "打开文件夹");
  ttsVoiceFolder.type = "button";
  coreSetUIAttribute(ttsVoiceFolder, "aria-label", corePhrase("打开 TTS 音色文件夹"));
  const voiceFolderTitle = "打开本机自定义音色目录（references），用于管理参考音频";
  ttsVoiceFolder.title = voiceFolderTitle;
  const defaultPreviewText = "你好，这是一段语音试听。让我们听听当前音色的效果。";
  const ttsSendMode = document.createElement("select");
  ttsSendMode.append(
    new Option("按需发送 · 仅工具调用", "on_demand"),
    new Option("普通 QQ 回复附带语音", "reply_with_voice")
  );
  const ttsSpeed = document.createElement("select");
  ttsSpeed.disabled = true;
  ttsSpeed.append(new Option("1.0 · 当前固定", "1.0"));
  const ttsPreview = node2("button", "infinity-action", "试听当前音色");
  ttsPreview.type = "button";
  const ttsPreviewText = document.createElement("textarea");
  ttsPreviewText.rows = 2;
  ttsPreviewText.maxLength = 200;
  coreSetUIText(ttsPreviewText, "placeholder", corePhrase("输入 1—200 字试听文字"));
  const ttsHint = node2("p", "infinity-hint");
  const ttsAudio = document.createElement("audio");
  ttsAudio.controls = true;
  ttsAudio.className = "infinity-voice-preview";
  ttsAudio.hidden = true;
  const ttsFolder = node2("button", "infinity-action", "打开文件夹");
  ttsFolder.type = "button";
  coreSetUIAttribute(ttsFolder, "aria-label", corePhrase("打开 TTS 本地模型文件夹"));
  coreSetUIText(ttsFolder, "title", corePhrase("未选模型时打开 TTS 模型根目录"));
  const save = node2("button", "infinity-action", "保存语音设置");
  save.type = "button";
  const asrAuto = document.createElement("input");
  const ttsAuto = document.createElement("input");
  asrAuto.type = ttsAuto.type = "checkbox";
  const asrProcess = node2("button", "infinity-action qq-primary-action", "启动 ASR");
  asrProcess.type = "button";
  const ttsProvider = document.createElement("select");
  ttsProvider.append(new Option("内置 CosyVoice3", "builtin"), new Option("外部兼容 TTS", "external"));
  const servicePort = document.createElement("input");
  servicePort.type = "number";
  servicePort.min = "1024";
  servicePort.max = "65535";
  const serviceName = document.createElement("input");
  serviceName.placeholder = "可选，用于启动和停止外部服务";
  const serviceField = infinityField("系统服务名（Linux 用户服务 / Windows 服务）", serviceName);
  ttsProvider.addEventListener("change", () => {
    serviceField.hidden = ttsProvider.value !== "external";
  });
  const ttsProcess = node2("button", "infinity-action", "启动 TTS");
  ttsProcess.type = "button";
  const snowProcess = node2("button", "infinity-action", "启动 SnowLuma");
  snowProcess.type = "button";
  const ttsState = node2("span", "infinity-service-state");
  const ttsRuntimeIssue = node2("p", "infinity-hint");
  ttsRuntimeIssue.hidden = true;
  const snowUsername = document.createElement("input");
  snowUsername.autocomplete = "off";
  const snowPassword = document.createElement("input");
  snowPassword.type = "password";
  snowPassword.autocomplete = "new-password";
  coreSetUIText(snowPassword, "placeholder", corePhrase("请输入 SnowLuma 管理密码"));
  const snowTarget = document.createElement("input");
  snowTarget.inputMode = "numeric";
  coreSetUIText(snowTarget, "placeholder", corePhrase("建议填写；多个 QQ 进程时只连接此账号"));
  const snowSave = node2("button", "infinity-action", "保存设置");
  snowSave.type = "button";
  const snowForget = node2("button", "infinity-action", "移除 Core 保存的管理密码");
  snowForget.type = "button";
  const snowCredential = node2("span", "snow-service-badge");
  const passwordField = infinityField("SnowLuma 管理密码（用于协议同步）", snowPassword);
  passwordField.append(snowCredential);
  const checkConnection = node2("button", "infinity-action", "刷新连接状态");
  checkConnection.type = "button";
  const importConnection = node2(
    "button",
    "infinity-action",
    "同步协议配置"
  );
  importConnection.type = "button";
  coreSetUIText(importConnection, "title", corePhrase("读取此 QQ 的 HTTP / WebSocket 地址与令牌，保存到当前 Bot；不改变会话名单。"));
  const checkResult = node2("p", "infinity-hint");
  checkResult.setAttribute("role", "status");
  const snowManager = node2(
    "a",
    "infinity-manage",
    "打开 SnowLuma 管理页 →"
  );
  snowManager.href = "#";
  snowManager.target = "_blank";
  snowManager.rel = "noopener noreferrer";
  const feedback = node2("p", "infinity-message");
  feedback.setAttribute("role", "status");
  let feedbackTimer;
  const clearFeedback = () => {
    if (feedbackTimer !== void 0) window.clearTimeout(feedbackTimer);
    feedbackTimer = void 0;
    feedback.textContent = "";
  };
  const showFeedback = (message2, transient = true) => {
    clearFeedback();
    coreSetUIText(feedback, "textContent", message2);
    if (transient && typeof window.setTimeout === "function") {
      feedbackTimer = window.setTimeout(clearFeedback, 6500);
    }
  };
  const terminalView = createQQTerminal(() => pollLog(true));
  const privateIds = document.createElement("textarea");
  const groupIds = document.createElement("textarea");
  const proactiveTarget = document.createElement("input");
  const backfillMinutes = document.createElement("input");
  const reactionWindow = document.createElement("input");
  reactionWindow.type = "number";
  reactionWindow.min = "0";
  reactionWindow.max = "10";
  reactionWindow.step = "1";
  const saveRoutes = node2("button", "infinity-action", "保存会话路由");
  saveRoutes.type = "button";
  privateIds.rows = 2;
  groupIds.rows = 2;
  coreSetUIText(privateIds, "placeholder", corePhrase("每行一个 QQ 号，也可用逗号分隔"));
  coreSetUIText(groupIds, "placeholder", corePhrase("每行一个群号，也可用逗号分隔"));
  coreSetUIText(proactiveTarget, "placeholder", corePhrase("主动消息仅发送给主人"));
  backfillMinutes.type = "number";
  backfillMinutes.min = "0";
  backfillMinutes.max = "30";
  backfillMinutes.step = "1";
  const routeAccount = node2("p", "infinity-hint");
  const routeDestinations = node2("section", "qq-route-group");
  const destinationFields = node2("div", "qq-route-destinations");
  destinationFields.append(infinityField("允许的私聊 QQ 号", privateIds), infinityField("允许的群号", groupIds));
  routeDestinations.append(
    node2("h4", "", "允许接收哪些消息"),
    destinationFields,
    node2("p", "infinity-hint", "填写允许 Bot 接收消息的联系人和群。")
  );
  const ownerHint = node2("p", "infinity-hint");
  const ownerChoices = document.createElement("datalist");
  ownerChoices.id = "qq-confirmed-owner-accounts";
  proactiveTarget.setAttribute("list", ownerChoices.id);
  let ownerRouteKey = "";
  const drawOwner = (state3) => {
    const route2 = state3.routing.ownerRoute;
    if (!route2) return;
    const key2 = JSON.stringify([route2, state3.routing.proactiveTarget]);
    if (ownerRouteKey !== key2) {
      ownerRouteKey = key2;
      ownerChoices.replaceChildren(...route2.accounts.map((account) => {
        const option = document.createElement("option");
        option.value = account;
        return option;
      }));
      proactiveTarget.value = route2.target;
      proactiveTarget.readOnly = route2.accounts.length <= 1;
    }
    const hint = route2.unavailable ? "暂时无法读取身份绑定，主动投递已暂停。" : !route2.accounts.length ? "在人物画像里，把你的 QQ 画像与本机控制台画像确认为同一个人。" : !route2.target ? "你已关联多个 QQ，请选择用于接收主动消息的账号并保存会话路由。" : !state3.routing.privates.includes(route2.target) ? "已确认主人的 QQ。将它加入上方允许的私聊名单后即可投递。" : "已关联主人的 QQ。是否主动开口、聊什么由 Bot 自己决定。";
    coreSetUIText(ownerHint, "textContent", corePhrase(hint));
    coreSetUIText(proactiveTarget, "title", corePhrase(hint));
  };
  const routeBehavior = node2("section", "qq-route-group");
  const behaviorFields = node2("div", "qq-route-behavior");
  behaviorFields.append(
    infinityField("主人接收主动消息的 QQ", proactiveTarget),
    infinityField("断线补读 · 0—30 分钟", backfillMinutes),
    infinityField("消息反应窗口 · 0—10 秒", reactionWindow)
  );
  routeBehavior.append(
    node2("h4", "", "回复与主动发送"),
    behaviorFields,
    ownerChoices,
    ownerHint,
    node2("p", "infinity-hint", "默认 3 秒，按首条消息计时；窗口内撤回则取消，不叠加聚合等待。")
  );
  const routeColumns = node2("div", "qq-route-columns");
  routeColumns.append(routeDestinations, routeBehavior);
  const routeBack = node2("a", "infinity-manage", "查看 QQ 连接状态 →");
  routeBack.href = "#/infinity/core-infinity-qq";
  routeBack.addEventListener("click", (event) => {
    event.preventDefault();
    tabs.children[0]?.click();
  });
  saveRoutes.classList.add("qq-primary-action");
  const routeActions = node2("div", "snow-action-row");
  routeActions.append(saveRoutes, routeBack);
  routingCard.append(
    node2("h3", "", "会话路由"),
    routeAccount,
    routeColumns,
    routeActions
  );
  const asrSetup = voiceSetup("asr", () => ({ model: asrModel.value }));
  const ttsSetup = voiceSetup("tts", () => ({ model: ttsModel.value, voice: ttsVoice.value }));
  function transferButtons() {
    const group = node2("div", "snow-action-row");
    const pause = node2("button", "infinity-action", "暂停下载");
    const cancel = node2("button", "infinity-action", "取消下载");
    pause.type = cancel.type = "button";
    group.append(pause, cancel);
    group.hidden = true;
    return { group, pause, cancel };
  }
  const transfer = { asr: transferButtons(), tts: transferButtons() };
  const asrHeader = infinityVoiceHeading("ASR · 语音识别", asrDownload);
  asrCard.append(
    asrHeader,
    infinityToggle("接收语音时转写", asrSwitch),
    infinityToggle("随 Bot 唤醒自动启动 ASR", asrAuto),
    infinityModelPicker("识别模型", asrModel, asrFolder),
    infinityField("识别语言", asrLanguage),
    infinityToggle("数字与标点规范化", asrItn),
    infinityField("性能档", asrThreads),
    asrState,
    asrProcess,
    asrDownloadState,
    asrProgress,
    transfer.asr.group,
    asrSetup.section
  );
  const ttsHeader = infinityVoiceHeading("TTS · 语音合成", ttsDownload);
  ttsProcess.classList.add("qq-primary-action");
  ttsCard.append(
    ttsHeader,
    infinityToggle("启用语音发送", ttsSwitch),
    infinityToggle("随 Bot 唤醒自动启动 TTS", ttsAuto),
    infinityField("TTS 服务", ttsProvider),
    infinityField("本机 TTS 端口", servicePort),
    serviceField,
    infinityModelPicker("合成模型", ttsModel, ttsFolder),
    infinityModelPicker("音色", ttsVoice, ttsVoiceFolder),
    infinityField("语音发送方式", ttsSendMode),
    infinityField("语速", ttsSpeed),
    infinityField("试听文本", ttsPreviewText),
    ttsHint,
    ttsRuntimeIssue,
    ttsState,
    ttsProcess,
    ttsPreview,
    ttsAudio,
    ttsDownloadState,
    ttsProgress,
    transfer.tts.group,
    ttsSetup.section
  );
  const corpusPanel = voiceCorpusPanel();
  ttsCard.append(corpusPanel);
  const accountFields = node2("div", "snow-fields");
  accountFields.append(
    infinityField("目标 QQ 号", snowTarget),
    infinityField("SnowLuma 管理用户名", snowUsername),
    passwordField
  );
  const accountActions = node2("div", "snow-action-row");
  accountActions.append(
    snowSave,
    snowForget,
    node2("p", "infinity-hint", "更换密码后，在这里输入新密码并保存。移除密码会同时清除 Core 保存的管理登录会话。")
  );
  const routeLink = node2("a", "infinity-manage", "前往会话路由填写允许名单 →");
  routeLink.href = "#/infinity/core-infinity-qq";
  routeLink.addEventListener("click", (event) => {
    event.preventDefault();
    tabs.children[1]?.click();
  });
  const commonActions = node2("section", "snow-common-actions");
  coreSetUIAttribute(commonActions, "aria-label", corePhrase("SnowLuma 常用操作"));
  const serviceActions = node2("div", "snow-service-strip");
  const serviceIdentity = node2("div", "snow-service-identity");
  const serviceStatus = node2("span", "snow-service-badge");
  const controlStatus = node2("span", "snow-service-badge", "控制通道不可用");
  controlStatus.dataset.state = "off";
  controlStatus.hidden = true;
  serviceIdentity.append(node2("span", "snow-service-name", "协议服务"), serviceStatus, controlStatus);
  const serviceUtilities = node2("div", "snow-service-utilities");
  snowProcess.className += " snow-service-control";
  const initialPassword = node2("button", "infinity-action", "查看初始管理密码");
  initialPassword.type = "button";
  const passwordResult = node2("p", "infinity-hint");
  passwordResult.setAttribute("role", "status");
  let passwordTimer;
  let passwordRequest = 0;
  let passwordReadPending = false;
  let qqStatusPending;
  function clearInitialPassword() {
    passwordRequest += 1;
    if (passwordTimer !== void 0) window.clearTimeout(passwordTimer);
    passwordTimer = void 0;
    passwordResult.textContent = "";
  }
  initialPassword.addEventListener("click", async () => {
    clearInitialPassword();
    const current2 = passwordRequest;
    initialPassword.disabled = true;
    passwordReadPending = true;
    try {
      const response = await fetch("/infinity/qq/initial-password", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Core-Local-Password": "1" },
        body: "{}"
      });
      const result = await response.json();
      if (root.hidden || current2 !== passwordRequest) return;
      passwordResult.textContent = response.ok && result.ok ? `用户名 admin · 初始密码 ${result.password} · 请登录管理页并修改密码` : result.said || "暂时无法读取初始密码。";
      passwordTimer = window.setTimeout(clearInitialPassword, 6e4);
    } catch {
      if (!root.hidden && current2 === passwordRequest) passwordResult.textContent = "暂时无法读取初始密码。";
    } finally {
      passwordReadPending = false;
      initialPassword.disabled = false;
    }
  });
  serviceUtilities.append(snowManager, snowProcess);
  const firstSetup = node2("section", "snow-first-setup");
  const firstSetupToggle = node2("button", "infinity-action", "查看首次部署步骤");
  firstSetupToggle.type = "button";
  firstSetup.hidden = true;
  firstSetupToggle.setAttribute("aria-expanded", "false");
  const setupActions = node2("div", "snow-setup-actions");
  setupActions.append(initialPassword, passwordResult);
  const checklist = node2("ol", "snow-first-checklist");
  const checks = [
    "登录 SnowLuma 管理页并修改初始密码",
    "在 SnowLuma 管理页加载 QQ，确认绑定账号在线",
    "在会话路由保存至少一个允许的私聊 QQ 号"
  ].map((label) => {
    const row2 = node2("li", "snow-first-step");
    const status = node2("span", "snow-service-badge", "待完成");
    row2.append(node2("span", "", label), status);
    checklist.append(row2);
    return status;
  });
  checklist.children[0].append(setupActions);
  const syncStep = node2("li", "snow-first-step");
  syncStep.append(
    node2("span", "", "确认 Core 已接入 QQ"),
    node2(
      "p",
      "infinity-hint",
      "如果 SnowLuma 已显示 QQ 在线，但 Core 仍未连接，先在 SnowLuma 确认 OneBot 设置，再回到本页点击「同步协议配置」。已经能正常收发时可跳过。"
    )
  );
  checklist.append(syncStep);
  firstSetup.append(checklist);
  firstSetup.id = "snow-first-setup";
  firstSetupToggle.setAttribute("aria-controls", firstSetup.id);
  firstSetup.prepend(node2("h3", "", "首次部署步骤"));
  firstSetupToggle.addEventListener("click", () => {
    firstSetup.hidden = !firstSetup.hidden;
    firstSetupToggle.setAttribute("aria-expanded", String(!firstSetup.hidden));
    firstSetupToggle.textContent = firstSetup.hidden ? "查看首次部署步骤" : "收起首次部署步骤";
    if (!firstSetup.hidden) firstSetup.scrollIntoView({ block: "nearest" });
  });
  serviceActions.append(serviceIdentity, serviceUtilities);
  const connectionActions = node2("div", "snow-secondary-actions");
  importConnection.classList.add("qq-primary-action");
  checkConnection.classList.add("qq-quiet-action");
  firstSetupToggle.classList.add("qq-quiet-action");
  initialPassword.classList.add("qq-quiet-action");
  connectionActions.append(importConnection, checkConnection, routeLink);
  const connectionTools = node2("div", "snow-connection-tools");
  connectionTools.append(connectionActions, firstSetupToggle);
  commonActions.append(serviceActions, connectionTools);
  const setupDetails = node2("details", "snow-onboarding compact-disclosure");
  setupDetails.append(node2("summary", "", "账号与管理凭据"), accountFields, accountActions);
  const setupHint = node2("p", "infinity-hint");
  setupHint.setAttribute("role", "status");
  setupHint.hidden = true;
  snowCard.append(commonActions, setupHint, checkResult, firstSetup, setupDetails);
  if (pluginName === "core-infinity-qq") {
    const stickers = qqStickers();
    const sections = [
      { label: "SnowLuma", pane: snowPane, id: 0 },
      { label: "会话路由", pane: routePane, id: 1 },
      { label: "表情包", pane: stickers.root, id: 5 },
      { label: "语音模型", pane: voicePane, id: 2 },
      { label: "终端", pane: terminalPane, id: 3 },
      { label: "工具集", pane: toolbox.root, id: 6 },
      { label: "环境", pane: environmentPane, id: 4 }
    ];
    let tabTouched = false;
    sections.forEach((section, index) => {
      const tab = node2("button", "infinity-tab", section.label);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(index === 0));
      tab.addEventListener("click", () => {
        clearFeedback();
        tabTouched = true;
        panelScroll.scrollTop = 0;
        writePluginTab("core-infinity-qq", section.id);
        if (section.pane !== snowPane) clearInitialPassword();
        for (const [position, item] of sections.entries()) {
          item.pane.hidden = section !== item;
          tabs.children[position].setAttribute("aria-selected", String(section === item));
        }
        if (section.pane === terminalPane) terminalView.activate();
        else terminalView.deactivate();
        if (section.pane === stickers.root) stickers.open();
        if (section.pane === environmentPane) void environmentView.open();
        if (section.pane === toolbox.root) void toolbox.refresh();
      });
      tabs.append(tab);
      section.pane.hidden = index !== 0;
    });
    void readyPluginDisplay().then(() => {
      const saved = readPluginTab("core-infinity-qq");
      const position = sections.findIndex((section) => section.id === saved);
      if (!root.hidden && !tabTouched && position >= 0) tabs.children[position].click();
    });
    routePane.append(routingCard);
    save.classList.add("qq-primary-action");
    const voiceActions = node2("div", "qq-voice-save");
    voiceActions.append(save);
    voicePane.append(ttsCard, asrCard, voiceActions);
    snowPane.append(snowCard);
    terminalPane.append(terminalView.root);
    voiceGrid.append(...sections.filter((section) => section.pane !== toolbox.root && section.pane !== environmentPane).map((section) => section.pane));
  }
  const configFields = document.createElement("fieldset");
  configFields.style.display = "contents";
  configFields.disabled = true;
  configFields.append(voiceGrid);
  if (knownConfig) configPresentation(voiceGrid, "loading");
  panelScroll.append(configFields, ...knownConfig ? [toolbox.root, environmentPane] : []);
  qqPanel.append(
    node2("h2", "qq-section-title", "QQ 接入与消息设置"),
    health,
    tabs,
    panelScroll,
    feedback
  );
  qqPanel.hidden = !knownConfig;
  summary2.hidden = pluginName !== "";
  cards.hidden = pluginName !== "";
  save.hidden = pluginName !== "core-infinity-qq";
  if (knownConfig) root.append(head, message, qqPanel);
  else root.append(head, summary2, cards, message, qqPanel);
  const pageFooter = node2("footer", "page-footer qq-page-footer");
  const footerScope = node2("span", "page-footer-summary", "正在读取接收范围…");
  pageFooter.append(node2("span", "page-footer-label", "QQ 连接 · OneBot v11"), footerScope);
  if (knownConfig) root.append(pageFooter);
  shell2.content.replaceChildren(root);
  let requestId = 0;
  let currentQQ = null;
  let displayedTarget = "";
  let hasPlugin = false;
  let pluginLoaded = false;
  let pollTimer;
  let mutationPending = false;
  let previewPending = false;
  let previewController;
  let pluginsDrawn = false;
  let pluginsFailed = false;
  let pluginMissing = false;
  let qqLoaded = false;
  let qqFailed = false;
  let formFilled = false;
  let checkPending = false;
  let importPending = false;
  let asrDownloadPending = false;
  let ttsDownloadPending = false;
  const downloadErrors = { asr: "", tts: "" };
  const commandButtons = [
    save,
    saveRoutes,
    asrProcess,
    ttsProcess,
    ttsPreview,
    snowProcess,
    snowSave,
    snowForget,
    importConnection,
    checkConnection,
    asrDownload,
    ttsDownload,
    asrFolder,
    ttsFolder,
    ttsVoiceFolder
  ];
  const lockCommands = () => {
    configFields.disabled = true;
    for (const item of commandButtons) item.disabled = true;
  };
  const renderWaitingHealth = (failed) => {
    if (currentQQ !== null) return;
    health.replaceChildren();
    const badge = node2(
      "span",
      "infinity-health-item",
      failed ? "QQ 状态 · 读取失败 · 自动重试" : "QQ 状态 · 读取中…"
    );
    badge.dataset.state = "warn";
    health.append(badge);
  };
  const syncNotice = () => {
    liveNotice.hidden = false;
    if (mutationPending) {
      coreSetUIText(liveNotice, "textContent", corePhrase("正在处理…"));
      return;
    }
    if (qqLoaded && qqFailed) {
      coreSetUIText(liveNotice, "textContent", corePhrase("QQ 状态更新暂不可用 · 自动重试"));
      return;
    }
    if (!qqLoaded && knownConfig) {
      coreSetUIText(liveNotice, "textContent", qqFailed ? corePhrase("QQ 状态读取失败 · 自动重试") : corePhrase("正在读取 QQ 状态…"));
      return;
    }
    if (pluginsDrawn && pluginsFailed) {
      coreSetUIText(liveNotice, "textContent", corePhrase("插件列表刷新失败 · 自动重试"));
      return;
    }
    if (!pluginsDrawn) {
      coreSetUIText(liveNotice, "textContent", pluginsFailed ? corePhrase("插件列表读取失败 · 自动重试") : corePhrase("正在读取插件列表…"));
      return;
    }
    liveNotice.textContent = "";
    liveNotice.hidden = true;
  };
  if (knownConfig) {
    lockCommands();
    renderWaitingHealth(false);
    showFeedback(corePhrase("正在读取 QQ 配置；数据就绪前不能保存、试听或启停进程。"));
    coreSetUIText(message, "textContent", corePhrase("正在读取插件状态…"));
  } else {
    coreSetUIText(message, "textContent", corePhrase("正在读取插件列表…"));
  }
  const updateLive = (state3) => {
    footerScope.textContent = `接收范围：私聊 ${state3.routing.privates.length} 个 · 群聊 ${state3.routing.groups.length} 个`;
    configFields.disabled = mutationPending || pluginMissing;
    currentQQ = state3;
    const commandsLocked = mutationPending || !formFilled || state3.snowluma.controlAvailable === false;
    const connectionReady = freshProtocolSample(state3.snowluma) && state3.snowluma.protocolHealth?.pluginConnected === true;
    const recoveryBusy = !!state3.snowluma.recovery?.busy;
    if (!recoveryBusy && connectionReady && !mutationPending && /^(恢复检测已提交；|加载确认已提交；)/.test(feedback.textContent || "")) {
      showFeedback(corePhrase("接入已完成：目标 QQ 在线、插件 WS 已连接。真实消息收发仍待验收。"));
    }
    for (const kind of ["tts", "asr"]) {
      const isTts = kind === "tts";
      const button4 = isTts ? ttsDownload : asrDownload;
      const status = isTts ? ttsDownloadState : asrDownloadState;
      const progress2 = isTts ? ttsProgress : asrProgress;
      const pending5 = isTts ? ttsDownloadPending : asrDownloadPending;
      const voice = state3[kind];
      const install = voice.install;
      const recommended = isTts ? "Fun-CosyVoice3-0.5B-2512" : "SenseVoiceSmall-onnx";
      const installed2 = voice.models.includes(recommended);
      const failed = install?.phase === "error" || install?.phase === "cleanup_failed";
      const busy = install?.busy === true;
      const complete = installed2 && install?.runtimeReady !== false && !voice.runtimeIssue;
      button4.hidden = complete && !busy;
      button4.disabled = mutationPending || !formFilled || pending5 || !install || busy || complete;
      const paused = install?.phase === "paused";
      const partial = !complete && (paused || failed && (install?.downloaded || 0) > 0);
      const changing = ["pausing", "cancelling", "publishing"].includes(install?.phase || "");
      const controls = transfer[kind];
      controls.group.hidden = !install?.controls || !(busy || partial);
      controls.pause.hidden = !busy;
      controls.pause.disabled = controls.cancel.disabled = pending5 || changing || mutationPending || !formFilled;
      button4.textContent = partial ? "继续安装" : installed2 ? "补全运行环境" : "下载推荐模型 ↓";
      progress2.hidden = !(busy || partial);
      progress2.value = Math.min(1, (install?.downloaded || 0) / (install?.total || 1));
      if (busy && ["environment", "selfcheck"].includes(install?.phase || "")) {
        progress2.hidden = true;
        status.textContent = install?.message || "正在准备语音环境…";
      } else if (busy) {
        const phase = { verify: "正在校验", publishing: "正在安装", pausing: "正在暂停", cancelling: "正在取消" }[install.phase || ""] || "正在下载";
        const source = install.source === "mirror" ? "国内镜像 · 直连" : install.route === "proxy" ? "官方源 · 代理" : install.route === "direct" ? "官方源 · 直连" : "官方源";
        const rate = install.bytesPerSecond || 0;
        const speed = rate >= 1048576 ? (rate / 1048576).toFixed(2) + " MiB/s" : (rate / 1024).toFixed(0) + " KiB/s";
        status.textContent = `${phase} · ${source} · ${(progress2.value * 100).toFixed(1)}% · ${(install.downloaded / 1048576).toFixed(1)} MiB` + (rate ? " · " + speed : "");
      } else if (paused || install?.phase === "cancelled") {
        status.textContent = paused ? "已暂停 · 已保留 " + (install.downloaded / 1048576).toFixed(1) + " MiB" : "已取消下载";
      } else if (pending5) {
        status.textContent = "正在准备下载…";
      } else if (complete) {
        status.textContent = install?.runtimeReady ? "模型与环境已就绪 · 自检通过" : "推荐模型已安装";
      } else if (downloadErrors[kind] || failed) {
        status.textContent = downloadErrors[kind] || install?.message || "下载失败，可以重试。";
      } else {
        status.textContent = installed2 ? "模型已下载，请补全运行环境。" : !install ? "当前 Core 未提供模型下载。" : "推荐模型未安装";
      }
      status.dataset.state = !busy && !pending5 && !complete && (failed || downloadErrors[kind]) ? "warn" : "neutral";
    }
    const ttsNames = Array.from(ttsModel.options).map((option) => option.value).filter(Boolean);
    if (JSON.stringify(ttsNames) !== JSON.stringify(state3.tts.models)) {
      const selected = ttsModel.value || state3.tts.model;
      fillSelect(ttsModel, state3.tts.models, selected, true);
    }
    const asrNames = Array.from(asrModel.options).map((option) => option.value).filter(Boolean);
    if (JSON.stringify(asrNames) !== JSON.stringify(state3.asr.models)) {
      fillSelect(asrModel, state3.asr.models, asrModel.value || state3.asr.model, false);
    }
    coreSetUIText(routeAccount, "textContent", state3.snowluma.recovery?.targetUin ? [corePhrase("当前 Bot 的 QQ："), state3.snowluma.recovery.targetUin, corePhrase(" · 名单只作用于这个 Bot。")] : corePhrase("尚未选择登录 QQ；可先保存会话名单，再到 SnowLuma 页完成账号接入。"));
    snowProcess.hidden = false;
    const serviceLabel = state3.snowluma.managed ? "已启动" : state3.snowluma.sharedService ? "共用服务" : state3.snowluma.webuiRunning ? "端口可达" : "未启动";
    if (uiSource(serviceStatus) !== serviceLabel) {
      coreSetUIText(serviceStatus, "textContent", corePhrase(serviceLabel));
    }
    const serviceColor = state3.snowluma.webuiRunning ? "on" : "off";
    if (serviceStatus.dataset.state !== serviceColor) serviceStatus.dataset.state = serviceColor;
    controlStatus.hidden = state3.snowluma.controlAvailable !== false;
    controlStatus.title = state3.snowluma.controlMessage || "";
    snowProcess.dataset.mode = state3.snowluma.sharedService ? "shared" : state3.snowluma.managed || state3.snowluma.canStopInstallation ? "stop" : "start";
    const passwordChanged = state3.snowluma.managementPasswordChanged;
    const accountOnline = state3.snowluma.localAccountOnline === true || freshProtocolSample(state3.snowluma) || freshSnowSample(state3.snowluma);
    const progress = [
      passwordChanged,
      accountOnline,
      state3.routing.privates.length > 0
    ];
    checks.forEach((badge, index) => {
      const value = progress[index];
      const label = value === true ? "已完成" : value === false ? "待完成" : "待检查";
      if (badge.textContent !== label) badge.textContent = label;
      badge.dataset.state = value === true ? "on" : "off";
    });
    const installation = state3.snowluma.installation;
    let guidance = "";
    if (installation?.ready === false) {
      guidance = "请从 Core 启动器完成 SnowLuma 运行环境自检。";
    } else if (state3.snowluma.webuiRunning && !connectionReady && !accountOnline && state3.snowluma.waitingForDesktopExit === true) {
      guidance = "当前 QQ 需要正常退出一次。Core 正在等待桌面 QQ 退出，连接状态会自动更新。";
    } else if (state3.snowluma.webuiRunning && !state3.snowluma.recovery?.targetUin) {
      guidance = "在 SnowLuma 管理页选择已登录的 QQ 并加载。识别成功后，Core 会保存目标 QQ。";
    } else if (state3.snowluma.webuiRunning && !connectionReady) {
      const protocol = state3.snowluma.protocolHealth;
      const savedRoutes = state3.routing.privates.length > 0 || state3.routing.groups.length > 0;
      if (accountOnline && !savedRoutes) {
        guidance = "QQ 已在线，请先到会话路由保存允许名单，再刷新连接状态。";
      } else if (accountOnline && !state3.plugin?.configured || protocol?.fresh && protocol.status === "auth_failed") {
        guidance = "请确认 SnowLuma 的 OneBot 地址和令牌，再点击「同步协议配置」。";
      } else if (state3.snowluma.localAccountOnline !== void 0 && state3.plugin?.running && state3.plugin.configured && !protocol?.pluginConnected && ["checking", "untrusted", "unavailable", "offline", "online"].includes(protocol?.status ?? "")) {
        const waiting = "Core 正在等待 QQ 事件连接。已配置的 OneBot 服务就绪后会自动重连。";
        const hookEvents = (state3.snowluma.log ?? "").split("\n").filter((line) => line.includes("[Hook]"));
        guidance = !accountOnline && hookEvents.at(-1)?.includes("[COMPONENT_LOAD_FAILED]") ? "最近一次组件加载失败。" + waiting : waiting;
      }
    }
    setupHint.hidden = !guidance;
    if (setupHint.textContent !== guidance) setupHint.textContent = guidance;
    snowSave.disabled = commandsLocked || !state3.snowluma.recovery || !!state3.snowluma.recovery.busy;
    snowForget.disabled = commandsLocked || !state3.snowluma.recovery?.hasCredentials || !!state3.snowluma.recovery?.busy;
    const credentialsSaved = state3.snowluma.recovery?.hasCredentials === true;
    snowCredential.dataset.state = credentialsSaved || passwordChanged ? "on" : "off";
    coreSetUIText(snowCredential, "textContent", credentialsSaved ? corePhrase("管理密码已保存") : passwordChanged ? corePhrase("SL 已改密 · Core 尚未保存管理凭据") : state3.snowluma.localAccountOnline ? corePhrase("本机协议可同步；管理密码按需保存") : corePhrase("管理密码未保存"));
    coreSetUIText(snowPassword, "placeholder", credentialsSaved ? corePhrase("已设置；留空保持原值") : passwordChanged ? corePhrase("SL 已改密，按需填写以保存到 Core") : corePhrase("请输入 SnowLuma 管理密码"));
    snowManager.hidden = false;
    const managerUrl = state3.snowluma.managementUrl || "";
    const managerAddress = state3.snowluma.webuiRunning && /^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(managerUrl) ? `${managerUrl}/processes` : "#";
    if (snowManager.getAttribute("href") !== managerAddress) snowManager.href = managerAddress;
    snowManager.setAttribute("aria-disabled", String(managerAddress === "#"));
    coreSetUIText(snowManager, "title", corePhrase("打开本机 SnowLuma 管理页"));
    coreSetUIText(snowProcess, "textContent", state3.snowluma.managed ? corePhrase("停止 SnowLuma") : state3.snowluma.sharedService ? corePhrase("共享 SnowLuma 运行中") : state3.snowluma.canStopInstallation ? corePhrase("停止此安装的 SnowLuma") : corePhrase("启动 SnowLuma"));
    snowProcess.disabled = commandsLocked || state3.snowluma.sharedService === true || (state3.snowluma.transportReachable || state3.snowluma.webuiRunning) && !state3.snowluma.managed && !state3.snowluma.canStopInstallation;
    coreSetUIText(snowProcess, "title", state3.snowluma.sharedService ? corePhrase("共享服务由其他实例管理；本页可恢复自己的 QQ，不允许停止其他 Bot 的协议服务。") : state3.snowluma.managed || state3.snowluma.canStopInstallation ? corePhrase("停止整个 SnowLuma 服务会断开使用它的所有 QQ 账号。") : corePhrase("启动 SnowLuma 并尝试恢复当前账号；不会跳过扫码或二次验证。"));
    if (state3.snowluma.recovery?.busy) {
      coreSetUIText(snowProcess, "textContent", connectionReady ? corePhrase("已连接，正在收尾…") : corePhrase("正在验证并接入…"));
      snowProcess.disabled = true;
    }
    save.disabled = commandsLocked;
    saveRoutes.disabled = commandsLocked;
    importConnection.disabled = commandsLocked || importPending;
    checkConnection.disabled = commandsLocked || checkPending;
    asrFolder.disabled = commandsLocked;
    ttsFolder.disabled = commandsLocked;
    ttsVoiceFolder.disabled = commandsLocked || !state3.tts.voiceFolderAvailable;
    coreSetUIText(ttsVoiceFolder, "title", state3.tts.voiceFolderAvailable ? voiceFolderTitle : state3.tts.voiceFolderAvailable === false ? corePhrase("请先安装本机 TTS 环境") : corePhrase("重启 Core 后可打开本机音色文件夹"));
    health.replaceChildren();
    const voiceBlocked = state3.tts.pluginEnabled === false;
    const markers = qqSignals(state3, pluginLoaded);
    const pluginEnabled = state3.tts.pluginEnabled;
    if (typeof pluginEnabled === "boolean") {
      const pluginStatus = node2("span", "qq-plugin-status");
      const badge = node2(
        "span",
        "infinity-health-item qq-plugin-state",
        pluginEnabled ? "QQ 插件 · 已启用" : "QQ 插件 · 未启用"
      );
      badge.dataset.state = pluginEnabled ? "on" : "warn";
      pluginStatus.append(badge);
      if (!pluginEnabled) {
        const link = node2("a", "qq-plugin-enable-link", "前往插件市场 →");
        link.href = "#/plugin";
        pluginStatus.append(link);
      }
      health.append(pluginStatus);
      markers.shift();
    }
    for (const { state: status, label } of markers) {
      const badge = node2("span", "infinity-health-item", label);
      badge.dataset.state = status;
      health.append(badge);
    }
    coreSetUIText(asrState, "textContent", corePhrase(voiceBlocked ? "QQ 插件已停用 · 语音设置已保留" : state3.asr.startError || (state3.asr.running ? "ASR 模型已加载" : "ASR 已停止")));
    const ttsStatus = voiceBlocked ? "QQ 插件已停用 · 语音设置已保留" : state3.tts.startError || (state3.tts.running ? state3.tts.managed ? "TTS 服务已就绪" : "外部 TTS 服务已就绪" : state3.tts.managed ? "正在加载 TTS 模型…" : "TTS 服务未启动");
    coreSetUIText(ttsState, "textContent", corePhrase(ttsStatus));
    asrProcess.textContent = state3.asr.running ? "停止 ASR" : "启动 ASR";
    asrProcess.disabled = commandsLocked || voiceBlocked && !state3.asr.running;
    coreSetUIText(ttsProcess, "textContent", state3.tts.managed || state3.tts.running ? corePhrase("停止 TTS") : corePhrase("启动 TTS"));
    asrSetup.status(voiceBlocked ? "启用 QQ 插件后才能运行自检。" : state3.asr.runtimeIssue || "", voiceBlocked);
    ttsSetup.status(voiceBlocked ? "启用 QQ 插件后才能运行自检。" : "", voiceBlocked);
    const runtimeIssue = state3.tts.running || state3.tts.managed ? "" : state3.tts.runtimeIssue || "";
    ttsRuntimeIssue.hidden = !runtimeIssue;
    coreSetUIText(ttsRuntimeIssue, "textContent", corePhrase(runtimeIssue));
    ttsProcess.disabled = commandsLocked || voiceBlocked && !state3.tts.managed || state3.tts.running && !state3.tts.managed && !(state3.tts.provider === "external" && state3.tts.service);
    ttsProcess.title = state3.tts.running && !state3.tts.managed && !(state3.tts.provider === "external" && state3.tts.service) ? "如需从 Core 停止外部 TTS，请选择外部服务并保存其系统服务名" : "";
    ttsPreview.disabled = commandsLocked || voiceBlocked || previewPending || !state3.tts.running || !state3.tts.voices;
  };
  const fillSelect = (select2, names, selected, blank) => {
    select2.replaceChildren();
    if (blank) {
      const empty = document.createElement("option");
      empty.value = "";
      coreSetUIText(empty, "textContent", corePhrase("暂不选择"));
      select2.append(empty);
    }
    for (const name of names) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select2.append(option);
    }
    select2.value = selected;
  };
  const fillQQForm = (state3) => {
    privateIds.value = state3.routing.privates.join("\n");
    groupIds.value = state3.routing.groups.join("\n");
    proactiveTarget.value = state3.routing.proactiveTarget;
    proactiveTarget.readOnly = Boolean(state3.routing.sharingOwner && state3.routing.sharingEnabled);
    coreSetUIText(proactiveTarget, "title", proactiveTarget.readOnly ? corePhrase("主动分享只发送给主人，收件人不可在这里更改") : corePhrase("未启用主人分享"));
    backfillMinutes.value = String(state3.routing.historyBackfillMinutes);
    reactionWindow.value = String(state3.routing.reactionWindowSeconds ?? 3);
    snowUsername.value = state3.snowluma.recovery?.username || "admin";
    snowTarget.value = state3.snowluma.recovery?.targetUin || "";
    asrSwitch.checked = state3.asr.enabled && state3.asr.setupReady !== false;
    asrLanguage.value = state3.asr.language || "auto";
    asrLanguage.disabled = !state3.asr.language;
    asrItn.checked = state3.asr.useItn ?? true;
    asrItn.disabled = state3.asr.useItn === void 0;
    asrThreads.value = String(state3.asr.threads ?? 3);
    asrThreads.disabled = state3.asr.threads === void 0;
    ttsSwitch.checked = state3.tts.enabled;
    asrAuto.checked = state3.asr.autoStart ?? state3.asr.enabled;
    ttsAuto.checked = state3.tts.autoStart ?? state3.tts.enabled;
    servicePort.value = String(state3.tts.port ?? 18084);
    ttsProvider.value = state3.tts.provider ?? "builtin";
    serviceName.value = state3.tts.service || "";
    serviceField.hidden = ttsProvider.value !== "external";
    fillSelect(asrModel, state3.asr.models, state3.asr.model, false);
    fillSelect(ttsModel, state3.tts.models, state3.tts.model, true);
    ttsVoice.replaceChildren();
    for (const voice of state3.tts.voices || [{ id: "default", label: "默认音色 · 通用" }]) {
      const label = voice.id === "telyin" && ["特琳 · 仅本机", "特琳（本机）"].includes(voice.label) ? "Telyin.wav" : voice.label;
      ttsVoice.append(new Option(label, voice.id));
    }
    ttsVoice.value = state3.tts.voice || "default";
    ttsVoice.disabled = !state3.tts.voices;
    ttsSendMode.value = state3.tts.sendMode || "on_demand";
    ttsSendMode.disabled = !state3.tts.sendMode;
    ttsSpeed.value = "1.0";
    if (!ttsPreviewText.value) {
      const text2 = state3.tts.previewText;
      ttsPreviewText.value = !text2 || text2 === "你好，我是特琳。欢迎来听一段语音。" ? defaultPreviewText : text2;
    }
    ttsPreviewText.disabled = !state3.tts.previewText;
    coreSetUIText(ttsHint, "textContent", corePhrase("按需发送由 Bot 调用。选择回复附语音时，先发文字，再发语音。"));
  };
  const drawQQ = (state3) => {
    drawOwner(state3);
    environmentView.update(state3.environment);
    terminalView.update(state3.snowluma.log);
    const boundTarget = state3.snowluma.recovery?.targetUin || "";
    if (snowTarget.value === displayedTarget) snowTarget.value = boundTarget;
    displayedTarget = boundTarget;
    currentQQ = state3;
    if (knownConfig) qqPanel.hidden = pluginMissing;
    updateLive(state3);
  };
  const draw = (answer) => {
    summary2.replaceChildren();
    cards.replaceChildren();
    message.textContent = "";
    if (answer === null) {
      hasPlugin = false;
      coreSetUIText(message, "textContent", knownConfig ? corePhrase("扩展状态暂时读不到，正在重试…") : corePhrase("暂时读不到扩展状态。"));
      if (knownConfig) qqPanel.hidden = false;
      return;
    }
    if (!answer.wired) {
      hasPlugin = false;
      pluginLoaded = false;
      pluginMissing = true;
      coreSetUIText(message, "textContent", corePhrase("扩展状态尚未接入。"));
      if (knownConfig) qqPanel.hidden = true;
      return;
    }
    const plugins = answer.plugins ?? [];
    const selected = plugins.find((one) => one.name === pluginName && one.installed !== false);
    hasPlugin = selected !== void 0;
    pluginMissing = !hasPlugin;
    pluginLoaded = selected?.loaded === true;
    if (pluginName) {
      qqPanel.hidden = !knownConfig || pluginMissing;
      if (!knownConfig) coreSetUIText(heading2, "textContent", selected ? [corePhrase(""), selected.displayName || selected.name, corePhrase(" · 配置")] : corePhrase("插件配置"));
      coreSetUIText(message, "textContent", selected ? knownConfig ? "" : selected.description || "" : corePhrase("这份插件尚未安装。"));
      if (selected && !knownConfig) coreSetUIText(message, "textContent", corePhrase("此插件暂未提供网页配置项。"));
      if (currentQQ) updateLive(currentQQ);
      return;
    }
    qqPanel.hidden = true;
    const installed2 = plugins.filter((one) => one.installed !== false);
    const metrics = [
      ["运行中", installed2.filter((one) => one.loaded).length],
      ["已安装", installed2.length],
      ["可安装", plugins.filter((one) => one.installed === false).length]
    ];
    for (const [label, count4] of metrics) {
      const metric = node2("div", "infinity-metric");
      metric.append(node2("strong", "", String(count4)), node2("span", "", label));
      summary2.append(metric);
    }
    if (plugins.length === 0) {
      coreSetUIText(message, "textContent", corePhrase("目前没有扩展。"));
    }
    for (const plugin of plugins) {
      const card = node2("article", "infinity-card");
      const title = node2("div", "infinity-card-title");
      title.append(node2("h2", "", plugin.displayName || plugin.name));
      const state3 = stateOf(plugin);
      const badge = node2("span", "infinity-state", state3);
      badge.dataset.state = plugin.loaded ? "running" : plugin.installed === false ? "available" : "idle";
      title.append(badge);
      card.append(title);
      const meta = node2(
        "p",
        "infinity-card-meta",
        [plugin.name, plugin.version ? `v${plugin.version}` : "", plugin.builtin ? "内置" : ""].filter(Boolean).join(" · ")
      );
      card.append(meta);
      if (plugin.description) {
        card.append(node2("p", "infinity-card-description", plugin.description));
      }
      if (plugin.contract) card.append(node2("p", "infinity-card-contract", plugin.contract));
      const installedHere = plugin.installed !== false;
      const manage = node2("a", "infinity-manage", installedHere ? "打开插件页面 →" : "前往插件市场 →");
      manage.href = installedHere ? `#/infinity/${encodeURIComponent(plugin.name)}` : "#/plugin";
      coreSetUIText(manage, "title", installedHere ? [corePhrase("打开 "), plugin.displayName || plugin.name, corePhrase(" 的页面")] : [corePhrase("在插件市场查看 "), plugin.displayName || plugin.name, corePhrase("")]);
      card.append(manage);
      cards.append(card);
    }
  };
  let lastPluginsView = "";
  const loadPlugins = async (mine) => {
    try {
      const answer = await getStatusJson("/plugins", 6e3);
      if (mine !== requestId || root.hidden || mutationPending) return;
      pluginsFailed = false;
      const view2 = JSON.stringify(answer);
      if (!pluginsDrawn || view2 !== lastPluginsView) {
        draw(answer);
        lastPluginsView = view2;
      }
      pluginsDrawn = true;
    } catch (error) {
      if (error instanceof Error && error.name === "StaleStatusError") return;
      if (mine !== requestId || root.hidden || mutationPending) return;
      pluginsFailed = true;
      if (!pluginsDrawn) draw(null);
    } finally {
      if (mine === requestId && !root.hidden) syncNotice();
    }
  };
  const loadQQ = async (mine, manual2 = false) => {
    if (!knownConfig || passwordReadPending || qqStatusPending) return;
    const statusRequest = /* @__PURE__ */ Symbol();
    qqStatusPending = statusRequest;
    try {
      const state3 = await getStatusJson("/infinity/qq", 8e3);
      if (mine !== requestId || root.hidden || mutationPending) return;
      const first = !formFilled;
      currentQQ = state3;
      qqLoaded = true;
      qqFailed = false;
      if (knownConfig) qqPanel.hidden = pluginMissing;
      if (first) {
        fillQQForm(state3);
        formFilled = true;
        configPresentation(voiceGrid, "ready");
        if (uiSource(feedback).startsWith("正在读取 QQ 配置") || uiSource(feedback).startsWith("QQ 状态读取失败")) feedback.textContent = "";
      }
      drawQQ(state3);
      if (manual2) terminalView.update(state3.snowluma.log, true);
    } catch (error) {
      if (error instanceof Error && error.name === "StaleStatusError") return;
      if (mine !== requestId || root.hidden) return;
      qqFailed = true;
      terminalView.unavailable();
      environmentView.unavailable();
      if (currentQQ === null) {
        renderWaitingHealth(true);
        if (!formFilled) {
          configPresentation(voiceGrid, "error");
          showFeedback(corePhrase("QQ 状态读取失败，正在自动重试；数据就绪前不能保存。"));
        }
      }
    } finally {
      if (qqStatusPending === statusRequest) qqStatusPending = void 0;
      if (mine === requestId && !root.hidden) syncNotice();
    }
  };
  const load = async () => {
    const mine = ++requestId;
    if (knownConfig) {
      qqPanel.hidden = false;
      lockCommands();
      renderWaitingHealth(false);
      syncNotice();
    }
    coreSetUIText(message, "textContent", knownConfig ? corePhrase("正在读取插件状态…") : corePhrase("正在读取插件列表…"));
    void loadPlugins(mine);
    void loadQQ(mine);
  };
  const pollLog = (manual2 = false) => {
    if (mutationPending || root.hidden || !manual2 && document.visibilityState === "hidden") return;
    const mine = requestId;
    void loadPlugins(mine);
    void loadQQ(mine, manual2);
  };
  checkConnection.addEventListener("click", () => {
    if (checkConnection.disabled || checkPending || mutationPending || currentQQ === null) return;
    const mine = requestId;
    checkPending = true;
    checkConnection.disabled = true;
    coreSetUIText(checkResult, "textContent", corePhrase("正在检测已保存的接入设置…"));
    void (async () => {
      try {
        const response = await fetch("/infinity/qq/check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        const answer = await response.json();
        if (mine === requestId && !root.hidden) {
          coreSetUIText(checkResult, "textContent", response.ok && answer.message ? answer.message : corePhrase("检测暂不可用。"));
        }
      } catch {
        if (mine === requestId && !root.hidden) {
          coreSetUIText(checkResult, "textContent", corePhrase("检测请求失败，请稍后重试。"));
        }
      } finally {
        checkPending = false;
        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
        else checkConnection.disabled = false;
      }
    })();
  });
  importConnection.addEventListener("click", async () => {
    if (importConnection.disabled || importPending || mutationPending || currentQQ === null || root.hidden) return;
    if (!await confirmAction(
      "同步协议配置",
      `读取 QQ ${currentQQ.snowluma.recovery?.targetUin || "（未选择）"} 的 HTTP／WebSocket 地址和令牌，保存到当前 Bot。会话名单与语音设置保留；已启用的 QQ 插件将重载。`,
      "确认同步"
    )) return;
    if (root.hidden || mutationPending || currentQQ === null) return;
    const mine = requestId;
    importPending = true;
    mutationPending = true;
    lockCommands();
    coreSetUIText(checkResult, "textContent", corePhrase("正在读取目标账号配置…"));
    try {
      const response = await fetch("/infinity/qq/import-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const answer = await response.json();
      if (mine !== requestId || root.hidden) return;
      checkResult.textContent = response.ok ? answer.message || "导入完成。" : answer.said || "导入失败，原配置已保留。";
      invalidateStatuses(["/infinity/qq", "/plugins"]);
      qqStatusPending = void 0;
      window.dispatchEvent(new Event("plugins-changed"));
    } catch {
      if (mine === requestId && !root.hidden) {
        coreSetUIText(checkResult, "textContent", corePhrase("导入结果未确认，请检测接入后再操作。"));
      }
    } finally {
      importPending = false;
      mutationPending = false;
      if (mine === requestId && !root.hidden) {
        if (currentQQ) updateLive(currentQQ);
        void loadPlugins(mine);
        void loadQQ(mine);
      }
    }
  });
  const openModelFolder = async (kind, model, button4) => {
    if (button4.disabled || currentQQ === null) return;
    const mine = requestId;
    button4.disabled = true;
    showFeedback(corePhrase("正在打开模型文件夹…"));
    try {
      const response = await fetch("/infinity/qq/model-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, model })
      });
      const answer = await response.json();
      if (!response.ok || !answer.ok) {
        throw new Error(answer.said || `打开失败（${response.status}）`);
      }
      if (mine === requestId && !root.hidden) {
        showFeedback([corePhrase("已打开 "), kind.toUpperCase(), corePhrase(" 模型文件夹。")]);
      }
    } catch (error) {
      if (mine === requestId && !root.hidden) {
        showFeedback(error instanceof Error ? error.message : corePhrase("无法打开模型文件夹。"));
      }
    } finally {
      if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
    }
  };
  ttsVoiceFolder.addEventListener("click", () => {
    if (ttsVoiceFolder.disabled || mutationPending || !currentQQ?.tts.voiceFolderAvailable || root.hidden) return;
    const mine = requestId;
    ttsVoiceFolder.disabled = true;
    showFeedback(corePhrase("正在打开音色文件夹…"));
    void (async () => {
      try {
        const response = await fetch("/infinity/qq/voice-folder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        const answer = await response.json();
        if (!response.ok || !answer.ok) throw new Error(answer.said || `打开失败（${response.status}）`);
        if (mine === requestId && !root.hidden) showFeedback(corePhrase("已打开本机音色目录（references）。"));
      } catch (error) {
        if (mine === requestId && !root.hidden) {
          showFeedback(error instanceof Error ? error.message : corePhrase("无法打开音色文件夹。"));
        }
      } finally {
        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
      }
    })();
  });
  ttsFolder.addEventListener("click", () => {
    void openModelFolder("tts", ttsModel.value, ttsFolder);
  });
  for (const kind of ["tts", "asr"]) {
    const isTts = kind === "tts";
    const button4 = isTts ? ttsDownload : asrDownload;
    const setPending = (value) => {
      if (isTts) ttsDownloadPending = value;
      else asrDownloadPending = value;
    };
    for (const action of ["pause", "cancel"]) {
      const control = transfer[kind][action];
      control.addEventListener("click", async () => {
        if (control.disabled || !currentQQ) return;
        const mine = requestId;
        if (action === "cancel" && !await confirmAction("取消模型下载", "将停止本次安装并清理未完成的模型下载。已安装的模型和依赖会保留。", "取消下载")) return;
        if (mine !== requestId || root.hidden || !currentQQ) return;
        setPending(true);
        updateLive(currentQQ);
        try {
          const response = await fetch(`/infinity/qq/${kind}-install`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action })
          });
          const install = await response.json();
          if (!response.ok || install.ok === false) throw new Error(install.said || "下载操作失败");
          if (mine === requestId && !root.hidden && currentQQ) {
            currentQQ = { ...currentQQ, [kind]: { ...currentQQ[kind], install } };
            invalidateStatus("/infinity/qq");
            void loadQQ(mine);
          }
        } catch (error) {
          if (mine === requestId && !root.hidden) showFeedback(error instanceof Error ? error.message : "下载操作失败");
        } finally {
          setPending(false);
          if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
        }
      });
    }
    button4.addEventListener("click", async () => {
      if (button4.disabled || !currentQQ) return;
      const mine = requestId;
      setPending(true);
      button4.disabled = true;
      try {
        const source = await chooseASRSource(isTts ? "TTS" : "ASR", currentQQ[kind].install?.source);
        if (!source || mine !== requestId || root.hidden) return;
        downloadErrors[kind] = "";
        updateLive(currentQQ);
        const response = await fetch(`/infinity/qq/${kind}-install`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source })
        });
        const install = await response.json();
        if (!response.ok || install.ok === false) throw new Error(install.said || "未能开始下载，请重试。");
        if (mine !== requestId || root.hidden || !currentQQ) return;
        currentQQ = { ...currentQQ, [kind]: { ...currentQQ[kind], install } };
        invalidateStatus("/infinity/qq");
        void loadQQ(mine);
      } catch (error) {
        if (mine === requestId && !root.hidden) {
          downloadErrors[kind] = error instanceof Error ? error.message : "未能开始下载，请重试。";
        }
      } finally {
        setPending(false);
        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
      }
    });
  }
  asrFolder.addEventListener("click", () => {
    void openModelFolder("asr", asrModel.value, asrFolder);
  });
  const voicePayload = () => ({
    asrAutoStart: asrAuto.checked,
    ttsAutoStart: ttsAuto.checked,
    ttsPort: Number(servicePort.value),
    ttsProvider: ttsProvider.value,
    ttsService: serviceName.value.trim(),
    ...currentQQ?.tts.voices ? { ttsVoice: ttsVoice.value } : {},
    ...currentQQ?.tts.sendMode ? { ttsSendMode: ttsSendMode.value } : {},
    ...currentQQ?.asr.language ? { asrLanguage: asrLanguage.value } : {},
    ...currentQQ?.asr.useItn !== void 0 ? { asrUseItn: asrItn.checked } : {},
    ...currentQQ?.asr.threads !== void 0 ? { asrThreads: Number(asrThreads.value) } : {}
  });
  ttsPreview.addEventListener("click", () => {
    if (ttsPreview.disabled || previewPending || mutationPending || currentQQ === null || root.hidden) return;
    const previewText = ttsPreviewText.value.trim();
    if (!previewText || previewText.length > 200) {
      showFeedback(corePhrase("请输入 1—200 字试听文字。"));
      return;
    }
    const mine = requestId;
    previewPending = true;
    const controller = new AbortController();
    previewController = controller;
    ttsPreview.disabled = true;
    showFeedback(corePhrase("正在合成试听音频…"), false);
    void (async () => {
      try {
        const response = await fetch("/infinity/qq/tts-preview", {
          signal: controller.signal,
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model: ttsModel.value,
            voice: ttsVoice.value,
            text: previewText
          })
        });
        const result = await response.json();
        if (!response.ok || !result.ok || !result.audio) {
          throw new Error(result.said || "试听失败。");
        }
        if (mine !== requestId || root.hidden) return;
        ttsAudio.pause();
        ttsAudio.src = `data:audio/wav;base64,${result.audio}`;
        ttsAudio.hidden = false;
        showFeedback(corePhrase("试听已生成；可在卡片内播放。"));
        void ttsAudio.play().catch(() => {
        });
      } catch (error) {
        if (mine === requestId && !root.hidden) {
          showFeedback(error instanceof Error ? error.message : corePhrase("试听失败。"));
        }
      } finally {
        if (previewController === controller) {
          previewController = void 0;
          previewPending = false;
        }
        if (mine === requestId && !root.hidden && currentQQ) updateLive(currentQQ);
      }
    })();
  });
  const send = async (route2, body, before) => {
    if (mutationPending || currentQQ === null || root.hidden) {
      if (currentQQ === null && !root.hidden) {
        showFeedback(corePhrase("正在读取配置，请稍候再操作。"));
      }
      return;
    }
    const mine = requestId;
    const serviceAction = route2 === "/infinity/qq/process" && "action" in body && (body.action === "snowluma_start_service" || body.action === "snowluma_stop");
    if (serviceAction) serviceUtilities.after(feedback);
    else qqPanel.append(feedback);
    let prepared = false;
    mutationPending = true;
    showFeedback(corePhrase("正在处理…"), false);
    lockCommands();
    try {
      for (const step of [...before ? [before] : [], { route: route2, body }]) {
        const response = await fetch(step.route, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(step.body)
        });
        const answer = await response.json();
        if (mine !== requestId || root.hidden) return;
        if (!response.ok || answer.ok === false) throw new Error(answer.said || `请求失败（${response.status}）`);
        currentQQ = answer;
        if (step === before) prepared = true;
      }
      drawQQ(currentQQ);
      const action = route2 === "/infinity/qq/process" && "action" in body ? body.action : void 0;
      if (action === "tts_start") {
        clearFeedback();
      } else if (action === "snowluma_start_service") {
        showFeedback(corePhrase("SnowLuma 已启动。管理页就绪后，在那里选择并加载 QQ。"));
      } else if (action === "snowluma_recover") {
        showFeedback(corePhrase("正在核对 QQ 进程，请查看检测结果。"));
      } else if (typeof action === "string" && action.startsWith("snowluma_confirm_")) {
        showFeedback(corePhrase("加载确认已提交；正在核验目标进程与实际账号。"));
      } else if (route2 === "/infinity/qq/routes" && currentQQ.plugin?.configured !== true) {
        showFeedback(corePhrase("会话路由已保存。请在 SnowLuma 管理台接入 QQ，再同步协议配置。"));
      } else {
        showFeedback(corePhrase("已更新。"));
      }
    } catch (error) {
      if (mine !== requestId || root.hidden) return;
      showFeedback([error instanceof Error ? error.message : corePhrase("操作失败。"), prepared ? corePhrase("；语音设置已保存，后续动作未确认。请刷新检查后再决定；未自动回滚或重试。") : corePhrase("")], !serviceAction);
      if (currentQQ) drawQQ(currentQQ);
    } finally {
      if (mine === requestId) {
        invalidateStatuses(["/infinity/qq", "/plugins"]);
        qqStatusPending = void 0;
        mutationPending = false;
        if (!root.hidden && currentQQ) {
          updateLive(currentQQ);
        }
      }
    }
    if (mine === requestId && !root.hidden) {
      void loadPlugins(mine);
      void loadQQ(mine);
    }
  };
  const voiceSetupReady = (kind) => {
    if (!currentQQ) return false;
    if (kind === "tts" && ttsProvider.value === "external") return true;
    const setup = kind === "asr" ? asrSetup : ttsSetup;
    const saved = currentQQ[kind];
    const matches = kind === "asr" ? saved.model === asrModel.value : saved.model === ttsModel.value && currentQQ.tts.voice === ttsVoice.value;
    const installedSelection = saved.install?.runtimeReady === true && !saved.runtimeIssue && (kind === "asr" ? asrModel.value === "SenseVoiceSmall-onnx" : ttsModel.value === "Fun-CosyVoice3-0.5B-2512" && ttsVoice.value === "default");
    if (saved.runtimeIssue === void 0 || matches && saved.setupReady && !saved.runtimeIssue || installedSelection || setup.ready()) return true;
    setup.require();
    return false;
  };
  asrSwitch.addEventListener("change", () => {
    if (asrSwitch.checked) voiceSetupReady("asr");
  });
  ttsSwitch.addEventListener("change", () => {
    if (ttsSwitch.checked) voiceSetupReady("tts");
  });
  save.addEventListener("click", () => {
    if (save.disabled || mutationPending || currentQQ === null) return;
    if (asrSwitch.checked && !voiceSetupReady("asr")) return;
    if (ttsSwitch.checked && !voiceSetupReady("tts")) return;
    void send("/infinity/qq/settings", {
      asrEnabled: asrSwitch.checked,
      asrModel: asrModel.value,
      ttsEnabled: ttsSwitch.checked,
      ttsModel: ttsModel.value,
      ...voicePayload()
    });
  });
  saveRoutes.addEventListener("click", () => {
    if (saveRoutes.disabled || mutationPending || currentQQ === null) return;
    const ids = (value) => value.split(/[\s,，;；]+/).filter(Boolean);
    const privates = ids(privateIds.value);
    const target = proactiveTarget.value.trim();
    if (!proactiveTarget.readOnly && target && target === currentQQ?.routing.proactiveTarget && currentQQ.routing.privates.includes(target) && !privates.includes(target)) {
      proactiveTarget.value = "";
      showFeedback(corePhrase("主动目标已移出私聊名单，已取消主动投递；正在保存。"));
    }
    void send("/infinity/qq/routes", {
      privates,
      groups: ids(groupIds.value),
      proactiveTarget: currentQQ.routing.ownerRoute && currentQQ.routing.ownerRoute.accounts.length <= 1 ? "" : proactiveTarget.value.trim(),
      historyBackfillMinutes: Number(backfillMinutes.value),
      reactionWindowSeconds: Number(reactionWindow.value)
    });
  });
  asrProcess.addEventListener("click", () => {
    if (asrProcess.disabled || mutationPending || !currentQQ) return;
    if (currentQQ.asr.running) {
      void send("/infinity/qq/process", { action: "asr_stop" });
    } else if (voiceSetupReady("asr")) {
      void send("/infinity/qq/process", { action: "asr_start" }, {
        route: "/infinity/qq/settings",
        body: {
          asrEnabled: true,
          asrModel: asrModel.value,
          ttsEnabled: ttsSwitch.checked,
          ttsModel: ttsModel.value,
          ...voicePayload()
        }
      });
    }
  });
  ttsProcess.addEventListener("click", () => {
    if (ttsProcess.disabled || mutationPending || currentQQ === null) return;
    if (currentQQ.tts.managed || currentQQ.tts.running) {
      void send("/infinity/qq/process", { action: "tts_stop" });
      return;
    }
    if (!ttsModel.value && ttsProvider.value !== "external") {
      showFeedback(corePhrase("请先选择 TTS 合成模型，再点击启动。"));
      return;
    }
    if (!voiceSetupReady("tts") || asrSwitch.checked && !voiceSetupReady("asr")) return;
    void send("/infinity/qq/process", { action: "tts_start" }, {
      route: "/infinity/qq/settings",
      body: {
        asrEnabled: asrSwitch.checked,
        asrModel: asrModel.value,
        ttsEnabled: true,
        ttsModel: ttsModel.value,
        ...voicePayload()
      }
    });
  });
  snowManager.addEventListener("click", (event) => {
    if (snowManager.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      showFeedback(corePhrase("请先启动 SnowLuma，再打开管理页。"));
    }
  });
  snowProcess.addEventListener("click", async () => {
    if (snowProcess.disabled || mutationPending || currentQQ === null || root.hidden) return;
    const managed = currentQQ.snowluma.managed;
    const canStopInstallation = currentQQ.snowluma.canStopInstallation;
    const target = currentQQ.snowluma.recovery?.targetUin;
    const unchanged = () => !root.hidden && !mutationPending && currentQQ !== null && currentQQ.snowluma.managed === managed && currentQQ.snowluma.canStopInstallation === canStopInstallation && currentQQ.snowluma.recovery?.targetUin === target;
    if (managed) {
      if (!await confirmAction(
        "停止 SnowLuma 服务",
        currentQQ.snowluma.isolatedInstance ? "当前 Bot 的 QQ 接入将断开。" : "使用这个协议服务的 QQ 接入将断开。",
        "停止服务"
      )) return;
      if (!unchanged()) return;
    }
    if (!managed && canStopInstallation) {
      if (!await confirmAction(
        "停止 SnowLuma",
        "将停止当前项目安装目录的协议进程，QQ 连接会断开，但 QQ 程序不会关闭。",
        "确认停止"
      )) {
        return;
      }
      if (!unchanged()) return;
      void send("/infinity/qq/process", { action: "snowluma_stop_installation" });
      return;
    }
    void send("/infinity/qq/process", {
      action: managed ? "snowluma_stop" : "snowluma_start_service"
    });
  });
  const recoveryPayload = () => ({
    qqPath: currentQQ?.snowluma.recovery?.qqPath || "",
    username: snowUsername.value.trim(),
    targetUin: snowTarget.value.trim()
  });
  snowSave.addEventListener("click", () => {
    if (snowSave.disabled || mutationPending || currentQQ === null) return;
    void send("/infinity/qq/recovery", {
      ...recoveryPayload(),
      password: snowPassword.value
    }).finally(() => {
      snowPassword.value = "";
    });
  });
  snowForget.addEventListener("click", async () => {
    if (snowForget.disabled || mutationPending || currentQQ === null) return;
    const recovery = currentQQ.snowluma.recovery;
    if (!recovery || !await confirmAction(
      "移除 Core 保存的管理密码",
      "同时清除本 Bot 的 SnowLuma 管理登录会话。SnowLuma 管理页仍可使用现有密码。下次从 Core 同步协议配置前，需要重新保存管理密码。",
      "移除密码"
    )) return;
    if (root.hidden || mutationPending || currentQQ?.snowluma.recovery?.targetUin !== recovery.targetUin || currentQQ?.snowluma.recovery?.username !== recovery.username) return;
    await send("/infinity/qq/recovery", {
      qqPath: recovery.qqPath,
      username: recovery.username,
      targetUin: recovery.targetUin,
      clearCredentials: true
    });
  });
  return {
    root,
    show() {
      corpusPanel.start();
      root.hidden = false;
      if (!terminalPane.hidden) terminalView.activate();
      if (!environmentPane.hidden && knownConfig) void environmentView.open();
      if (!toolbox.root.hidden && knownConfig) void toolbox.refresh();
      void load();
      if (pollTimer !== void 0) window.clearInterval(pollTimer);
      if (typeof window.setInterval === "function") {
        pollTimer = window.setInterval(() => {
          pollLog();
        }, 1e3);
      }
    },
    stop() {
      corpusPanel.stop();
      previewController?.abort();
      previewController = void 0;
      previewPending = false;
      ttsAudio.pause();
      ttsAudio.removeAttribute("src");
      ttsAudio.load();
      ttsAudio.hidden = true;
      requestId += 1;
      terminalView.deactivate();
      clearFeedback();
      clearInitialPassword();
      if (pollTimer !== void 0) window.clearInterval(pollTimer);
      pollTimer = void 0;
      for (const audio of root.querySelectorAll(".voice-corpus audio")) {
        audio.pause();
      }
      root.hidden = true;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-page-bridge.ts
function pluginPageBridge(options) {
  let busy = false;
  return (event) => {
    if (!options.active() || event.source !== options.frame.contentWindow) return;
    const request2 = event.data;
    if (!request2 || typeof request2 !== "object") return;
    if (request2.type === "telyincore:page:overview") {
      location.hash = "#/infinity";
      return;
    }
    if (request2.type !== "telyincore:page:request") return;
    const state3 = options.current();
    if (!state3 || typeof request2.id !== "string" || !/^[0-9]{1,12}$/.test(request2.id)) return;
    const frame = options.frame.contentWindow;
    const epoch = options.epoch();
    const live2 = () => options.active() && options.current() === state3 && options.epoch() === epoch && options.frame.contentWindow === frame;
    const reply = (value) => {
      if (live2()) frame?.postMessage({ type: "telyincore:page:result", id: request2.id, ...value }, "*");
    };
    const rule = state3.pageActions && Object.hasOwn(state3.pageActions, request2.action) ? state3.pageActions[request2.action] : null;
    if (!rule || request2.revision !== state3.revision || !request2.payload || typeof request2.payload !== "object" || Array.isArray(request2.payload) || busy) {
      reply({ ok: false, message: "页面请求无效、配置已变化或已有操作进行中；未执行新操作。" });
      return;
    }
    busy = true;
    void (async () => {
      try {
        if (rule.mutates) {
          const accepted = await confirmAction(rule.label, "", "确认");
          if (!live2()) return;
          if (!accepted) {
            reply({ ok: false, message: "已取消。" });
            return;
          }
        }
        const response = await pageFetch("/infinity/plugin-page/" + encodeURIComponent(options.name), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: request2.action,
            payload: request2.payload,
            revision: state3.revision,
            confirmed: rule.mutates
          })
        });
        const result = await response.json();
        if (!response.ok) throw new Error("页面操作失败，结果未确认；请刷新核对，不要自动重试。");
        reply({ ok: true, result });
      } catch {
        reply({ ok: false, message: "页面操作失败，结果未确认；请刷新核对，不要自动重试。" });
      } finally {
        busy = false;
      }
    })();
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/page-load.ts
function pageLoad(root, label, retry) {
  const note = document.createElement("p");
  note.className = "infinity-hint page-load-status";
  note.hidden = true;
  let hasContent = false;
  note.setAttribute("role", "status");
  const button4 = document.createElement("button");
  button4.type = "button";
  button4.className = "infinity-text-link";
  coreSetUIText(button4, "textContent", corePhrase("重试"));
  button4.hidden = true;
  note.append(button4);
  root.append(note);
  const show = (text2, failed) => {
    note.textContent = text2;
    button4.hidden = !failed;
    note.append(button4);
    note.hidden = false;
  };
  button4.addEventListener("click", retry);
  return {
    start() {
      if (!hasContent) show(`正在加载${label}…`, false);
    },
    ready() {
      hasContent = true;
      note.hidden = true;
    },
    fail() {
      show(hasContent ? `${label}刷新失败，当前显示上次内容。` : `${label}加载失败，请重试。`, true);
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-config-page.ts
function text(tag, className, value = "") {
  const node6 = document.createElement(tag);
  node6.className = className;
  node6.textContent = value;
  return node6;
}
function pluginConfigPage(shell2, name) {
  const root = text("section", "page-infinity page-world page-plugin-config");
  const title = text("h1", "", "插件配置");
  const info = text(
    "p",
    "infinity-hint",
    "配置按当前 Bot 独立保存；保存会重载已启用的插件，停用的插件不会自动启动。"
  );
  const tabs = text("div", "infinity-tabs");
  const form = text("div", "generic-plugin-form");
  const custom = document.createElement("iframe");
  custom.className = "plugin-config-frame";
  coreSetUIText(custom, "title", corePhrase("插件自定义配置页"));
  custom.setAttribute("sandbox", "allow-scripts");
  custom.referrerPolicy = "no-referrer";
  custom.hidden = true;
  const message = text("p", "infinity-message");
  message.setAttribute("role", "status");
  const save = text("button", "infinity-action", "保存配置");
  save.type = "button";
  save.disabled = true;
  const reload = text("button", "infinity-action", "重新读取配置");
  reload.type = "button";
  const status = text("p", "infinity-hint");
  const url = "/infinity/plugin-config/" + encodeURIComponent(name);
  let state3 = null;
  let busy = false;
  let generation = 0;
  let dirty = false;
  let editVersion = 0;
  let unprotect;
  let customOpened = false;
  let integrated = false;
  let tabEdits = 0;
  let values = [];
  const draw = (answer) => {
    integrated = false;
    tabs.hidden = false;
    reload.hidden = false;
    status.hidden = false;
    custom.style.height = "";
    state3 = answer;
    dirty = false;
    values = [];
    form.replaceChildren();
    tabs.replaceChildren();
    coreSetUIText(title, "textContent", [corePhrase(""), answer.displayName, corePhrase(" · 配置")]);
    for (const field of answer.fields) {
      const row2 = text("label", "generic-plugin-field");
      row2.append(text("span", "", field.label));
      row2.append(text("small", "infinity-hint", field.path.join(" → ")));
      let input2;
      if (field.enum && !field.secret) {
        input2 = document.createElement("select");
        input2.append(...field.enum.map((value) => new Option(String(value), JSON.stringify(value))));
        input2.value = JSON.stringify(field.value);
      } else if (["array", "object", "null"].includes(field.type)) {
        input2 = document.createElement("textarea");
        input2.rows = 4;
        input2.value = field.secret ? "" : JSON.stringify(field.value, null, 2);
      } else {
        input2 = document.createElement("input");
        input2.type = field.type === "boolean" ? "checkbox" : field.secret ? "password" : ["integer", "number"].includes(field.type) ? "number" : "text";
        input2.autocomplete = "off";
        if (field.type === "boolean") input2.checked = field.value === true;
        else if (!field.secret) input2.value = field.value == null ? "" : String(field.value);
        if (input2.type === "number") {
          input2.step = field.type === "integer" ? "1" : "any";
          if (field.minimum !== void 0) input2.min = String(field.minimum);
          if (field.maximum !== void 0) input2.max = String(field.maximum);
        }
      }
      input2.disabled = !!field.readOnly;
      input2.setAttribute("aria-label", field.label);
      if (field.secret && !(input2 instanceof HTMLSelectElement)) {
        coreSetUIText(input2, "placeholder", field.hasValue ? corePhrase("已设置；留空保持原值") : corePhrase("未设置"));
      }
      row2.append(input2);
      const clear = document.createElement("input");
      clear.type = "checkbox";
      if (field.secret) {
        const clearLabel = text("span", "generic-plugin-clear");
        clearLabel.append(clear, text("span", "", "清空此字段"));
        row2.append(clearLabel);
      }
      if (field.description) row2.append(text("small", "infinity-hint", field.description));
      let touched = false;
      const mark = () => {
        editVersion++;
        dirty = true;
        touched = true;
        coreSetUIText(status, "textContent", corePhrase("有未保存的修改"));
      };
      input2.addEventListener("input", mark);
      input2.addEventListener("change", mark);
      clear.addEventListener("change", mark);
      values.push(() => {
        if (!touched || field.readOnly || field.secret && !clear.checked && !input2.value) {
          return null;
        }
        let value;
        if (clear.checked) value = field.type === "array" ? [] : field.type === "object" ? {} : "";
        else if (field.enum && !field.secret) value = JSON.parse(input2.value);
        else if (field.type === "boolean") value = input2.checked;
        else if (["integer", "number"].includes(field.type)) {
          if (!input2.value.trim()) throw new Error(`${field.label} 需要填写数值`);
          value = Number(input2.value);
          if (!Number.isFinite(value)) throw new Error(`${field.label} 数值无效`);
        } else if (["array", "object", "null"].includes(field.type)) {
          value = JSON.parse(input2.value);
        } else value = input2.value;
        return { path: field.path, value };
      });
      form.append(row2);
    }
    coreSetUIText(message, "textContent", answer.fields.length ? corePhrase("") : corePhrase("此插件未声明配置字段，也没有 config.json 或 config.example.json；请由作者提供声明。"));
    save.disabled = answer.fields.length === 0;
    custom.hidden = true;
    form.hidden = false;
    save.hidden = false;
    if (answer.page) {
      const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'";
      custom.srcdoc = `<meta http-equiv="Content-Security-Policy" content="${policy}">` + answer.page;
      for (const [label, isCustom] of [["常规配置", false], ["插件页面", true]]) {
        const tab = text("button", "infinity-tab", label);
        tab.type = "button";
        tab.setAttribute("aria-selected", String(!isCustom));
        tab.addEventListener("click", () => {
          if (integrated) return;
          tabEdits++;
          writePluginTab(name, isCustom ? 1 : 0);
          if (isCustom) customOpened = true;
          form.hidden = isCustom;
          custom.hidden = !isCustom;
          save.hidden = isCustom;
          for (const item of tabs.children) item.setAttribute("aria-selected", "false");
          tab.setAttribute("aria-selected", "true");
          if (isCustom && state3) {
            const { page: _, ...publicState } = state3;
            custom.contentWindow?.postMessage({ type: "telyincore:config:state", config: publicState, toolCatalog: true, displayCache: displayCacheCapability, active: true }, "*");
          }
        });
        tabs.append(tab);
      }
      const tabVersion = tabEdits;
      void readyPluginDisplay().then(() => {
        if (!integrated && !root.hidden && !dirty && state3 === answer && tabVersion === tabEdits && readPluginTab(name) === 1)
          tabs.children[1].click();
      });
    }
  };
  const readHint = pageLoad(root, "插件配置", () => {
    void load();
  });
  const load = async () => {
    if (busy || dirty) return;
    readHint.start();
    const mine = ++generation;
    try {
      const response = await pageFetch(url, { cache: "no-store" });
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.said || "无法读取插件配置");
      if (mine === generation && !root.hidden && !dirty) {
        draw(answer);
        status.textContent = "";
        readHint.ready();
      }
    } catch (error) {
      if (mine !== generation || root.hidden) return;
      if (error instanceof StaleStatusError) {
        void load();
        return;
      }
      readHint.fail();
    }
  };
  const persist = async (changes, revision) => {
    if (busy) throw new Error("正在保存，请稍候");
    const edits = editVersion;
    const mine = ++generation;
    busy = true;
    save.disabled = reload.disabled = true;
    try {
      const response = await pageFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes, revision })
      });
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.said || "保存失败");
      if (mine !== generation || root.hidden) return;
      if (edits !== editVersion) {
        state3 = answer.config;
        coreSetUIText(status, "textContent", corePhrase("本次保存已完成，后续修改仍未保存。"));
      } else {
        if (custom.hidden) draw(answer.config);
        else {
          state3 = answer.config;
          dirty = false;
        }
        status.textContent = answer.message;
      }
      window.dispatchEvent(new Event("plugins-changed"));
    } finally {
      busy = false;
      reload.disabled = false;
      save.disabled = !state3?.fields.length;
    }
  };
  save.addEventListener("click", () => {
    if (!state3 || busy) return;
    try {
      const changes = values.map((get2) => get2()).filter((one) => one !== null);
      if (!changes.length) {
        coreSetUIText(status, "textContent", corePhrase("没有需要保存的修改"));
        return;
      }
      void persist(changes, state3.revision).catch((error) => {
        status.textContent = String(error);
      });
    } catch (error) {
      status.textContent = String(error);
    }
  });
  reload.addEventListener("click", () => {
    if (dirty) {
      coreSetUIText(status, "textContent", corePhrase("有未保存的修改；请先保存，或离开本页后重新进入。"));
      return;
    }
    void load();
  });
  const operatorBridge = pluginPageBridge({
    name,
    frame: custom,
    current: () => state3,
    active: () => !root.hidden && !custom.hidden,
    epoch: () => generation
  });
  const displayBridge = pluginDisplayBridge({
    name,
    frame: custom,
    active: () => !root.hidden && !custom.hidden,
    epoch: () => generation
  });
  const bridge = (event) => {
    displayBridge(event);
    operatorBridge(event);
    if (event.source !== custom.contentWindow || !state3 || root.hidden) return;
    const request2 = event.data;
    if (!request2 || typeof request2 !== "object") return;
    const reply = (payload) => custom.contentWindow?.postMessage(payload, "*");
    if (request2.type === "telyincore:config:layout" && request2.mode === "integrated" && state3.page) {
      integrated = true;
      customOpened = true;
      tabs.hidden = form.hidden = save.hidden = reload.hidden = status.hidden = true;
      custom.hidden = false;
      custom.style.height = "max(620px, calc(100dvh - 250px))";
      const { page: _, ...publicState } = state3;
      reply({
        type: "telyincore:config:state",
        config: publicState,
        active: true,
        toolCatalog: true,
        displayCache: displayCacheCapability
      });
    }
    if (request2.type === "telyincore:config:dirty" && integrated && typeof request2.dirty === "boolean") {
      dirty = request2.dirty;
      editVersion++;
    }
    if (request2.type === "telyincore:tools:get" && !custom.hidden && Number.isSafeInteger(request2.id)) {
      const epoch = generation;
      const send = (catalog) => {
        if (epoch === generation && !root.hidden && !custom.hidden)
          reply({ type: "telyincore:tools:result", id: request2.id, catalog });
      };
      void getStatusJson("/infinity/plugin-tools/" + encodeURIComponent(name)).then(send, () => send(null));
      return;
    }
    if (request2.type === "telyincore:config:get") {
      const { page: _, ...publicState } = state3;
      reply({ type: "telyincore:config:state", config: publicState, toolCatalog: true, displayCache: displayCacheCapability, active: !custom.hidden });
    }
    if (request2.type === "telyincore:config:save" && !custom.hidden && Array.isArray(request2.changes)) {
      void persist(request2.changes, request2.revision).then(() => {
        reply({ type: "telyincore:config:saved", ok: true });
      }).catch((error) => {
        reply({ type: "telyincore:config:saved", ok: false, message: String(error) });
      });
    }
  };
  const header = text("header", "plugin-config-head");
  header.append(title, pluginUpdateNotice(name), info, tabs, message);
  const content = text("div", "plugin-config-content");
  content.append(form, custom);
  const actions = text("footer", "plugin-config-actions");
  actions.append(save, reload, status);
  root.append(header, content, actions);
  shell2.content.replaceChildren(root);
  return { root, show() {
    root.hidden = false;
    unprotect ??= protectPluginDisplay(name, () => dirty || busy || customOpened);
    window.addEventListener("message", bridge);
    void load();
  }, stop() {
    unprotect?.();
    unprotect = void 0;
    root.hidden = true;
    generation++;
    window.removeEventListener("message", bridge);
  } };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-ui-registry.ts
var legacyParents = /* @__PURE__ */ new Map([
  ["infinity-comfyui", "core-infinity-qq"],
  ["core-infinity-wavesuid", "core-infinity-qq"]
]);
function navigationParent(plugin) {
  const parent = plugin.navigationParent ?? legacyParents.get(plugin.name);
  return parent ? "infinity/" + encodeURIComponent(parent) : void 0;
}
var adapters = /* @__PURE__ */ new Map();
function registerPluginUI(name, adapter) {
  if (adapters.has(name)) throw new Error(`插件页面重复注册：${name}`);
  if (!Object.hasOwn(ICONS, adapter.icon)) throw new Error(`未知图标：${adapter.icon}`);
  adapters.set(name, adapter);
}
registerPluginUI("core-infinity-qq", {
  icon: "chat",
  createPage: infinityPage,
  health: { endpoint: "/infinity/qq", signals: (data, plugin) => qqSignals(data, plugin.loaded === true) }
});
function lifecycle(plugin) {
  return [{
    state: plugin.loaded ? "on" : plugin.enabled ? "warn" : "off",
    label: plugin.loaded ? "插件运行中" : plugin.enabled ? "插件已启用但未加载" : "插件已停用"
  }];
}
function pluginPage(shell2, name) {
  return (adapters.get(name)?.createPage ?? pluginConfigPage)(shell2, name);
}
function pluginIcon(plugin) {
  return plugin.icon && Object.hasOwn(ICONS, plugin.icon) ? plugin.icon : adapters.get(plugin.name)?.icon ?? "puzzle";
}
var snapshots = /* @__PURE__ */ new Map();
var pending4 = /* @__PURE__ */ new Set();
async function refreshPluginHealth(plugins) {
  await Promise.all(plugins.map(async (plugin) => {
    const provider = adapters.get(plugin.name)?.health;
    if (!provider || pending4.has(plugin.name)) return;
    pending4.add(plugin.name);
    try {
      snapshots.set(plugin.name, await getStatusJson(provider.endpoint, 5e3));
    } catch {
      snapshots.delete(plugin.name);
    } finally {
      pending4.delete(plugin.name);
    }
  }));
}
function pluginSignals(plugin) {
  const provider = adapters.get(plugin.name)?.health;
  const value = snapshots.get(plugin.name);
  return provider && value ? provider.signals(value, plugin) : lifecycle(plugin);
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/engine.ts
async function ask(url, init) {
  try {
    const resp = await pageFetch(url, init);
    const text2 = await resp.text();
    let data = null;
    try {
      data = JSON.parse(text2);
    } catch {
      data = null;
    }
    return {
      ok: resp.ok,
      status: resp.status,
      data: resp.ok ? data : null,
      note: resp.ok ? "" : text2.trim() || `HTTP ${resp.status}`
    };
  } catch (exc) {
    return { ok: false, status: 0, data: null, note: `连不上：${String(exc)}` };
  }
}
var JSON_POST = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body)
});
function engineReport() {
  return ask("/engine");
}
async function engineCommand(asked) {
  const reply = await ask("/engine", JSON_POST(asked));
  if (reply.ok && reply.data?.code === "ok") notifyEngineChanged();
  if (reply.ok && reply.data?.code !== "ok") return {
    ...reply,
    ok: false,
    note: reply.data?.error || "保存回执无效，结果未确认"
  };
  return reply;
}
async function engineSecret(name, value, expected) {
  const reply = await ask("/engine/secret", JSON_POST({
    name,
    value,
    expected
  }));
  if (reply.ok && reply.data?.code !== "ok") return {
    ...reply,
    ok: false,
    note: reply.data?.error || "密钥写入结果未确认"
  };
  return reply;
}
function probeEngine(asked) {
  return ask("/engine/probe", JSON_POST(asked));
}
async function chatgptCommand(name, action) {
  const got = await ask("/engine/chatgpt", JSON_POST({ name, action }));
  if (!got.ok && (got.status === 404 || got.status === 501)) {
    return { ...got, note: "后端尚未加载订阅登录功能，请重启 Core 后刷新此页。" };
  }
  if (!got.ok && /<!doctype|<html|<head|<body/i.test(got.note)) {
    return { ...got, note: `登录服务返回异常（HTTP ${got.status}），请重启 Core 后重试。` };
  }
  return got;
}
async function saveEngineDraft(asked, typed) {
  const target = { ...asked.profile };
  const request2 = { ...asked, action: "save", profile: target };
  const wantsSecret = typed !== "" && target.auth !== "chatgpt" && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(
    String(target.base_url).trim()
  );
  const profile = await engineCommand(request2);
  if (!profile.ok) return { profile };
  const saved = profile.data?.profile, binding = profile.data?.secret_binding;
  if (!saved || saved.name !== target.name || saved.kind !== target.kind || (saved.auth || "api_key") !== (target.auth || "api_key") || saved.base_url !== String(target.base_url).trim() || !binding || binding.profile_name !== saved.name || binding.secret !== saved.secret || typeof binding.revision !== "string" || binding.revision.trim() === "" || wantsSecret && (typeof saved.secret !== "string" || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(saved.secret))) {
    return { profile, error: "档案保存已返回，但权威绑定回执未确认；未写入密钥，请刷新核对。" };
  }
  if (!wantsSecret) return { profile };
  const expected = {
    profile_name: binding.profile_name,
    secret: binding.secret,
    revision: binding.revision
  };
  const secret = await engineSecret(expected.secret, typed, expected);
  return { profile, secret };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/engine-controls.ts
var selects = /* @__PURE__ */ new Map();
var menus = /* @__PURE__ */ new Map();
var serial = 0;
var listening = false;
function node3(tag, cls, text2 = "") {
  const el6 = document.createElement(tag);
  el6.className = cls;
  el6.textContent = text2;
  return el6;
}
function remember(box, close2) {
  menus.set(box, close2);
  if (listening) return;
  listening = true;
  const outside = (event) => {
    for (const [root, hide] of menus) {
      if (!root.contains(event.target)) hide();
    }
  };
  document.addEventListener("pointerdown", outside);
  document.addEventListener("click", outside);
}
function resetControls() {
  for (const hide of menus.values()) hide();
  menus.clear();
  selects.clear();
}
function refreshControls() {
  for (const sync of selects.values()) sync();
}
function menuFor(anchor, items, selected, choose) {
  const menu = node3("div", "engine-menu");
  menu.id = `engine-menu-${++serial}`;
  menu.setAttribute("popover", "manual");
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  anchor.setAttribute("aria-controls", menu.id);
  anchor.setAttribute("aria-expanded", "false");
  let rows = [], active2 = -1;
  const moved = (event) => {
    if (event.target instanceof Node && menu.contains(event.target)) return;
    close2();
  };
  const close2 = () => {
    if (typeof menu.hidePopover === "function" && menu.matches(":popover-open")) menu.hidePopover();
    menu.hidden = true;
    anchor.setAttribute("aria-expanded", "false");
    anchor.removeAttribute("aria-activedescendant");
    window.removeEventListener("scroll", moved, true);
    window.removeEventListener("resize", moved);
  };
  const highlight = () => {
    Array.from(menu.children).forEach((option, index) => {
      option.classList.toggle("active", index === active2);
    });
    const current2 = menu.children[active2];
    if (current2) {
      anchor.setAttribute("aria-activedescendant", current2.id);
      current2.scrollIntoView?.({ block: "nearest" });
    }
  };
  const open = (filter = "") => {
    for (const hide of menus.values()) hide();
    const query = filter.trim().toLowerCase();
    rows = items().filter((it) => `${it.label} ${it.value}`.toLowerCase().includes(query));
    menu.replaceChildren();
    if (!rows.length) menu.append(node3("div", "engine-empty", "没有匹配项，可直接输入"));
    active2 = rows.findIndex((it) => it.value === selected() || it.field === selected());
    rows.forEach((it, index) => {
      const option = node3("div", "engine-option", it.field ?? it.label);
      option.id = `${menu.id}-${index}`;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(index === active2));
      if (it.field && it.value !== it.field) {
        option.append(node3("small", "engine-option-id", it.value));
      }
      option.addEventListener("pointermove", () => {
        active2 = index;
        highlight();
      });
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
      });
      option.addEventListener("click", () => {
        choose(it);
        close2();
      });
      menu.append(option);
    });
    const rect = anchor.getBoundingClientRect?.();
    menu.dataset.side = rect && rect.top > window.innerHeight - rect.bottom && window.innerHeight - rect.bottom < 190 ? "above" : "below";
    menu.hidden = false;
    if (rect) {
      const above = menu.dataset.side === "above";
      const room = Math.max(60, (above ? rect.top : window.innerHeight - rect.bottom) - 14);
      Object.assign(menu.style, {
        position: "fixed",
        margin: "0",
        bottom: "auto",
        left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)) + "px",
        width: Math.min(rect.width, window.innerWidth - 16) + "px",
        maxHeight: Math.min(300, room) + "px"
      });
      menu.showPopover?.();
      menu.style.top = (above ? Math.max(8, rect.top - menu.getBoundingClientRect().height - 6) : rect.bottom + 6) + "px";
    }
    anchor.setAttribute("aria-expanded", "true");
    highlight();
    window.addEventListener("scroll", moved, true);
    window.addEventListener("resize", moved);
  };
  const key2 = (event) => {
    if (event.key === "Escape" || event.key === "Tab") {
      close2();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.hidden) open();
      if (rows.length) active2 = active2 < 0 ? event.key === "ArrowDown" ? 0 : rows.length - 1 : (active2 + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
      highlight();
    } else if (event.key === "Enter" && !menu.hidden) {
      event.preventDefault();
      if (rows.length) choose(rows[active2 < 0 ? 0 : active2]);
      close2();
    }
  };
  return { menu, open, close: close2, key: key2 };
}
function combo(cell, items, onPick) {
  const box = node3("div", "engine-combo");
  cell.autocomplete = "off";
  cell.spellcheck = false;
  cell.setAttribute("autocomplete", "off");
  cell.setAttribute("autocorrect", "off");
  cell.setAttribute("autocapitalize", "off");
  cell.setAttribute("aria-autocomplete", "list");
  cell.setAttribute("role", "combobox");
  cell.setAttribute("aria-haspopup", "listbox");
  cell.setAttribute("data-lpignore", "true");
  cell.setAttribute("data-1p-ignore", "true");
  const choose = (item) => {
    cell.value = item.field ?? item.value;
    onPick?.(item);
    cell.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const list = menuFor(cell, items, () => cell.value, choose);
  const toggle = node3("button", "engine-menu-toggle");
  toggle.type = "button";
  toggle.tabIndex = -1;
  coreSetUIAttribute(toggle, "aria-label", corePhrase("展开候选"));
  toggle.addEventListener("mousedown", (event) => {
    event.preventDefault();
    if (list.menu.hidden) {
      cell.focus();
      list.open();
    } else list.close();
  });
  cell.addEventListener("focus", () => {
    list.open();
    cell.select();
  });
  cell.addEventListener("click", () => {
    if (list.menu.hidden) list.open();
  });
  cell.addEventListener("input", () => list.open(cell.value));
  cell.addEventListener("keydown", list.key);
  cell.addEventListener("blur", list.close);
  cell.addEventListener("change", list.close);
  box.append(cell, toggle, list.menu);
  remember(box, list.close);
  cell.openAll = () => list.open();
  return box;
}
function selectControl(select2, label) {
  const box = node3("div", "engine-combo engine-select");
  const trigger = node3("button", "engine-input engine-select-trigger");
  trigger.type = "button";
  trigger.id = `${select2.id}-button`;
  trigger.setAttribute("aria-label", label);
  trigger.setAttribute("aria-haspopup", "listbox");
  select2.hidden = true;
  select2.setAttribute("aria-hidden", "true");
  select2.tabIndex = -1;
  const sync = () => {
    const chosen = Array.from(select2.options).find((option) => option.value === select2.value);
    trigger.textContent = chosen?.textContent ?? "上游默认";
    trigger.disabled = select2.disabled;
    trigger.title = select2.title;
  };
  const list = menuFor(trigger, () => Array.from(select2.options).map((option) => ({
    value: option.value,
    label: option.textContent ?? option.value
  })), () => select2.value, (item) => {
    select2.value = item.value;
    sync();
    select2.dispatchEvent(new Event("change", { bubbles: true }));
  });
  trigger.addEventListener("click", () => {
    if (trigger.disabled) return;
    if (list.menu.hidden) list.open();
    else list.close();
  });
  trigger.addEventListener("keydown", list.key);
  trigger.addEventListener("blur", list.close);
  select2.addEventListener("change", sync);
  box.append(select2, trigger, list.menu);
  remember(box, list.close);
  selects.set(select2, sync);
  sync();
  return box;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/chatgpt-auth.ts
function chatgptAuth(readName, backendReady = true) {
  const root = document.createElement("div");
  root.className = "engine-chatgpt";
  const heading2 = document.createElement("div");
  heading2.className = "engine-chatgpt-heading";
  const title = document.createElement("strong");
  coreSetUIText(title, "textContent", corePhrase("ChatGPT 订阅"));
  const badge = document.createElement("span");
  badge.className = "pill";
  coreSetUIText(badge, "textContent", corePhrase("未登录"));
  heading2.append(title, badge);
  const status = document.createElement("p");
  status.className = "engine-chatgpt-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const hint = document.createElement("p");
  hint.className = "engine-chatgpt-note";
  coreSetUIText(hint, "textContent", corePhrase("使用符合条件的 ChatGPT 订阅，不需要 API Key。模型和额度以账号授权为准；此入口不会自动切换到付费 API。"));
  const actions = document.createElement("div");
  actions.className = "engine-chatgpt-actions";
  const make = (text2, cls = "btn ghost") => {
    const node6 = document.createElement("button");
    node6.type = "button";
    node6.className = cls;
    node6.textContent = text2;
    actions.append(node6);
    return node6;
  };
  const login = make("Continue with ChatGPT", "btn");
  const refresh2 = make("刷新状态");
  const cancel = make("取消登录");
  const logout = make("退出登录");
  const security = document.createElement("p");
  security.className = "engine-chatgpt-note";
  root.append(heading2, status, hint, actions, security);
  let stopped = false, epoch = 0, timer;
  let authWindow = null;
  let authName = "";
  const closeAuth = () => {
    try {
      authWindow?.close();
    } catch {
    }
    authWindow = null;
    authName = "";
  };
  let busy = false, state3 = null;
  const clear = () => {
    if (timer !== void 0) clearTimeout(timer);
    timer = void 0;
  };
  const paint4 = () => {
    coreSetUIText(badge, "textContent", state3?.pending ? corePhrase("等待授权") : state3?.connected ? corePhrase("已登录") : corePhrase("未登录"));
    badge.dataset.state = state3?.connected ? "ok" : "";
    const message = state3?.connected && state3.message.startsWith("尚未登录") ? "已从本机载入登录" : state3?.message;
    coreSetUIText(status, "textContent", !backendReady ? corePhrase("后端仍是旧版本，请重启 Core 后刷新此页。") : state3 ? [state3.account, message].filter(Boolean).join(" · ") : corePhrase("正在读取登录状态…"));
    coreSetUIText(security, "textContent", state3 ? [corePhrase("凭证仅保存在本机 · "), state3.storage, corePhrase(" · 不读取 Codex 或浏览器的登录文件")] : corePhrase(""));
    login.disabled = !backendReady || busy || !!state3?.pending;
    refresh2.disabled = !backendReady || busy;
    cancel.hidden = !state3?.pending;
    cancel.disabled = busy;
    logout.hidden = !state3?.connected;
    logout.disabled = busy;
  };
  const run = async (action, popup) => {
    if (!backendReady) {
      popup?.close();
      return;
    }
    clear();
    busy = true;
    paint4();
    const current2 = ++epoch, name = readName();
    try {
      const got = await chatgptCommand(name, action);
      if (stopped || current2 !== epoch || name !== readName()) {
        popup?.close();
        return;
      }
      if (!got.ok || !got.data) {
        popup?.close();
        status.textContent = got.note || "无法读取登录状态";
        return;
      }
      const next = { ...got.data, pending: got.data.pending || !!got.data.login_in_progress };
      const completed = next.login_completed === true;
      state3 = next;
      paint4();
      if (authWindow && authName === name && !state3.pending && (completed || action === "cancel" || action === "logout")) {
        const returned = completed && action !== "cancel" && action !== "logout";
        closeAuth();
        if (returned) {
          try {
            window.focus();
          } catch {
          }
          coreSetUIText(status, "textContent", corePhrase("已登录，可以测试连通。"));
        }
      }
      if (action === "start" && state3.authorization_url) {
        const url = new URL(state3.authorization_url);
        if (url.origin !== "https://auth.openai.com" || url.pathname !== "/api/accounts/authorize") {
          popup?.close();
          coreSetUIText(status, "textContent", corePhrase("授权地址校验失败，未打开"));
          return;
        }
        if (popup) {
          authWindow = popup;
          authName = name;
          popup.opener = null;
          popup.location.replace(url.href);
        } else {
          coreSetUIText(status, "textContent", corePhrase("浏览器阻止了授权窗口，请允许弹出窗口后重新登录。"));
          await chatgptCommand(name, "cancel");
          state3.pending = false;
        }
      }
      if (state3.pending) timer = setTimeout(() => {
        void run("status");
      }, 2e3);
    } catch {
      popup?.close();
      if (!stopped && current2 === epoch) {
        coreSetUIText(status, "textContent", corePhrase("登录操作未完成，请刷新状态后重试。"));
      }
    } finally {
      if (!stopped && current2 === epoch) {
        busy = false;
        login.disabled = !!state3?.pending;
        refresh2.disabled = false;
        cancel.disabled = false;
        logout.disabled = false;
      }
    }
  };
  login.addEventListener("click", () => {
    if (!backendReady || busy) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    void run("start", popup);
  });
  refresh2.addEventListener("click", () => {
    if (!busy) void run("status");
  });
  cancel.addEventListener("click", () => {
    if (!busy) void run("cancel");
  });
  let confirming = false;
  logout.addEventListener("click", () => {
    if (busy || confirming || stopped) return;
    confirming = true;
    const identity = readName(), revision = epoch;
    void confirmAction(
      "退出 ChatGPT 登录",
      "仅退出此模型档案，后续订阅请求将停止。其他模型和当前配置不受影响。",
      "退出登录"
    ).then((accepted) => {
      if (accepted && !stopped && !busy && identity === readName() && revision === epoch) {
        void run("logout");
      }
    }).finally(() => {
      confirming = false;
    });
  });
  paint4();
  return {
    root,
    refresh: () => {
      if (!stopped && !busy) void run("status");
    },
    stop: () => {
      stopped = true;
      epoch++;
      clear();
      closeAuth();
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/engine-providers.ts
function isPlanProvider(preset) {
  return preset.auth === "chatgpt" || /\/api\/coding\/paas\/v4\/?$/.test(preset.base_url);
}
function providerChoices(rows, plan) {
  const choices = rows.filter((row2) => isPlanProvider(row2) === plan);
  if (plan && !choices.some((row2) => row2.auth === "chatgpt")) {
    const official = rows.find((row2) => row2.name === "OpenAI");
    if (official) choices.unshift({ ...official, name: "ChatGPT-Subscription", auth: "chatgpt" });
  }
  return choices;
}
function nextProviderName(base, names) {
  const taken = new Set(names);
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base}-${n}`;
  return name;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/engine-page.ts
function readDrafts() {
  try {
    const stored = JSON.parse(readDisplaySession("engine-drafts") ?? "{}");
    return { fresh: stored.fresh, selected: stored.selected, profiles: Object.assign(/* @__PURE__ */ Object.create(null), stored.profiles) };
  } catch {
    return { profiles: /* @__PURE__ */ Object.create(null) };
  }
}
function hasEngineDraft() {
  const drafts = readDrafts();
  return !!drafts.fresh || Object.keys(drafts.profiles).length > 0;
}
var SECTIONS = ["API 设置", "模型设置", "高级"];
function el3(tag, cls, text2) {
  const one = document.createElement(tag);
  if (cls !== void 0) one.className = cls;
  if (text2 !== void 0) {
    if (typeof text2 === "string") one.textContent = text2;
    else coreSetUIText(one, "textContent", text2);
  }
  return one;
}
function button2(label, cls = "btn") {
  const one = document.createElement("button");
  one.type = "button";
  one.className = cls;
  uiLabel(one, label);
  return one;
}
var fieldId = 0;
function row(label, cell, hint, required = false) {
  const box = el3("div", "engine-row");
  const tag = el3("label", "engine-label", label);
  const control = cell.tagName === "DIV" ? cell.querySelector("input") : cell;
  if (control) {
    control.id = `engine-field-${++fieldId}`;
    control.setAttribute("aria-label", label);
    tag.setAttribute("for", cell.tagName === "SELECT" ? `${control.id}-button` : control.id);
  }
  if (required) {
    const mark = el3("span", "engine-required", "*");
    tag.append(mark);
    cell.requiredMark = mark;
  }
  box.append(tag, cell.tagName === "SELECT" ? selectControl(cell, label) : cell);
  box.append(el3("span", "engine-hint", hint ?? ""));
  return box;
}
function input(kind) {
  const one = document.createElement("input");
  one.type = kind;
  one.autocomplete = kind === "password" ? "new-password" : "off";
  one.setAttribute("autocomplete", one.autocomplete);
  one.className = "engine-input";
  return one;
}
function pick(choices) {
  const one = document.createElement("select");
  one.className = "engine-input";
  for (const choice of choices) {
    const opt = document.createElement("option");
    opt.value = choice;
    coreSetUIText(opt, "textContent", choice === "" ? corePhrase("上游默认") : choice);
    one.append(opt);
  }
  return one;
}
function enginePage(shell2) {
  const column = el3("div", "page-engine");
  const left = el3("div", "engine-left");
  const right = el3("div", "engine-right");
  const notice = el3("p", "engine-notice");
  notice.setAttribute("role", "status");
  const title = el3("h1", "page-title", corePhrase("模型 · MODEL"));
  const workspace = el3("div", "engine-workspace");
  const editor = el3("div", "engine-editor");
  const actionsHost = el3("div", "engine-page-actions");
  actionsHost.append(notice);
  editor.append(actionsHost, right);
  workspace.append(left, editor);
  column.append(title, workspace);
  shell2.content.replaceChildren(column);
  let report = null;
  let picked = null;
  let fresh = false;
  let typedKey = "";
  let editEpoch = 0;
  let saving = false;
  let captureDraft;
  const draftKeys = /* @__PURE__ */ new Map();
  const draftKey = (name) => name === void 0 ? "new" : "saved:" + name;
  const updateDraft = () => {
    editEpoch++;
    captureDraft?.();
  };
  right.addEventListener("input", updateDraft);
  right.addEventListener("change", updateDraft);
  function rememberSelection(name) {
    writeDisplaySession("engine-drafts", JSON.stringify({ ...readDrafts(), selected: name }));
  }
  function changeDraft(name, draft) {
    const drafts = readDrafts();
    if (name === void 0) drafts.fresh = draft;
    else if (draft) drafts.profiles[name] = draft;
    else delete drafts.profiles[name];
    writeDisplaySession("engine-drafts", JSON.stringify(drafts));
    if (!draft) draftKeys.delete(draftKey(name));
  }
  let stopAuth;
  let modelItems = [];
  let modelValue = "";
  let modelSpecs = [];
  let modelEffortLevels = [];
  const specOverrides = /* @__PURE__ */ new Set();
  let rememberedEffort = "";
  let cellsVision = null;
  let visionHint = null;
  const cells = {};
  function applySpecOf(id) {
    const spec = modelSpecs.find((each) => each.id === id);
    modelEffortLevels = spec?.effort_levels ?? [];
    fillEfforts();
    const window2 = cells.context_window;
    const temp = cells.temperature;
    const effort = cells.reasoning_effort;
    const maxOut = cells.max_tokens;
    if (window2 && !specOverrides.has("context_window")) {
      window2.value = "";
      coreSetUIText(window2, "placeholder", spec?.context_window ? [corePhrase("自动跟随上游（当前 "), spec.context_window, corePhrase("）")] : corePhrase("自动查询；未知时使用 128K（131072）"));
    }
    if (maxOut && spec?.max_output_tokens && !specOverrides.has("max_tokens")) {
      const ours = Number(maxOut.value || 0);
      if (ours > spec.max_output_tokens) {
        maxOut.value = String(spec.max_output_tokens);
        coreSetUIText(maxOut, "title", [corePhrase("该模型的输出上限为 "), spec.max_output_tokens, corePhrase(" token，已自动调整")]);
      }
    }
    if (temp && !specOverrides.has("temperature")) {
      temp.value = spec?.temperature != null ? String(spec.temperature) : "";
      coreSetUIText(temp, "placeholder", temp.dataset.subscription === "yes" ? corePhrase("订阅接口不支持调整") : spec?.temperature != null ? corePhrase("已配置——上游默认") : corePhrase("上游默认"));
    }
    if (effort && !specOverrides.has("reasoning_effort")) {
      effort.value = spec?.effort_default ?? "";
      rememberedEffort = effort.value;
    }
    if (effort) {
      coreSetUIText(effort, "title", rememberedEffort && !modelEffortLevels.includes(rememberedEffort) ? corePhrase("保留手动推理强度；上游尚未确认此档位，实际支持以上游为准") : corePhrase(""));
      refreshControls();
    }
    if (visionHint) {
      coreSetUIText(visionHint, "textContent", spec?.input_images === true ? corePhrase("模型支持图片输入") : spec?.input_images === false ? corePhrase("模型仅支持文本") : corePhrase("上游未报告图片能力"));
    }
    if (cellsVision) {
      const said = chosen(report)?.multimodal;
      if (said === void 0 || said === null) {
        cellsVision.dataset.on = spec?.input_images === true ? "yes" : "no";
      }
      coreSetUIText(cellsVision, "title", spec?.input_images === true ? corePhrase("服务商或模型目录声明支持图片输入") : spec?.input_images === false ? corePhrase("服务商声明仅支持文本输入") : corePhrase("尚未确认图片能力，可手动设置"));
    }
  }
  function effortLevels() {
    const withSaved = (levels) => [.../* @__PURE__ */ new Set(["", ...levels, ...rememberedEffort ? [rememberedEffort] : []])];
    if (modelEffortLevels.length > 0) {
      return withSaved(modelEffortLevels);
    }
    const declared = (currentPreset()?.efforts ?? []).filter(Boolean);
    if (declared.length > 0) {
      return withSaved(declared);
    }
    const endpoint = cells.base_url?.value ?? "";
    if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(endpoint.trim())) {
      return withSaved(["low", "medium", "high", "xhigh"]);
    }
    if (isDeepSeekEndpoint(endpoint)) {
      return withSaved(["low", "high", "max"]);
    }
    const suggests = (currentPreset()?.effort_suggestions ?? []).filter(Boolean);
    return withSaved(suggests.length > 0 ? suggests : ["low", "medium", "high"]);
  }
  function presetOf(who) {
    const want = who.trim().toLowerCase();
    const original = report?.data?.presets ?? [];
    const rows = [...original, ...providerChoices(original, true)];
    return rows.find((each) => each.name.toLowerCase() === want) ?? rows.find((each) => each.name.toLowerCase() === want.replace(/-\d+$/, ""));
  }
  function spareName() {
    const taken = new Set((report?.data?.profiles ?? []).map((each) => each.name));
    let spare = "DeepSeek";
    for (let n = 2; taken.has(spare); n += 1) {
      spare = `DeepSeek-${n}`;
    }
    return spare;
  }
  function currentPreset() {
    const name = cells.provider?.value;
    return name ? presetOf(name) : void 0;
  }
  function fillEfforts() {
    const sel = cells.reasoning_effort;
    if (!sel) {
      return;
    }
    const keep = specOverrides.has("reasoning_effort") ? sel.value : rememberedEffort;
    const levels = effortLevels();
    sel.replaceChildren();
    for (const level of levels) {
      const opt = document.createElement("option");
      opt.value = level;
      coreSetUIText(opt, "textContent", level === "" ? corePhrase("上游默认") : level);
      sel.append(opt);
    }
    sel.value = levels.includes(keep) ? keep : "";
    refreshControls();
  }
  function modelValueOf() {
    return modelValue || cells.model?.value || "";
  }
  function onClick(target, run) {
    target.addEventListener("click", () => {
      try {
        run();
      } catch (exc) {
        say(`操作失败：${short(exc, 60)}`, true);
        throw exc;
      }
    });
  }
  function short(text2, n = 42) {
    const one = String(text2 ?? "").replace(/\s+/g, " ").trim();
    return one.length > n ? `${one.slice(0, n)}…` : one;
  }
  function say(text2, bad = false) {
    notice.textContent = text2;
    notice.dataset.bad = bad ? "1" : "";
  }
  function chosen(one) {
    if (fresh) {
      return null;
    }
    const rows = one?.data?.profiles ?? [];
    const want = picked ?? one?.data?.current ?? "";
    return rows.find((r) => r.name === want) ?? null;
  }
  const isLocalService = (url) => /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(url.trim());
  function drawLeft(one) {
    left.replaceChildren();
    const list = el3("div", "engine-cards");
    for (const rowOne of one.data?.profiles ?? []) {
      const cardEl = el3("div", "engine-card");
      cardEl.setAttribute("role", "button");
      cardEl.setAttribute("tabindex", "0");
      coreSetUIAttribute(cardEl, "aria-label", [corePhrase("编辑 "), rowOne.name, corePhrase("")]);
      cardEl.setAttribute("aria-pressed", String(
        !fresh && rowOne.name === (picked ?? one.data?.current)
      ));
      if (!fresh && rowOne.name === (picked ?? one.data?.current)) {
        cardEl.classList.add("picked");
      }
      cardEl.title = rowOne.name;
      cardEl.append(el3("strong", "engine-card-name", rowOne.display_name || rowOne.name));
      const pills = el3("div", "engine-pills");
      const shownModel = (one.data?.profiles ?? []).find((p) => p.name === rowOne.name)?.model_name || rowOne.model;
      for (const text2 of [rowOne.kind, shownModel || "未选择模型", rowOne.base_url]) {
        pills.append(el3("span", "pill", text2));
      }
      cardEl.append(pills);
      const marks = el3("div", "engine-card-marks");
      if (rowOne.name === one.data?.current) marks.append(el3("span", "dot-mark", corePhrase("● 当前")));
      const hasModel = one.data?.availability?.[rowOne.name];
      const missingKey = rowOne.auth !== "chatgpt" && !isLocalService(rowOne.base_url) && one.data?.secret?.[rowOne.secret] === "missing";
      marks.append(el3(
        "span",
        hasModel && !missingKey ? "ok-mark" : "warn-mark",
        missingKey ? "待配置 API 密钥" : hasModel ? "模型已配置" : "未选择模型"
      ));
      cardEl.append(marks);
      cardEl.addEventListener("click", () => {
        fresh = false;
        picked = rowOne.name;
        rememberSelection(picked);
        typedKey = "";
        modelValue = "";
        say("");
        draw();
      });
      cardEl.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          cardEl.click();
        }
      });
      list.append(cardEl);
    }
    const make = button2("+ 新建实例", "btn ghost");
    list.append(make);
    if (fresh || readDrafts().fresh) {
      const draft = el3("div", fresh ? "engine-card draft picked" : "engine-card draft");
      if (fresh) draft.setAttribute("aria-current", "true");
      const planned = readDrafts().fresh?.values?.displayName || spareName();
      const maybe = presetOf(planned);
      draft.append(el3("strong", "engine-card-name", planned || "未命名实例"));
      const pills = el3("div", "engine-pills");
      for (const text2 of [
        maybe?.kind || "kind",
        maybe?.model || "model —",
        maybe?.base_url || "baseUrl —"
      ]) {
        pills.append(el3("span", "pill", text2));
      }
      draft.append(pills);
      const draftFoot = el3("div", "engine-draft-foot");
      draftFoot.append(el3("span", "engine-draftmark", corePhrase("✎ 草稿")));
      const giveUp = el3("button", "engine-card-drop", "🗑");
      coreSetUIText(giveUp, "title", corePhrase("删除草稿"));
      giveUp.addEventListener("click", (ev) => {
        ev.stopPropagation();
        changeDraft(void 0);
        fresh = false;
        typedKey = "";
        say("");
        draw();
      });
      draftFoot.append(giveUp);
      draft.append(draftFoot);
      draft.addEventListener("click", () => {
        fresh = true;
        rememberSelection(null);
        say("草稿尚未保存");
        draw();
      });
      list.append(draft);
    }
    make.addEventListener("click", () => {
      fresh = true;
      picked = null;
      rememberSelection(null);
      typedKey = "";
      modelValue = "";
      say("已填入默认值，请检查后保存。");
      draw();
    });
    left.append(list);
  }
  function drawRight(one) {
    editEpoch++;
    captureDraft = void 0;
    stopAuth?.();
    stopAuth = void 0;
    resetControls();
    modelSpecs = [];
    modelEffortLevels = [];
    cellsVision = null;
    visionHint = null;
    const profile = chosen(one);
    const draftName = fresh ? void 0 : profile?.name;
    const drafts = readDrafts();
    const restored = fresh ? drafts.fresh : profile && drafts.profiles[profile.name];
    typedKey = draftKeys.get(draftKey(draftName)) ?? "";
    specOverrides.clear();
    const specFields = ["context_window", "temperature", "reasoning_effort", "max_tokens"];
    for (const field of specFields) {
      const value = profile?.[field];
      if (value !== null && value !== void 0 && String(value) !== "") specOverrides.add(field);
    }
    rememberedEffort = profile?.reasoning_effort ?? "";
    let modelsEpoch = 0;
    const colLeft = el3("div", "engine-col engine-connection");
    const colRight = el3("div", "engine-col");
    right.replaceChildren(colLeft, colRight);
    const basic = el3("div", "engine-section");
    basic.append(el3("h3", "engine-sub", corePhrase("基本信息")));
    const preset = profile ? (one.data?.presets ?? []).find((p) => p.base_url === profile.base_url && p.kind === profile.kind && (p.auth ?? "api_key") === (profile.auth ?? "api_key")) : presetOf(spareName());
    const authIn = pick(["api_key", "chatgpt"]);
    coreSetUIText(authIn.options[0], "textContent", corePhrase("API 密钥认证"));
    coreSetUIText(authIn.options[1], "textContent", corePhrase("订阅套餐"));
    authIn.value = profile?.auth === "chatgpt" || preset && isPlanProvider(preset) || profile && /\/api\/coding\/paas\/v4\/?$/.test(profile.base_url) ? "chatgpt" : "api_key";
    const isPlan = () => authIn.value === "chatgpt";
    const chosenPreset = () => presetOf(providerIn.value);
    const isSubscription = () => isPlan() && (chosenPreset()?.auth === "chatgpt" || profile?.auth === "chatgpt" && providerIn.value === (preset?.name || "Custom"));
    basic.append(row("认证方式", authIn));
    const nameIn = input("text");
    nameIn.value = profile?.name || spareName();
    const providerIn = input("text");
    providerIn.classList.add("engine-input-wide");
    providerIn.value = preset?.name || "Custom";
    cells.provider = providerIn;
    const providerBox = combo(
      providerIn,
      () => providerChoices(one.data?.presets ?? [], isPlan()).map((each) => ({
        value: each.name,
        label: each.name
      }))
    );
    basic.append(row("模型供应商", providerBox, void 0, true));
    const instanceNameIn = input("text");
    instanceNameIn.value = profile?.display_name || profile?.name || nameIn.value;
    coreSetUIText(instanceNameIn, "placeholder", corePhrase("例如：工作账号、备用API（可随时改名）"));
    instanceNameIn.maxLength = 80;
    basic.append(row("实例名称", instanceNameIn));
    const vendor = preset !== void 0;
    const kindIn = pick(["chat", "responses"]);
    kindIn.replaceChildren();
    for (const [value, label] of [
      ["chat", "Chat Completions"],
      ["responses", "Responses"]
    ]) {
      const opt = document.createElement("option");
      opt.value = value;
      uiLabel(opt, label);
      kindIn.append(opt);
    }
    kindIn.value = profile?.kind ?? preset?.kind ?? "chat";
    coreSetUIText(kindIn, "title", corePhrase("选择服务支持的 API 协议，地址与协议需匹配。"));
    basic.append(row("协议", kindIn));
    colLeft.append(basic);
    const kind = kindIn.value;
    const flow = el3("div", "engine-section engine-api");
    flow.append(el3("h3", "engine-sub", SECTIONS[0]));
    const urlIn = input("url");
    urlIn.value = profile?.base_url || preset?.base_url || String(one.data?.keys?.base_url?.value ?? "");
    cells.base_url = urlIn;
    const urlRow = row("API 地址", urlIn, void 0, true);
    flow.append(urlRow);
    providerIn.addEventListener("change", () => {
      const maybe = chosenPreset();
      const allowed = providerChoices(one.data?.presets ?? [], isPlan());
      if (!maybe || !allowed.some((p) => p.name === maybe.name)) {
        if (isPlan()) {
          providerIn.value = allowed[0]?.name ?? "ChatGPT-Subscription";
          selectProvider();
          say("订阅供应商请从套餐列表选择；未实现的账号登录不会冒充支持", true);
        } else {
          modelItems = [];
          modelSpecs = [];
          modelValue = "";
          modelEffortLevels = [];
          rebindSecret();
          applyAuth();
        }
        syncDraft();
        return;
      }
      selectProvider();
      say(`已带出「${maybe.name}」的认证、地址与协议`);
    });
    const ownName = (nameIn.value.trim() || "CUSTOM").toUpperCase().replace(/[^A-Z0-9]+/g, "_");
    let secretName = profile?.secret || `TELYIN_KEY_${ownName}`;
    const probeSecretName = () => profile?.name === nameIn.value.trim() ? profile.secret : secretName;
    const secretIn = input("password");
    let configured = typedKey !== "" || !fresh && (one.data?.secret?.[secretName] ?? "missing") !== "missing";
    coreSetUIText(secretIn, "placeholder", configured ? corePhrase("已配置——输入新 API 密钥可替换") : corePhrase("输入 API Key"));
    coreSetUIText(secretIn, "title", configured ? [corePhrase(""), secretName, corePhrase(" 已保存密钥，输入新值可替换")] : [corePhrase("保存时写进 data/engine.env（变量名 "), secretName, corePhrase("）")]);
    secretIn.value = typedKey;
    secretIn.addEventListener("input", () => {
      typedKey = secretIn.value;
    });
    const applyKeyRow = () => {
      const local = isLocalService(urlIn.value);
      secretIn.disabled = local;
      if (isSubscription()) secretIn.disabled = true;
      const mark = secretIn.requiredMark;
      if (mark) {
        mark.hidden = local || isSubscription();
      }
      if (local) {
        secretIn.value = "";
        coreSetUIText(secretIn, "placeholder", corePhrase("无需 API 密钥"));
        coreSetUIText(secretIn, "title", corePhrase("当前本地服务无需 API 密钥"));
      } else {
        coreSetUIText(secretIn, "placeholder", configured ? corePhrase("已配置——输入新 API 密钥可替换") : corePhrase("输入 API Key"));
        coreSetUIText(secretIn, "title", configured ? [corePhrase(""), secretName, corePhrase(" 已保存密钥，输入新值可替换")] : [corePhrase("保存时写进 data/engine.env（变量名 "), secretName, corePhrase("）")]);
      }
    };
    urlIn.addEventListener("input", applyKeyRow);
    const keyRow = row("API 密钥", secretIn, void 0, true);
    flow.append(keyRow);
    const authSlot = el3("div", "engine-chatgpt-slot");
    flow.append(authSlot);
    const planNote = el3("p", "engine-chatgpt-note");
    flow.append(planNote);
    let oauthIdentity = "";
    const syncDraft = () => {
      if (!fresh) return;
      const draft = left.querySelector(".engine-card.draft");
      const title2 = draft?.querySelector(".engine-card-name");
      const pills = draft?.querySelector(".engine-pills");
      if (title2) title2.textContent = instanceNameIn.value.trim() || nameIn.value.trim() || "未命名实例";
      if (pills) {
        const model = cells.model?.value || "未选择模型";
        pills.replaceChildren(...[kindIn.value, model, urlIn.value || "（未填地址）"].map((value) => el3("span", "pill", value)));
      }
    };
    const rebindSecret = () => {
      typedKey = "";
      secretIn.value = "";
      const sameSaved = profile?.name === nameIn.value.trim();
      const keyBase = nameIn.value.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "_");
      secretName = sameSaved && profile.secret ? profile.secret : `TELYIN_KEY_${keyBase}`;
      configured = sameSaved && (one.data?.secret?.[secretName] ?? "missing") !== "missing";
    };
    const selectProvider = () => {
      const selected = chosenPreset();
      if (!selected) return;
      if (fresh) {
        const previousName = nameIn.value;
        nameIn.value = nextProviderName(selected.name, (one.data?.profiles ?? []).map((p) => p.name));
        if (!instanceNameIn.value.trim() || instanceNameIn.value === previousName) {
          instanceNameIn.value = nameIn.value;
        }
      }
      urlIn.value = selected.base_url;
      kindIn.value = selected.kind;
      modelItems = [];
      modelSpecs = [];
      modelValue = "";
      modelEffortLevels = [];
      const model = cells.model;
      if (model) model.value = selected.model || "";
      rebindSecret();
      specOverrides.clear();
      rememberedEffort = "";
      applySpecOf("");
      fillEfforts();
      applyAuth();
      syncDraft();
    };
    function applyAuth() {
      const subscribed = isSubscription();
      keyRow.hidden = false;
      authSlot.hidden = !subscribed;
      urlIn.readOnly = isPlan();
      urlIn.disabled = isPlan();
      kindIn.disabled = isPlan();
      const mark = urlIn.requiredMark;
      if (mark) mark.hidden = isPlan();
      if (isPlan()) {
        const selected = subscribed ? providerChoices(one.data?.presets ?? [], true).find((p) => p.auth === "chatgpt") : chosenPreset();
        urlIn.value = selected?.base_url ?? "";
        kindIn.value = selected?.kind ?? "responses";
      }
      applyKeyRow();
      if (subscribed) {
        coreSetUIText(secretIn, "placeholder", corePhrase("订阅登录无需 API Key"));
        coreSetUIText(secretIn, "title", corePhrase("请通过下方账号授权登录；不使用 API Key"));
      } else if (isPlan()) {
        coreSetUIText(secretIn, "placeholder", corePhrase("输入此套餐的 API Key"));
        coreSetUIText(secretIn, "title", corePhrase("GLM / Z.ai Coding Plan 仍需套餐密钥；不是 ChatGPT 账号登录"));
      }
      planNote.hidden = !isPlan() || subscribed;
      coreSetUIText(planNote, "textContent", isPlan() && !subscribed ? corePhrase("此套餐仍需 API Key；已锁定专用地址。仅限厂商允许的编码场景，不是通用聊天订阅。") : corePhrase(""));
      const identity = subscribed ? nameIn.value.trim() : "";
      if (identity !== oauthIdentity) {
        stopAuth?.();
        stopAuth = void 0;
        authSlot.replaceChildren();
        oauthIdentity = identity;
        if (subscribed) {
          const backendReady = (one.data?.presets ?? []).some((p) => p.auth === "chatgpt");
          const auth = chatgptAuth(() => nameIn.value.trim(), backendReady);
          authSlot.append(auth.root);
          stopAuth = auth.stop;
          auth.refresh();
        }
      }
      const temperature = cells.temperature;
      if (temperature) {
        temperature.dataset.subscription = subscribed ? "yes" : "no";
        if (subscribed) coreSetUIText(temperature, "placeholder", corePhrase("订阅接口不支持调整"));
      }
      const thinking = cells.thinking;
      if (thinking) {
        for (const option of Array.from(thinking.options)) {
          coreSetUIText(option, "textContent", subscribed ? corePhrase("由模型决定") : option.value);
        }
      }
      for (const field of ["temperature", "max_tokens", "thinking"]) {
        const cell = cells[field];
        if (cell) {
          cell.disabled = subscribed;
          coreSetUIText(cell, "title", subscribed ? field === "max_tokens" ? corePhrase("订阅接口不发送输出上限；此值仍作为 Core 本地输出保护预算") : corePhrase("订阅接口不发送此参数") : corePhrase(""));
        }
      }
      refreshControls();
    }
    authIn.addEventListener("change", () => {
      const allowed = providerChoices(one.data?.presets ?? [], isPlan());
      const selected = chosenPreset();
      if (!selected || !allowed.some((p) => p.name === selected.name)) {
        const target = isPlan() ? allowed.find((p) => p.auth === "chatgpt") : allowed[0];
        if (target) providerIn.value = target.name;
      }
      selectProvider();
    });
    for (const cell of [providerIn, instanceNameIn, kindIn, urlIn]) {
      cell.addEventListener("input", syncDraft);
      cell.addEventListener("change", syncDraft);
    }
    applyKeyRow();
    const tester = button2("测试连通", "btn ghost");
    const probeOut = el3("p", "engine-hint", "");
    probeOut.dataset.state = "";
    const admitProbeTarget = () => {
      if (isSubscription() && profile && (profile.auth !== "chatgpt" || profile.kind !== kindIn.value || profile.base_url !== urlIn.value.trim())) {
        say("此实例已有不同的认证方式或地址。请新建 ChatGPT 订阅实例，再在其中登录。", true);
        return false;
      }
      const changedEndpoint = profile && ((profile.auth || "api_key") !== "api_key" || profile.kind !== kindIn.value || profile.base_url !== urlIn.value.trim());
      if (!isSubscription() && changedEndpoint && !secretIn.value.trim()) {
        say("API 地址或协议已变更，请填入该服务的 API 密钥后测试。", true);
        return false;
      }
      return true;
    };
    const probeTarget = () => ({
      name: nameIn.value.trim(),
      kind: kindIn.value,
      base_url: urlIn.value.trim(),
      auth: isSubscription() ? "chatgpt" : "api_key",
      api_key: isSubscription() ? "" : secretIn.value,
      secret_name: isSubscription() ? "" : probeSecretName()
    });
    onClick(tester, () => {
      if (!admitProbeTarget()) return;
      const epoch = editEpoch;
      probeOut.title = "";
      coreSetUIText(probeOut, "textContent", corePhrase("正在测试连通性…"));
      tester.disabled = true;
      probeOut.dataset.state = "";
      void probeEngine({ mode: "connection", ...probeTarget() }).then((got) => {
        if (epoch !== editEpoch || column.hidden) return;
        if (got.ok && got.data?.ok) {
          coreSetUIText(probeOut, "textContent", corePhrase("测试成功"));
          probeOut.dataset.state = "ok";
          return;
        }
        const why = got.data?.error ?? `服务未返回有效响应（HTTP ${got.status}）`;
        const said = String(got.data?.detail ?? "");
        const detail = said ? `（上游：${short(said)}）` : "";
        coreSetUIText(probeOut, "textContent", [corePhrase("测试失败: "), why, corePhrase(""), detail, corePhrase("")]);
        probeOut.title = said || why;
        probeOut.dataset.state = "bad";
      }).finally(() => {
        tester.disabled = false;
      });
    });
    flow.append(tester, probeOut);
    colLeft.append(flow);
    const gen = el3("div", "engine-section");
    gen.append(el3("h3", "engine-sub", SECTIONS[1]));
    for (const [field, label] of FIELD_ROWS) {
      const supported = accepts(kind, field, urlIn.value);
      const hint = void 0;
      if (field === "model") {
        const modelIn = input("text");
        modelIn.classList.add("engine-input-wide");
        cells.model = modelIn;
        modelIn.value = profile?.model_name || profile?.model || preset?.model || "";
        coreSetUIText(modelIn, "placeholder", corePhrase("选择或输入模型"));
        modelItems = [];
        modelValue = profile?.model ?? "";
        modelIn.addEventListener("input", () => {
          modelValue = "";
        });
        const modelBox = combo(
          modelIn,
          () => modelItems,
          (picked2) => {
            const changed = modelValue !== picked2.value;
            modelValue = picked2.value;
            if (changed) {
              specOverrides.clear();
              rememberedEffort = "";
            }
            applySpecOf(picked2.value);
          }
        );
        const line = row(label, modelBox, void 0, true);
        const grab = button2("获取模型列表", "btn ghost");
        const loadModels = () => {
          if (!admitProbeTarget()) return;
          if (grab.getAttribute("aria-busy") === "true") return;
          const epoch = editEpoch, request2 = ++modelsEpoch;
          grab.setAttribute("aria-busy", "true");
          say("正在获取模型列表…");
          void probeEngine({ mode: "models", ...probeTarget() }).then((got) => {
            if (column.hidden || request2 !== modelsEpoch || epoch !== editEpoch || cells.model !== modelIn) return;
            const names = got.ok && got.data?.ok ? got.data.models : null;
            if (!names || names.length === 0) {
              const why = got.data?.error ?? (got.data?.ok ? "上游没有返回可选模型" : `HTTP ${got.status}`);
              const said = String(got.data?.detail ?? "");
              say(`无法获取模型列表：${why}${said ? `（上游：${short(said)}）` : ""}`, true);
              notice.title = said;
              return;
            }
            const specs = got.data?.specs ?? [];
            const keep = modelValueOf();
            modelSpecs = specs;
            modelItems = names.map((id) => {
              const spec = specs.find((each) => each.id === id);
              const shown = spec?.display_name || id;
              return { value: id, field: shown, label: `${shown}（${id}）` };
            });
            modelValue = keep || names[0];
            const selected = modelItems.find((item) => item.value === modelValue);
            modelIn.value = selected?.field ?? modelValue;
            coreSetUIText(modelIn, "placeholder", corePhrase("选择或搜索模型"));
            applySpecOf(modelValue);
            fillEfforts();
            captureDraft?.();
            say(`上游 ${names.length} 个模型 · 当前 ${modelValue}`);
            if (document.activeElement === grab || document.activeElement === modelIn) {
              modelIn.focus();
              modelIn.openAll();
            }
          }).finally(() => {
            grab.removeAttribute("aria-busy");
          });
        };
        onClick(grab, loadModels);
        line.append(grab);
        gen.append(line);
        continue;
      }
      const cell = field === "thinking" ? pick(["开", "关"]) : field === "reasoning_effort" ? pick(effortLevels()) : input("text");
      if (field === "thinking") {
        cell.value = profile?.thinking ?? true ? "开" : "关";
      } else if (field === "reasoning_effort") {
        const effort = cell;
        const saved = profile?.reasoning_effort ?? "";
        effort.value = Array.from(effort.options).some((o) => o.value === saved) ? saved : "";
      } else {
        const now = profile?.[field];
        const wrote = now !== null && now !== void 0 && String(now) !== "";
        const live2 = fresh ? one.data?.defaults?.[field] : one.data?.keys?.[field]?.value;
        const ours = field !== "context_window" && field !== "reasoning_budget_tokens" && (field === "max_tokens" || !vendor);
        const fallback = ours && live2 !== null && live2 !== void 0 && live2 !== "" ? String(live2) : "";
        cell.value = wrote ? String(now) : fallback;
        if (!wrote && !ours) {
          const capacity = profile ? one.data?.capacities?.[profile.name] : void 0;
          const value = capacity?.value ?? (profile?.name === one.data?.current ? live2 : null);
          const status = capacity?.status ?? "not_checked";
          const capacityText = capacity?.source === "fallback" ? "回退容量：128K（131072）" : typeof value === "number" ? `自动容量：${value}` : status === "not_reported" ? "容量未知：上游未提供" : status.startsWith("lookup_failed") ? `容量查询失败${capacity?.http_status ? " · HTTP " + capacity.http_status : ""}` : "容量尚未查询";
          coreSetUIText(cell, "placeholder", field === "context_window" ? capacityText : corePhrase("上游默认"));
          if (field === "context_window") coreSetUIText(cell, "title", corePhrase("优先使用上游容量；未知或查询失败时按 128K（131072）规划交接。回退值不代表上游承诺支持，可手动填写已确认的容量。"));
        }
      }
      cells[field] = cell;
      if (["context_window", "temperature", "reasoning_effort", "max_tokens"].includes(field)) {
        const preserve = () => {
          specOverrides.add(field);
          if (field === "reasoning_effort") rememberedEffort = cell.value;
        };
        cell.addEventListener("input", preserve);
        cell.addEventListener("change", preserve);
      }
      gen.append(row(label, cell, hint));
      if (field === "max_tokens") {
        const sees = el3("button", "engine-switch");
        sees.dataset.on = profile?.multimodal ? "yes" : "no";
        coreSetUIText(sees, "title", corePhrase("根据模型信息自动设置，也可手动调整"));
        sees.addEventListener("click", () => {
          sees.dataset.on = sees.dataset.on === "yes" ? "no" : "yes";
          updateDraft();
          say(sees.dataset.on === "yes" ? "图片输入：开" : "图片输入：关");
        });
        cellsVision = sees;
        const visionRow = row("图片输入", sees);
        visionHint = el3("p", "engine-hint", "");
        visionRow.append(visionHint);
        gen.append(visionRow);
      }
    }
    for (const [field] of FIELD_ROWS) {
      const one2 = cells[field];
      if (one2 && !accepts(kind, field, urlIn.value)) {
        one2.disabled = true;
        one2.title = WHY_NOT[field] ?? "";
      }
    }
    const subscriptionNote = el3(
      "p",
      "engine-hint",
      corePhrase("温度由上游控制；输出值为本地预算。")
    );
    gen.append(subscriptionNote);
    const refreshSubscriptionNote = () => {
      subscriptionNote.hidden = !isSubscription();
    };
    authIn.addEventListener("change", refreshSubscriptionNote);
    providerIn.addEventListener("change", refreshSubscriptionNote);
    fillEfforts();
    applyAuth();
    const fields = {
      identity: nameIn,
      auth: authIn,
      provider: providerIn,
      displayName: instanceNameIn,
      protocol: kindIn,
      url: urlIn,
      ...Object.fromEntries(FIELD_ROWS.map(([field]) => [field, cells[field]]))
    };
    const snapshot2 = () => ({
      values: Object.fromEntries(Object.entries(fields).map(([key2, cell]) => [key2, cell?.value ?? ""])),
      modelId: modelValueOf(),
      vision: cellsVision?.dataset.on === "yes"
    });
    const baseline = JSON.stringify(snapshot2());
    if (restored?.values && typeof restored.values === "object") {
      for (const [key2, cell] of Object.entries(fields)) {
        const value = restored.values[key2];
        if (!cell || typeof value !== "string") continue;
        if (cell === nameIn && (!fresh || one.data?.profiles.some((row2) => row2.name === value))) continue;
        if (cell === cells.reasoning_effort && value && cell instanceof HTMLSelectElement && !Array.from(cell.options).some((option) => option.value === value)) {
          cell.add(new Option(value, value));
        }
        cell.value = value;
      }
      modelValue = typeof restored.modelId === "string" ? restored.modelId : "";
      if (cellsVision) cellsVision.dataset.on = restored.vision ? "yes" : "no";
      rebindSecret();
      typedKey = draftKeys.get(draftKey(draftName)) ?? "";
      secretIn.value = typedKey;
      applyAuth();
      for (const field of ["thinking", "reasoning_effort"]) {
        const cell = cells[field];
        if (cell) cell.disabled = field === "thinking" && isSubscription() || !accepts(kindIn.value, field, urlIn.value);
      }
      refreshControls();
      for (const field of specFields) specOverrides.add(field);
    }
    captureDraft = () => {
      if (!fresh && !profile) return;
      const next = snapshot2();
      if (!fresh && !typedKey && JSON.stringify(next) === baseline) {
        changeDraft(draftName);
      } else {
        if (typedKey) draftKeys.set(draftKey(draftName), typedKey);
        else draftKeys.delete(draftKey(draftName));
        changeDraft(draftName, next);
      }
    };
    if (fresh && !restored) captureDraft();
    refreshSubscriptionNote();
    const noteCells = [cells.context_window, cells.max_tokens];
    const refreshHandoffNote = () => {
      const said = handoffNote(
        numberOrNull(cells.context_window),
        numberOrNull(cells.max_tokens)
      );
      for (const one2 of noteCells) {
        if (one2 && !one2.disabled) {
          one2.title = said;
        }
      }
    };
    refreshHandoffNote();
    for (const one2 of noteCells) {
      one2?.addEventListener("input", refreshHandoffNote);
    }
    colRight.append(gen);
    const act = el3("div", "engine-actions");
    const actionTitle = el3("strong", "engine-action-title", profile?.display_name || profile?.name || corePhrase("新建模型配置"));
    act.append(actionTitle);
    const actions = el3("div", "engine-foot");
    act.append(actions, notice);
    actionsHost.replaceChildren(act);
    const drop = button2("删除", "btn ghost");
    drop.disabled = profile === null;
    let armed = false;
    onClick(drop, () => {
      if (profile === null) {
        return;
      }
      if (!armed) {
        armed = true;
        coreSetUIText(drop, "textContent", corePhrase("🗑 确认删除"));
        drop.className = "btn danger";
        say("再点一下确认删除");
        window.setTimeout(() => {
          armed = false;
          coreSetUIText(drop, "textContent", corePhrase("删除"));
          drop.className = "btn ghost";
        }, 5e3);
        return;
      }
      void engineCommand({ action: "delete", name: profile.name }).then((result) => {
        if (result.ok) changeDraft(profile.name);
        after(result);
      });
    });
    const save = button2("保存");
    onClick(save, () => {
      if (saving) return;
      if (isSubscription() && !(one.data?.presets ?? []).some((p) => p.auth === "chatgpt")) {
        say("后端尚未加载订阅登录功能，请先重启 Core，再保存", true);
        return;
      }
      const winNow = numberOrNull(cells.context_window);
      const outNow = numberOrNull(cells.max_tokens);
      if (winNow !== null && outNow !== null && winNow <= outNow) {
        say("总输出上限必须小于上下文容量", true);
        return;
      }
      if (!isSubscription() && !isLocalService(urlIn.value) && secretIn.value.trim() === "" && !configured) {
        say("有必填项未填，请检查", true);
        return;
      }
      const one2 = {
        action: "save",
        set_current: true,
        create_only: fresh,
        profile: {
          // ⚠️ **读那一格当时的值**（不是画这一页时捕获的那个常量）——
          //    协议现在**可以改**，读常量的话"改了协议、存下来还是旧的"。
          // ⚠️ 这里从前写的是 `wanted` —— 那是第 3 步**删掉**的那句必填判断里的局部量
          //    ⇒ 点保存当场 `ReferenceError` ⇒ **按键没反应**（作者点到的那一次）。
          //    ⇒ 直接读那一格（服务那一处会判必填，错由它说清）。
          name: nameIn.value.trim(),
          display_name: instanceNameIn.value.trim(),
          kind: kindIn.value,
          auth: isSubscription() ? "chatgpt" : "api_key",
          base_url: cells.base_url?.value ?? urlIn.value,
          model: modelValueOf(),
          // 一起把"给人看的名字"写进去（`model_name`）—— 不然刷新之后那一格只剩 id。
          model_name: cells.model?.value ?? "",
          // **空着的那几个数就不发**（2026-10-05）：`numberOrNull('')` 交回 `null`，
          //    而服务那一处只认正整数 ⇒ 会**误拒**；而"上游没报就空着"是**正常**状态
          //    （第 1 步那条规矩）⇒ 缺省 ＝ **用默认那一份**（`config.load_config`）。
          ...numberOrNull(cells.max_tokens) === null ? {} : { max_tokens: numberOrNull(cells.max_tokens) },
          reasoning_budget_tokens: numberOrNull(cells.reasoning_budget_tokens),
          thinking: String(cells.thinking?.value) === "开",
          ...numberOrNull(cells.context_window) === null ? {} : { context_window: numberOrNull(cells.context_window) },
          ...numberOrNull(cells.temperature) === null ? {} : { temperature: numberOrNull(cells.temperature) },
          ...cells.reasoning_effort?.value ? { reasoning_effort: cells.reasoning_effort.value } : {},
          // **本地服务这一条不写变量名**（2026-10-05 作者："本地服务不用密钥"）——
          // 地址是本机 ⇒ 存空串 （＝"这一条没有钥匙" 与那四条预设的 `secret=""` 一致 ）。
          // ⚠️ 从前这里一律写 `secretName` ⇒ 一条本地档案会顶着别人的变量名（今天量到
          // `Local` 就写着 `DEEPSEEK_API_KEY` ）⇒ 现在它保存一次就自己清干净 。
          secret: isSubscription() || isLocalService(urlIn.value) ? "" : secretName,
          // **那个开关的值也要存**（照参考 `multimodal`）：不存它 ⇒ 保存时开着、
          //    刷新回来就关（作者点出来的那一处）。
          multimodal: cellsVision?.dataset.on === "yes"
        }
      };
      const typed = secretIn.value;
      const submitted = JSON.stringify(snapshot2());
      const epoch = editEpoch;
      saving = true;
      void saveEngineDraft(one2, typed).then((result) => {
        const stored = readDrafts();
        const currentDraft = draftName === void 0 ? stored.fresh : stored.profiles[draftName];
        if (result.profile.ok && !result.error && (!result.secret || result.secret.ok) && JSON.stringify(currentDraft) === submitted && (draftKeys.get(draftKey(draftName)) ?? "") === typed) {
          changeDraft(draftName);
        }
        if (epoch !== editEpoch) {
          say(
            result.error || (!result.profile.ok ? `档案未确认：${result.profile.note}` : result.secret && !result.secret.ok ? `档案已保存，密钥未确认：${result.secret.note}` : "原档案保存已完成；当前编辑保留，请刷新核对。"),
            !!result.error || !result.profile.ok || !!result.secret && !result.secret.ok
          );
          return;
        }
        if (result.error || result.secret && !result.secret.ok) {
          say(result.error || `档案已保存，密钥未确认：${result.secret.note}`, true);
          return;
        }
        if (result.secret?.ok) {
          secretIn.value = "";
          typedKey = "";
        }
        if (result.profile.ok) changeDraft(draftName);
        after(result.profile);
      }).catch(() => {
        say("保存结果未确认；请刷新核对后再决定，未自动重试。", true);
      }).finally(() => {
        saving = false;
      });
    });
    const use = button2("设为当前", "btn ghost");
    use.disabled = !(profile && profile.model.trim());
    coreSetUIText(use, "title", use.disabled ? corePhrase("请先选择模型，再设为当前") : corePhrase(""));
    onClick(use, () => {
      void engineCommand({ action: "use", name: profile?.name ?? "" }).then(after);
    });
    actions.append(save, use, drop);
    const modelCell = cells.model;
    modelCell?.addEventListener("input", syncDraft);
    modelCell?.addEventListener("change", syncDraft);
    syncDraft();
  }
  function after(got) {
    say(got.ok ? "已保存" : `操作失败（HTTP ${got.status}）：${short(got.note)}`, !got.ok);
    if (got.ok) {
      fresh = false;
      const epoch = editEpoch;
      void engineReport().then((again) => {
        if (epoch !== editEpoch) return;
        typedKey = "";
        report = again;
        if (again.ok) {
          picked = again.data?.current ?? null;
          rememberSelection(picked);
        }
        draw();
      });
    }
  }
  function draw() {
    if (report === null) {
      return;
    }
    try {
      drawLeft(report);
      drawRight(report);
    } catch (exc) {
      say(`页面加载失败：${short(exc, 60)}`, true);
      throw exc;
    }
  }
  function sourceOf(one, field) {
    const got = one.data?.keys?.[field];
    return got ? `来源：${got.source}` : "";
  }
  function skeleton() {
    left.replaceChildren();
    const bone = () => {
      const col = el3("div", "engine-col");
      col.append(el3("div", "engine-section"), el3("div", "engine-section"));
      return col;
    };
    right.replaceChildren(el3("h2", "card-title", corePhrase("加载中…")), bone(), bone());
  }
  let readEpoch = 0;
  const readHint = pageLoad(column, "模型档案", () => {
    load();
  });
  function load() {
    editEpoch++;
    typedKey = "";
    const epoch = ++readEpoch;
    readHint.start();
    void Promise.all([engineReport(), initializeDisplayScope()]).then(([got]) => {
      if (epoch !== readEpoch || column.hidden) return;
      report = got;
      if (!got.ok) {
        say(`读不到（HTTP ${got.status}）：${short(got.note)}`, true);
        readHint.fail();
        return;
      }
      if (picked === null) {
        const drafts = readDrafts();
        const selected = got.data?.profiles.find((row2) => row2.name === drafts.selected);
        if (selected) picked = selected.name;
        else if (drafts.fresh) fresh = true;
        else picked = got.data?.profiles.find((row2) => drafts.profiles[row2.name])?.name ?? null;
      }
      draw();
      readHint.ready();
    });
  }
  return {
    root: column,
    show: () => {
      column.hidden = false;
      shell2.bottom.hidden = true;
      skeleton();
      load();
    },
    stop: () => {
      resetControls();
      captureDraft = void 0;
      editEpoch++;
      typedKey = "";
      readEpoch++;
      stopAuth?.();
      stopAuth = void 0;
      column.hidden = true;
      shell2.bottom.hidden = false;
    }
  };
}
var FIELD_ROWS = [
  ["model", "模型名称"],
  ["thinking", "思维链"],
  ["reasoning_effort", "推理强度"],
  ["reasoning_budget_tokens", "思考预算"],
  ["temperature", "温度"],
  ["max_tokens", "最大输出 token"],
  ["context_window", "上下文上限"]
];
function isDeepSeekEndpoint(url) {
  try {
    return new URL(url ?? "").hostname === "api.deepseek.com";
  } catch {
    return false;
  }
}
function accepts(kind, field, url) {
  if (field === "thinking" || field === "reasoning_effort") {
    return kind === "responses" || kind === "chat" && isDeepSeekEndpoint(url);
  }
  return true;
}
var WHY_NOT = {
  thinking: "当前协议及服务尚未适配思考开关",
  reasoning_effort: "当前协议及服务尚未适配推理强度（实测 7 档全过 ，嵌在 `reasoning.effort` 里发 ）"
};
function numberOrNull(one) {
  const text2 = one?.value ?? "";
  if (text2.trim() === "") {
    return null;
  }
  const got = Number(text2);
  return Number.isFinite(got) ? got : null;
}
var SOFT_RATIO = 0.85;
function handoffNote(windowTokens, outTokens) {
  if (windowTokens === null || outTokens === null) {
    return "";
  }
  if (windowTokens <= 0 || outTokens <= 0) {
    return "";
  }
  const hard = Math.max(0, windowTokens - outTokens);
  return ` 输入硬阈 ${hard}；软提醒 ${Math.floor(hard * SOFT_RATIO)}，余下 15% 为后续增长预留。`;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/composer-popovers.ts
function popoverPosition(anchor, height, view2) {
  const edge = 12;
  const gap = 10;
  const width = Math.max(0, Math.min(336, view2.width - edge * 2));
  const above = Math.max(0, anchor.top - view2.top - edge - gap);
  const below = Math.max(0, view2.top + view2.height - edge - anchor.top - anchor.height - gap);
  const side = above >= height || above >= below ? "above" : "below";
  const maxHeight = side === "above" ? above : below;
  const size = Math.min(height, maxHeight);
  const left = Math.max(
    view2.left + edge,
    Math.min(anchor.left, view2.left + view2.width - edge - width)
  );
  const top = side === "above" ? anchor.top - gap - size : anchor.top + anchor.height + gap;
  return { left, top, width, maxHeight, side };
}
var active = null;
var wired = false;
var sequence = 0;
var observer = null;
function close(restore = false) {
  if (active === null) {
    return;
  }
  const old = active;
  active = null;
  observer?.disconnect();
  if (old.panel.isConnected) {
    old.panel.hidePopover?.();
  }
  old.panel.hidden = true;
  old.trigger.setAttribute("aria-expanded", "false");
  old.trigger.dataset.open = "no";
  if (restore && old.trigger.isConnected) {
    old.trigger.focus();
  }
}
function positionComposerPopover() {
  if (active === null || typeof window === "undefined") {
    return;
  }
  const { trigger, panel: panel2 } = active;
  if (!trigger.isConnected) {
    close();
    return;
  }
  const vv = window.visualViewport;
  const view2 = {
    left: vv?.offsetLeft ?? 0,
    top: vv?.offsetTop ?? 0,
    width: vv?.width ?? window.innerWidth,
    height: vv?.height ?? window.innerHeight
  };
  panel2.style.width = `${Math.max(0, Math.min(336, view2.width - 24))}px`;
  panel2.style.maxHeight = "none";
  const box = popoverPosition(
    trigger.getBoundingClientRect(),
    panel2.getBoundingClientRect().height,
    view2
  );
  panel2.style.left = `${box.left}px`;
  panel2.style.top = `${box.top}px`;
  panel2.style.maxHeight = `${box.maxHeight}px`;
  panel2.dataset.side = box.side;
}
function wire() {
  if (wired) {
    return;
  }
  wired = true;
  const outside = (event) => {
    if (active === null) {
      return;
    }
    const target = event.target;
    if (target && (active.panel.contains(target) || active.trigger.contains(target))) {
      return;
    }
    close();
  };
  document.addEventListener("click", outside);
  document.addEventListener("focusin", outside);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && active !== null) {
      event.preventDefault();
      close(true);
    }
  });
  if (typeof window === "undefined") {
    return;
  }
  window.addEventListener("resize", positionComposerPopover);
  window.addEventListener("scroll", positionComposerPopover, true);
  window.visualViewport?.addEventListener("resize", positionComposerPopover);
  window.visualViewport?.addEventListener("scroll", positionComposerPopover);
  window.addEventListener("hashchange", () => close());
  window.addEventListener("pagehide", () => close());
  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(positionComposerPopover);
  }
}
function wireComposerPopovers(entries, refresh2) {
  wire();
  for (const entry of entries) {
    const { trigger, panel: panel2, label } = entry;
    const id = `composer-inspector-${++sequence}`;
    trigger.id = `${id}-trigger`;
    panel2.id = id;
    panel2.className += " composer-popover";
    panel2.hidden = true;
    panel2.tabIndex = -1;
    panel2.setAttribute("role", "region");
    panel2.setAttribute("aria-labelledby", trigger.id);
    panel2.setAttribute("popover", "manual");
    trigger.setAttribute("aria-label", label);
    trigger.setAttribute("aria-controls", id);
    trigger.setAttribute("aria-expanded", "false");
    const open = () => {
      close();
      active = entry;
      panel2.hidden = false;
      panel2.showPopover?.();
      trigger.setAttribute("aria-expanded", "true");
      trigger.dataset.open = "yes";
      positionComposerPopover();
      observer?.observe(panel2);
      observer?.observe(trigger);
      refresh2();
    };
    trigger.addEventListener("click", () => {
      if (active === entry) {
        close();
      } else {
        open();
      }
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (active !== entry) {
          open();
        }
        panel2.focus();
      }
    });
  }
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/cache-view.ts
var object = (value) => typeof value === "object" && value !== null ? value : {};
var number = (value) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
var count = (value) => {
  const n = number(value);
  return n === null ? "—" : n.toLocaleString("en-US");
};
var compact = (value) => {
  const n = number(value);
  if (n === null) {
    return "—";
  }
  if (n >= 1e6) {
    return `${Number((n / 1e6).toFixed(1))}M`;
  }
  if (n >= 1e3) {
    return `${Number((n / 1e3).toFixed(1))}K`;
  }
  return String(n);
};
var percent = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100 ? `${Number(value.toFixed(1))}%` : "—";
function cachePresentation(got) {
  const allPaths = "cache_all_view" in got;
  const view2 = object(allPaths ? got.cache_all_view : got.cache_view);
  const valid = view2.version === 1 && view2.scope === (allPaths ? "all_paths" : "console") && view2.unit === "http_attempt";
  const selected = object(view2.selected);
  const total = object(selected.cumulative);
  const ready2 = valid && view2.status === "ready";
  const reported = number(total.reported_attempts);
  const usable = ready2 && reported !== null && reported > 0;
  const share = usable ? percent(total.share) : "—";
  const diagnostics = ["统计单位：HTTP 尝试；不回退旧混合累计"];
  if (valid && typeof view2.model === "string") {
    diagnostics.push(`模型：${view2.model}`);
  }
  for (const [label, raw] of [
    ["累计", selected.cumulative],
    ...!allPaths ? [["近20次", selected.recent20]] : [],
    ...!allPaths ? [["最近一次", object(selected.last).metrics]] : []
  ]) {
    if (!ready2) {
      break;
    }
    const metric = object(raw);
    diagnostics.push(`${label}：${percent(metric.share)} · 上报 ${count(metric.reported_attempts)} / 尝试 ${count(metric.attempts)}；未上报 ${count(metric.unknown_attempts)}，无效 ${count(metric.invalid_attempts)}`);
  }
  diagnostics.push("比率按有效命中 / 输入 token 总和加权，非各次百分比平均。");
  if (allPaths) {
    const groups = object(view2.groups);
    for (const [key2, label] of [["conversation", "对话与交接后续"], ["consolidation", "后台整理"], ["auxiliary", "辅助请求"]]) {
      const row2 = object(groups[key2]);
      diagnostics.push(`${label}：${count(row2.input)} 输入 tok · 命中 ${percent(row2.share)}；未上报 ${count(row2.unknown_attempts)}`);
    }
    diagnostics.push("当前 Bot、当前提供商与模型的本次交接周期；交接成功后重新计数，重启不主动清零。");
    if (typeof view2.coverage_note === "string") {
      diagnostics.push(view2.coverage_note);
    }
  } else {
    diagnostics.push("累计仅限当前控制台、模型与上下文分区，不代表本次上线增量。");
  }
  let note = view2.status === "read_failed" ? "读取失败，旧读数已隐藏" : view2.status === "selection_failed" ? "分区选择失败" : valid && view2.status === "no_sample" ? "尚无样本" : !ready2 ? "缓存观测不可用或版本不兼容" : !usable ? "上游未上报完整缓存用量" : allPaths ? "全链路 · 本次交接周期" : "当前控制台 · 当前模型累计";
  if (usable && ((number(total.unknown_attempts) ?? 0) > 0 || (number(total.invalid_attempts) ?? 0) > 0)) {
    note = "统计不完整，仅含有效上报用量";
  }
  const snapshot2 = object(got.cache_observations);
  if (snapshot2.persistence_error || snapshot2.observation_error || got.cache_observation_error) {
    note = "观测异常，统计可能不完整";
  }
  const local = object(got.cache_local_view);
  const localReady = local.version === 1 && local.status === "ready" && local.unit === "serialized_utf8_bytes" && local.scope === "process_all_paths" && number(local.measured) !== null && Number(local.measured) > 0 && percent(local.share) !== "—";
  const localShare = percent(local.share);
  const localNote = "本地请求前缀复用估算，按序列化 UTF-8 字节加权；不是上游实际缓存命中或计费 token。按 1KiB 连续前缀保守匹配，完整相同请求精确匹配。按交接周期分区，仅本次进程留存，重启清零；与同分区最近 4 次请求比较，非上游缓存有效期。";
  if (localReady) {
    diagnostics.push(localNote);
    diagnostics.push(`本地：${count(local.measured)} 次已计算 / ${count(local.attempts)} 次尝试；跳过 ${count(local.skipped)} 次。`);
  }
  if (!usable && localReady) {
    return {
      label: `本地前缀复用 ${localShare}（估）`,
      title: `实际缓存命中：上游未提供有效统计
${localNote}`,
      heading: "本地前缀复用（估算）",
      amount: localShare,
      rows: [
        ["实际缓存命中", "未知"],
        ["本地前缀复用", localShare],
        ["比较单位", "序列化字节，非 token"]
      ],
      note: localNote,
      diagnostics
    };
  }
  return {
    label: usable ? `${allPaths ? "本周期全链路" : "累计"} ${compact(total.input)} tok · 命中 ${share}` : "缓存命中 —",
    title: `输入用量 · ${allPaths ? "当前 Bot 全链路" : "当前控制台"} / ${allPaths ? "本次交接周期" : "当前模型累计"}
${note}`,
    heading: allPaths ? "本次交接周期输入用量" : "累计输入用量",
    amount: usable ? `${count(total.input)} tok` : "—",
    rows: usable ? [
      ["缓存命中", share],
      ["未缓存输入", `${count(total.uncached)} tok`],
      ["缓存读取", `${count(total.cached)} tok`],
      ...localReady ? [["本地前缀复用（估算）", localShare]] : []
    ] : [],
    note,
    diagnostics
  };
}
function latestCacheRead(fetcher) {
  let sequence2 = 0;
  return async (draw) => {
    const mine = ++sequence2;
    try {
      const response = await fetcher("/usage");
      if (!response.ok) {
        throw new Error("unavailable");
      }
      const got = object(await response.json());
      if (mine === sequence2) {
        draw(got);
      }
    } catch {
      if (mine === sequence2) {
        draw({ cache_view: { status: "read_failed" } });
      }
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/console-introduction.ts
function mountConsoleIntroduction(root, composer2) {
  const guide2 = document.createElement("section");
  guide2.className = "console-introduction";
  guide2.hidden = true;
  guide2.setAttribute("aria-label", "首次见面");
  root.querySelector(".live-stream")?.before(guide2);
  const setPending = (pending5) => {
    composer2.toggleAttribute("data-introducing", pending5);
  };
  const line = (text2) => {
    const el6 = document.createElement("p");
    el6.textContent = text2;
    return el6;
  };
  async function load() {
    try {
      const response = await fetch("/console/introduction", { cache: "no-store" });
      if (response.status === 404) return;
      if (!response.ok) throw new Error();
      const state3 = await response.json();
      if (!state3.pending) {
        guide2.hidden = true;
        setPending(false);
        return;
      }
      const form = document.createElement("form");
      const heading2 = line(String(state3.bot) + " · 系统引导");
      const greeting = line("你好，初次见面！我该怎么称呼你？");
      const input2 = document.createElement("input");
      input2.name = "name";
      input2.placeholder = "你的称呼";
      input2.setAttribute("aria-label", "你的控制台称呼");
      input2.required = true;
      input2.autocomplete = "off";
      const save = document.createElement("button");
      save.type = "submit";
      save.textContent = "保存称呼";
      const later = document.createElement("button");
      later.type = "button";
      later.textContent = "稍后再填";
      later.onclick = () => {
        guide2.hidden = true;
        setPending(false);
      };
      const status = line("");
      status.setAttribute("role", "status");
      form.append(input2, save, later);
      guide2.replaceChildren(heading2, greeting, line("设置你在控制台使用的称呼。"), form, status);
      guide2.hidden = false;
      setPending(true);
      form.onsubmit = async (event) => {
        event.preventDefault();
        if (save.disabled) return;
        const name = input2.value.trim();
        if (!name || Array.from(name).length > 80) {
          status.textContent = "请输入 1 至 80 个字符的称呼。";
          input2.focus();
          return;
        }
        input2.disabled = save.disabled = later.disabled = true;
        try {
          const reply = await fetch("/console/introduction", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bot: state3.bot, name })
          });
          const result = await reply.json();
          if (!reply.ok || result.pending !== false) {
            if (reply.status === 409) {
              form.remove();
              setPending(false);
            }
            throw new Error(result.said || "保存失败，请重试。");
          }
          guide2.replaceChildren(heading2, line("已保存称呼：" + name));
          setPending(false);
        } catch (error) {
          status.textContent = error instanceof Error ? error.message : "保存状态未确认，请重试。";
        } finally {
          input2.disabled = save.disabled = later.disabled = false;
        }
      };
    } catch {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "重新读取";
      retry.onclick = () => {
        retry.disabled = true;
        void load();
      };
      guide2.replaceChildren(line("暂时读不到控制台画像。"), retry);
      guide2.hidden = false;
      setPending(false);
    }
  }
  void load();
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/tool-activity.ts
function toolReceiptFailed(name, text2) {
  return name !== "handoff_note" && /^\s*\[tool failed\]/.test(text2 || "");
}
function describeTool(name, call, result, fallback, knownTitles) {
  let args = {};
  try {
    const value2 = JSON.parse(call || "{}");
    if (value2 && typeof value2 === "object" && !Array.isArray(value2)) args = value2;
  } catch {
  }
  const compact2 = (s, max = 100) => {
    const t = s.replace(/\s+/g, " ").trim();
    return t.length > max ? t.slice(0, max) + "…" : t;
  };
  const value = (key2) => typeof args[key2] === "string" ? String(args[key2]).trim() : "";
  const reason = ["why", "reason", "purpose"].map(value).find(Boolean);
  const failed = toolReceiptFailed(name, result);
  let action = fallback;
  if (name === "skill_read" || name === "kb_read") {
    const target = value("name");
    let title = "";
    if (!failed && result) {
      if (name === "skill_read") title = result.match(/^\[sk-[^\]]+\]\s*(.*?)\s*｜/m)?.[1] || "";
      else title = result.match(/^\[kb-[^\]]+\][^\n]*\n([^\n]+)/)?.[1] || "";
      if (/^(记录时间|修订来历|第\s*\d)/.test(title)) title = "";
    }
    if (!title && target) title = knownTitles?.get(target) || "";
    if (!title && target && !/^(kb|sk)-[\w-]+$/.test(target)) title = target;
    const offset = typeof args.offset === "number" ? args.offset : 0;
    action = !target && name === "skill_read" ? "查看技能目录" : (offset > 0 ? "续读" : "读取") + (title ? "《" + compact2(title) + "》" : name === "skill_read" ? "指定技能" : "关联知识");
    const range = result?.match(/第\s*(\d+)\s*[–—-]\s*(\d+)\s*行/);
    const total = result?.match(/正文共\s*(\d+)\s*行/);
    if (range) action += ` · 第 ${range[1]}–${range[2]} 行`;
    else if (offset > 0) action += ` · 从第 ${offset + 1} 行起`;
    else if (total) action += ` · ${total[1]} 行`;
  } else if (value("query")) {
    action = "检索「" + compact2(value("query")) + "」";
    const count4 = result?.match(/(?:有|查到)\s*(\d+)\s*(?:份|条)/);
    if (!failed && count4) action += " · 找到 " + count4[1] + " 条";
  } else if (value("text")) action = "记录「" + compact2(value("text")) + "」";
  else if (value("name")) action = fallback + "《" + compact2(value("name")) + "》" + (value("section") ? " · " + value("section") : "");
  if (reason) action = compact2(reason, 140) + " · " + action;
  if (failed) action += " · 未完成";
  return action;
}
function rememberToolTitles(result, titles) {
  for (const match of result.matchAll(/^·\s+(kb-[a-zA-Z0-9-]+)\s+(.+?)(?:（匹配分数|｜)/gm)) titles.set(match[1], match[2].trim());
  const skill = result.match(/^\[(sk-[^\]]+)\]\s*(.*?)\s*｜/m);
  if (skill) titles.set(skill[1], skill[2]);
  const knowledge = result.match(/^\[(kb-[^\]]+)\][^\n]*\n([^\n]+)/);
  if (knowledge && !/^(记录时间|修订来历|第\s*\d)/.test(knowledge[2])) titles.set(knowledge[1], knowledge[2]);
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/history-feed.ts
function mergeHistory(history, live2) {
  const result = [];
  const known = /* @__PURE__ */ new Set();
  const id = (frame) => typeof frame.display_id === "string" && frame.display_id ? frame.display_id : null;
  for (const frame of history) {
    const key2 = id(frame);
    if (key2 && known.has(key2)) continue;
    result.push(frame);
    if (key2) known.add(key2);
  }
  for (let i = 0; i < live2.length; i++) {
    const frame = live2[i], key2 = id(frame);
    if (key2 && known.has(key2)) continue;
    let before = -1;
    for (let j = i + 1; j < live2.length; j++) {
      const next = id(live2[j]);
      if (next && known.has(next)) {
        before = result.findIndex((row2) => id(row2) === next);
        break;
      }
    }
    if (before < 0) result.push(frame);
    else result.splice(before, 0, frame);
    if (key2) known.add(key2);
  }
  return result;
}
var HistoryFeed = class {
  constructor(append, replace) {
    this.append = append;
    this.replace = replace;
  }
  append;
  replace;
  sequence = 0;
  loading = false;
  live = [];
  visible = /* @__PURE__ */ new Set();
  begin() {
    this.loading = true;
    return ++this.sequence;
  }
  receive(wire2) {
    let frame;
    try {
      frame = JSON.parse(wire2);
    } catch {
      this.append(wire2);
      return;
    }
    if (!frame || typeof frame !== "object" || Array.isArray(frame)) {
      this.append(wire2);
      return;
    }
    const key2 = typeof frame.display_id === "string" ? frame.display_id : "";
    if (key2 && this.live.some((row2) => row2.display_id === key2)) return;
    this.live.push(frame);
    if (this.loading || key2 && this.visible.has(key2)) return;
    if (key2) this.visible.add(key2);
    this.append(wire2);
  }
  finish(ticket, frames) {
    if (ticket !== this.sequence) return;
    const merged = mergeHistory(frames, this.live);
    this.visible = new Set(merged.map((row2) => row2.display_id).filter((id) => !!id));
    this.replace(merged);
    this.loading = false;
  }
  fail(ticket) {
    if (ticket !== this.sequence) return;
    this.loading = false;
    for (const frame of this.live) {
      const key2 = frame.display_id;
      if (key2 && this.visible.has(key2)) continue;
      this.append(JSON.stringify(frame));
      if (key2) this.visible.add(key2);
    }
  }
};

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/chat.ts
var ME = "user";
var OTHER = "bot";
var A_BATCH = "batch";
var A_MESSAGE = "msg";
function asText(value) {
  return typeof value === "string" ? value : null;
}
function asFrame(wire2) {
  try {
    const got = JSON.parse(wire2);
    if (typeof got === "object" && got !== null) {
      const it = got;
      return {
        kind: asText(it.kind) ?? A_MESSAGE,
        who: asText(it.who),
        at: asText(it.at),
        text: asText(it.text) ?? "",
        tool: asText(it.tool) ?? void 0,
        stage: asText(it.stage) ?? void 0,
        // **2026-10-10 卡片第四刀**： 轮号与**思考**也得抄
        // —— 同一个坑： 它们在线上有 、 而这一步不抄就**到不了页上** 。
        run: asText(it.run) ?? void 0,
        display_id: asText(it.display_id) ?? void 0
      };
    }
  } catch {
  }
  return { kind: A_MESSAGE, who: null, at: null, text: wire2 };
}
function whoOf(frame) {
  if (frame.who === ME) return ME;
  if (frame.who === OTHER) return OTHER;
  return null;
}
function catchUpTheAvatar(one) {
  const url = theAvatarUrl();
  if (url === null) {
    return;
  }
  one.classList.add("has-pic");
  one.style.backgroundImage = `url(${url})`;
}
function theMoment(at, mine) {
  const clock = at.slice(-5);
  if (!mine || at.length <= 5 || !/^[0-9]{2}:[0-9]{2}$/.test(clock)) {
    return at;
  }
  const day = at.slice(0, -6);
  if (!day.includes(" ")) {
    return at;
  }
  return `${clock} ${day}`;
}
function isMine(frame) {
  return whoOf(frame) === ME;
}
function bubble(frame, theirs = NEUTRAL) {
  const mine = isMine(frame);
  const row2 = document.createElement("div");
  row2.className = mine ? "msg mine" : "msg";
  const body = document.createElement("div");
  body.className = "msg-body";
  const head = document.createElement("div");
  head.className = "msg-head";
  if (frame.at !== null) {
    const stamp = document.createElement("span");
    stamp.className = "msg-at";
    stamp.textContent = theMoment(frame.at, mine);
    head.append(stamp);
  }
  const name = document.createElement("div");
  name.className = "msg-who";
  name.textContent = mine ? userLabel() : theirs;
  head.append(name);
  body.append(head);
  const part = document.createElement("p");
  part.className = "msg-text";
  part.textContent = frame.text;
  body.append(part);
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "msg-copy";
  coreSetUIAttribute(copy, "aria-label", corePhrase("复制这条消息"));
  coreSetUIText(copy, "title", corePhrase("复制这条消息"));
  const svgNS = "http://www.w3.org/2000/svg";
  const icon2 = document.createElementNS(svgNS, "svg");
  icon2.classList.add("msg-copy-icon");
  icon2.setAttribute("viewBox", "0 0 20 20");
  icon2.setAttribute("aria-hidden", "true");
  const outline = document.createElementNS(svgNS, "path");
  outline.setAttribute("d", "M5 13H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v1 M9 7h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z");
  icon2.append(outline);
  copy.append(icon2);
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(frame.text);
      coreSetUIText(copy, "title", corePhrase("复制这条消息"));
    } catch {
      coreSetUIText(copy, "title", corePhrase("复制失败，请手动选择消息文字"));
    }
  });
  actions.append(copy);
  body.append(actions);
  if (mine) {
    row2.append(body);
  } else {
    const avatar = document.createElement("span");
    avatar.className = "msg-avatar";
    avatar.textContent = theirs.slice(0, 1);
    row2.append(avatar, body);
    catchUpTheAvatar(avatar);
  }
  return row2;
}
var toolGroups = /* @__PURE__ */ new WeakMap();
var handoffGroups = /* @__PURE__ */ new WeakMap();
var toolInspectorSerial = 0;
var toolLabels = {
  pending_read: "读取待议记录",
  decide_change: "处理设定修订",
  propose_change: "提出设定修订",
  profile_add: "写入画像",
  profile_read: "读取画像",
  kb_search: "检索知识",
  kb_read: "阅读知识",
  memory_search: "检索记忆",
  relations_read: "查看关系",
  read_events: "查阅经历",
  skill_read: "查看做法",
  handoff_note: "交接笔记",
  skill_note: "记录做法",
  self_account_add: "补充自述",
  self_account_set: "更新自述",
  finish_turn: "结束本轮"
};
function toolText(frame) {
  const text2 = (frame.text || "").trim();
  if (!text2) return frame.stage === "result" ? "未返回结果正文。" : "未记录调用参数。";
  if (frame.stage !== "result" && text2 === "{}") return "无需参数。";
  return frame.text;
}
var knownToolTitles = /* @__PURE__ */ new WeakMap();
function activityText(step, chat2) {
  return describeTool(
    step.name,
    step.call?.text,
    step.result?.text,
    toolLabels[step.name] || "调用工具",
    knownToolTitles.get(chat2)
  );
}
function showTool(group) {
  for (const step2 of group.steps) step2.element.setAttribute("aria-expanded", String(group.selected === step2));
  const step = group.selected;
  group.inspector.hidden = !step;
  group.inspector.replaceChildren();
  if (!step) return;
  const failed = toolReceiptFailed(step.name, step.result?.text);
  const head = document.createElement("div");
  head.className = "tool-inspector-head";
  const identity = document.createElement("div");
  identity.className = "tool-inspector-identity";
  const name = document.createElement("span");
  name.className = "tool-inspector-title";
  name.textContent = step.name;
  name.title = step.name;
  const caption = document.createElement("span");
  caption.className = "tool-inspector-caption";
  caption.textContent = toolLabels[step.name] || "工具详情";
  identity.append(name, caption);
  const state3 = document.createElement("span");
  state3.className = "tool-inspector-status" + (failed ? " has-error" : "");
  coreSetUIText(state3, "textContent", failed ? corePhrase("执行失败") : step.result ? step.call ? corePhrase("已返回") : corePhrase("仅有结果") : corePhrase("等待结果"));
  const close2 = document.createElement("button");
  close2.className = "tool-inspector-close";
  close2.type = "button";
  coreSetUIAttribute(close2, "aria-label", corePhrase("关闭工具详情"));
  close2.addEventListener("click", () => {
    const button4 = group.selected?.element;
    group.selected = null;
    showTool(group);
    button4?.focus();
  });
  head.append(identity, state3, close2);
  let parameters = step.call ? toolText(step.call) : "未记录对应调用参数。";
  if (step.call && step.call.text.trim() && step.call.text.trim() !== "{}") {
    try {
      parameters = JSON.stringify(JSON.parse(step.call.text), null, 2);
    } catch {
    }
  }
  const output = step.result ? toolText(step.result) : "尚未收到结果。";
  const toolbar = document.createElement("div");
  toolbar.className = "tool-inspector-toolbar";
  const tabs = document.createElement("div");
  tabs.className = "tool-inspector-tabs";
  tabs.setAttribute("role", "tablist");
  coreSetUIAttribute(tabs, "aria-label", corePhrase("工具详情内容"));
  for (const [key2, label] of [["result", "执行结果"], ["arguments", "调用参数"]]) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "tool-inspector-tab";
    tab.textContent = label;
    tab.id = group.inspector.id + "-" + key2;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(group.view === key2));
    tab.tabIndex = group.view === key2 ? 0 : -1;
    tab.setAttribute("aria-controls", group.inspector.id + "-pane");
    tab.addEventListener("click", () => {
      group.view = key2;
      showTool(group);
      group.inspector.children[1].children[0].children[key2 === "result" ? 0 : 1].focus();
    });
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      group.view = event.key === "Home" ? "result" : event.key === "End" ? "arguments" : key2 === "result" ? "arguments" : "result";
      showTool(group);
      group.inspector.children[1].children[0].children[group.view === "result" ? 0 : 1].focus();
    });
    tabs.append(tab);
  }
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "tool-inspector-copy";
  coreSetUIText(copy, "textContent", corePhrase("复制"));
  const content = group.view === "arguments" ? parameters : output;
  copy.addEventListener("click", () => {
    void (async () => {
      try {
        await navigator.clipboard.writeText(content);
        coreSetUIText(copy, "textContent", corePhrase("已复制"));
      } catch {
        coreSetUIText(copy, "textContent", corePhrase("复制失败"));
      }
    })();
  });
  toolbar.append(tabs, copy);
  const pane = document.createElement("pre");
  pane.tabIndex = 0;
  coreSetUIAttribute(pane, "aria-label", corePhrase("工具详情正文，可滚动查看"));
  pane.className = "tool-inspector-pane" + (group.view === "arguments" ? " is-code" : "");
  pane.id = group.inspector.id + "-pane";
  pane.setAttribute("aria-labelledby", group.inspector.id + "-" + group.view);
  pane.textContent = content;
  pane.setAttribute("role", "tabpanel");
  coreSetUIAttribute(pane, "aria-label", group.view === "arguments" ? corePhrase("调用参数") : corePhrase("执行结果"));
  group.inspector.append(head, toolbar, pane);
}
function appendTool(chat2, frame) {
  let group = toolGroups.get(chat2);
  if (!group || chat2.children[chat2.children.length - 1] !== group.row) {
    const row2 = document.createElement("section");
    row2.className = "line tool tool-group";
    const summary2 = document.createElement("div");
    summary2.className = "tool-head";
    const body = document.createElement("div");
    body.className = "tool-process";
    const inspector = document.createElement("section");
    inspector.className = "tool-inspector";
    inspector.hidden = true;
    inspector.id = "tool-inspector-" + ++toolInspectorSerial;
    coreSetUIAttribute(inspector, "aria-label", corePhrase("工具参数与结果"));
    row2.append(summary2, body, inspector);
    group = { row: row2, summary: summary2, body, inspector, selected: null, view: "result", steps: [] };
    toolGroups.set(chat2, group);
    chat2.append(row2);
  }
  const name = frame.tool || "未命名工具";
  const ref = (frame.display_id || "").replace(/:function_(call|result)$/, "");
  const stage = frame.stage === "result" ? "result" : "call";
  let step = ref ? group.steps.find((s) => s.ref === ref && s.name === name) : void 0;
  if (!ref && stage === "result") {
    const pending5 = group.steps.filter((s) => s.name === name && s.call && !s.result && !s.ref);
    if (pending5.length === 1) step = pending5[0];
  }
  if (!step) {
    const element3 = document.createElement("button");
    element3.type = "button";
    const context = document.createElement("span");
    context.className = "tool-process-context";
    step = { name, ref, element: element3, context };
    const selectedStep = step;
    const selectedGroup = group;
    element3.addEventListener("click", () => {
      selectedGroup.selected = selectedGroup.selected === selectedStep ? null : selectedStep;
      selectedGroup.view = "result";
      showTool(selectedGroup);
    });
    if (group.steps.length) {
      const separator = document.createElement("span");
      separator.className = "tool-process-separator";
      separator.textContent = "；";
      group.body.append(separator);
    }
    group.steps.push(step);
    group.body.append(context, element3);
  }
  step[stage] = frame;
  let titles = knownToolTitles.get(chat2);
  if (!titles) {
    titles = /* @__PURE__ */ new Map();
    knownToolTitles.set(chat2, titles);
  }
  if (stage === "result") rememberToolTitles(frame.text, titles);
  const failed = toolReceiptFailed(step.name, step.result?.text);
  step.element.className = "tool-process-link" + (failed ? " has-error" : step.result ? " is-returned" : " is-pending");
  step.context.textContent = activityText(step, chat2) + " ";
  step.element.textContent = name;
  if (name === "handoff_note") {
    coreSetUIText(step.context, "textContent", corePhrase("交接笔记 · 点击查看 "));
    handoffGroups.set(chat2, group);
  }
  coreSetUIText(step.element, "title", [[name, corePhrase(" · ")], failed ? corePhrase("执行失败") : step.result ? step.call ? corePhrase("已返回") : corePhrase("仅有结果") : corePhrase("等待结果")]);
  coreSetUIAttribute(step.element, "aria-label", [step.element.title, corePhrase("，查看参数和结果")]);
  step.element.setAttribute("aria-expanded", String(group.selected === step));
  step.element.setAttribute("aria-controls", group.inspector.id);
  const calls = group.steps.filter((s) => s.call).length;
  const results = group.steps.filter((s) => s.result).length;
  const failures = group.steps.filter((s) => toolReceiptFailed(s.name, s.result?.text)).length;
  group.summary.replaceChildren();
  const title = document.createElement("span");
  title.className = "tool-group-title";
  coreSetUIText(title, "textContent", corePhrase("过程"));
  const count4 = document.createElement("span");
  count4.className = "tool-group-count";
  coreSetUIText(count4, "textContent", [[[calls, corePhrase(" 次调用 · ")], results], corePhrase(" 条结果")]);
  group.summary.append(title, count4);
  if (failures) {
    const state3 = document.createElement("span");
    state3.className = "tool-group-status has-error";
    coreSetUIText(state3, "textContent", [failures, corePhrase(" 项失败")]);
    group.summary.append(state3);
  }
  if (group.selected === step) showTool(group);
  return group.row;
}
function stickToTheBottom(el6) {
  if (!el6) return;
  for (let one = el6; one; one = one.parentElement) {
    if (one.scrollHeight > one.clientHeight + 1) {
      one.scrollTop = one.scrollHeight;
    }
  }
}
var visibleBatches = /* @__PURE__ */ new WeakMap();
function appendFrame(chat2, frame, theirs = NEUTRAL) {
  let drawn;
  const batch = visibleBatches.get(chat2);
  const handoffReceipt = frame.kind === "tool" && frame.tool === "handoff_note";
  if (batch && !handoffReceipt && frame.kind !== A_BATCH && frame.kind !== "batch-status" && frame.kind !== "system-notice") {
    batch.content = true;
    batch.heading.style.display = "";
  }
  if (frame.kind === "system-notice") {
    const row2 = document.createElement("section");
    row2.className = "console-system-notice";
    const summary2 = document.createElement("button");
    summary2.type = "button";
    const body = document.createElement("div");
    body.className = "console-system-detail";
    const text2 = frame.text.replace(/^\[system\]\s*/i, "");
    const heading2 = text2.split(/[。\n]/)[0];
    coreSetUIText(summary2, "textContent", [corePhrase("系统 · "), heading2.length > 48 ? [heading2.slice(0, 48), corePhrase("…")] : heading2]);
    summary2.title = frame.at || "查看系统事件";
    summary2.addEventListener("click", () => showTextDetail("系统事件", text2));
    body.textContent = text2;
    row2.append(summary2);
    const handoff = /^交接完/.test(text2) ? handoffGroups.get(chat2) : void 0;
    if (handoff && chat2.contains(handoff.row)) {
      handoff.row.prepend(row2);
      chat2.append(handoff.row);
      toolGroups.set(chat2, handoff);
      handoffGroups.delete(chat2);
    } else {
      chat2.append(row2);
    }
    drawn = row2;
  } else if (frame.kind === A_BATCH) {
    const row2 = document.createElement("p");
    row2.className = "line batch";
    row2.style.display = "none";
    visibleBatches.set(chat2, { heading: row2, content: false });
    coreSetUIText(row2, "textContent", [corePhrase("第 "), frame.text.slice(2, -1), corePhrase(" 批")]);
    drawn = row2;
    chat2.append(row2);
  } else if (frame.kind === "batch-status") {
    const row2 = document.createElement("div");
    row2.className = "console-control-note console-completed";
    const exceptional = /失败|停止|中断|错误/.test(frame.text);
    if ((!batch || !batch.content) && !exceptional) row2.style.display = "none";
    if (batch && exceptional) batch.heading.style.display = "";
    const label = document.createElement("span");
    label.textContent = frame.text.replace(/^本轮处理已结束$/, "本轮已结束").replace(/^已处理 (\d+) 秒$/, "用时 $1 秒").replace(/^用时 0 秒$/, "已结束 · 不足 1 秒").replace(/^已停止 · 已处理 (\d+) 秒$/, "已停止 · 用时 $1 秒");
    if (frame.at) label.title = frame.at;
    row2.append(label);
    drawn = row2;
    chat2.append(row2);
  } else if (frame.kind === "tool") {
    drawn = appendTool(chat2, frame);
  } else {
    drawn = bubble(frame, theirs);
    chat2.append(drawn);
  }
  stickToTheBottom(chat2);
  return drawn;
}
function appendNote(chat2, text2, action) {
  const row2 = document.createElement("p");
  row2.className = "line note console-control-note console-feedback";
  const label = document.createElement("span");
  label.textContent = text2;
  if (action) label.append(action);
  row2.append(label);
  chat2.append(row2);
  stickToTheBottom(chat2);
}
function mountChat(host) {
  const column = document.createElement("div");
  column.className = "live";
  const title = document.createElement("h1");
  title.className = "page-title";
  coreSetUIText(title, "textContent", corePhrase("控制台 · CONSOLE"));
  const stream = document.createElement("div");
  stream.className = "live-stream";
  const back = () => {
    if (document.visibilityState !== "hidden") stickToTheBottom(stream);
  };
  document.addEventListener("visibilitychange", back);
  window.addEventListener("focus", back);
  back();
  const page2 = document.createElement("section");
  page2.className = "page-live";
  const header = document.createElement("header");
  header.className = "live-page-head";
  header.append(title);
  column.append(stream);
  page2.append(header, column);
  host.replaceChildren(page2);
  let theirs = speaker();
  let watcher = null;
  const pending5 = [];
  let intentSequence = 0;
  const view2 = {
    root: column,
    listen: (active2) => {
      document.removeEventListener("visibilitychange", back);
      window.removeEventListener("focus", back);
      if (active2) {
        document.addEventListener("visibilitychange", back);
        window.addEventListener("focus", back);
      }
    },
    append: (wire2) => {
      const frame = asFrame(wire2);
      if (frame.kind === A_BATCH && pending5.length > 0) {
        const head = appendFrame(stream, frame, theirs);
        stream.insertBefore(head, pending5[0].row);
        head.style.display = "";
        const batch = visibleBatches.get(stream);
        if (batch) batch.content = true;
        if (watcher !== null) {
          watcher();
        }
        return;
      }
      appendFrame(stream, frame, theirs);
      if (watcher !== null) {
        watcher();
      }
    },
    watch: (fn) => {
      watcher = fn;
    },
    /** 名字到位（或换了一份档案）⇒ 记下来 ＋ **把已经画上去的那些重画一遍**。 */
    setSpeaker: (name) => {
      theirs = name;
      for (const row2 of stream.querySelectorAll(".msg")) {
        if (row2.classList.contains("mine")) {
          const who2 = row2.querySelector(".msg-who");
          if (who2 !== null) who2.textContent = userLabel();
          continue;
        }
        const who = row2.querySelector(".msg-who");
        const face = row2.querySelector(".msg-avatar");
        if (who !== null) who.textContent = theirs;
        if (face !== null) face.textContent = theirs.slice(0, 1);
      }
    },
    /** **我说的话** —— 立刻画一条自己的气泡；带图的话把缩略图也画上 。
     * ⚠️ 画完滚到底（`appendFrame` 自己会滚一次，而缩略图是在那之后加的 ⇒ 再滚一次 ）。 */
    mine: (text2, images) => {
      const row2 = appendFrame(stream, { kind: A_MESSAGE, who: ME, at: null, text: text2 }, theirs);
      const id = String(++intentSequence);
      pending5.push({ id, text: text2, row: row2 });
      row2.setAttribute("data-delivery", "pending");
      const stamp = row2.querySelector(".msg-at");
      if (stamp) coreSetUIText(stamp, "textContent", corePhrase("发送中，尚未确认"));
      if (images.length > 0) {
        const strip = document.createElement("div");
        strip.className = "msg-thumbs";
        for (const url of images) {
          const picture = document.createElement("img");
          picture.src = url;
          picture.alt = "已发送图片";
          strip.append(picture);
        }
        row2.append(strip);
        column.scrollTop = column.scrollHeight;
      }
      return id;
    },
    clearPending: (id) => {
      const index = pending5.findIndex((item) => item.id === id);
      if (index >= 0) pending5.splice(index, 1)[0].row.remove();
    },
    note: (text2, action) => appendNote(stream, text2, action)
  };
  const directAppend = view2.append;
  const feed = new HistoryFeed(directAppend, (frames) => {
    stream.replaceChildren();
    for (const frame of frames) directAppend(JSON.stringify(frame));
    for (const item of pending5) stream.append(item.row);
  });
  view2.append = (wire2) => feed.receive(wire2);
  view2.beginHistory = () => feed.begin();
  view2.finishHistory = (ticket, frames) => feed.finish(ticket, frames);
  view2.failHistory = (ticket) => feed.fail(ticket);
  return view2;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/live.ts
var EXPORT_LABEL = "导出诊断";
var SEND_LABEL = "▶";
var HANDOFF_LABEL = "交接";
function gaugeIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  const trace = document.createElementNS(ns, "circle");
  trace.setAttribute("cx", "8");
  trace.setAttribute("cy", "8");
  trace.setAttribute("r", "5.3");
  trace.setAttribute("fill", "none");
  trace.setAttribute("stroke", "currentColor");
  trace.setAttribute("stroke-width", "1.5");
  trace.setAttribute("opacity", "0.35");
  const mark = document.createElementNS(ns, "path");
  mark.setAttribute("d", "M8 2.7A5.3 5.3 0 0 0 3.3 6.1");
  mark.setAttribute("fill", "none");
  mark.setAttribute("stroke", "currentColor");
  mark.setAttribute("stroke-width", "1.8");
  mark.setAttribute("stroke-linecap", "round");
  svg.append(trace, mark);
  return svg;
}
function imageIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const frame = document.createElementNS(ns, "rect");
  frame.setAttribute("x", "2.6");
  frame.setAttribute("y", "3.6");
  frame.setAttribute("width", "14.8");
  frame.setAttribute("height", "12.8");
  frame.setAttribute("rx", "2.4");
  frame.setAttribute("fill", "none");
  frame.setAttribute("stroke", "currentColor");
  frame.setAttribute("stroke-width", "1.5");
  const dot = document.createElementNS(ns, "circle");
  dot.setAttribute("cx", "7.2");
  dot.setAttribute("cy", "8.2");
  dot.setAttribute("r", "1.4");
  dot.setAttribute("fill", "currentColor");
  const hill = document.createElementNS(ns, "path");
  hill.setAttribute("d", "M3.6 15.4l4.2-4.2 2.5 2.5 2.5-2.5 3.6 3.6");
  hill.setAttribute("fill", "none");
  hill.setAttribute("stroke", "currentColor");
  hill.setAttribute("stroke-width", "1.5");
  hill.setAttribute("stroke-linecap", "round");
  hill.setAttribute("stroke-linejoin", "round");
  svg.append(frame, dot, hill);
  return svg;
}
var MAX_SAY_BYTES = 16 * 1024 * 1024;
var MAX_IMAGES = 8;
var MAX_IMAGE_BYTES = 6 * 1024 * 1024;
var MAX_IMAGE_EDGE = 2048;
var IMAGE_MIMES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
function fitWithin(width, height, maxEdge) {
  const edge = Math.max(width, height);
  if (!(edge > maxEdge) || maxEdge <= 0) {
    return { width, height };
  }
  const scale = maxEdge / edge;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale))
  };
}
function readAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.onload = () => {
      resolve(String(reader.result ?? ""));
    };
    reader.readAsDataURL(blob);
  });
}
function encodeTo(bitmap, width, height, mime, quality) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("当前浏览器无法处理图片");
  }
  ctx.drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("图片转换失败")),
      mime,
      quality
    );
  });
}
async function normalizeImage(file) {
  if (!IMAGE_MIMES.includes(file.type)) {
    throw new Error("请选择 JPEG、PNG、WebP 或 GIF 图片");
  }
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("无法打开图片，文件格式不支持或已损坏");
  }
  try {
    const fitted = fitWithin(bitmap.width, bitmap.height, MAX_IMAGE_EDGE);
    const untouched = fitted.width === bitmap.width && fitted.height === bitmap.height && file.size <= MAX_IMAGE_BYTES;
    if (untouched) {
      return await readAsDataUrl(file);
    }
    const preferred = file.type === "image/gif" ? "image/png" : file.type;
    let blob = await encodeTo(bitmap, fitted.width, fitted.height, preferred);
    if (blob.size > MAX_IMAGE_BYTES && preferred !== "image/jpeg") {
      blob = await encodeTo(bitmap, fitted.width, fitted.height, "image/jpeg", 0.85);
    }
    if (blob.size > MAX_IMAGE_BYTES) {
      throw new Error("图片压缩后仍超过 6 MB，请先缩小图片");
    }
    return await readAsDataUrl(blob);
  } finally {
    bitmap.close();
  }
}
var PLACEHOLDER = "输入消息…";
function composer(onSend, onNote, onStop, canSend = () => true) {
  const form = document.createElement("form");
  form.className = "composer";
  form.autocomplete = "off";
  coreSetUIAttribute(form, "aria-label", corePhrase("消息输入"));
  const box = document.createElement("div");
  box.className = "composer-box";
  const input2 = document.createElement("textarea");
  input2.id = "line";
  input2.name = "line";
  input2.rows = 1;
  input2.placeholder = PLACEHOLDER;
  coreSetUIAttribute(input2, "aria-label", corePhrase("说一句"));
  const row2 = document.createElement("div");
  row2.className = "composer-row";
  const exportButton = document.createElement("button");
  exportButton.type = "button";
  exportButton.className = "btn ghost composer-export";
  exportButton.textContent = EXPORT_LABEL;
  coreSetUIText(exportButton, "title", corePhrase("下载诊断 ZIP，包含最近对话和工具回执，转发前请核对内容。"));
  const diagnosticDownload = document.createElement("a");
  diagnosticDownload.className = "composer-diagnostic-download";
  coreSetUIText(diagnosticDownload, "textContent", corePhrase("下载诊断包"));
  diagnosticDownload.hidden = true;
  let diagnosticUrl = null;
  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    coreSetUIText(exportButton, "textContent", corePhrase("正在生成…"));
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 3e4);
    try {
      const response = await pageFetch("/diagnostics/export", { method: "POST", signal: controller.signal });
      if (!response.ok) throw new Error("diagnostic export failed");
      const blob = await response.blob();
      if (diagnosticUrl) URL.revokeObjectURL(diagnosticUrl);
      diagnosticUrl = URL.createObjectURL(blob);
      diagnosticDownload.href = diagnosticUrl;
      diagnosticDownload.download = `telyincore-diagnose-${(/* @__PURE__ */ new Date()).toISOString().replace(/[:.]/g, "-").slice(0, 19)}.zip`;
      diagnosticDownload.hidden = false;
      onNote("诊断包已生成；未自动下载可点击 ", diagnosticDownload);
      diagnosticDownload.click();
    } catch {
      onNote(controller.signal.aborted ? "诊断生成超时，请重试。" : "导出诊断失败，请重试。");
    } finally {
      window.clearTimeout(timeout);
      exportButton.disabled = false;
      exportButton.textContent = EXPORT_LABEL;
    }
  });
  const handoffButton = document.createElement("button");
  handoffButton.type = "button";
  handoffButton.className = "btn ghost composer-handoff";
  handoffButton.textContent = HANDOFF_LABEL;
  coreSetUIText(handoffButton, "title", [corePhrase("整理当前对话摘要，并在新的上下文中继续。"), corePhrase("相关记忆会在后台整理。")]);
  handoffButton.addEventListener("click", () => {
    void pageFetch("/handoff", { method: "POST" }).then(async (res) => {
      if (!res.ok) {
        onNote(res.status === 500 ? "交接失败，请导出诊断" : `交接暂不可用（${res.status}）`);
        return;
      }
      const got = await res.json();
      if (got.answered === false) {
        onNote("交接进行中，请稍候");
        return;
      }
      if (got.asked === false) {
        onNote("交接已在进行中");
        return;
      }
      onNote("交接已完成，整理任务已安排");
    }).catch(() => {
      onNote("交接状态未确认，请检查连接");
    });
  });
  const grow = document.createElement("span");
  grow.className = "grow";
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "image/*";
  picker.multiple = true;
  picker.hidden = true;
  let attached = [];
  const usagePanel = document.createElement("div");
  usagePanel.className = "composer-usage";
  usagePanel.hidden = true;
  const USAGE_LABEL = {
    framing: "结构 · 基座",
    prefix: "系统消息 · 人设",
    tools: "工具声明 · 插件",
    dialogue: "对话 · 往来"
  };
  const usageLine = (text2, cls) => {
    const one = document.createElement("div");
    one.className = cls;
    one.textContent = text2;
    return one;
  };
  const drawCache = (got) => {
    const batch = typeof got.batch_timing === "object" && got.batch_timing !== null ? got.batch_timing : null;
    const t = batch ?? (typeof got.timing === "object" && got.timing !== null ? got.timing : null);
    const total = batch?.total ?? t?.total;
    const title = batch ? "上一批用时" : "最近一次模型请求";
    coreSetUIText(whenLabel, "textContent", typeof total === "number" ? [corePhrase(""), batch ? "共" : "请求", corePhrase(" "), total.toFixed(1), corePhrase("s")] : corePhrase("共 —"));
    coreSetUIText(whenCell, "title", [corePhrase(""), title, corePhrase("（点一下看明细）")]);
    whenBody.replaceChildren();
    const whenHead = document.createElement("div");
    whenHead.className = "usage-head";
    whenHead.append(usageLine(title, "usage-title"));
    whenHead.append(usageLine(typeof total === "number" ? `${total.toFixed(1)}s` : "—", "usage-num"));
    whenBody.append(whenHead);
    whenBody.append(usageLine(batch ? `与消息结束提示使用同一计时。已记录 ${batch.requests ?? 0} 次模型请求；分段不含工具执行等间隔。` : "单次请求时间；首段可能是思考或正文，等待包含网络与服务端处理。", "usage-note"));
    const stages = [
      ["等待首段输出", t?.thinking],
      ["流式生成", t?.output],
      ...batch ? [["模型请求合计", batch.model_total]] : [["ASR", t?.asr], ["TTS", t?.tts]]
    ];
    for (let i = 0; i < stages.length; i++) {
      const [name, value] = stages[i];
      const row3 = document.createElement("div");
      row3.className = "usage-row";
      const dot = document.createElement("span");
      dot.className = `usage-dot usage-stage-${i}`;
      row3.append(dot);
      row3.append(usageLine(name, "usage-name"));
      row3.append(usageLine("", "usage-pct"));
      row3.append(usageLine(secs(value), "usage-tok"));
      whenBody.append(row3);
    }
    const cache = cachePresentation(got);
    cacheLabel.textContent = cache.label;
    cacheCell.title = cache.title;
    cacheBody.replaceChildren();
    const cacheHead = document.createElement("div");
    cacheHead.className = "cache-summary-head";
    cacheHead.append(usageLine(cache.heading, ""), usageLine(cache.amount, ""));
    cacheBody.append(cacheHead);
    for (const [name, value] of cache.rows) {
      const row3 = document.createElement("div");
      row3.className = "cache-summary-row";
      row3.append(usageLine(name, ""), usageLine(value, ""));
      cacheBody.append(row3);
    }
    cacheBody.append(usageLine(cache.note, "cache-summary-note"));
    const diagnostic = document.createElement("section");
    diagnostic.className = "cache-diagnostics";
    const summary2 = document.createElement("h3");
    coreSetUIText(summary2, "textContent", corePhrase("诊断"));
    diagnostic.append(summary2);
    for (const line of cache.diagnostics) {
      diagnostic.append(usageLine(line, "usage-note"));
    }
    cacheBody.append(diagnostic);
    const woke = typeof got.next_wake === "object" && got.next_wake !== null ? got.next_wake : null;
    if (woke !== null && typeof woke.at === "string") {
      const what = typeof woke.what === "string" && woke.what !== "" ? woke.what : "主动唤醒";
      coreSetUIText(cacheCell, "title", [corePhrase(""), cacheCell.title, corePhrase("\n下一次"), what, corePhrase("："), woke.at.replace("T", " ").slice(0, 16), corePhrase("")]);
    }
  };
  const drawUsage = (got) => {
    const window2 = typeof got.window === "number" ? got.window : null;
    const total = typeof got.total === "number" ? got.total : null;
    const parts = typeof got.parts === "object" && got.parts !== null ? got.parts : {};
    const share = (n) => total && total > 0 ? Math.round(n / total * 100) : 0;
    const num = (n) => n.toLocaleString("en-US");
    const kfmt = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
    usagePanel.replaceChildren();
    const head = document.createElement("div");
    head.className = "usage-head";
    head.append(usageLine(got.estimate_source === "completed_batch_snapshot" ? "上一批输入估算 / 当前配置窗口" : got.estimate_source === "standby_archive_preview" ? "待机基础上下文估算" : "上下文估算", "usage-title"));
    head.append(usageLine(total === null ? "—" : `~${num(total)}${window2 === null ? "" : ` / ${kfmt(window2)}`} tok`, "usage-num"));
    const ofWindow = total !== null && window2 ? Math.round(total / window2 * 100) : null;
    usagePanel.append(head);
    usagePanel.append(usageLine(
      ofWindow === null ? "预算未知" : `占预算 ${ofWindow}%`,
      "usage-sub"
    ));
    if (got.estimate_source === "completed_batch_snapshot" && typeof got.estimate_captured_at_ms === "number") {
      usagePanel.append(usageLine(`快照 ${new Date(got.estimate_captured_at_ms).toLocaleTimeString()}`, "usage-sub"));
    }
    if (got.estimate_source === "standby_archive_preview") {
      usagePanel.append(usageLine("静候预览，不含本轮动态信息", "usage-sub"));
    }
    const bar2 = document.createElement("div");
    bar2.className = "usage-bar";
    const usedBar = document.createElement("div");
    usedBar.className = "usage-used";
    usedBar.style.width = `${ofWindow === null ? 0 : ofWindow}%`;
    for (const key2 of Object.keys(USAGE_LABEL)) {
      const slice = document.createElement("span");
      slice.className = `usage-slice usage-${key2}`;
      slice.style.width = `${share(parts[key2] ?? 0)}%`;
      usedBar.append(slice);
    }
    bar2.append(usedBar);
    usagePanel.append(bar2);
    for (const key2 of Object.keys(USAGE_LABEL)) {
      const size = parts[key2];
      const row3 = document.createElement("div");
      row3.className = "usage-row";
      const dot = document.createElement("span");
      dot.className = `usage-dot usage-${key2}`;
      row3.append(dot);
      row3.append(usageLine(USAGE_LABEL[key2], "usage-name"));
      row3.append(usageLine(
        size === null || size === void 0 ? "—" : `${share(size)}%`,
        "usage-pct"
      ));
      row3.append(usageLine(size === null || size === void 0 ? "—" : num(size), "usage-tok"));
      usagePanel.append(row3);
    }
  };
  const thumbs = document.createElement("div");
  thumbs.className = "composer-thumbs";
  thumbs.hidden = true;
  const glyph = document.createElement("button");
  glyph.type = "button";
  glyph.className = "composer-glyph";
  const glyphCount = document.createElement("span");
  glyphCount.className = "composer-count";
  glyph.append(imageIcon(), glyphCount);
  box.append(thumbs, usagePanel, input2);
  coreSetUIText(glyph, "title", corePhrase("载入图片（也可以拖进来 ／ 粘贴）—— 随这一行一起发；回车是发送，Shift+回车换行"));
  const drawAttached = () => {
    glyphCount.textContent = attached.length === 0 ? "" : String(attached.length);
    glyph.dataset.on = attached.length === 0 ? "no" : "yes";
    thumbs.replaceChildren();
    attached.forEach((url, index) => {
      const cell = document.createElement("span");
      cell.className = "composer-thumb";
      const picture = document.createElement("img");
      picture.src = url;
      picture.alt = "待发送图片";
      const drop = document.createElement("button");
      drop.type = "button";
      drop.className = "composer-thumb-x";
      drop.textContent = "×";
      coreSetUIText(drop, "title", corePhrase("移除图片"));
      drop.addEventListener("click", () => {
        attached.splice(index, 1);
        drawAttached();
      });
      cell.append(picture, drop);
      thumbs.append(cell);
    });
    thumbs.hidden = attached.length === 0;
  };
  const addFiles = async (files) => {
    for (const file of files) {
      if (attached.length >= MAX_IMAGES) {
        onNote("一次最多 8 张");
        break;
      }
      try {
        attached.push(await normalizeImage(file));
      } catch (exc) {
        onNote(exc instanceof Error ? exc.message : "图片读取失败");
      }
    }
    drawAttached();
  };
  glyph.addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => {
    void addFiles(picker.files ?? []);
    picker.value = "";
  });
  const ring = document.createElement("button");
  ring.type = "button";
  ring.className = "composer-ring";
  const ringLabel = document.createElement("span");
  ringLabel.textContent = "—";
  ring.append(gaugeIcon(), ringLabel);
  coreSetUIText(ring, "title", corePhrase("查看上下文用量明细"));
  const readCache = latestCacheRead(pageFetch);
  const showUsage = async () => {
    await readCache((got) => {
      const used = typeof got.total === "number" ? got.total : null;
      const window2 = typeof got.window === "number" ? got.window : null;
      const percent2 = used !== null && window2 !== null && window2 > 0 ? Math.round(used / window2 * 100) : null;
      ringLabel.textContent = percent2 === null ? "—" : `${percent2}%`;
      coreSetUIText(ring, "title", percent2 === null ? [corePhrase("上下文估算暂不可用"), window2 !== null ? `；当前模型配置窗口 ${window2} token` : "", corePhrase("")] : [corePhrase(""), got.estimate_source === "completed_batch_snapshot" ? "上一批输入估算 / 当前配置窗口" : got.estimate_source === "standby_archive_preview" ? "待机基础上下文估算" : "上下文估算", corePhrase(" "), used, corePhrase(" ／ "), window2, corePhrase(" token（"), percent2, corePhrase("%）")]);
      drawCache(got);
      if (!usagePanel.hidden) {
        drawUsage(got);
      }
      positionComposerPopover();
    });
  };
  const databaseIcon = () => {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    const lid = document.createElementNS(ns, "ellipse");
    lid.setAttribute("cx", "8");
    lid.setAttribute("cy", "4");
    lid.setAttribute("rx", "5.2");
    lid.setAttribute("ry", "2.1");
    lid.setAttribute("fill", "none");
    lid.setAttribute("stroke", "currentColor");
    lid.setAttribute("stroke-width", "1.3");
    const body = document.createElementNS(ns, "path");
    body.setAttribute("d", "M2.8 4v8c0 1.15 2.35 2.1 5.2 2.1s5.2-.95 5.2-2.1V4");
    body.setAttribute("fill", "none");
    body.setAttribute("stroke", "currentColor");
    body.setAttribute("stroke-width", "1.3");
    const band = document.createElementNS(ns, "path");
    band.setAttribute("d", "M2.8 8c0 1.15 2.35 2.1 5.2 2.1s5.2-.95 5.2-2.1");
    band.setAttribute("fill", "none");
    band.setAttribute("stroke", "currentColor");
    band.setAttribute("stroke-width", "1.3");
    svg.append(lid, body, band);
    return svg;
  };
  const cacheCell = document.createElement("button");
  cacheCell.type = "button";
  cacheCell.className = "composer-cache";
  const cacheLabel = document.createElement("span");
  coreSetUIText(cacheLabel, "textContent", corePhrase("缓存命中 —"));
  cacheCell.append(databaseIcon(), cacheLabel);
  coreSetUIText(cacheCell, "title", corePhrase("当前控制台 · 当前模型累计输入用量与缓存命中"));
  cacheCell.setAttribute("aria-expanded", "false");
  const cachePanel = document.createElement("div");
  cachePanel.className = "composer-stages composer-cache-details";
  cachePanel.hidden = true;
  const cacheBody = document.createElement("div");
  cachePanel.append(cacheBody);
  const whenCell = document.createElement("button");
  whenCell.type = "button";
  whenCell.className = "composer-when";
  const secs = (n) => typeof n === "number" ? `${n.toFixed(1)}s` : "—";
  const whenLabel = document.createElement("span");
  coreSetUIText(whenLabel, "textContent", corePhrase("共 —"));
  whenCell.append(whenLabel);
  coreSetUIText(whenCell, "title", corePhrase("上一轮：思考 ／ 输出各花了多久（点一下看明细）"));
  const whenPanel = document.createElement("div");
  whenPanel.className = "composer-stages composer-time-details";
  whenPanel.hidden = true;
  const whenBody = document.createElement("div");
  whenPanel.append(whenBody);
  wireComposerPopovers([
    { trigger: ring, panel: usagePanel, label: "上下文占用" },
    { trigger: whenCell, panel: whenPanel, label: "回复用时" },
    { trigger: cacheCell, panel: cachePanel, label: "输入用量与缓存命中" }
  ], () => {
    void showUsage();
  });
  const send = document.createElement("button");
  send.type = "submit";
  send.className = "composer-send";
  send.textContent = SEND_LABEL;
  coreSetUIText(send, "title", corePhrase("发送消息 · Enter（Shift+Enter 换行）"));
  coreSetUIAttribute(send, "aria-label", corePhrase("发送消息"));
  let processing = false;
  let interrupting = false;
  const setProcessing = (active2, stopping = false) => {
    processing = active2;
    interrupting = stopping;
    send.dataset.processing = String(active2);
    send.textContent = active2 ? "■" : SEND_LABEL;
    coreSetUIText(send, "title", stopping ? corePhrase("正在停止，请稍候") : active2 ? corePhrase("停止当前处理") : corePhrase("发送消息 · Enter（Shift+Enter 换行）"));
    coreSetUIAttribute(send, "aria-label", stopping ? corePhrase("正在停止") : active2 ? corePhrase("停止当前处理") : corePhrase("发送消息"));
    send.disabled = stopping;
  };
  send.addEventListener("click", (event) => {
    if (processing) {
      event.preventDefault();
      if (!interrupting) onStop?.();
    }
  });
  row2.append(exportButton, handoffButton, ring, whenCell, cacheCell, grow, glyph, send);
  box.append(cachePanel);
  box.append(whenPanel);
  form.append(box, row2);
  const refresh2 = () => {
    void showUsage();
  };
  input2.addEventListener("input", refresh2);
  void showUsage();
  input2.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!canSend()) return;
    const text2 = input2.value;
    if (text2.trim() === "" && attached.length === 0) {
      void showUsage();
      return;
    }
    const images = attached;
    attached = [];
    drawAttached();
    input2.value = "";
    onSend(text2, images);
    void showUsage();
  });
  return { form, refresh: refresh2, setProcessing };
}
function chatPage(shell2) {
  const chat2 = mountChat(shell2.content);
  const page2 = chat2.root.parentElement ?? chat2.root;
  const activity = document.createElement("div");
  activity.className = "console-control-note";
  activity.setAttribute("role", "status");
  activity.setAttribute("aria-live", "polite");
  activity.hidden = true;
  const activityBadge = document.createElement("span");
  const activityIcon = document.createElement("i");
  activityIcon.className = "console-activity-icon";
  activityIcon.setAttribute("aria-hidden", "true");
  const activityText2 = document.createElement("span");
  activityText2.className = "console-activity-text";
  activityBadge.append(activityIcon, activityText2);
  activity.append(activityBadge);
  const showActivity = (text2, busy = false) => {
    const changed = activityText2.textContent !== text2 || activity.hidden === !!text2;
    const atBottom = chat2.root.scrollHeight - chat2.root.scrollTop - chat2.root.clientHeight < 60;
    if (activityText2.textContent !== text2) activityText2.textContent = text2;
    activity.dataset.busy = String(busy);
    activity.hidden = !text2;
    if (changed && atBottom) requestAnimationFrame(() => stickToTheBottom(activity));
  };
  chat2.root.append(activity);
  let roundId = null;
  let wasProcessing = false;
  let gatePaused = false;
  let hasActiveModel = null;
  let activityRevision = 0;
  const readActivity = (event) => {
    const state3 = event.detail;
    if (!state3 || typeof state3.processing !== "boolean") return;
    if ("model" in state3) hasActiveModel = typeof state3.model === "string" && !!state3.model.trim();
    if (typeof state3.stopped_by_hand === "boolean") gatePaused = state3.stopped_by_hand;
    if (wasProcessing !== state3.processing || roundId !== (state3.round_id ?? null)) activityRevision++;
    roundId = state3.round_id ?? null;
    setProcessing(state3.processing, state3.interrupting === true);
    if (state3.processing) showActivity(state3.interrupting ? "正在中断并收尾…" : state3.yielding ? "正在收尾，随后转接…" : "正在处理…", true);
    else if (state3.activity?.background_running > 0) showActivity("正在整理…", true);
    else if (state3.activity?.local_observations?.uncertain > 0) showActivity("有本地输入的处理结果待核对，不会自动重试。");
    else if (state3.activity?.state === "paused") showActivity("静候中，新事件会保留。");
    else if (wasProcessing) showActivity("");
    wasProcessing = state3.processing;
  };
  window.addEventListener("core-console-state", readActivity);
  window.addEventListener("core-control-feedback", (event) => {
    showActivity(event.detail?.text ?? "");
  });
  const stopCurrent = () => {
    const requested3 = roundId;
    if (!requested3) return;
    setProcessing(true, true);
    showActivity("正在停止，请稍候…", true);
    void pageFetch("/interrupt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ round_id: requested3 })
    }).then(async (response) => {
      const state3 = await response.json();
      if (roundId === requested3 && typeof state3.processing === "boolean") {
        readActivity(new CustomEvent("core-console-state", { detail: state3 }));
      }
      if (!response.ok) showActivity(response.status === 409 ? "本轮已结束，请等待状态更新" : "停止状态未确认，请检查连接");
      window.dispatchEvent(new Event("core-control-refresh"));
    }).catch(() => {
      showActivity("停止状态未确认，请检查连接");
      window.dispatchEvent(new Event("core-control-refresh"));
    });
  };
  const { form, refresh: refresh2, setProcessing } = composer((text2, images) => {
    const intent = chat2.mine(text2, images);
    const body = JSON.stringify({ text: text2, images });
    const bodyBytes = new TextEncoder().encode(body).byteLength;
    if (bodyBytes > MAX_SAY_BYTES) {
      chat2.clearPending(intent);
      const mb = Math.round(bodyBytes / 1024 / 1024 * 10) / 10;
      chat2.note(`这一次太大了（约 ${mb} MB，上限 ${MAX_SAY_BYTES / 1024 / 1024} MB）——去掉一两张图，或把它们裁小一点。`);
      return;
    }
    if (hasEngineDraft()) {
      chat2.note(hasActiveModel === false ? "模型配置尚未保存，当前没有可用的模型配置。" : "模型配置尚未保存，本次仍使用当前已保存的配置。");
    }
    const revision = activityRevision;
    showActivity("正在发送…", true);
    void pageFetch("/say", { method: "POST", headers: { "Content-Type": "application/json" }, body }).then(async (res) => {
      chat2.clearPending(intent);
      if (res.ok) {
        if (!wasProcessing && revision === activityRevision) showActivity(gatePaused ? "消息已保留，唤醒后再处理。" : "消息已接收，等待处理…", !gatePaused);
        window.dispatchEvent(new Event("core-control-refresh"));
      }
      if (!res.ok) {
        showActivity("发送失败");
        const detail = (await res.text()).trim().slice(0, 500);
        chat2.note(`发送失败（HTTP ${res.status}）${detail ? "：" + detail : "。服务未返回原因。"}`);
      }
    }).catch(() => {
      chat2.clearPending(intent);
      showActivity("发送状态未确认");
      chat2.note("发送状态未确认，请先核对记录，避免重复发送。");
    });
  }, (said, action) => {
    chat2.note(said, action);
  }, stopCurrent, () => {
    if (gatePaused) showActivity("静候中，输入会保留，唤醒后再处理。");
    return true;
  });
  chat2.watch(refresh2);
  let stopped = false;
  chat2.refreshHistory = () => {
    const ticket = chat2.beginHistory();
    void pageFetch("/history").then((res) => {
      if (!res.ok) throw new Error("history unavailable");
      return res.json();
    }).then((got) => {
      if (stopped) return;
      const box = got;
      const frames = box !== null && Array.isArray(box.frames) ? box.frames : [];
      chat2.finishHistory(ticket, frames);
    }).catch(() => {
      if (!stopped) {
        chat2.failHistory(ticket);
        chat2.note("历史记录暂不可用；重新进入可重试。");
      }
    });
  };
  chat2.refreshHistory();
  mountConsoleIntroduction(chat2.root, form);
  shell2.bottom.replaceChildren(form);
  return {
    root: page2,
    view: chat2,
    show: () => {
      const wasStopped = stopped;
      stopped = false;
      if (wasStopped) chat2.refreshHistory();
      chat2.listen(true);
      page2.hidden = false;
      chat2.root.hidden = false;
      form.hidden = false;
    },
    stop: () => {
      stopped = true;
      chat2.listen(false);
      page2.hidden = true;
      chat2.root.hidden = true;
      form.hidden = true;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/persona-memory.ts
function personaMemoryTail() {
  const tail = document.createElement("footer");
  tail.className = "persona-memory-tail page-footer";
  coreSetUIText(tail, "textContent", corePhrase("人设与记忆"));
  return tail;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/persona-page.ts
var BOUNDARY_LINE_SHORT = "修改会写入人设文件。仅限本机访问不等于账号鉴权。";
var A_STAMP = /^\d{8}-\d{6}$/;
var REPORT_ROUTE = "/persona";
var SAVE_ROUTE = "/persona";
var DEFAULT_SLOT = "edit";
var COLLISION_MARKS = [
  "那一版已经在盘上了",
  "同一秒里的第二次保存",
  "FileExistsError"
];
var COLLISION_LINE = "这一版没写成：同一秒里已经存过一版了 —— 等一秒再按一次「保存」，或者换一格版本名（slot）再存。盘上原来那一版一个字都没动。";
function editableSegments(report) {
  return (report.segments ?? []).flatMap((segment) => {
    const file = segment.files?.find((item) => item.name === segment.file);
    if (file?.sections !== void 0) {
      return file.sections.map((section) => {
        const key2 = file.name + "#" + section.id;
        const heading2 = /^#{1,6}\s+([^\r\n]+)/.exec(section.body)?.[1] ?? section.id;
        return {
          ...segment,
          name: heading2,
          file: key2,
          body: section.body,
          files: [{ name: key2, revision: file.revision }]
        };
      });
    }
    if (report.persona_scope !== "user-authored" || (segment.file ?? "").startsWith("你定的/") || segment.files?.some((item) => item.name.startsWith("你定的/"))) return [segment];
    return [];
  });
}
function stampOf(when) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${when.getFullYear()}${pad(when.getMonth() + 1)}${pad(when.getDate())}-${pad(when.getHours())}${pad(when.getMinutes())}${pad(when.getSeconds())}`;
}
function isAStamp(stamp) {
  return typeof stamp === "string" && A_STAMP.test(stamp);
}
function tokenEstimate(text2) {
  let wide = 0;
  let rest = 0;
  for (const ch of text2) {
    const code = ch.codePointAt(0) ?? 0;
    const cjk = code >= 11904 && code <= 40959 || code >= 44032 && code <= 55203 || code >= 63744 && code <= 64255 || code >= 65280 && code <= 65376;
    if (cjk) wide += 1;
    else rest += 1;
  }
  return wide + Math.ceil(rest / 4);
}
function linesOf(text2) {
  return text2.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
}
function isCollision(text2) {
  return COLLISION_MARKS.some((mark) => text2.includes(mark));
}
function outcomeOf(status, body) {
  if (status >= 200 && status < 300) {
    return { kind: "saved", bytes: 0, version: "", history: null, left: false };
  }
  if (status === 409) {
    try {
      const got = JSON.parse(body);
      if (got.error === "persona_revision_conflict") {
        return { kind: "conflict", line: "版本冲突：草稿已保留。读取新版手动合并，或确认重载后放弃草稿。" };
      }
    } catch {
    }
  }
  if (status === 400 && isCollision(body)) {
    return { kind: "collision", line: COLLISION_LINE };
  }
  return { kind: "refused", line: `保存失败（HTTP ${status}）：${body.trim()}` };
}
function hintNode(text2) {
  const hint = document.createElement("span");
  hint.className = "persona-savehint";
  hint.textContent = text2;
  return hint;
}
var THE_READABLE_TITLE = {
  "她写的那两份": "自述",
  "世界认知": "世界认知"
};
function besideLines(info) {
  const infos = info?.file_infos ?? [];
  const name = infos[0]?.name ?? "";
  const lines = [];
  for (const one of infos) {
    if (one.placeholder === true) continue;
    if (one.body === null || one.body === void 0) continue;
    if (one.body === "") continue;
    const hidden = hiddenIn(one.shape, one.body);
    linesOf(one.body).forEach((text2, index) => {
      if (hidden.has(index)) return;
      lines.push(text2);
    });
  }
  return lines.length === 0 ? null : { name, lines };
}
function draftOf(segment) {
  if (segment.file === null || segment.file === void 0) return null;
  return {
    name: segment.file,
    lines: linesOf(segment.body ?? ""),
    revision: segment.revision ?? segment.files?.find((one) => one.name === segment.file)?.revision,
    base: segment.body ?? "",
    generation: 0
  };
}
function node4(tag, cls, text2) {
  const el6 = document.createElement(tag);
  if (cls !== void 0) el6.className = cls;
  if (text2 !== void 0) el6.textContent = text2;
  return el6;
}
function surfaceOf(el6) {
  let one = el6;
  for (let hops = 0; one !== null && one !== void 0 && hops < 8; hops += 1) {
    if (one.classList !== void 0 && one.classList.contains("persona-body")) {
      return one;
    }
    one = one.parentElement ?? one.parent ?? null;
  }
  return null;
}
function ownerClass(owner) {
  if (owner === null) return "persona-owner none";
  if (owner === "跟框架走") return "persona-owner framework";
  if (owner.startsWith("归 ")) return "persona-owner world";
  return "persona-owner bot";
}
function presenceMark(count4) {
  const hasFile = count4 > 0;
  const mark = node4("span", "persona-presence");
  mark.dataset.file = hasFile ? "yes" : "no";
  mark.dataset.files = String(count4);
  return mark;
}
function ownedByTheAuthor(owner) {
  return owner === "你定的";
}
function mayWrite(segment) {
  return segment.writable !== false;
}
var SHAPE_CLASS_EMPHASIS = "12:32（写法只有一条纪律：**观测、推测、事实分开写**）";
var SHAPE_CLASS_COMMENT = "web.py::_lead_comment() → owner_of() ／ 边界.md:1（按部署可覆盖）";
function hiddenIn(shape, body) {
  const out = /* @__PURE__ */ new Set();
  for (const one of shape ?? []) {
    if (one.cite !== SHAPE_CLASS_COMMENT) continue;
    const extra = Array.from(body).slice(one.start, one.end).join("").split("\n").length - 1;
    for (let at = 0; at <= extra; at += 1) out.add(one.line - 1 + at);
  }
  return out;
}
function hiddenLines(segment) {
  const file = (segment.files ?? []).find((one) => one.name === segment.file);
  const body = segment.body ?? "";
  return hiddenIn(file?.shape, body);
}
function lockedLines(segment) {
  const out = /* @__PURE__ */ new Set();
  const file = (segment.files ?? []).find((one) => one.name === segment.file);
  const body = segment.body ?? "";
  for (const one of file?.shape ?? []) {
    if (one.cite === SHAPE_CLASS_EMPHASIS) continue;
    const extra = Array.from(body).slice(one.start, one.end).join("").split("\n").length - 1;
    for (let at = 0; at <= extra; at += 1) out.add(one.line + at);
  }
  return out;
}
function ownerText(owner) {
  return owner === null ? "—" : owner;
}
function personaPage(shell2, options = {}) {
  const now = options.now ?? (() => stampOf(/* @__PURE__ */ new Date()));
  const send = options.send ?? ((url, init) => pageFetch(url, init));
  const column = node4("div", "page-persona persona-studio");
  const stack = node4("div", "persona-column");
  const head = node4("div", "persona-head");
  const title = node4("h1", "persona-title", "人设 · PERSONA");
  const count4 = node4("span", "persona-count", "正在加载…");
  const saveHint = node4("span", "persona-hint", "修改后保存 · Ctrl+S");
  const saveButton = node4("button", "btn persona-save", "保存");
  saveButton.type = "button";
  coreSetUIText(saveButton, "title", corePhrase("保存当前章节，保留其他未保存草稿"));
  const reloadButton = node4("button", "btn ghost persona-reload", "重载");
  reloadButton.type = "button";
  coreSetUIText(reloadButton, "title", corePhrase("重新读取已保存的人设"));
  const status = node4("div", "persona-status");
  const statusLine = node4("span", "persona-status-line");
  const receipt = node4("button", "persona-receipt", "查看详情");
  receipt.type = "button";
  receipt.hidden = true;
  const receiptText = node4("p", "persona-receipt-text");
  receipt.addEventListener("click", () => showTextDetail("操作详情", receiptText.textContent || ""));
  status.append(statusLine, receipt);
  status.dataset.state = "idle";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.hidden = true;
  const bar2 = node4("div", "persona-bar");
  const meta = node4("div", "persona-meta");
  meta.append(count4, saveHint);
  bar2.append(meta, status);
  const actions = node4("div", "persona-actions");
  actions.append(saveButton, reloadButton);
  head.append(title, actions, bar2);
  const navList = node4("nav", "persona-navlist", "");
  coreSetUIAttribute(navList, "aria-label", corePhrase("人设章节"));
  const nav = node4("div", "persona-nav");
  nav.append(navList);
  const list = node4("div", "persona-segments");
  const otherList = node4("div", "persona-beside");
  const otherBox = node4("section", "persona-other");
  otherBox.append(otherList);
  otherBox.hidden = true;
  const workspace = node4("div", "persona-workspace");
  const scroll = node4("div", "page-scroll persona-scroll");
  scroll.append(list, otherBox);
  workspace.append(nav, scroll);
  stack.append(head, workspace, personaMemoryTail());
  column.append(stack);
  shell2.content.replaceChildren(column);
  let segments = [];
  const drafts = /* @__PURE__ */ new Map();
  const dirty = [];
  let editEpoch = 0;
  let saving = false;
  const conflictBox = node4("section", "persona-conflict");
  conflictBox.hidden = true;
  head.append(conflictBox);
  const say = (line, state3, detail = "") => {
    const long = line.length > 100 || line.includes("\n");
    coreSetUIText(statusLine, "textContent", long ? corePhrase("操作未完成，请查看详情。") : line);
    receiptText.textContent = detail || (long ? line : "");
    receipt.hidden = receiptText.textContent === "";
    status.dataset.state = state3;
    status.hidden = line === "";
  };
  const refreshHead = () => {
    let tokens = 0;
    for (const segment of segments) {
      const draft = segment.file === null || segment.file === void 0 ? null : drafts.get(segment.file) ?? draftOf(segment);
      tokens += draft === null ? 0 : tokenEstimate(draft.lines.join("\n"));
    }
    coreSetUIText(count4, "textContent", [corePhrase(""), segments.length, corePhrase(" 段 · 约 "), tokens, corePhrase(" tokens")]);
    coreSetUIText(saveHint, "textContent", dirty.length > 0 ? [corePhrase(""), dirty.length, corePhrase(" 段未保存 · Ctrl+S 保存当前章节")] : corePhrase("修改后保存 · Ctrl+S"));
    saveHint.dataset.dirty = String(dirty.length > 0);
    coreSetUIText(saveButton, "textContent", saving ? corePhrase("保存中…") : corePhrase("保存"));
  };
  const nameOfRow = (row2) => {
    const label = row2.dataset.segment ?? row2.dataset.beside ?? "";
    return THE_READABLE_TITLE[label] ?? label;
  };
  const anchorOf = (row2) => row2;
  const toneOf = (row2) => {
    const badge = row2.querySelector(".persona-home .persona-owner");
    if (badge === null) return "";
    const cls = (badge.className || "").split(" ");
    return (cls[cls.length - 1] ?? "").replace(/[^a-z]/g, "");
  };
  const navRows = () => [
    ...list.querySelectorAll(".persona-segment[data-navrow]"),
    ...otherList.querySelectorAll(".persona-segment[data-navrow]")
  ];
  const markActive = (index) => {
    for (const one of navList.querySelectorAll(".persona-navitem")) {
      one.setAttribute(
        "aria-current",
        index !== null && one.dataset.navIndex === String(index) ? "location" : "false"
      );
      if (index === null || one.dataset.navIndex !== String(index)) delete one.dataset.on;
      else one.dataset.on = "true";
    }
  };
  const scrollerOf = () => scroll;
  const NAV_GAP = 8;
  const sideRail = () => {
    try {
      return window.getComputedStyle(navList).flexDirection === "column";
    } catch {
      return false;
    }
  };
  const theLine = () => NAV_GAP;
  const jump = (row2) => {
    const scroller2 = scrollerOf();
    const anchor = anchorOf(row2);
    const box = scroller2.getBoundingClientRect();
    const here = anchor.getBoundingClientRect().top - box.top + scroller2.scrollTop;
    scroller2.scrollTop = Math.max(0, Math.ceil(here - theLine()));
  };
  let jumpedTo = null;
  let jumpedAt = -1;
  const jumpAndMark = (row2, index) => {
    jump(row2);
    markActive(index);
    jumpedTo = row2;
    jumpedAt = scrollerOf().scrollTop;
  };
  const refreshNav = () => {
    navList.replaceChildren();
    const rows = navRows();
    rows.forEach((row2, index) => {
      const item = node4("button", "persona-navitem");
      item.type = "button";
      if (row2.dataset.dirty === "true") item.dataset.dirty = "true";
      item.dataset.navIndex = String(index);
      item.dataset.tone = toneOf(row2);
      const text2 = node4("span", "persona-navtext", nameOfRow(row2));
      item.append(text2);
      if (row2.dataset.dirty === "true") item.append(node4("span", "persona-navmark", "未保存"));
      coreSetUIText(item, "title", [corePhrase(""), nameOfRow(row2), corePhrase(" · 点击查看章节")]);
      if (row2.dataset.dirty === "true") item.title += "（未保存）";
      item.addEventListener("click", () => {
        jumpAndMark(row2, index);
      });
      navList.append(item);
    });
    for (const one of soonItems("PERSONA & MEMORY")) {
      const item = node4("button", "persona-navitem", one.label);
      item.type = "button";
      item.dataset.soon = "true";
      item.disabled = true;
      item.title = `${one.label} —— ${one.why}`;
      navList.append(item);
    }
    markActive(null);
  };
  const scroller = scrollerOf();
  const atBottom = () => {
    const most = scroller.scrollHeight - scroller.clientHeight;
    return most > 0 && scroller.scrollTop >= most;
  };
  const trackActive = () => {
    const rows = navRows();
    if (rows.length === 0) return;
    if (jumpedTo !== null) {
      const who = scroller.scrollTop === jumpedAt ? rows.indexOf(jumpedTo) : -1;
      jumpedTo = null;
      if (who >= 0) {
        markActive(who);
        return;
      }
    }
    const line = sideRail() ? scroller.getBoundingClientRect().top + 24 : nav.getBoundingClientRect().bottom + NAV_GAP;
    let here = 0;
    rows.forEach((row2, index) => {
      if (anchorOf(row2).getBoundingClientRect().top <= line) here = index;
    });
    if (atBottom()) here = rows.length - 1;
    markActive(here);
  };
  const markDirty = (name) => {
    editEpoch++;
    const draft = drafts.get(name);
    if (draft) draft.generation = (draft.generation ?? 0) + 1;
    const changed = draft !== void 0 && draft.lines.join("\n") !== linesOf(draft.base ?? "").join("\n");
    const at = dirty.indexOf(name);
    if (changed && at < 0) dirty.push(name);
    if (!changed && at >= 0) dirty.splice(at, 1);
    const hit2 = segments.filter((one) => one.file === name).map((one) => one.name);
    for (const row2 of list.querySelectorAll(".persona-segment")) {
      if (hit2.includes(nameOfRow(row2))) {
        const baseline = linesOf(draft?.base ?? "");
        const sectionChanged = changed && Array.from(row2.querySelectorAll(".persona-line[data-line]")).some((line) => draft?.lines[Number(line.dataset.line)] !== baseline[Number(line.dataset.line)]);
        if (sectionChanged) row2.dataset.dirty = "true";
        else {
          delete row2.dataset.dirty;
          for (const hint of row2.querySelectorAll(".persona-savehint")) hint.textContent = "";
        }
      }
    }
    refreshNav();
  };
  const HINT_UNSAVED = "未保存 · Ctrl+S";
  const HINT_SAVED = "已保存";
  let activeHint = null;
  const showHintOn = (row2, name) => {
    activeHint = { row: row2, name };
    for (const one of row2.closest(".persona-segment")?.querySelectorAll(".persona-savehint") ?? []) one.textContent = "";
    const here = row2.querySelector(".persona-savehint");
    if (here !== null && row2.closest(".persona-segment")?.dataset.dirty === "true") here.textContent = HINT_UNSAVED;
  };
  const markAllSaved = () => {
    if (activeHint !== null && dirty.includes(activeHint.name)) {
      const hint = activeHint.row.querySelector(".persona-savehint");
      if (hint !== null) hint.textContent = HINT_UNSAVED;
    }
    for (const row2 of list.querySelectorAll(".persona-segment")) {
      const file = segments.find((one) => one.name === nameOfRow(row2))?.file;
      if (file && dirty.includes(file)) continue;
      for (const one of row2.querySelectorAll(".persona-savehint")) {
        if (one.textContent !== "") one.textContent = HINT_SAVED;
      }
    }
    for (const row2 of list.querySelectorAll(".persona-segment[data-dirty]")) {
      const segment = segments.find((one) => one.name === nameOfRow(row2));
      const file = segment?.file;
      if (file === null || file === void 0 || !dirty.includes(file)) delete row2.dataset.dirty;
    }
    refreshNav();
  };
  const fixedLine = (draft, index) => {
    const row2 = node4("div", "persona-line");
    row2.dataset.fixed = "true";
    row2.append(node4("span", "persona-lineno", String(index + 1)));
    row2.append(node4("div", "persona-linetext", draft.lines[index] ?? ""));
    return row2;
  };
  const shapeLine = (draft, index) => {
    const row2 = node4("div", "persona-line");
    row2.dataset.fixed = "true";
    row2.dataset.shape = "true";
    row2.append(node4("span", "persona-lineno", String(index + 1)));
    const cell = node4("div", "persona-linetext", draft.lines[index] ?? "");
    cell.setAttribute("contenteditable", "false");
    row2.append(cell);
    return row2;
  };
  const editorLine = (draft, index) => {
    const row2 = node4("div", "persona-line");
    const number2 = node4("span", "persona-lineno", String(index + 1));
    row2.dataset.line = String(index);
    const text2 = node4("div", "persona-linetext", draft.lines[index] ?? "");
    const lineHint = hintNode("");
    row2.append(number2, text2, lineHint);
    return row2;
  };
  const attachSurface = (surface, draft, locks, hidden) => {
    surface.setAttribute("contenteditable", "plaintext-only");
    const seal = () => {
      for (const one of surface.querySelectorAll(".persona-lineno")) {
        one.setAttribute("contenteditable", "false");
      }
      for (const one of surface.querySelectorAll(".persona-savehint")) {
        one.setAttribute("contenteditable", "false");
      }
    };
    seal();
    surface.dataset.rows = String(draft.lines.length - hidden.size);
    surface.dataset.locked = [...locks].filter((line) => !hidden.has(line - 1)).join(",");
    const chosen = () => {
      const sel = selectionNow();
      return sel !== null && sel.rangeCount > 0 ? sel : null;
    };
    const selectionNow = () => {
      const win = typeof window === "undefined" ? void 0 : window;
      if (win === void 0 || typeof win.getSelection !== "function") return null;
      const sel = win.getSelection();
      return sel === null || sel === void 0 ? null : sel;
    };
    const caret = () => {
      const sel = chosen();
      return sel === null ? null : sel.getRangeAt(0);
    };
    const rowOf = (start) => {
      let one = start;
      for (let hops = 0; one !== null && one !== void 0 && hops < 8; hops += 1) {
        if (one.classList !== void 0 && one.classList.contains("persona-line")) {
          return one;
        }
        one = one.parentElement ?? one.parent ?? null;
      }
      return null;
    };
    const rowsNow = () => surface.querySelectorAll(".persona-line");
    const shapeIntact = () => {
      if (rowsNow().length !== Number(surface.dataset.rows ?? "0")) return false;
      const want = (surface.dataset.locked ?? "") === "" ? 0 : String(surface.dataset.locked).split(",").length;
      return surface.querySelectorAll('.persona-line[data-shape="true"]').length === want;
    };
    const atHeadOf = (row2) => {
      const range = caret();
      const cell = row2.querySelector(".persona-linetext");
      if (range === null || cell === null || !range.collapsed) return false;
      return range.startContainer === cell && range.startOffset === 0;
    };
    const atTailOf = (row2) => {
      const range = caret();
      const cell = row2.querySelector(".persona-linetext");
      if (range === null || cell === null || !range.collapsed) return false;
      return range.startContainer === cell && range.startOffset >= (cell.textContent ?? "").length;
    };
    const stopTheEvent = (ev) => {
      const one = ev;
      if (typeof one.preventDefault === "function") one.preventDefault();
      if (typeof one.stopPropagation === "function") one.stopPropagation();
    };
    const insertPlain = (text2) => {
      const sel = chosen();
      if (text2 === "" || sel === null) return;
      const range = sel.getRangeAt(0);
      range.deleteContents();
      const made = document.createTextNode(text2);
      range.insertNode(made);
      range.setStartAfter(made);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
    };
    const rebuildSurface = () => {
      const at = Number(rowOf(caret()?.startContainer ?? null)?.dataset.line ?? "-1");
      surface.replaceChildren();
      draft.lines.forEach((_one, index) => {
        if (hidden.has(index)) return;
        surface.append(locks.has(index + 1) ? shapeLine(draft, index) : editorLine(draft, index));
      });
      seal();
      const rows = surface.querySelectorAll(".persona-line[data-line]");
      let back;
      if (at >= 0) {
        for (let i = 0; i < rows.length; i += 1) {
          if (Number(rows[i]?.dataset.line) === at) {
            back = rows[i];
            break;
          }
        }
        if (back === void 0) back = rows[0];
      } else {
        back = rows[rows.length - 1];
      }
      const sel = chosen();
      const cell = back?.querySelector(".persona-linetext") ?? null;
      if (sel !== null && cell !== null) {
        const range = document.createRange();
        range.selectNodeContents(cell);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      refreshHead();
    };
    const takeSurface = () => {
      if (!shapeIntact()) {
        rebuildSurface();
        return;
      }
      const hit2 = [];
      for (const row2 of surface.querySelectorAll(".persona-line[data-line]")) {
        const index = Number(row2.dataset.line);
        const cell = row2.querySelector(".persona-linetext");
        if (cell === null) continue;
        const got = cell.textContent ?? "";
        if (got === draft.lines[index]) continue;
        draft.lines[index] = got;
        hit2.push(row2);
      }
      if (hit2.length === 0) return;
      markDirty(draft.name);
      showHintOn(rowOf(caret()?.startContainer ?? null) ?? hit2[hit2.length - 1], draft.name);
      refreshHead();
    };
    surface.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") stopTheEvent(ev);
    });
    surface.addEventListener("beforeinput", (ev) => {
      const one = ev;
      const kind = one.inputType ?? "";
      if (kind === "insertParagraph" || kind === "insertLineBreak") {
        stopTheEvent(ev);
        return;
      }
      if (kind.startsWith("insertFromPaste") || kind.startsWith("insertFromDrop")) {
        stopTheEvent(ev);
        insertPlain((one.data ?? "").replace(/[\r\n]+/g, " "));
        takeSurface();
        return;
      }
      if (!kind.startsWith("delete")) return;
      const range = caret();
      if (range === null) return;
      const from = rowOf(range.startContainer);
      if (!range.collapsed) {
        const to = rowOf(range.endContainer);
        if (from !== null && to !== null && from !== to) stopTheEvent(ev);
        return;
      }
      if (from === null) return;
      if (kind.includes("Forward")) {
        if (atTailOf(from)) stopTheEvent(ev);
        return;
      }
      if (atHeadOf(from)) stopTheEvent(ev);
    });
    surface.addEventListener("paste", (event) => {
      event.preventDefault();
      insertPlain((event.clipboardData?.getData("text/plain") ?? "").replace(/[\r\n]+/g, " "));
      takeSurface();
    });
    surface.addEventListener("input", takeSurface);
    surface.addEventListener("blur", takeSurface);
    let dragging = false;
    const pointRange = (x, y) => {
      const doc = document;
      return typeof doc.caretRangeFromPoint === "function" ? doc.caretRangeFromPoint(x, y) : null;
    };
    const extendTo = (one) => {
      const sel = selectionNow();
      const to = pointRange(one.clientX, one.clientY);
      if (sel === null || to === null) return;
      sel.extend(to.startContainer, to.startOffset);
    };
    const stopDrag = () => {
      dragging = false;
      document.removeEventListener("mousemove", extendTo);
      document.removeEventListener("mouseup", stopDrag);
    };
    surface.addEventListener("mousedown", (ev) => {
      const one = ev;
      const row2 = rowOf(one.target ?? null);
      if (row2 === null || row2.dataset.fixed !== "true") return;
      const sel = selectionNow();
      const start = pointRange(one.clientX, one.clientY);
      if (sel === null || start === null) return;
      stopTheEvent(ev);
      sel.removeAllRanges();
      sel.addRange(start);
      dragging = true;
      document.addEventListener("mousemove", extendTo);
      document.addEventListener("mouseup", stopDrag);
    });
    const firstRow = () => surface.querySelector(".persona-line[data-line]");
    const followCaret = () => {
      const row2 = rowOf(caret()?.startContainer ?? null) ?? firstRow();
      if (row2 !== null) showHintOn(row2, draft.name);
    };
    surface.addEventListener("focus", followCaret);
    surface.addEventListener("keyup", followCaret);
    surface.addEventListener("mouseup", followCaret);
  };
  const flushFocused = () => {
    const el6 = document.activeElement;
    if (el6 === null || el6 === void 0) return;
    const surface = surfaceOf(el6);
    if (surface !== null) surface.blur();
  };
  const segmentRow = (segment) => {
    const row2 = node4("section", "persona-segment");
    row2.dataset.segment = segment.name;
    if (!ownedByTheAuthor(segment.owner)) row2.dataset.owned = "other";
    const writable = mayWrite(segment);
    if (!writable) row2.dataset.readonly = "true";
    row2.addEventListener("mouseenter", () => {
      row2.dataset.here = "true";
    });
    row2.addEventListener("mouseleave", () => {
      delete row2.dataset.here;
    });
    row2.addEventListener("focusin", () => {
      row2.dataset.active = "true";
    });
    row2.addEventListener("focusout", () => {
      delete row2.dataset.active;
    });
    const left = node4("div", "persona-name");
    left.append(node4("span", "persona-tag", segment.name));
    const middle = node4("div", "persona-body");
    const draft = segment.file === null || segment.file === void 0 ? null : drafts.get(segment.file) ?? draftOf(segment);
    if (!writable) {
      middle.append(node4("p", "persona-lock", "此内容为只读"));
    }
    if (draft !== null) {
      const locks = writable ? lockedLines(segment) : /* @__PURE__ */ new Set();
      const hidden = hiddenLines(segment);
      draft.lines.forEach((_one, index) => {
        if (hidden.has(index)) return;
        if (!writable) {
          middle.append(fixedLine(draft, index));
        } else if (locks.has(index + 1)) {
          middle.append(shapeLine(draft, index));
        } else {
          middle.append(editorLine(draft, index));
        }
      });
      if (writable) attachSurface(middle, draft, locks, hidden);
    }
    const right = node4("div", "persona-home");
    right.append(node4("span", ownerClass(segment.owner), ownerText(segment.owner)));
    right.append(presenceMark(draft === null ? 0 : 1));
    row2.append(left, middle, right);
    row2.dataset.navrow = "true";
    return row2;
  };
  const besideRow = (label, info) => {
    const row2 = node4("section", "persona-segment persona-besiderow");
    row2.dataset.beside = label;
    row2.dataset.owned = "other";
    const infos = info?.file_infos ?? [];
    if (infos.length > 0 && infos.every((one) => one.writable === false)) {
      row2.dataset.readonly = "true";
    }
    const left = node4("div", "persona-name");
    left.append(node4("span", "persona-tag", THE_READABLE_TITLE[label] ?? label));
    const middle = node4("div", "persona-body");
    const draft = besideLines(info);
    if (draft !== null) {
      draft.lines.forEach((_one, index) => {
        middle.append(fixedLine(draft, index));
      });
    }
    const right = node4("div", "persona-home");
    right.append(node4("span", ownerClass(null), ownerText(null)));
    right.append(presenceMark((info?.files ?? []).length));
    row2.append(left, middle, right);
    row2.dataset.navrow = "true";
    return row2;
  };
  const draw = (report) => {
    segments = editableSegments(report);
    drafts.clear();
    activeHint = null;
    dirty.length = 0;
    for (const segment of segments) {
      const draft = draftOf(segment);
      if (draft !== null) drafts.set(draft.name, draft);
    }
    list.replaceChildren();
    for (const segment of segments) {
      list.append(segmentRow(segment));
    }
    otherList.replaceChildren();
    const beside = report.persona_scope === "user-authored" ? [] : report.unplaced ?? [];
    const byLabel = new Map((report.rows ?? []).map((one) => [one.label, one]));
    for (const label of beside) otherList.append(besideRow(label, byLabel.get(label)));
    otherBox.hidden = beside.length === 0;
    refreshHead();
    refreshNav();
    trackActive();
    scroller.removeEventListener("scroll", trackActive);
    scroller.addEventListener("scroll", trackActive, { passive: true });
    trackActive();
  };
  let readEpoch = 0;
  let pageStopped = false;
  const readHint = pageLoad(column, "人设", () => {
    void load();
  });
  const load = async (discard = false) => {
    flushFocused();
    if (saving || dirty.length > 0 && !discard) return;
    const edits = editEpoch;
    const epoch = ++readEpoch;
    readHint.start();
    saveButton.disabled = true;
    say("正在加载人设…", "busy");
    try {
      const response = await send(REPORT_ROUTE, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        readHint.fail();
        say(`读取人设失败（HTTP ${response.status}）`, "bad");
        return;
      }
      const report = await response.json();
      if (pageStopped || epoch !== readEpoch || editEpoch !== edits) return;
      draw(report);
      conflictBox.hidden = true;
      readHint.ready();
      saveButton.disabled = false;
      say("", "idle");
    } catch (exc) {
      if (pageStopped || epoch !== readEpoch) return;
      readHint.fail();
      say(`读取人设失败（${exc.message}）`, "bad");
    } finally {
      if (!pageStopped && epoch === readEpoch) saveButton.disabled = false;
    }
  };
  const offerMerge = (name) => {
    conflictBox.replaceChildren();
    conflictBox.hidden = false;
    const read4 = node4("button", "btn persona-merge-read", "读取新版供手动合并");
    read4.setAttribute("type", "button");
    conflictBox.append(node4("p", void 0, name + "：草稿保留，未自动刷新。"), read4);
    read4.addEventListener("click", () => {
      void (async () => {
        read4.setAttribute("disabled", "true");
        try {
          const response = await send(REPORT_ROUTE, { headers: { Accept: "application/json" } });
          if (!response.ok) throw new Error(`服务回 ${response.status}`);
          const report = await response.json();
          if (pageStopped) return;
          const latest = report.segments.flatMap((one) => one.files ?? []).find((one) => one.name === name);
          const segment = editableSegments(report).find((one) => one.file === name);
          const revision = latest?.revision ?? segment?.revision;
          const remote = latest?.body ?? segment?.body ?? "";
          if (typeof revision !== "string") throw new Error("新版没有可用 revision，请检查服务版本");
          flushFocused();
          const draft = drafts.get(name);
          if (!draft) return;
          const generation = draft.generation;
          const area = (cls, title2, value, readonly) => {
            conflictBox.append(node4("p", void 0, title2));
            const cell = document.createElement("textarea");
            cell.className = cls;
            cell.value = value;
            cell.readOnly = readonly;
            conflictBox.append(cell);
            return cell;
          };
          conflictBox.replaceChildren();
          area("persona-merge-base", "编辑前版本", draft.base ?? "", true);
          area("persona-merge-remote", "已保存的最新版本", remote, true);
          const merged = area("persona-merge-text", "手动合并稿（请对照新版编辑）", draft.lines.join("\n"), false);
          const apply = node4("button", "btn persona-merge-apply", "使用合并稿（尚未保存）");
          apply.setAttribute("type", "button");
          conflictBox.append(apply);
          const cancel = node4("button", "btn persona-merge-cancel", "取消合并（保留原草稿）");
          cancel.setAttribute("type", "button");
          conflictBox.append(cancel);
          cancel.addEventListener("click", () => {
            conflictBox.hidden = true;
            say("已取消合并；原草稿和读时版本未变，尚未保存。", "warn");
          });
          apply.addEventListener("click", () => {
            flushFocused();
            if (saving || draft.generation !== generation) {
              say("原草稿又有新输入，未覆盖。请重新读取新版后合并。", "warn");
              return;
            }
            draft.lines.splice(0, draft.lines.length, ...linesOf(merged.value));
            draft.revision = revision;
            draft.base = remote;
            markDirty(name);
            const scrollTop = scroll.scrollTop;
            list.replaceChildren(...segments.map(segmentRow));
            scroll.scrollTop = scrollTop;
            markDirty(name);
            refreshHead();
            refreshNav();
            conflictBox.hidden = true;
            say("合并稿已放回编辑区，尚未保存。请核对后再保存。", "warn");
          });
        } catch (exc) {
          say(`读取合并依据失败：${exc.message}。草稿仍保留。`, "bad");
          read4.removeAttribute("disabled");
        }
      })();
    });
  };
  const saveText = async (name, text2, expect) => {
    const stamp = now();
    if (!isAStamp(stamp)) {
      say(`保存时间格式无效：${stamp}`, "bad");
      return null;
    }
    const target = /^(宪法\.md|角色设定\.md)#([a-z][a-z-]{0,63})$/.exec(name);
    const ask2 = target ? { name: target[1], section: target[2], text: text2, stamp, slot: "section-" + target[2], expect } : { name, text: text2, stamp, slot: DEFAULT_SLOT, expect };
    let response;
    try {
      response = await send(SAVE_ROUTE, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ask2)
      });
    } catch (exc) {
      say(`提交失败：${exc.message}`, "bad");
      return null;
    }
    const body = await response.text();
    const outcome = outcomeOf(response.status, body);
    if (outcome.kind !== "saved") {
      say(outcome.line, outcome.kind === "collision" ? "warn" : "bad");
      if (outcome.kind === "conflict") offerMerge(name);
      return outcome;
    }
    let got = {};
    try {
      got = JSON.parse(body);
    } catch {
      got = {};
    }
    const draft = drafts.get(name);
    if (draft) {
      draft.revision = got.revision ?? null;
      draft.base = text2;
    }
    if (target && got.revision) {
      for (const other of drafts.values()) {
        if (other.name.startsWith(target[1] + "#") && other.revision === expect)
          other.revision = got.revision;
      }
    }
    const kept = got.history;
    const version = got.version ?? "";
    const where = kept === null || kept === void 0 ? " · 盘上原来没有这一份 ⇒ 没留下旧版" : ` · 旧版留在 ${kept}`;
    const detail = `存了 ${got.bytes ?? text2.length} 字节` + (version === "" ? "" : ` · 这一版：${version}`) + where + " · " + BOUNDARY_LINE_SHORT;
    say(kept === null || kept === void 0 ? "已保存" : "已保存 · 历史版本已保留", "ok", detail);
    return {
      kind: "saved",
      bytes: got.bytes ?? text2.length,
      version,
      history: kept ?? null,
      left: kept !== null && kept !== void 0
    };
  };
  const saveDirty = async () => {
    if (saving || pageStopped) return;
    flushFocused();
    const selected = activeHint?.name ?? (dirty.length === 1 ? dirty[0] : null);
    const names = selected && dirty.includes(selected) ? [selected] : [];
    if (names.length === 0 && dirty.length > 0) {
      say("请选中需要保存的未保存章节；其他草稿会保留。", "warn");
      return;
    }
    if (names.length === 0) {
      say("没有需要保存的修改。可点击“重载”获取最新内容。", "warn");
      return;
    }
    saving = true;
    saveButton.disabled = true;
    reloadButton.disabled = true;
    refreshHead();
    try {
      for (const name of names) {
        flushFocused();
        const draft = drafts.get(name);
        if (!draft) continue;
        if (typeof draft.revision !== "string") {
          say("缺少读时 revision，未保存。请保留草稿并重新加载新版后合并。", "bad");
          offerMerge(name);
          return;
        }
        const text2 = draft.lines.join("\n");
        const generation = draft.generation;
        const outcome = await saveText(name, text2, draft.revision);
        flushFocused();
        if (outcome === null || outcome.kind !== "saved" || pageStopped) return;
        if (draft.generation === generation && draft.lines.join("\n") === text2) {
          const at = dirty.indexOf(name);
          if (at >= 0) dirty.splice(at, 1);
        } else {
          markDirty(name);
          say("已保存提交的内容；保存期间新增的修改仍需保存。", "warn");
        }
        markAllSaved();
        refreshHead();
      }
    } finally {
      saving = false;
      saveButton.disabled = false;
      reloadButton.disabled = false;
      markAllSaved();
      refreshHead();
    }
  };
  saveButton.addEventListener("click", () => {
    void saveDirty();
  });
  let confirmingReload = false;
  reloadButton.addEventListener("click", () => {
    void (async () => {
      flushFocused();
      if (saving || confirmingReload) return;
      if (dirty.length > 0) {
        confirmingReload = true;
        let accepted = false;
        try {
          accepted = await confirmAction(
            "放弃未保存草稿？",
            "重载会放弃所有未保存草稿，并读取已保存的最新版本。",
            "放弃草稿并重载"
          );
        } finally {
          confirmingReload = false;
        }
        if (!accepted || saving || pageStopped) return;
      }
      void load(true);
    })();
  });
  const onKey = (ev) => {
    const key2 = ev;
    if (key2.ctrlKey && !key2.altKey && (key2.key === "s" || key2.key === "S")) {
      ev.preventDefault();
      if (saving) return;
      flushFocused();
      void saveDirty();
    }
  };
  window.addEventListener("keydown", onKey);
  void load();
  return {
    root: column,
    show: () => {
      column.hidden = false;
      shell2.bottom.hidden = true;
    },
    /** **离开这一页**（路由器换页时叫的这一下）—— 本波补的那两条：
     *
     *  ⚠️⚠️ **两条都不许省**（那一条 Minor 说的是"它悄悄把这一页藏起来就走了"）：
     * · **先把光标那一格的字收回来**（`flushFocused()`）—— 与 Ctrl+S 走**同一处
     *      判据**；少了它，屏幕上最后那几笔连草稿都没有进去过；
     * · **有没保存的就说出来**（不许静默）：这一页的把手会被丢掉
     *      （`pages.ts::route()` 每次换页都重新 `build()` 一份）⇒ 那几段改过的字
     *      **真的会没** —— 而"真的会没"这件事**必须在页上说出来**。
     * 说在哪儿：那一页的状态行（回来时照旧看得见）＋ **左栏那一项的 `title`**
     *      （那是这一页走了之后**唯一还留在屏幕上**的地方 —— 不写它，那句话就白说了）。
     *  ⚠️ 换页那一颗 Ctrl+S 的钉子照旧要拔（少了它，浏览器那一份保存会被吞掉）。 */
    stop: () => {
      pageStopped = true;
      readEpoch++;
      flushFocused();
      if (dirty.length > 0) {
        const line = `有 ${dirty.length} 段修改未保存，请保存后再离开。`;
        say(line, "warn");
        const item = shell2.railNav.querySelector('.rail-item[data-page="persona"]');
        if (item !== null) item.setAttribute("title", line);
      }
      column.hidden = true;
      shell2.bottom.hidden = false;
      window.removeEventListener("keydown", onKey);
      scroller.removeEventListener("scroll", trackActive);
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/display-date.ts
function displayDate(value) {
  if (!value) return "";
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "日期未知" : `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/record-refresh.ts
function recordRefresh(root, read4) {
  let active2 = false;
  let running = false;
  let dirty = false;
  let timer;
  let poll;
  const flush = async () => {
    timer = void 0;
    if (!active2 || root.hidden || document.visibilityState === "hidden") return;
    if (running) {
      dirty = true;
      return;
    }
    running = true;
    try {
      await read4();
    } finally {
      running = false;
      if (active2 && dirty) {
        dirty = false;
        schedule();
      }
    }
  };
  const schedule = () => {
    if (active2 && timer === void 0) timer = setTimeout(() => {
      void flush();
    }, 200);
  };
  return {
    start() {
      if (active2 || typeof window === "undefined" || typeof window.addEventListener !== "function") return;
      active2 = true;
      window.addEventListener("core-records-changed", schedule);
      document.addEventListener("visibilitychange", schedule);
      poll = setInterval(schedule, 5e3);
    },
    stop() {
      if (!active2) return;
      active2 = false;
      dirty = false;
      clearTimeout(timer);
      clearInterval(poll);
      timer = void 0;
      window.removeEventListener("core-records-changed", schedule);
      document.removeEventListener("visibilitychange", schedule);
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/memory-page.ts
function recentPeople(everyone) {
  const recordedAt = (one) => {
    if (typeof one.recent_at === "number" && Number.isFinite(one.recent_at)) {
      return one.recent_at;
    }
    const times = (one.said ?? []).map(({ ts }) => {
      const raw = String(ts).trim();
      if (/^\d+$/.test(raw)) {
        const value2 = Number(raw);
        return value2 > 1e10 ? value2 : value2 * 1e3;
      }
      const compact2 = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(raw);
      const value = compact2 ? new Date(
        +compact2[1],
        +compact2[2] - 1,
        +compact2[3],
        +compact2[4],
        +compact2[5],
        +compact2[6]
      ).getTime() : Date.parse(raw);
      if (compact2) {
        const date = new Date(value);
        if (date.getFullYear() !== +compact2[1] || date.getMonth() !== +compact2[2] - 1 || date.getDate() !== +compact2[3] || date.getHours() !== +compact2[4] || date.getMinutes() !== +compact2[5] || date.getSeconds() !== +compact2[6]) {
          return 0;
        }
      }
      return Number.isFinite(value) ? value : 0;
    });
    return Math.max(0, ...times);
  };
  return everyone.map((one) => ({ one, at: recordedAt(one) })).sort((a, b) => b.at - a.at).slice(0, 6).map(({ one }) => one);
}
var READ_FAILED = "记忆加载失败，请重试。";
var NOT_WIRED = "当前服务暂不支持查看记忆。";
var RUN_STATE = [
  ["running", "运行中"],
  ["idle", "空闲"],
  ["failed", "失败"],
  ["partial", "部分完成"],
  ["recovery-pending", "待恢复"],
  ["unknown", "未知／未提供"]
];
var stateName = (said) => RUN_STATE.find((one) => one[0] === said)?.[1] ?? said;
function memoryPage(shell2) {
  const root = document.createElement("section");
  root.className = "memory page-memory memory-studio";
  const title = document.createElement("h1");
  title.className = "plugins-title";
  coreSetUIText(title, "textContent", corePhrase("记忆 · MEMORY"));
  const note = document.createElement("p");
  note.className = "plugins-note";
  note.setAttribute("role", "status");
  note.setAttribute("aria-live", "polite");
  note.textContent = "";
  const head = document.createElement("header");
  head.className = "memory-head";
  head.append(title, note);
  const scroll = document.createElement("div");
  scroll.className = "page-scroll memory-scroll";
  root.append(head, scroll);
  shell2.content.replaceChildren(root);
  const keepPosition = (render2) => {
    const parents = [];
    for (let el6 = scroll; el6; el6 = el6.parentElement) parents.push([el6, el6.scrollTop, el6.scrollLeft]);
    const x = window.scrollX, y = window.scrollY;
    const inner = [...root.querySelectorAll(".memory-pull-content")].map((el6) => el6.scrollTop);
    render2();
    root.querySelectorAll(".memory-pull-content").forEach((el6, i) => {
      el6.scrollTop = inner[i] || 0;
    });
    for (const [el6, top, left] of parents) {
      el6.scrollTop = top;
      el6.scrollLeft = left;
    }
    window.scrollTo(x, y);
  };
  const draw = (answer) => keepPosition(() => drawContents(answer));
  const drawContents = (answer) => {
    for (const old of root.querySelectorAll(".memory-row")) {
      old.remove();
    }
    if (answer === null) {
      note.textContent = READ_FAILED;
      return;
    }
    if (!answer.wired) {
      note.textContent = NOT_WIRED;
      return;
    }
    note.textContent = "";
    const mine = answer.pending ?? [];
    const selfmade = mine.filter((one) => (one.kind ?? "proposal") === "selfmade");
    const unfixed = mine.filter((one) => (one.kind ?? "proposal") === "unfixed");
    const proposals = mine.filter((one) => (one.kind ?? "proposal") === "proposal");
    const THE_THREE = [
      ["proposal", "待讨论", "暂无待处理事项。"],
      ["selfmade", "自我记录", "暂无自我记录。"],
      ["unfixed", "未完成事项", "暂无未完成事项。"]
    ];
    const nameOf = (kind) => THE_THREE.find((one) => one[0] === kind)?.[1] ?? kind;
    const blurbOf = (kind) => {
      const said = THE_THREE.find((one2) => one2[0] === kind)?.[2] ?? "暂无记录。";
      const one = mine.find((it) => (it.kind ?? "proposal") === kind);
      if (one !== void 0 && one.full === true) {
        return said + " 已达记录上限，新增记录会替换最旧记录。";
      }
      return said;
    };
    const star = document.createElement("div");
    star.className = selfmade.length === 0 ? "memory-star memory-row is-empty" : "memory-star memory-row";
    const starLbl = document.createElement("div");
    starLbl.className = "lbl";
    coreSetUIText(starLbl, "textContent", corePhrase("最近的自我记录"));
    const starSay = document.createElement("p");
    starSay.className = selfmade.length === 0 ? "say void" : "say";
    coreSetUIText(starSay, "textContent", selfmade.length === 0 ? corePhrase("暂无自我记录。") : String(selfmade[selfmade.length - 1].text));
    const starBy = document.createElement("p");
    starBy.className = "by";
    coreSetUIText(starBy, "textContent", corePhrase("Bot 在整理记忆时更新。"));
    star.append(starLbl, starSay, starBy);
    scroll.append(star);
    const howMany = (kind) => mine.filter((one) => (one.kind ?? "proposal") === kind).length;
    const figs = document.createElement("div");
    figs.className = "memory-row memory-figs";
    const FIG_ROWS = [
      [howMany("proposal"), "件", nameOf("proposal"), true],
      [howMany("selfmade"), "条", nameOf("selfmade"), false],
      [howMany("unfixed"), "件", nameOf("unfixed"), false]
    ];
    for (const [count4, unit, label, hot] of FIG_ROWS) {
      const one = document.createElement("div");
      one.className = hot ? "memory-fig hot" : "memory-fig";
      const num = document.createElement("div");
      num.className = "n";
      num.textContent = String(count4);
      const em = document.createElement("em");
      em.textContent = unit;
      num.append(em);
      const key2 = document.createElement("div");
      key2.className = "k";
      uiLabel(key2, label);
      one.append(num, key2);
      figs.append(one);
    }
    head.append(figs);
    const head2 = document.createElement("h2");
    head2.className = "memory-document-title";
    coreSetUIText(head2, "textContent", corePhrase("自我记录"));
    const documents = document.createElement("section");
    documents.className = "memory-documents memory-row";
    documents.append(head2);
    const list = document.createElement("ul");
    list.className = "memory-list";
    const hers = answer.hers ?? [];
    const sizeOf = (k) => hers.find((one) => one.key === k)?.lines ?? 0;
    const atOf = (k) => hers.find((one) => one.key === k)?.at ?? "";
    const ROWS = [];
    for (const one of hers) {
      ROWS.push([
        one.name,
        one.size === 0 ? "暂无内容" : String(one.size) + " 字节",
        one.size === 0 ? "void" : "",
        one.key
      ]);
    }
    ROWS.push(["浮现", "暂无主动记录。", "void", ""]);
    for (const [key2, value, cls, keyOf] of ROWS) {
      const li = document.createElement("li");
      const line = document.createElement("div");
      line.className = "memory-line";
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = key2;
      const v = document.createElement("span");
      v.className = cls === "void" ? "v void" : "v";
      v.textContent = value;
      const doit = document.createElement("span");
      doit.className = "do";
      if (keyOf === "") {
        doit.textContent = "";
      } else {
        const meta = document.createElement("span");
        meta.className = "when";
        coreSetUIText(meta, "textContent", [[String(sizeOf(keyOf)), corePhrase(" 行")], atOf(keyOf) === "" ? corePhrase("") : [corePhrase(" · 最后写于 "), atOf(keyOf)]]);
        const open = document.createElement("button");
        open.className = "op";
        open.type = "button";
        coreSetUIAttribute(open, "aria-label", [corePhrase("在文件夹里打开"), key2]);
        coreSetUIText(open, "textContent", corePhrase("在文件夹里打开"));
        const openStatus = document.createElement("span");
        openStatus.setAttribute("role", "status");
        open.addEventListener("click", (ev) => {
          ev.stopPropagation();
          open.disabled = true;
          openStatus.textContent = "";
          void pageFetch("/open", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind: "persona", key: keyOf })
          }).then((response) => {
            if (!response.ok) coreSetUIText(openStatus, "textContent", response.status === 404 ? corePhrase(" · 未找到文件") : corePhrase(" · 无法打开文件夹"));
          }).catch(() => {
            coreSetUIText(openStatus, "textContent", corePhrase(" · 无法连接本机服务"));
          }).finally(() => {
            open.disabled = false;
          });
        });
        doit.append(meta, document.createTextNode(" · "), open, openStatus);
      }
      line.append(k, v, doit);
      li.append(line);
      list.append(li);
    }
    documents.append(list);
    scroll.append(documents);
    scroll.append(skillsBox);
    const pull = document.createElement("div");
    pull.className = "memory-row memory-pull";
    const pullHead = document.createElement("h2");
    pullHead.className = "memory-sec memory-row";
    coreSetUIText(pullHead, "textContent", corePhrase("记忆记录"));
    scroll.append(pullHead);
    const CARDS = [
      ["proposal", proposals, false],
      ["selfmade", selfmade, false],
      ["unfixed", unfixed, false]
    ];
    for (const [kindOf2, rowsOf, quiet] of CARDS) {
      const title2 = nameOf(kindOf2);
      const one = document.createElement("div");
      one.className = (quiet ? "item quiet" : "item") + (rowsOf.length === 0 ? " is-empty" : "");
      const h = document.createElement("h3");
      uiLabel(h, title2);
      one.append(h);
      const content = document.createElement("div");
      content.className = "memory-pull-content";
      if (rowsOf.length === 0) {
        const said = document.createElement("p");
        uiLabel(said, blurbOf(kindOf2));
        content.append(said);
      }
      const merged = /* @__PURE__ */ new Map();
      for (const each of rowsOf) {
        const key2 = each.proposal?.id || each.text;
        const had = merged.get(key2);
        if (had) {
          had.how += 1;
        } else {
          merged.set(key2, { one: each, how: 1 });
        }
      }
      const entries = document.createElement("div");
      entries.className = "memory-entry-list";
      const latest = [...merged.values()].reverse();
      const shown = latest.slice(0, 1);
      for (const { one: each, how } of shown) {
        const entry = document.createElement("section");
        entry.className = "memory-entry";
        entry.id = "memory-entry-" + encodeURIComponent(kindOf2 + ":" + (each.proposal?.id || String(each.ts || "") + ":" + each.text.slice(0, 160)));
        const summary2 = document.createElement("h3");
        const short = (value) => value.replace(/\s+/g, " ").slice(0, 160);
        summary2.textContent = each.proposal ? short(each.source + " · " + each.proposal.section + "：" + each.proposal.why) : short(String(each.text));
        if (how > 1) summary2.textContent += " ×" + String(how);
        const body = document.createElement("div");
        body.className = "memory-entry-detail";
        const said = document.createElement("p");
        coreSetUIText(said, "textContent", each.proposal ? [corePhrase("讨论稿\n"), each.text] : String(each.text));
        body.append(said);
        if (each.proposal) {
          const original = document.createElement("p");
          coreSetUIText(original, "textContent", [corePhrase("原文\n"), each.proposal.before]);
          body.append(original);
          const state3 = document.createElement("p");
          state3.className = "when";
          coreSetUIText(state3, "textContent", corePhrase("待讨论。可在对话中确认修改、保留或暂缓。"));
          body.append(state3);
        }
        if (each.ts) {
          const when = document.createElement("p");
          when.className = "when";
          when.textContent = String(each.ts);
          body.append(when);
        }
        entry.append(summary2, body);
        entries.append(entry);
      }
      if (latest.length > shown.length) {
        const count4 = document.createElement("p");
        count4.className = "when";
        coreSetUIText(count4, "textContent", [[[[corePhrase("显示最近 "), String(shown.length)], corePhrase(" 条，共 ")], String(latest.length)], corePhrase(" 条；完整记录保留在文件中。")]);
        entries.append(count4);
      }
      content.append(entries);
      one.append(content);
      pull.append(one);
    }
    scroll.append(pull);
    const people = document.createElement("section");
    people.className = "memory-row memory-people";
    const peopleHead = document.createElement("div");
    peopleHead.className = "memory-people-head";
    const peopleTitle = document.createElement("h2");
    coreSetUIText(peopleTitle, "textContent", corePhrase("最近的画像"));
    coreSetUIText(peopleTitle, "title", corePhrase("按画像与关系的最近记录时间排列"));
    const allPeople = document.createElement("a");
    allPeople.className = "memory-people-more";
    allPeople.href = "#/people";
    coreSetUIText(allPeople, "textContent", corePhrase("查看全部画像 →"));
    peopleHead.append(peopleTitle, allPeople);
    people.append(peopleHead);
    const recent = recentPeople(answer.everyone);
    if (recent.length > 0) {
      const plist = document.createElement("ul");
      plist.className = "memory-people-grid";
      for (const one of recent) {
        const item = document.createElement("li");
        item.className = "memory-person";
        const who = document.createElement("strong");
        who.className = "memory-person-name";
        who.textContent = one.display_name || one.who;
        who.title = one.who;
        who.title = one.who;
        const said = document.createElement("p");
        said.className = "memory-person-standing";
        said.textContent = one.standing || "";
        said.hidden = !one.standing;
        said.title = said.textContent;
        const meta = document.createElement("span");
        meta.className = "memory-person-meta";
        coreSetUIText(meta, "textContent", [[[[corePhrase("关系评分 "), one.favor === null ? corePhrase("—") : String(one.favor)], corePhrase(" · 记过 ")], String(one.how_many_times)], corePhrase(" 条")]);
        item.append(who, said, meta);
        if (one.blocked) {
          const blocked = document.createElement("span");
          blocked.className = "memory-person-blocked";
          coreSetUIText(blocked, "textContent", corePhrase("已屏蔽"));
          item.append(blocked);
        }
        plist.append(item);
      }
      people.append(plist);
    }
    scroll.append(people);
    const quietOne = document.createElement("div");
    quietOne.className = "item quiet";
    const qh = document.createElement("h3");
    coreSetUIText(qh, "textContent", corePhrase("主动请求"));
    const qp = document.createElement("p");
    const sent = mine.find((one) => (one.kind ?? "") === "outbox");
    const dayOf = (raw) => {
      const got = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(raw || ""));
      return got ? `${Number(got[2])} 月 ${Number(got[3])} 日` : "";
    };
    if (sent === void 0) {
      coreSetUIText(qp, "textContent", corePhrase("当前服务暂不支持。"));
    } else if (!sent.how_many) {
      coreSetUIText(qp, "textContent", corePhrase("暂无主动请求。"));
    } else {
      const when = dayOf(String(sent.day || ""));
      const parts = ["今天 " + String(sent.today ?? 0) + " 封"];
      parts.push((when === "" ? "最近一封" : "最近一封是 " + when) + "：" + String(sent.text || ""));
      qp.textContent = parts.join("；");
    }
    const quietContent = document.createElement("div");
    quietContent.className = "memory-pull-content";
    quietContent.append(qp);
    quietOne.append(qh, quietContent);
    pull.append(quietOne);
    const tail = personaMemoryTail();
    tail.classList.add("memory-row");
    root.append(tail);
  };
  const skillsBox = document.createElement("section");
  skillsBox.className = "memory-skills";
  scroll.append(skillsBox);
  let latestSkills = null;
  const visibleLimits = /* @__PURE__ */ new Map();
  const toolbar = document.createElement("div");
  toolbar.className = "memory-skill-toolbar";
  const search = document.createElement("input");
  search.type = "search";
  coreSetUIText(search, "placeholder", corePhrase("搜索标题或正文"));
  coreSetUIAttribute(search, "aria-label", corePhrase("搜索做法与技能的标题或正文"));
  const category = document.createElement("select");
  coreSetUIAttribute(category, "aria-label", corePhrase("筛选技能类别"));
  for (const label of ["全部类别", "沟通做法", "操作技能", "待分类", "已并入（历史）", "正文缺失"]) {
    const option = document.createElement("option");
    option.value = label;
    uiLabel(option, label);
    category.append(option);
  }
  toolbar.append(search, category);
  const refilter = () => {
    visibleLimits.clear();
    drawSkills(latestSkills);
  };
  let composing = false;
  search.addEventListener("compositionstart", () => {
    composing = true;
  });
  search.addEventListener("compositionend", () => {
    composing = false;
    refilter();
  });
  search.addEventListener("input", (event) => {
    if (!composing && !event.isComposing) refilter();
  });
  category.addEventListener("change", refilter);
  const drawSkills = (answer) => keepPosition(() => drawSkillsContents(answer));
  const drawSkillsContents = (answer) => {
    latestSkills = answer;
    if (composing) return;
    for (const child of [...skillsBox.children]) if (child !== toolbar) child.remove();
    const head2 = document.createElement("h2");
    head2.className = "memory-document-title";
    coreSetUIText(head2, "textContent", corePhrase("做法与技能"));
    skillsBox.prepend(head2);
    if (answer === null) {
      const bad = document.createElement("p");
      bad.className = "memory-quiet";
      coreSetUIText(bad, "textContent", corePhrase("技能加载失败，请重试。"));
      skillsBox.append(bad);
      return;
    }
    if (!answer.wired) {
      const off = document.createElement("p");
      off.className = "memory-quiet";
      coreSetUIText(off, "textContent", corePhrase("当前服务暂不支持查看技能。"));
      skillsBox.append(off);
      return;
    }
    const maintenance = document.createElement("section");
    maintenance.className = "memory-skill-maintenance";
    maintenance.dataset.section = "maintenance";
    const maintenanceTitle = document.createElement("h3");
    maintenance.append(maintenanceTitle);
    const status = answer.status ?? { state: "unknown" };
    const runline = document.createElement("p");
    runline.className = "memory-quiet";
    const pending5 = Number(status.recovery_pending ?? 0);
    coreSetUIText(runline, "textContent", [[status.state === "unknown" && (answer.active_count ?? answer.active?.length ?? 0) === 0 ? corePhrase("尚无整理记录") : [corePhrase("整理状态："), stateName(String(status.state))], pending5 > 0 ? [[corePhrase("（"), String(pending5)], corePhrase(" 项未完成）")] : corePhrase("")], status.why && status.state !== "unknown" ? [corePhrase(" —— "), String(status.why)] : corePhrase("")]);
    maintenance.append(runline);
    const index = answer.index_status;
    if (index) {
      const labels = {
        waiting: "等待首次同步",
        running: "同步中",
        ready: "已同步",
        retry: "待重试"
      };
      const indexLine = document.createElement("p");
      indexLine.className = "memory-quiet";
      coreSetUIText(indexLine, "textContent", [[corePhrase("索引状态："), labels[index.state] ?? "未知"], index.indexed_at ? [corePhrase(" · 上次同步 "), displayDate(index.indexed_at)] : corePhrase("")]);
      maintenance.append(indexLine);
    }
    const detail = status.detail;
    if (detail && (detail.partial || detail.error)) {
      const past = document.createElement("p");
      past.className = "memory-quiet";
      const bits = [];
      if (detail.partial) {
        const committed = detail.committed ?? [];
        const names = committed.length > 0 ? committed.join("、") : "无已保存文件";
        bits.push((detail.resolved === true ? "此前已保存：" : "上次已保存：") + names + (detail.resolved === true ? " · 后续操作已完成" : detail.followup_uncertain ? " —— 后续投递结果待核对" : " · 文件已保存，后续操作未完成"));
      }
      if (detail.error) bits.push("上一次失败：" + (detail.error_class === "PersonaShapeRefused" ? "文档结构保护拒绝了本次改写；已保存的内容保留，草稿待复核。" : String(detail.error)));
      if (detail.failed_at) bits.push("中断于 " + detail.failed_at);
      past.textContent = bits.join("；");
      maintenance.append(past);
    }
    if (!toolbar.isConnected) skillsBox.append(toolbar);
    const query = search.value.trim().toLocaleLowerCase();
    let matched = 0;
    const active2 = answer.active ?? [];
    const merged = answer.merged ?? [];
    const drawRows = (rows, said) => {
      if (category.value !== "全部类别" && category.value !== said) return;
      const newest = [...rows].sort((a, b) => (Date.parse(b.recorded_at || "") || 0) - (Date.parse(a.recorded_at || "") || 0) || b.id.localeCompare(a.id, void 0, { numeric: true }));
      const filtered = newest.filter((one) => !query || [one.title, one.body || "", one.id].join(" ").toLocaleLowerCase().includes(query));
      matched += filtered.length;
      if (query && !filtered.length) return;
      const pageSize = query ? 8 : 2;
      const limit = visibleLimits.get(said) ?? pageSize;
      const section = document.createElement("section");
      section.className = "memory-skill-section";
      const heading2 = document.createElement("h3");
      heading2.textContent = said;
      section.append(heading2);
      if (!rows.length) {
        const empty = document.createElement("p");
        empty.className = "memory-skill-empty";
        coreSetUIText(empty, "textContent", corePhrase("尚未记录"));
        section.append(empty);
      }
      for (const one of filtered.slice(0, limit)) {
        const item = document.createElement("section");
        item.className = "memory-skill-entry";
        item.dataset.section = one.id;
        const title2 = document.createElement("h3");
        const name = document.createElement("span");
        name.textContent = one.title;
        const overview = document.createElement("span");
        overview.className = "memory-skill-overview";
        const excerpt = document.createElement("span");
        excerpt.className = "memory-skill-excerpt";
        excerpt.textContent = (one.body || "").split("\n").find((line) => line.trim())?.slice(0, 140) || "暂无正文";
        overview.append(name, excerpt);
        title2.append(overview);
        item.append(title2);
        const body = document.createElement("div");
        body.className = "memory-skill-body";
        body.textContent = one.body ?? (one.state === "missing" ? "正文文件缺失。" : "正文未提供，请更新服务后重试。");
        item.append(body);
        const metadata = document.createElement("div");
        metadata.className = "memory-skill-metadata";
        coreSetUIText(metadata, "textContent", [[[one.recorded_at ? [[corePhrase("记录于 "), displayDate(one.recorded_at)], corePhrase(" · ")] : corePhrase(""), corePhrase("历史 ")], String(one.history ?? 0)], corePhrase(" 版")]);
        coreSetUIText(metadata, "title", [[[one.id, corePhrase(" · 版本 ")], one.version], one.recorded_at ? [corePhrase(" · "), one.recorded_at] : corePhrase("")]);
        const more = document.createElement("button");
        more.type = "button";
        more.className = "compact-more";
        more.textContent = "显示全部";
        more.setAttribute("aria-label", "显示全部：" + one.title);
        more.onclick = () => showTextDetail(one.title, [
          body.textContent,
          metadata.textContent,
          one.references?.length ? "关联资料：" + one.references.join("、") : ""
        ].filter(Boolean).join("\n\n"));
        const footer = document.createElement("div");
        footer.className = "memory-skill-footer";
        footer.append(metadata, more);
        item.append(footer);
        section.append(item);
      }
      if (filtered.length > limit) {
        const more = document.createElement("button");
        more.type = "button";
        more.className = "memory-skill-more";
        coreSetUIText(more, "textContent", corePhrase("显示更多"));
        coreSetUIAttribute(more, "aria-label", [corePhrase("显示更多"), said]);
        more.addEventListener("click", () => {
          visibleLimits.set(said, limit + pageSize);
          drawSkills(latestSkills);
          const updated = [...skillsBox.querySelectorAll(".memory-skill-section")].find((el6) => el6.querySelector("h3")?.textContent === said);
          const next = updated?.querySelectorAll("h3")[limit];
          next?.focus();
        });
        section.append(more);
      }
      skillsBox.append(section);
    };
    drawRows(active2.filter((one) => one.kind === "communication"), "沟通做法");
    drawRows(active2.filter((one) => one.kind === "operation"), "操作技能");
    const unclassified = active2.filter((one) => !one.kind || one.kind === "unclassified");
    if (unclassified.length) drawRows(unclassified, "待分类");
    if (merged.length) drawRows(merged, "已并入（历史）");
    coreSetUIText(maintenanceTitle, "textContent", [[corePhrase("维护记录 · "), status.state === "failed" ? corePhrase("上次文档整理未完成") : [corePhrase("整理"), stateName(String(status.state))]], index ? [corePhrase(" · 索引"), { ready: "已同步", running: "同步中", waiting: "待同步", retry: "待重试" }[index.state] ?? "未知"] : corePhrase("")]);
    skillsBox.append(maintenance);
    if (answer.missing?.length) drawRows(answer.missing, "正文缺失");
    if (!matched && (query || category.value !== "全部类别" && !skillsBox.querySelector(".memory-skill-section"))) {
      const empty = document.createElement("p");
      empty.className = "memory-skill-empty";
      coreSetUIText(empty, "textContent", query ? corePhrase("未找到匹配条目，请换个关键词或类别。") : corePhrase("此类别尚未记录。"));
      skillsBox.insertBefore(empty, maintenance);
    }
    const ops = answer.ops ?? [];
    if (ops.length > 0) {
      const box = document.createElement("div");
      const lbl = document.createElement("div");
      lbl.className = "memory-star-label";
      coreSetUIText(lbl, "textContent", [[corePhrase("操作记录（最近 "), String(ops.length)], corePhrase(" 笔）")]);
      box.append(lbl);
      const ul = document.createElement("ul");
      ul.className = "memory-list";
      for (const one of ops) {
        const li = document.createElement("li");
        const line = document.createElement("div");
        line.className = "memory-skill-line";
        coreSetUIText(line, "textContent", [[[[[String(one.op), corePhrase(" ｜ ")], String(one.stage)], corePhrase(" ｜ 留 ")], String(one.keep)], one.sources.length > 0 ? [corePhrase(" ｜ 并 "), one.sources.join("、")] : corePhrase("")]);
        li.append(line);
        ul.append(li);
      }
      box.append(ul);
      maintenance.append(box);
    }
  };
  let skillsRead = 0;
  let skillsSnapshot = "";
  let memorySnapshot = "";
  const skillsHint = pageLoad(root, "做法", () => {
    void refreshSkills();
  });
  const refreshSkills = async () => {
    const mine = ++skillsRead;
    skillsHint.start();
    try {
      const answer = await getStatusJson("/skills");
      if (mine !== skillsRead || root.hidden) return;
      const snapshot2 = JSON.stringify(answer);
      if (snapshot2 !== skillsSnapshot && !composing && !skillsBox.contains(document.activeElement)) {
        drawSkills(answer);
        skillsSnapshot = snapshot2;
      }
      skillsHint.ready();
    } catch (why) {
      if (mine !== skillsRead || root.hidden) return;
      if (why instanceof StaleStatusError) {
        void refreshSkills();
        return;
      }
      console.error("做法读取失败：", why);
      skillsHint.fail();
    }
  };
  let requestId = 0;
  const readHint = pageLoad(root, "记忆", () => {
    void refresh2();
  });
  const refresh2 = async () => {
    const mine = ++requestId;
    readHint.start();
    try {
      const answer = await getStatusJson("/memory");
      if (mine !== requestId || root.hidden) return;
      const snapshot2 = JSON.stringify(answer);
      if (snapshot2 !== memorySnapshot && !root.contains(document.activeElement)) {
        draw(answer);
        memorySnapshot = snapshot2;
      }
      readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) {
        void refresh2();
        return;
      }
      console.error("记忆读取失败：", why);
      readHint.fail();
    }
  };
  const liveRecords = recordRefresh(root, async () => {
    await Promise.all([refresh2(), refreshSkills()]);
  });
  return {
    root,
    show() {
      root.hidden = false;
      liveRecords.start();
      void refresh2();
      void refreshSkills();
    },
    stop() {
      liveRecords.stop();
      requestId += 1;
      skillsRead += 1;
      root.hidden = true;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-versions.ts
var states = /* @__PURE__ */ new Map();
var painters = /* @__PURE__ */ new Map();
var el4 = (tag, text2 = "", cls = "") => {
  const n = document.createElement(tag);
  n.textContent = text2;
  n.className = cls;
  return n;
};
async function releasePage(repo2, page2) {
  const params = new URLSearchParams({ repository: repo2, page: String(page2), kind: "releases" });
  const response = await pageFetch("/plugin-versions?" + params, { cache: "no-store" }, 2e4);
  if (!response.ok) throw Error("版本读取失败，请检查网络后重试。");
  const result = await response.json();
  if (!Array.isArray(result.rows)) throw Error("版本列表格式异常");
  return result;
}
async function tagForVersion(repo2, version) {
  for (let page2 = 1; page2 <= 5; page2++) {
    const result = await releasePage(repo2, page2);
    const match = result.rows.find((row2) => row2.tag.replace(/^v/, "") === version && !row2.prerelease);
    if (match) return match.tag;
    if (!result.more) break;
  }
  throw Error("发布记录中未找到对应版本，请重新检查更新。");
}
function pluginVersions(repo2, name, current2, blocked, install) {
  const key2 = repo2 + "/" + name;
  let state3 = states.get(key2);
  if (!state3) {
    state3 = { kind: "releases", page: 1, selected: "", error: "", loading: false };
    states.set(key2, state3);
  }
  const s = state3;
  const box = el4("section", "", "pw-versions");
  const body = el4("div", "", "pw-version-body");
  if (!current2) box.append(el4("h3", "选择安装版本"));
  box.append(body);
  const link = (label, url) => {
    const a = el4("a", label, "pw-button");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  };
  const button4 = (label, click) => {
    const b = el4("button", label, "pw-button");
    b.type = "button";
    b.onclick = click;
    return b;
  };
  function draw() {
    body.replaceChildren();
    const controls = el4("div", "", "pw-version-controls");
    const refresh2 = button4(s.loading ? "正在读取…" : s.data ? "刷新版本" : "读取版本", () => {
      void read4();
    });
    refresh2.disabled = s.loading || blocked;
    controls.append(el4("span", "正式发布版本"), refresh2);
    body.append(controls);
    if (s.error) {
      const error = el4("p", s.error, "pw-operation");
      error.setAttribute("role", "status");
      body.append(error);
    }
    if (!s.data) {
      body.append(el4("p", s.loading ? "正在读取版本…" : "点击“读取版本”查看正式版和历史正式版。"));
      return;
    }
    if (!s.data.rows.length) {
      body.append(el4("p", "作者尚未发布正式 Release，暂不提供下载。"));
    } else {
      if (current2 && s.page === 1 && !s.data.more && s.data.rows.length === 1) body.append(el4("p", "该仓库目前仅有一个正式发布版本。"));
      const select2 = el4("select");
      select2.setAttribute("aria-label", "选择插件版本");
      select2.disabled = blocked || s.loading;
      for (const row3 of s.data.rows) select2.add(new Option(row3.tag + (row3.prerelease ? " · 预发布" : "") + (row3.date ? " · " + row3.date.slice(0, 10) : "") + (row3.tag.replace(/^v/, "") === current2 ? " · 当前版本" : ""), row3.tag));
      if (!s.data.rows.some((row3) => row3.tag === s.selected)) s.selected = s.data.rows[0].tag;
      select2.value = s.selected;
      select2.onchange = () => {
        s.selected = select2.value;
        draw();
      };
      body.append(select2);
      const row2 = s.data.rows.find((row3) => row3.tag === s.selected);
      const actions = el4("div", "", "pw-version-controls");
      const apply = button4(current2 ? "替换安装" : "安装此版本", () => install(row2));
      apply.disabled = blocked || s.loading || row2.tag.replace(/^v/, "") === current2;
      actions.append(apply, link("下载源码包 ↗", row2.download), link("发布说明 ↗", row2.source));
      body.append(actions);
      if (row2.notes) {
        const notes = el4("section", "", "pw-version-notes");
        notes.append(el4("h3", "版本说明"), el4("p", row2.notes));
        body.append(notes);
      }
      body.append(el4("p", current2 ? "替换安装会备份原安装包并保留配置和缓存。旧版本的配置兼容性请查看发布说明。" : "按所选正式版本安装并核对插件清单，安装后默认停用。", "pw-version-hint"));
    }
    if (s.page > 1 || s.data.more) {
      const pages = el4("div", "", "pw-version-controls");
      const previous = button4("上一页", () => {
        s.page--;
        s.selected = "";
        void read4();
      });
      previous.disabled = blocked || s.loading || s.page === 1;
      const next = button4("下一页", () => {
        s.page++;
        s.selected = "";
        void read4();
      });
      next.disabled = blocked || s.loading || !s.data.more;
      pages.append(previous, el4("span", "第 " + s.page + " 页"), next);
      body.append(pages);
    }
  }
  async function read4() {
    if (s.loading) return;
    s.loading = true;
    s.error = "";
    draw();
    try {
      s.data = await releasePage(repo2, s.page);
    } catch (error) {
      s.error = error instanceof Error ? error.message : "版本读取失败";
    } finally {
      s.loading = false;
      painters.get(key2)?.();
    }
  }
  painters.set(key2, draw);
  draw();
  return Object.assign(box, { loadVersions: () => s.data ? Promise.resolve() : read4() });
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-origin.ts
function githubRepository(value) {
  return /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+?)(?:\.git)?(?:\/|$)/.exec(value)?.[1] || "";
}
function pluginOrigin(plugin, repository = "") {
  if (plugin.builtin === true) return "Core";
  if (githubRepository(plugin.origin?.url || "") || githubRepository(plugin.repository || "") || githubRepository(repository)) return "GitHub";
  return "本地导入";
}
var pluginOriginOrder = (group) => ({ "Core": 0, "GitHub": 1, "本地导入": 2, "来源待确认": 3 })[group];

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-operation.ts
var state2;
var continuation;
var pluginOperation = () => state2;
var update = (phase, text2, busy = true) => {
  if (state2) state2 = { ...state2, phase, text: text2, busy };
  window.dispatchEvent(new Event("plugin-operation-changed"));
};
var sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function read3() {
  const r = await pageFetch("/plugins", {}, 4e3);
  if (!r.ok) throw Error("无法读取插件状态");
  const data = await r.json();
  if (!data.wired || !Array.isArray(data.plugins)) throw Error("插件状态未接入");
  acceptPluginRuntime(data.plugins);
  return data;
}
var find = (data, name) => data.plugins.find((p) => p.name === name && p.installed !== false);
function samePackage(before, after) {
  if (before.package_id && after.package_id) return before.package_id === after.package_id;
  return before.version === after.version && before.where === after.where;
}
function sameBot(before, after) {
  if (!after?.instance || before.bot !== after.bot) throw Error("连接到了不同的 Bot，操作已停止，请核对服务。");
}
var RejectedOperation = class extends Error {
};
async function post(path, body, instance) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1e4);
  try {
    const response = await pageFetch(path, { method: "POST", headers: { "Content-Type": "application/json", ...instance ? { "X-Telyin-Shutdown-Instance": instance } : {} }, body: JSON.stringify(body), signal: controller.signal });
    const answer = await response.json();
    if (!response.ok) throw new RejectedOperation(answer.said || answer.error || `请求失败（${response.status}）`);
    return answer;
  } finally {
    clearTimeout(timer);
  }
}
function finish(data, name, action) {
  const p = find(data, name);
  if (action === "remove") {
    if (p) throw Error("插件仍在安装列表中，卸载尚未完成。");
    update("complete", "已卸载，配置和缓存按原有规则保留。", false);
  } else {
    if (!p) throw Error("插件已不在安装列表中，请检查是否被其他页面更改。");
    if (p.lifecycle_error) throw Error(p.lifecycle_error);
    if (p.restart_required) throw Error("插件仍有待生效更改，不能确认操作完成。");
    if (action === "enable" && (!p.enabled || !p.loaded)) throw Error("插件尚未正常运行，请打开配置查看缺失项或错误。");
    if (action === "disable" && (p.enabled || p.loaded || p.tracked)) throw Error("插件尚未完全停用，实例仍需清理。");
    if (action === "apply" && p.enabled && !p.loaded) throw Error("重启后插件未正常运行，请检查配置和日志。");
    update("complete", action === "disable" ? "已停用。" : action === "enable" ? "已启用，插件正常运行。" : "重启完成，更改已生效。", false);
  }
  continuation = void 0;
  window.dispatchEvent(new Event("plugins-changed"));
}
async function afterRestart(before, p, action) {
  const old = before.runtime;
  const deadline = Date.now() + 18e4;
  update("reconnecting", "等待 Bot 服务重新连接，当前页面会保留。");
  while (Date.now() < deadline) {
    let next;
    try {
      next = await read3();
    } catch {
      await sleep(1200);
      continue;
    }
    sameBot(old, next.runtime);
    if (next.runtime.instance === old.instance) {
      await sleep(1200);
      continue;
    }
    const current2 = find(next, p.name);
    if (!current2) {
      if (action === "remove") {
        finish(next, p.name, action);
        return;
      }
      throw Error("重启后找不到此插件。");
    }
    if (!samePackage(p, current2)) throw Error("重启期间插件安装包发生变化，已停止后续操作。");
    update("verifying", "已重新连接，正在核对插件实际状态…");
    if (action === "remove") {
      if (current2.enabled || current2.loaded || current2.tracked || current2.restart_required) throw Error("重启后插件尚未完全停止，已保留安装包。");
      update("removing", "插件已停止，正在完成卸载…");
      try {
        const answer = await post("/plugins/remove", { name: p.name, confirm_shared: true }, next.runtime.instance);
        if (!answer.ok) throw new RejectedOperation(answer.said || "卸载尚未完成，安装包已保留。");
      } catch (error) {
        if (error instanceof RejectedOperation) throw error;
        const observed = await read3();
        sameBot(old, observed.runtime);
        if (find(observed, p.name)) throw Error("卸载结果尚未确认，安装列表中仍有此插件；没有重复提交。");
      }
      next = await read3();
      sameBot(old, next.runtime);
    }
    finish(next, p.name, action);
    return;
  }
  update("uncertain", "尚未确认 Bot 已重新启动。页面和操作记录已保留，可继续检查；不会重复发送重启请求。", false);
}
async function continuePluginOperation() {
  if (state2?.busy || !continuation) return;
  try {
    await continuation();
  } catch (e) {
    update("failed", e instanceof Error ? e.message : String(e), false);
  }
}
async function runPluginOperation(name, action, expectedVersion) {
  if (state2?.busy) throw Error("请等待当前插件操作完成。");
  continuation = void 0;
  state2 = { name, action, phase: "changing", text: "正在读取并核对插件状态…", busy: true };
  update("changing", state2.text);
  try {
    const before = await read3();
    const p = find(before, name);
    if (!p) throw Error("插件未安装，请刷新列表。");
    if (expectedVersion !== void 0 && p.version !== expectedVersion) throw Error("当前插件版本已变化，请重新核对。");
    let next = before;
    const predictableRestart = action === "apply" && p.restart_required || p.reload_mode === "restart" && (action === "enable" ? !p.loaded : action === "disable" || action === "remove" ? p.loaded || p.tracked || p.restart_action === "disable" : false);
    if (predictableRestart && !before.runtime?.restart_supported) throw Error(before.runtime?.restart_reason || "当前启动器不支持页面内重启，请先更新并重启启动器。");
    if (action !== "apply") {
      update("changing", action === "remove" ? "正在停止插件并检查卸载条件…" : action === "enable" ? "正在启用插件…" : "正在停用插件…");
      let answer;
      try {
        answer = await post(action === "remove" ? "/plugins/remove" : "/plugins/switch", action === "remove" ? { name, confirm_shared: true } : { name, on: action === "enable" }, before.runtime?.instance);
      } catch (error) {
        if (error instanceof RejectedOperation) throw error;
        const observed = await read3();
        const actual = find(observed, name);
        const saved = action === "remove" ? !actual || !actual.enabled && actual.restart_action === "disable" : actual?.enabled === (action === "enable");
        if (!saved) throw Error("操作结果未能确认，请检查当前插件状态；没有重复提交请求。");
        answer = { ok: true };
      }
      if (answer.restart_required && answer.restart_local === false) throw Error(answer.said || "其他 Bot 仍需重启，安装包已保留。");
      if (answer.ok === false && !answer.restart_required) throw Error(answer.said || "操作未完成");
      next = await read3();
      if (before.runtime) {
        sameBot(before.runtime, next.runtime);
        if (next.runtime.instance !== before.runtime.instance) throw Error("操作期间服务已变化，请重新核对状态。");
      }
    }
    const current2 = find(next, name);
    if (current2 && !samePackage(p, current2)) throw Error("插件包已变化，操作已停止。");
    if (!current2?.restart_required) {
      finish(next, name, action);
      return;
    }
    if (!next.runtime?.restart_supported) throw Error(next.runtime?.restart_reason || "设置已保存，当前启动器不支持页面内重启。");
    const restartAction = action === "apply" ? current2.restart_action === "config" ? "config" : current2.restart_action : action;
    update("restarting", "设置已保存，正在请求 Bot 安全退出并重新启动…");
    continuation = () => afterRestart(next, p, action);
    try {
      await post("/plugins/restart", { name, action: restartAction }, next.runtime.instance);
    } catch (e) {
      if (e instanceof RejectedOperation) throw e;
    }
    await continuation();
  } catch (e) {
    continuation = void 0;
    update("failed", e instanceof Error ? e.message : String(e), false);
  } finally {
    window.dispatchEvent(new Event("plugins-changed"));
  }
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-guide-version.ts
var PLUGIN_GUIDE_VERSION = true ? "v1.19" : "";

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-catalog.ts
var pluginCategories = [
  ["all", "全部"],
  ["communication", "通讯接入"],
  ["creation", "内容创作"],
  ["information", "信息查询"],
  ["automation", "自动任务"],
  ["interaction", "互动体验"],
  ["uncategorized", "未分类"]
];
var reviewed = {
  "ar15mod3/infinity-comfyui": "creation",
  "ar15mod3/core-infinity-wavesuid": "information",
  "yluow/core-infinity-chaofan": "automation",
  "yluow/jingjing-town": "interaction"
};
function pluginCategory(one) {
  const declared = pluginCategories.filter(([id]) => id !== "all" && one.keywords?.includes(`category-${id}`));
  if (declared.length === 1) return declared[0][0];
  if (declared.length > 1) return "uncategorized";
  return reviewed[(one.full || "").toLowerCase()] || "uncategorized";
}
function pluginCategoryLabel(one) {
  return pluginCategories.find(([id]) => id === pluginCategory(one))[1];
}
function pluginRequirement(one) {
  return (one.full || "").toLowerCase() === "ar15mod3/infinity-comfyui" ? "需启用 QQ 连接" : "";
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-platforms.ts
var platformNames = { windows: "Windows", linux: "Linux", macos: "macOS" };
function pluginPlatforms(one) {
  const platforms2 = Object.keys(platformNames);
  const tags = new Set(one.keywords);
  const conditional = platforms2.filter((id) => tags.has(`platform-${id}-conditional`));
  const supported = platforms2.filter((id) => tags.has(`platform-${id}`) && !conditional.includes(id));
  if (supported.length || conditional.length) {
    const source = /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/?$/.test(one.repository) ? one.repository : void 0;
    return {
      supported,
      conditional,
      source,
      note: conditional.length ? "有条件支持的平台需满足作者列出的运行环境。请查看项目说明。" : "运行平台由插件作者声明。"
    };
  }
  if (one.repository.toLowerCase() === "https://github.com/ar15mod3/infinity-comfyui" && one.version?.replace(/^v/, "") === "0.2.4") return {
    supported: ["windows"],
    conditional: ["linux"],
    note: "Windows 支持本机部署。Linux 仅连接 Windows 上的 ComfyUI；生图服务必须使用 Windows 秋叶整合包。macOS 未声明。",
    source: one.repository + "#运行环境"
  };
  return { supported: [], conditional: [], note: "作者尚未声明该版本的运行平台。安装前请查看项目说明，确认系统和依赖要求。" };
}
function platformSummary(info) {
  const values = [...info.supported.map((id) => platformNames[id]), ...info.conditional.map((id) => platformNames[id] + "（有条件）")];
  return values.length ? values.join(" / ") : "平台未声明";
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-workspace.ts
var node5 = (tag, cls = "", text2 = "") => {
  const n = document.createElement(tag);
  n.className = cls;
  n.textContent = text2;
  return n;
};
var button3 = (text2, action, cls = "") => {
  const b = node5("button", "pw-button " + cls, text2);
  b.type = "button";
  b.onclick = action;
  return b;
};
var installed = (p) => !!p && p.installed !== false;
var runtime = (p) => {
  const operation = pluginOperation();
  if (operation?.busy && operation.name === p.name) return { enable: "正在启用", disable: "正在停用", remove: "正在卸载", apply: "正在应用更改" }[operation.action];
  return p.installed === false ? "未安装" : p.contract ? "不兼容" : p.lifecycle_error ? "运行异常" : p.restart_required ? p.loaded ? p.restart_action === "config" ? "运行中 · 配置待重启" : "仍在运行 · 重启后停用" : p.restart_action === "enable" ? "重启后启用" : "重启后完成停用" : p.loaded ? "运行中" : p.enabled ? "已启用 · 未运行" : "已停用";
};
var repo = (entry) => {
  if (entry.local?.builtin) return "https://github.com/AR15MOD3/TelyinCore/tree/main/core/builtin/infinity/" + encodeURIComponent(entry.name);
  if (entry.market?.full && /^[\w.-]+\/[\w.-]+$/.test(entry.market.full)) return "https://github.com/" + entry.market.full;
  const url = entry.local?.origin?.url || "";
  const match = /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+?)(?:\.git)?(?:\/|$)/.exec(url);
  if (match) return "https://github.com/" + match[1];
  if (entry.local?.repository) return entry.local.repository;
  const remote = updateFor(entry.name)?.repository;
  return remote && /^[\w.-]+\/[\w.-]+$/.test(remote) ? "https://github.com/" + remote : "";
};
var originGroup = (entry) => pluginOrigin(entry.local || {}, repo(entry));
var originOrder = (entry) => pluginOriginOrder(originGroup(entry));
var platforms = (entry) => pluginPlatforms({ repository: repo(entry), version: entry.local?.version || entry.market?.version, keywords: entry.local ? entry.local.keywords : entry.market?.keywords });
function pluginWorkspace(shell2, view2 = "local") {
  const root = node5("section", "plugin-workspace page-world");
  root.dataset.pluginView = view2;
  const header = node5("header", "pw-header");
  const heading2 = node5("div", "pw-heading");
  heading2.append(node5("h1", "page-title", view2 === "local" ? "插件总览 · PLUGIN OVERVIEW" : "插件市场 · PLUGIN MARKETPLACE"));
  if (view2 === "discover") {
    const guide2 = node5("a", "pw-guide", "开发规范手册 ↗");
    guide2.href = assetUrl("infinity-plugin-spec.html");
    guide2.target = "_blank";
    guide2.rel = "noopener noreferrer";
    guide2.title = "插件开发规范 " + PLUGIN_GUIDE_VERSION;
    heading2.append(guide2);
  }
  header.append(heading2);
  const scope2 = node5("div", "pw-scope");
  const scopeTitle = node5("h2", "", view2 === "local" ? "内置 & 已安装" : "浏览插件");
  const scopeCount = node5("span", "pw-scope-count");
  scope2.append(scopeTitle, scopeCount);
  const body = node5("div", "pw-body");
  const browse = node5("section", "pw-browser");
  browse.setAttribute("aria-label", "插件列表");
  const toolbar = node5("div", "pw-searchbar");
  const search = node5("input");
  search.type = "search";
  search.placeholder = "搜索插件";
  search.setAttribute("aria-label", "搜索插件");
  toolbar.append(icon("search"), search);
  const controls = node5("div", "pw-filters");
  controls.setAttribute("role", "group");
  controls.setAttribute("aria-label", "筛选插件");
  const refresh2 = button3("刷新", () => {
    void read4();
  }, "pw-quiet");
  const searchActions = node5("div", "pw-search-actions");
  const includeLocal = node5("input");
  includeLocal.type = "checkbox";
  includeLocal.setAttribute("aria-label", "显示内置与已安装");
  if (view2 === "discover") {
    const label = node5("label", "pw-include-local");
    label.append(includeLocal, document.createTextNode("显示内置与已安装"));
    searchActions.append(label);
  }
  searchActions.append(refresh2);
  const navigation = node5("div", "pw-navigation");
  navigation.append(scope2, searchActions);
  const searchRow = node5("div", "pw-search-row");
  searchRow.append(toolbar);
  const searchTools = node5("section", "pw-search-tools");
  searchTools.setAttribute("aria-label", "插件搜索与标签");
  searchTools.append(searchRow, controls);
  const feedback = node5("p", "pw-feedback");
  feedback.setAttribute("role", "status");
  const list = node5("div", "pw-list");
  const footer = node5("footer", "page-footer pw-page-footer");
  browse.append(searchTools, feedback, list);
  const detail = node5("section", "pw-detail");
  detail.setAttribute("aria-label", "插件详情");
  const restartSummary = node5("div", "pw-restart-summary");
  restartSummary.setAttribute("role", "status");
  restartSummary.hidden = true;
  const progress = node5("section", "pw-operation-progress");
  progress.setAttribute("role", "status");
  progress.setAttribute("aria-live", "polite");
  progress.hidden = true;
  body.append(browse, detail);
  root.append(header, navigation, progress, restartSummary, body, footer);
  shell2.content.replaceChildren(root);
  let selected = "", locals = [], market = [], alive = false, epoch = 0, busy = false;
  let localLoaded = false, marketLoaded = false, localError = "", marketError = "", operationError = "", page2 = 1;
  let lastDetail = "", lastList = "";
  let filterValue = "all";
  let checkingUpdates = false;
  let receiptPlugin = "";
  let historyName = "";
  let requestedName;
  const filterButtons = /* @__PURE__ */ new Map();
  function setFilters() {
    filterValue = "all";
    controls.replaceChildren();
    filterButtons.clear();
    search.placeholder = view2 === "discover" ? "搜索插件、作者或 #标签" : "搜索插件、作者或标签";
    const values = view2 === "discover" ? pluginCategories : [["all", "全部"], ["running", "运行中"], ["disabled", "已停用"], ["available", "可更新"]];
    for (const [value, label] of values) {
      const chip = button3(label, () => {
        if (busy || pluginOperation()?.busy) return;
        filterValue = filterValue === value ? "all" : value;
        page2 = 1;
        lastList = "";
        render2();
      }, "pw-filter-tag");
      chip.dataset.value = value;
      filterButtons.set(value, chip);
      controls.append(chip);
    }
  }
  function discoverable() {
    if (!localLoaded) return [];
    const rows = market.map((m) => ({ name: m.name, market: m, local: locals.find((p) => p.name === m.name) }));
    if (includeLocal.checked) {
      const names = new Set(rows.map((e) => e.name));
      for (const p of locals) if (!names.has(p.name)) {
        rows.push({ name: p.name, local: p });
        names.add(p.name);
      }
      return rows;
    }
    return rows.filter((e) => !e.local?.builtin && !installed(e.local));
  }
  function entries() {
    const all = view2 === "discover" ? discoverable() : locals.map((p) => ({ name: p.name, local: p, market: market.find((m) => m.name === p.name) }));
    return all.filter((e) => {
      const tags = [...e.local?.keywords || [], ...e.market?.keywords || [], ...e.market ? [pluginCategory(e.market), pluginCategoryLabel(e.market)] : []].map((tag) => tag.toLowerCase());
      const searchable = [e.name, e.local?.displayName, e.local?.description, e.market?.description, e.local?.author, e.market?.author, ...tags].join(" ").toLowerCase();
      const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (!terms.every((term) => term.startsWith("#") ? term.length === 1 || tags.some((tag) => tag === term.slice(1)) : searchable.includes(term))) return false;
      const f = filterValue;
      if (f === "all") return true;
      if (view2 === "discover") return pluginCategory(e.market || { keywords: e.local?.keywords }) === f;
      if (f === "available") return installed(e.local) && !e.local?.builtin && updateFor(e.name)?.status === "available";
      return f === "running" ? !!e.local?.loaded : installed(e.local) && !e.local?.enabled && !e.local?.loaded && !e.local?.tracked && !e.local?.restart_required;
    }).sort((a, b) => view2 === "discover" ? 0 : originOrder(a) - originOrder(b) || Number(updateFor(b.name)?.status === "available") - Number(updateFor(a.name)?.status === "available") || a.name.localeCompare(b.name));
  }
  search.oninput = () => {
    page2 = 1;
    lastList = "";
    render2();
  };
  includeLocal.onchange = () => {
    page2 = 1;
    lastList = lastDetail = "";
    render2();
  };
  function render2() {
    if (!alive) return;
    const op = pluginOperation();
    const showProgress = op && (op.busy || op.phase === "uncertain" || op.name === receiptPlugin);
    progress.hidden = !showProgress;
    progress.replaceChildren();
    if (op && showProgress) {
      progress.dataset.phase = op.phase;
      progress.setAttribute("aria-busy", String(op.busy));
      const label = { enable: "启用插件", disable: "停用插件", remove: "卸载插件", apply: "应用更改" }[op.action];
      progress.append(node5("strong", "", label + " · " + (locals.find((p) => p.name === op.name)?.displayName || op.name)), node5("p", "", op.text));
      if (op.phase === "uncertain") progress.append(button3("继续检查", () => {
        receiptPlugin = op.name;
        void continuePluginOperation();
      }));
    }
    const pending5 = locals.filter((p) => installed(p) && p.restart_required);
    restartSummary.hidden = view2 !== "local" || !pending5.length || !!op?.busy;
    restartSummary.replaceChildren();
    if (pending5.length) {
      restartSummary.append(node5("span", "", `${pending5.length} 个插件有待生效的更改。可在详情中重启并应用，当前页面会保留。`), button3("查看待生效", () => {
        filterValue = "all";
        search.value = "";
        page2 = 1;
        requestedName = pending5[0].name;
        lastList = lastDetail = "";
        render2();
      }));
    }
    const counts = { local: locals.filter(installed).length, discover: discoverable().length, updates: locals.filter((p) => installed(p) && !p.builtin && updateFor(p.name)?.status === "available").length };
    search.disabled = busy || !!pluginOperation()?.busy;
    includeLocal.disabled = search.disabled;
    scopeCount.textContent = "";
    refresh2.textContent = busy ? "处理中…" : "刷新";
    refresh2.disabled = busy || !!pluginOperation()?.busy;
    for (const [value, chip] of filterButtons) {
      chip.disabled = busy || !!pluginOperation()?.busy;
      chip.setAttribute("aria-pressed", String(value === filterValue));
      if (value === "available") chip.textContent = `可更新 ${counts.updates}`;
    }
    const status = pluginUpdateSnapshot();
    feedback.textContent = view2 === "discover" ? [localError, marketError].filter(Boolean).join(" ") : [localError, status.error, status.checking ? "正在后台检查更新…" : status.message].filter(Boolean).join(" ");
    const rows = entries();
    const pages = Math.max(1, Math.ceil(rows.length / 30));
    if (requestedName) {
      const index = rows.findIndex((e) => e.name === requestedName);
      if (index >= 0) {
        selected = requestedName;
        page2 = Math.floor(index / 30) + 1;
        requestedName = void 0;
        requestAnimationFrame(() => {
          if (alive) detail.querySelector(".pw-update-note")?.scrollIntoView({ block: "nearest" });
        });
      } else if (localLoaded) requestedName = void 0;
    }
    page2 = Math.min(page2, pages);
    const shown = rows.slice((page2 - 1) * 30, page2 * 30);
    if (!shown.some((e) => e.name === selected)) {
      if (selected && installed(locals.find((p) => p.name === selected))) receiptPlugin = "";
      selected = shown[0]?.name || "";
      lastDetail = "";
    }
    const listKey = JSON.stringify([view2, shown, selected, busy, pluginOperation(), localLoaded, marketLoaded, localError, marketError, search.value, filterValue, shown.map((e) => [updateFor(e.name), updateMuted(e.name)])]);
    if (listKey !== lastList) {
      lastList = listKey;
      list.replaceChildren();
      let group = "";
      if (!shown.length) {
        const waiting = !localLoaded || view2 === "discover" && !marketLoaded;
        const failed = !localLoaded && localError || view2 === "discover" && !marketLoaded && marketError;
        list.append(node5("p", "pw-empty", waiting ? failed ? "暂时无法列出插件，请刷新重试。" : "正在读取插件…" : !search.value && filterValue === "available" ? "暂无可更新的插件" : search.value || filterValue !== "all" ? "没有匹配的插件" : view2 === "discover" ? "没有匹配的插件" : "暂无插件"));
      }
      for (const e of shown) {
        if (view2 === "local" && originGroup(e) !== group) {
          group = originGroup(e);
          const label = node5("h3", "pw-group", group);
          label.append(node5("span", "", String(rows.filter((row2) => originGroup(row2) === group).length)));
          list.append(label);
        }
        const item = button3("", () => {
          if (e.name !== selected) receiptPlugin = "";
          operationError = "";
          selected = e.name;
          lastDetail = "";
          render2();
        }, "pw-entry");
        item.disabled = busy || !!pluginOperation()?.busy;
        item.dataset.name = e.name;
        item.setAttribute("aria-pressed", String(e.name === selected));
        const mark = node5("span", "pw-mark");
        mark.append(icon(e.local?.builtin ? "grid" : "puzzle"));
        const copy = node5("span", "pw-entry-copy");
        copy.append(node5("strong", "", e.local?.displayName || e.name));
        const text2 = filterValue === "available" && updateFor(e.name) ? updateText(updateFor(e.name)) : e.local?.description || e.market?.description || e.name;
        const summary2 = node5("span", "pw-entry-summary", text2);
        summary2.title = text2;
        copy.append(summary2);
        const state3 = node5("span", "pw-entry-state");
        const update2 = updateFor(e.name);
        if (update2?.status === "available" && (filterValue === "available" || !updateMuted(e.name)) && installed(e.local) && !e.local?.builtin) {
          state3.textContent = `${runtime(e.local)} · 可更新`;
          state3.dataset.tone = "update";
        } else if (e.local) {
          state3.textContent = (e.local.builtin ? "内置 · " : "") + runtime(e.local);
          state3.dataset.tone = e.local.loaded ? "on" : "off";
        } else state3.textContent = e.market ? pluginCategoryLabel(e.market) : "";
        const meta = node5("span", "pw-entry-meta");
        meta.append(state3);
        if (view2 === "discover") meta.append(node5("span", "pw-entry-platform", platformSummary(platforms(e))));
        copy.append(meta);
        item.append(mark, copy);
        list.append(item);
      }
    }
    footer.replaceChildren(node5("span", "page-footer-label", view2 === "local" ? "内置与已安装" : "社区插件"));
    const footerInfo = node5("div", "page-footer-summary");
    footerInfo.append(node5("span", "", !localLoaded || view2 === "discover" && !marketLoaded ? "正在读取…" : view2 === "local" ? `共 ${counts.local} 个 · 运行中 ${locals.filter((p) => installed(p) && p.loaded).length} 个 · 可更新 ${counts.updates} 个${rows.length !== counts.local ? " · 筛选结果 " + rows.length + " 个" : ""}` : `搜索到 ${rows.length} 个插件`));
    footer.append(footerInfo);
    if (pages > 1) {
      const back = button3("上一页", () => {
        page2--;
        lastList = "";
        render2();
      });
      back.disabled = page2 === 1;
      const next = button3("下一页", () => {
        page2++;
        lastList = "";
        render2();
      });
      next.disabled = page2 === pages;
      footerInfo.append(back, node5("span", "", `${page2}/${pages}`), next);
    }
    const entry = shown.find((e) => e.name === selected);
    const detailKey = JSON.stringify([entry, view2, entry && updateFor(entry.name), operationError, busy, pluginOperation(), checkingUpdates, status.checking, historyName, entry && updateMuted(entry.name)]);
    if (detailKey !== lastDetail) {
      lastDetail = detailKey;
      drawDetail(entry);
    }
  }
  function drawDetail(e) {
    detail.replaceChildren();
    if (!e) {
      detail.append(node5("div", "pw-detail-empty", "选择一个插件，查看详情与管理选项"));
      return;
    }
    const p = e.local, m = e.market, exists = installed(p), u = updateFor(e.name);
    detail.classList.toggle("pw-builtin-qq", e.name === "core-infinity-qq" && !!p?.builtin);
    const hero = node5("header", "pw-hero");
    const mark = node5("div", "pw-mark pw-mark-large");
    mark.append(icon(p?.builtin ? "grid" : "puzzle"));
    const title = node5("div", "pw-identity");
    title.append(node5("div", "pw-eyebrow", p?.builtin ? "CORE 内置" : m ? pluginCategoryLabel(m) : "本地插件"), node5("h2", "", p?.displayName || e.name), node5("p", "pw-package", e.name));
    hero.append(mark, title);
    detail.append(hero);
    const metadata = node5("div", "pw-metadata");
    metadata.append(node5("span", "", `版本 ${p?.version || m?.version || "未声明"}`), node5("span", "", p?.author || m?.author || "未声明作者"));
    if (p?.license) metadata.append(node5("span", "", p.license));
    detail.append(metadata);
    let compatibilityBox;
    if (!p?.builtin) {
      const compatibility = platforms(e);
      const box = node5("section", "pw-compatibility");
      box.append(node5("h3", "", "运行平台"));
      const badges = node5("div", "pw-platforms");
      for (const [id, label] of Object.entries(platformNames)) {
        const support = compatibility.supported.some((value) => value === id) ? "supported" : compatibility.conditional.some((value) => value === id) ? "conditional" : "unknown";
        const badge = node5("div", "pw-platform");
        badge.dataset.support = support;
        badge.append(node5("strong", "", label), node5("span", "", support === "supported" ? "已声明支持" : support === "conditional" ? "有条件支持" : "未声明"));
        badges.append(badge);
      }
      box.append(badges, node5("p", "", compatibility.note));
      if (compatibility.source) {
        const link = node5("a", "pw-source", "平台说明 ↗");
        link.href = compatibility.source;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        box.append(link);
      }
      compatibilityBox = box;
    }
    const actions = node5("div", "pw-actions");
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", "插件操作");
    if (exists) {
      const configure = node5("a", "pw-button pw-primary", "打开配置");
      configure.href = `#/infinity/${encodeURIComponent(e.name)}`;
      if (busy) {
        configure.removeAttribute("href");
        configure.setAttribute("aria-disabled", "true");
      }
      actions.append(configure);
      const toggle = button3(p.restart_required && p.restart_action !== "config" ? p.enabled ? "撤销启用" : "撤销停用" : p.enabled ? "停用插件" : "启用插件", () => {
        void mutate(e, "switch");
      });
      toggle.disabled = busy || !!pluginOperation()?.busy;
      actions.append(toggle);
    } else {
      const install = button3(p?.builtin ? "恢复安装" : !m?.url ? "暂无正式版" : "安装插件", () => {
        void mutate(e, "install");
      }, "pw-primary");
      install.disabled = busy || !!pluginOperation()?.busy || !p?.builtin && !m?.url;
      actions.append(install);
    }
    if (exists && p?.builtin) {
      const check2 = button3("检查 Core 更新", () => openCoreUpdateDialog(check2));
      actions.append(check2);
    } else if (exists) {
      const check2 = button3(checkingUpdates || pluginUpdateSnapshot().checking ? "检查中…" : "检查更新", () => {
        void checkUpdates();
      });
      check2.disabled = busy || !!pluginOperation()?.busy || checkingUpdates || pluginUpdateSnapshot().checking;
      actions.append(check2);
    }
    const source = repo(e);
    if (source) {
      const link = node5("a", "pw-button", "GitHub 源代码 ↗");
      link.href = source;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.append(link);
    }
    if (exists) {
      const remove = button3("卸载插件", () => {
        void mutate(e, "remove");
      }, "pw-remove");
      remove.disabled = busy || !!pluginOperation()?.busy || p?.can_remove === false;
      actions.append(remove);
    }
    if (exists && p?.restart_required) {
      const apply = button3("重启并应用", () => {
        void mutate(e, "apply");
      });
      apply.disabled = busy || !!pluginOperation()?.busy;
      actions.append(apply);
    }
    const versionRepo = !p?.builtin && source.startsWith("https://github.com/") ? source.slice("https://github.com/".length) : "";
    detail.append(actions);
    const blocked = busy || !!pluginOperation()?.busy;
    const history = () => pluginVersions(
      versionRepo,
      e.name,
      exists ? p?.version || "" : "",
      blocked,
      (row2) => {
        void changeVersion(e, versionRepo, row2.tag);
      }
    );
    if (exists && !p?.builtin) {
      const update2 = node5("section", "pw-update-note");
      const heading3 = node5("div", "pw-update-heading");
      heading3.append(node5("h3", "", u?.status === "available" ? "有新版本可用" : "版本检查"));
      if (versionRepo) {
        const label = node5("label", "pw-update-reminder", "更新提醒");
        label.title = "在当前浏览器中接收此 Bot 的插件更新提醒";
        const input2 = node5("input");
        input2.type = "checkbox";
        input2.setAttribute("role", "switch");
        input2.checked = !updateMuted(e.name);
        input2.onchange = () => {
          setUpdateMuted(e.name, !input2.checked);
          detail.querySelector(".pw-update-reminder input")?.focus({ preventScroll: true });
        };
        label.append(input2);
        heading3.append(label);
      }
      update2.append(heading3);
      const row2 = node5("div", "pw-version-controls");
      row2.append(node5("span", "pw-update-version", u ? updateText(u) : "等待读取版本信息"));
      if (versionRepo && u?.status === "available" && u.latest) {
        const apply = button3("停用并更新", () => {
          void changeVersion(e, versionRepo, u.tag, u.latest);
        }, "pw-primary");
        apply.disabled = blocked || u.current !== p?.version;
        row2.append(apply);
      }
      if (versionRepo) {
        const expanded = historyName === e.name;
        const toggle = button3("查看历史版本", () => {
          historyName = expanded ? "" : e.name;
          render2();
          detail.querySelector('[aria-controls="pw-version-history"]')?.focus({ preventScroll: true });
        });
        toggle.disabled = blocked;
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.setAttribute("aria-controls", "pw-version-history");
        row2.append(toggle);
      }
      update2.append(row2);
      if (u?.checkedAt) update2.append(node5("small", "", `上次成功检查 ${new Date(u.checkedAt * 1e3).toLocaleString()}`));
      if (u?.error) update2.append(node5("p", "", u.error));
      if (versionRepo && historyName === e.name) {
        const panel2 = history();
        panel2.id = "pw-version-history";
        update2.append(panel2);
        void panel2.loadVersions();
      }
      detail.append(update2);
    } else if (versionRepo) detail.append(history());
    const result = node5("p", "pw-operation", operationError);
    result.setAttribute("role", "status");
    detail.append(result);
    if (p?.restart_required) {
      const note = node5("section", "pw-restart-note");
      note.append(node5("h3", "", "重启后生效"), node5("p", "", p.restart_reason || "设置已保存，重启当前 Bot 的 Core 服务后生效。"), node5("p", "", p.loaded ? "插件当前仍在运行，任务结束后再重启服务。刷新网页不会使此更改生效。" : "点击“重启并应用”后，页面将显示重启进度并核对生效结果。"));
      detail.append(note);
    }
    if (p?.lifecycle_error) detail.append(node5("p", "pw-operation", p.lifecycle_error));
    if (compatibilityBox) detail.append(compatibilityBox);
    const descriptions = node5("div", "pw-detail-sections");
    const about = node5("section", "pw-about");
    about.append(node5("h3", "", "关于插件"), node5("p", "", p?.description || m?.description || "作者尚未提供简介。"));
    const requirement = m && pluginRequirement(m);
    if (requirement) about.append(node5("p", "pw-requirement", requirement));
    if (p?.contract) about.append(node5("p", "pw-operation", p.contract));
    descriptions.append(about);
    if (p?.builtin && e.name === "core-infinity-qq") {
      const features = node5("div", "pw-feature-list");
      for (const [title2, text2] of [
        ["消息与会话", "收发私聊与群聊消息，配置允许接入的账号和群。"],
        ["图片与表情", "查看图片、收藏表情，并发送已保存的图片。"],
        ["语音交流", "配置语音识别和合成模型，进行本机自检。"],
        ["工具与扩展", "查看已注册工具，为 ComfyUI 等 QQ 扩展提供消息通道。"]
      ]) {
        const row2 = node5("div", "pw-feature");
        row2.append(node5("h4", "", title2), node5("p", "", text2));
        features.append(row2);
      }
      about.append(features, node5("p", "pw-feature-note", "首次使用请打开配置，连接 SnowLuma 并设置会话路由。具体可用功能取决于当前配置与权限。"));
    }
    const info = node5("section", "pw-info");
    info.append(node5("h3", "", "安装信息"));
    const fields = node5("dl");
    const field = (label, value) => {
      fields.append(node5("dt", "", label), node5("dd", "", value || "未声明"));
    };
    field("来源", p?.builtin ? "Core 内置" : p?.origin?.source === "github" ? "GitHub" : p ? "本地导入" : m?.source || "GitHub");
    field("安装状态", exists ? "已安装" : p?.builtin ? "可从 Core 恢复" : "尚未安装");
    const parent = p?.navigationParent;
    if (parent) field("所属主插件", locals.find((one) => one.name === parent)?.displayName || parent);
    if (p?.where) field("安装位置", p.where);
    if (p?.api !== void 0) field("兼容接口", `Infinity API ${p.api ?? "未声明"}`);
    if (m?.date) field("仓库更新", m.date);
    if (p?.builtin) field("更新方式", "随 Core 更新");
    if (p?.reload_mode) field("生效方式", p.reload_mode === "restart" ? "启用和停用需重启 Core 服务" : p.reload_mode === "hot" ? "支持热加载" : "声明无效");
    info.append(fields);
    descriptions.append(info);
    detail.append(descriptions);
    const scroll = node5("div", "pw-detail-scroll");
    const children = [...detail.children];
    scroll.append(...children.slice(children.indexOf(actions) + 1));
    detail.append(scroll);
  }
  async function changeVersion(e, repository, tag, latest) {
    if (busy || pluginOperation()?.busy || !alive) return;
    const wasInstalled = installed(e.local);
    const mustStop = !!(e.local?.enabled || e.local?.loaded || e.local?.tracked || e.local?.restart_required);
    const label = latest ? "停用并更新" : wasInstalled ? "替换安装" : "安装此版本";
    const description = [
      `将安装 ${latest || tag}。`,
      mustStop ? "将先停用当前 Bot 的插件。" : "",
      mustStop && (e.local?.reload_mode === "restart" || e.local?.restart_required) ? "此操作可能重启当前 Bot 服务。" : "",
      wasInstalled ? "原安装包会备份，配置和缓存保留。" : "",
      "安装后插件保持停用。"
    ].join("");
    const accepted = await confirmAction(label, description, label, false, { className: "pw-confirm", detail: e.local?.displayName || e.name });
    if (!accepted || !alive || busy || selected !== e.name || pluginOperation()?.busy) return;
    busy = true;
    operationError = "正在核对所选版本…";
    render2();
    try {
      const releaseTag = tag || latest && await tagForVersion(repository, latest);
      if (!releaseTag) throw Error("缺少正式版本标签，请重新检查更新。");
      if (mustStop) {
        receiptPlugin = e.name;
        await runPluginOperation(e.name, "disable", e.local?.version);
        const stop = pluginOperation();
        if (stop?.phase !== "complete") throw Error(stop?.text || "尚未确认插件已停用。");
      }
      operationError = "正在下载并安装 " + releaseTag + "…";
      render2();
      const response = await pageFetch("/market/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: { repository, tag: releaseTag, name: e.name, current: wasInstalled ? e.local?.version || "" : "" } })
      });
      if (!response.ok) throw Error((await response.text()).trim() || "版本安装失败");
      const result = await response.json();
      if (!result.ok) throw Error(result.said || result.error || "版本安装失败");
      operationError = result.said || "安装完成。";
    } catch (error) {
      operationError = error instanceof Error ? error.message : "版本安装失败";
    } finally {
      const token = ++epoch;
      await readLocal(token);
      if (view2 === "discover") await readMarket(token);
      window.dispatchEvent(new Event("plugins-changed"));
      busy = false;
      lastList = lastDetail = "";
      render2();
    }
  }
  async function checkUpdates() {
    if (checkingUpdates || busy) return;
    checkingUpdates = true;
    render2();
    try {
      await checkPluginUpdates(true);
    } finally {
      checkingUpdates = false;
      render2();
    }
  }
  async function mutate(e, action) {
    if (busy || pluginOperation()?.busy || !alive) return;
    busy = true;
    operationError = "";
    const startedEpoch = ++epoch;
    render2();
    try {
      const label = action === "apply" ? "重启并应用" : action === "remove" ? "卸载插件" : action === "install" ? e.local?.builtin ? "恢复安装" : "安装插件" : e.local?.enabled ? "停用插件" : "启用插件";
      let message = action === "apply" ? "重启当前 Bot 服务并应用更改。页面会保留并显示进度。" : action === "remove" ? "该安装包由所有 Bot 共用。卸载将先在各 Bot 停用插件，保留配置和缓存。" : action === "install" ? "安装插件程序，完成后再配置并启用。" + (e.local?.builtin ? "" : ` 运行平台：${platformSummary(platforms(e))}。${platforms(e).note}`) : `确认${e.local?.enabled ? "停用" : "启用"}当前 Bot 的这个插件？`;
      if (action === "switch" && e.local?.reload_mode === "restart") message = e.local.restart_required && e.local.restart_action !== "config" ? "撤销尚未生效的切换，恢复之前的启用设置。" : "如果插件需要重启，将在保存设置后自动重启当前 Bot 服务。页面会保留并显示进度。";
      if (action === "remove" && e.local?.reload_mode === "restart") message += " 如当前 Bot 需要重启，页面会等待重连后继续完成卸载。其他 Bot 仍占用时会保留安装包并说明原因。";
      const accepted = await confirmAction(label, message, label, false, { className: "pw-confirm", detail: e.local?.displayName || e.name });
      if (!accepted || !alive || epoch !== startedEpoch) return;
      if (action !== "install") {
        receiptPlugin = e.name;
        await runPluginOperation(e.name, action === "switch" ? e.local?.enabled ? "disable" : "enable" : action);
        epoch++;
        await readLocal(epoch);
        if (view2 === "discover") await readMarket(epoch);
        return;
      }
      const path = "/market/install";
      const payload = e.local?.builtin ? { from: e.local.where } : { url: e.market?.url };
      try {
        const response = await pageFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        if (!response.ok) throw new Error((await response.text()).trim() || `${label}失败`);
        const answer = await response.json();
        if (answer.ok === false) throw new Error(answer.said || `${label}失败`);
      } catch (error) {
        operationError = error instanceof Error ? error.message : `${label}失败`;
      }
      epoch++;
      await readLocal(epoch);
      if (view2 === "discover") await readMarket(epoch);
      window.dispatchEvent(new Event("plugins-changed"));
      if (localError) operationError = [operationError, "操作后未能刷新安装状态，请重新刷新。"].filter(Boolean).join(" ");
    } finally {
      busy = false;
      lastList = lastDetail = "";
      render2();
      requested3();
    }
  }
  const navOrder = node5("section", "pw-order-preview");
  const orderRows = node5("div", "pw-order-rows");
  navOrder.append(node5("p", "pw-order-help", "拖动侧栏的六点手柄。主插件带着扩展一起移动，扩展可在同组换位。"), orderRows);
  const orderActions = node5("div", "pw-order-actions");
  const railButton = (selector) => shell2.railNav.querySelector(selector);
  const editOrder = button3("调整顺序", () => {
    const toggle = document.querySelector(".compact-menu-toggle");
    if (toggle?.offsetWidth && toggle.getAttribute("aria-expanded") === "false") toggle.click();
    railButton(".plugin-nav-edit")?.click();
  }, "pw-quiet");
  orderActions.append(editOrder);
  if (view2 === "local") {
    header.append(orderActions);
    header.after(navOrder);
  }
  function reflectNavigation() {
    if (view2 !== "local") return;
    const tree = shell2.railNav.querySelector(".rail-sublist.is-sorting");
    const sorting = !!tree;
    root.classList.toggle("pw-ordering", sorting);
    orderActions.hidden = sorting || !railButton(".plugin-nav-edit");
    editOrder.hidden = sorting;
    navOrder.hidden = !sorting;
    heading2.querySelector("h1").textContent = sorting ? "调整顺序" : "插件总览 · PLUGIN OVERVIEW";
    orderRows.replaceChildren();
    if (tree) for (const unit of tree.querySelectorAll("[data-nav-id]")) {
      const link = unit.querySelector(":scope > .plugin-nav-row .rail-item");
      const entry = node5("div", "pw-order-row");
      entry.append(
        icon("puzzle"),
        node5("span", "", link.querySelector(".rail-label")?.textContent || ""),
        node5("small", "", unit.parentElement?.classList.contains("plugin-nav-children") ? "扩展插件" : unit.querySelector(":scope > .plugin-nav-children") ? "主插件" : "独立插件")
      );
      orderRows.append(entry);
    }
  }
  async function readLocal(token) {
    try {
      const response = await pageFetch("/plugins", { cache: "no-store" });
      if (!response.ok) throw Error();
      const data = await response.json();
      if (!data.wired || !Array.isArray(data.plugins)) throw Error();
      if (alive && epoch === token) {
        locals = data.plugins;
        acceptPluginRuntime(locals);
        localLoaded = true;
        localError = data.broken?.length ? "部分插件清单读取失败：" + data.broken.join("、") : "";
      }
    } catch {
      if (alive && epoch === token) localError = localLoaded ? "无法刷新，当前显示上次读取的本机插件。" : "无法读取本机插件，请重试。";
    }
    render2();
  }
  let marketRequest = 0;
  async function readMarket(token) {
    const request2 = ++marketRequest;
    try {
      const response = await pageFetch("/market", { cache: "no-store" });
      if (!response.ok) throw Error();
      const data = await response.json();
      if (!data.wired || !Array.isArray(data.found)) throw Error(data.said);
      if (alive && epoch === token && request2 === marketRequest) {
        market = data.found;
        marketLoaded = true;
        marketError = data.stale ? data.error || "显示上次缓存，市场正在后台刷新。" : "";
      }
    } catch {
      if (alive && epoch === token) marketError = marketLoaded ? "市场暂时无法刷新，保留上次结果。" : "无法读取插件市场，请重试。";
    } finally {
      render2();
    }
  }
  async function read4() {
    if (busy || !alive) return;
    const token = ++epoch;
    await Promise.allSettled([readLocal(token), view2 === "discover" ? readMarket(token) : Promise.resolve()]);
  }
  function requested3() {
    if (busy || !alive || view2 !== "local") return;
    const request2 = takePluginUpdateRequest();
    if (request2) {
      search.value = "";
      page2 = 1;
      selected = "";
      operationError = "";
      filterValue = request2.name && updateFor(request2.name)?.status !== "available" ? "all" : "available";
      requestedName = request2.name || pluginUpdateSnapshot().rows.find((row2) => row2.status === "available")?.name;
      lastList = lastDetail = "";
      render2();
    }
  }
  function restartRequested() {
    if (busy || !alive || view2 !== "local") return;
    const name = takePluginRestartRequest();
    if (name) {
      filterValue = "all";
      search.value = "";
      page2 = 1;
      requestedName = name;
      lastList = lastDetail = "";
      render2();
    }
  }
  const updateChanged = () => render2();
  const pluginsChanged = () => {
    if (!busy) void read4();
  };
  let timer;
  setFilters();
  return { root, show() {
    if (view2 === "local") {
      shell2.railNav.addEventListener("plugin-navigation-order", reflectNavigation);
      reflectNavigation();
    }
    alive = true;
    root.hidden = false;
    window.addEventListener("plugin-updates-changed", updateChanged);
    window.addEventListener("plugin-operation-changed", updateChanged);
    window.addEventListener("plugin-updates-open", requested3);
    window.addEventListener("plugin-restart-open", restartRequested);
    window.addEventListener("plugins-changed", pluginsChanged);
    requested3();
    restartRequested();
    render2();
    void read4();
    timer = window.setInterval(() => {
      if (!document.hidden && view2 === "local" && !busy) void read4();
    }, 3e4);
  }, stop() {
    shell2.railNav.removeEventListener("plugin-navigation-order", reflectNavigation);
    alive = false;
    epoch++;
    root.hidden = true;
    clearInterval(timer);
    window.removeEventListener("plugin-updates-changed", updateChanged);
    window.removeEventListener("plugin-operation-changed", updateChanged);
    window.removeEventListener("plugin-updates-open", requested3);
    window.removeEventListener("plugin-restart-open", restartRequested);
    window.removeEventListener("plugins-changed", pluginsChanged);
  } };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-market-page.ts
function pluginMarketPage(shell2) {
  return pluginWorkspace(shell2, "discover");
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/infinity-plugin-page.ts
function infinityPluginPage(shell2) {
  const route2 = location.hash.replace(/^#\/?infinity\//, "");
  let name = "";
  try {
    name = decodeURIComponent(route2);
  } catch {
    name = "";
  }
  return pluginPage(shell2, name);
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/knowledge-page.ts
var READ_FAILED2 = "知识库加载失败，请重试。";
var NOT_WIRED2 = "当前服务暂不支持查看知识库。";
var NOTHING_YET = "暂无知识库内容。";
var stripComments = (one) => one.replace(/<!--[\s\S]*?-->/g, "");
var theLines = (text2) => stripComments(String(text2 || "")).split("\n").filter((one) => one.trim() !== "");
var theShownName = (one) => String(one || "").replace(/\.md$/i, "");
function knowledgePage(shell2) {
  const root = document.createElement("section");
  root.className = "page-knowledge page-world world-studio";
  const line = (tag, cls, text2) => {
    const one = document.createElement(tag);
    if (cls !== "") one.className = cls;
    if (text2 !== void 0) one.textContent = text2;
    return one;
  };
  const head = document.createElement("div");
  head.className = "head";
  const title = document.createElement("h1");
  coreSetUIText(title, "textContent", corePhrase("知识库 · KNOWLEDGE"));
  const find2 = searchBox("搜索文档名称");
  head.append(title, find2.element);
  root.append(head);
  const book = document.createElement("div");
  book.className = "book";
  const toc = document.createElement("div");
  toc.className = "toc";
  const body = document.createElement("div");
  body.className = "page-body";
  book.append(toc, body);
  root.append(book);
  const tail = document.createElement("div");
  tail.className = "tail page-footer";
  root.append(tail);
  shell2.content.replaceChildren(root);
  const draw = (answer, q) => {
    toc.replaceChildren();
    body.replaceChildren();
    tail.replaceChildren(line("span", "page-footer-label", "知识与资料"));
    if (answer === null || answer.wired === false) {
      const prose = line("div", "prose");
      prose.append(line("p", "fade", answer === null ? READ_FAILED2 : NOT_WIRED2));
      body.append(prose);
      return;
    }
    if (q && answer.search_limited) {
      tail.append(line("span", "fade", "本次使用字面检索，语义检索暂不可用。"));
    }
    const rows = answer.knowledge ?? [];
    const howMany = rows.reduce((sum, one) => sum + (one.lines ?? theLines(one.text).length), 0);
    if (rows.length === 0) {
      toc.append(line("div", "empty", q === "" ? "暂无文档。" : "没有匹配结果。"));
      const prose = line("div", "prose");
      prose.append(line("p", "fade", q === "" ? NOTHING_YET : "「" + q + "」没有匹配文档。"));
      body.append(prose);
      tail.append(line("span", "", "共 0 份 · 0 行"));
      return;
    }
    let at = 0;
    const showBody = (index) => {
      const one = rows[index];
      const heads = theLines(one.text);
      body.replaceChildren();
      body.scrollTop = 0;
      body.append(line("h2", "", one.title ?? theShownName(one.name)));
      body.append(line("p", "gist", heads[0] ?? "暂无正文。"));
      const prose = line("div", "prose");
      for (const said of heads.slice(1)) prose.append(line("p", "", said));
      body.append(prose);
      const foot = line("div", "foot");
      foot.append(line("span", "", String(one.lines ?? heads.length) + " 行"));
      if (one.version) foot.append(line("span", "", one.name + " · 版本 " + String(one.version)));
      if ((one.at ?? "") !== "") foot.append(line("span", "", "最后写于 " + displayDate(one.at)));
      body.append(foot);
    };
    const pick2 = (index) => {
      at = index;
      for (const el6 of toc.querySelectorAll(".one")) {
        el6.classList.toggle("on", el6.dataset.at === String(index));
      }
      showBody(index);
    };
    const keyOf = (name) => {
      const shown = String(name ?? "").replace(/\.md$/, "");
      const parts = shown.split("·");
      return parts.length >= 3 ? parts.slice(0, 2).join("·") : shown;
    };
    const drawOne = (index) => {
      const one = rows[index];
      const item = line("div", "one");
      item.dataset.at = String(index);
      item.title = one.at || "";
      item.append(line("div", "n", one.title ?? theShownName(one.name)));
      const n = one.lines ?? theLines(one.text).length;
      item.append(line("div", "m", String(n) + " 行" + ((one.at ?? "") === "" ? "" : " · " + displayDate(one.at))));
      item.addEventListener("click", () => {
        pick2(index);
      });
      return item;
    };
    if (q !== "") {
      rows.forEach((_, index) => toc.append(drawOne(index)));
    } else {
      const groups = /* @__PURE__ */ new Map();
      rows.forEach((one, index) => {
        const key2 = String(one.group ?? "") || keyOf(one.name);
        groups.set(key2, [...groups.get(key2) ?? [], index]);
      });
      groups.forEach((idxs, key2) => {
        const box = document.createElement("section");
        box.className = "group";
        const head2 = document.createElement("h3");
        head2.className = "group-head";
        head2.textContent = key2 + " · " + String(idxs.length);
        box.append(head2);
        let shown = 0;
        const more = document.createElement("button");
        more.type = "button";
        more.className = "infinity-text-link";
        const build = () => {
          const next = idxs.slice(shown, shown + 30);
          shown += next.length;
          for (const i of next) box.insertBefore(drawOne(i), more);
          more.hidden = shown >= idxs.length;
          more.textContent = "显示更多（剩余 " + (idxs.length - shown) + "）";
        };
        more.onclick = build;
        box.append(more);
        build();
        toc.append(box);
      });
    }
    pick2(at);
    tail.append(
      line("span", "", "共 " + String(rows.length) + " 份 · " + String(howMany) + " 行")
    );
  };
  let requestId = 0;
  const readHint = pageLoad(root, "知识库", () => {
    void refresh2(find2.input.value.trim());
  });
  const refresh2 = async (q) => {
    const mine = ++requestId;
    const ask2 = q === "" ? "/knowledge" : "/knowledge?q=" + encodeURIComponent(q);
    readHint.start();
    try {
      const answer = await getStatusJson(ask2);
      if (mine !== requestId || root.hidden) return;
      draw(answer, q);
      readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) {
        void refresh2(q);
        return;
      }
      console.error("知识库读取失败：", why);
      readHint.fail();
    }
  };
  find2.onAsk((q) => {
    void refresh2(q);
  });
  return {
    root,
    show() {
      root.hidden = false;
      void refresh2(find2.input.value.trim());
    },
    stop() {
      requestId += 1;
      root.hidden = true;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/portrait-display.ts
function portraitAccount(raw) {
  const group = /^group:qq:\d+\|sender=(qq:\d+)$/.exec(raw);
  if (group) return group[1];
  if (/^private:(qq:\d+|console:local-owner)$/.test(raw)) return raw.slice(8);
  if (/^QQ-\d+$/.test(raw)) return "qq:" + raw.slice(3);
  if (raw === "Console-local-owner") return "console:local-owner";
  return raw;
}
function portraitOrigin(raw) {
  const account = portraitAccount(raw);
  if (/^qq:\d+$/.test(account)) return "QQ · " + account.slice(3);
  if (account === "console:local-owner") return "控制台 · 本机";
  return raw;
}
function portraitSource(raw) {
  const group = /^group:qq:(\d+)\|sender=qq:(\d+)$/.exec(raw);
  return group ? "QQ 群 · " + group[1] : portraitOrigin(raw);
}
function portraitEncounters(person, people) {
  const account = portraitAccount(person.display_account || person.plat || person.who);
  if (!/^qq:\d+$/.test(account)) return [];
  const rooms = /* @__PURE__ */ new Map();
  const add = (visit) => {
    if (visit.account !== account || !/^\d+$/.test(visit.room)) return;
    const old = rooms.get(visit.room);
    const dates = [old?.first, old?.last, visit.first, visit.last].filter((v) => !!v && !Number.isNaN(Date.parse(v))).sort((a, b) => Date.parse(a) - Date.parse(b));
    rooms.set(visit.room, { account, room: visit.room, label: visit.label || old?.label, first: dates[0], last: dates.at(-1) });
  };
  for (const row2 of people) {
    for (const visit of row2.encounters || []) add(visit);
    for (const source of [{ from: row2.plat || "", ts: "" }, ...row2.said || []]) {
      const match = /^group:qq:(\d+)\|sender=(qq:\d+)$/.exec(source.from);
      if (match) add({ room: match[1], account: match[2], first: source.ts, last: source.ts });
    }
  }
  return [...rooms.values()].sort((a, b) => (b.last || "").localeCompare(a.last || "") || a.room.localeCompare(b.room));
}
function portraitPlatform(person) {
  const account = portraitAccount(person.display_account || person.plat || person.who);
  const key2 = /^(?:private:|group:)?([a-z][a-z0-9_-]*):/i.exec(account)?.[1].toLowerCase() || "unknown";
  const labels = { console: "控制台", qq: "QQ", discord: "Discord", telegram: "Telegram", wechat: "微信" };
  return { key: key2, label: labels[key2] || (key2 === "unknown" ? "其他" : key2) };
}
function portraitLinkedToConsole(person) {
  const account = portraitAccount(person.display_account || person.plat || person.who);
  return portraitPlatform(person).key !== "console" && !!person.identity?.accounts.includes(account) && !!person.identity.accounts.some((value) => value.startsWith("console:"));
}
function portraitGroups(people) {
  const groups = /* @__PURE__ */ new Map();
  for (const person of people) {
    const platform = portraitPlatform(person);
    if (!groups.has(platform.key)) groups.set(platform.key, { ...platform, people: [] });
    groups.get(platform.key).people.push(person);
  }
  return [...groups.values()].sort((a, b) => Number(b.key === "console") - Number(a.key === "console")).map((group) => ({ ...group, people: group.people.sort((a, b) => Number(portraitLinkedToConsole(b)) - Number(portraitLinkedToConsole(a))) }));
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/people-page.ts
var READ_FAILED3 = "画像加载失败，请重试。";
var NOT_WIRED3 = "当前服务暂不支持查看画像。";
var NOBODY_YET = "暂无画像记录。";
var ROSTER_EMPTY = "暂无人物。";
var SAID_SHOWN = 5;
function peoplePage(shell2) {
  const expandedPeople = /* @__PURE__ */ new Set();
  const root = document.createElement("section");
  root.className = "page-people page-world world-studio";
  const head = document.createElement("div");
  head.className = "head";
  const title = document.createElement("h1");
  coreSetUIText(title, "textContent", corePhrase("画像 · PROFILE"));
  const find2 = searchBox("搜索姓名或账号");
  head.append(title, find2.element);
  root.append(head);
  const faces = document.createElement("div");
  faces.className = "faces";
  const roster = document.createElement("div");
  roster.className = "roster";
  const face = document.createElement("div");
  face.className = "portrait-detail";
  faces.append(roster, face);
  root.append(faces);
  const tail = document.createElement("div");
  tail.className = "tail page-footer";
  root.append(tail);
  shell2.content.replaceChildren(root);
  const line = (tag, cls, text2) => {
    const one = document.createElement(tag);
    if (cls !== "") one.className = cls;
    if (text2 !== void 0) one.textContent = text2;
    return one;
  };
  const confirmIdentity = (heading2, subject, detail, action, submit) => {
    const opener = document.activeElement;
    const shade = line("div", "identity-confirm-backdrop");
    const dialog = line("section", "identity-confirm");
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    const title2 = line("h2", "", heading2);
    const subjectLine = line("div", "identity-confirm-subject", subject);
    const description = line("p", "", detail);
    const error = line("p", "identity-confirm-error");
    title2.id = "identity-confirm-title";
    description.id = "identity-confirm-description";
    dialog.setAttribute("aria-labelledby", title2.id);
    dialog.setAttribute("aria-describedby", description.id);
    error.setAttribute("role", "status");
    const actions = line("div", "identity-confirm-actions");
    const cancel = line("button", "", "取消");
    const proceed = line("button", "primary", action);
    cancel.type = "button";
    proceed.type = "button";
    let busy = false;
    const close2 = () => {
      if (busy) return;
      shade.remove();
      document.removeEventListener("keydown", onKey);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
      else find2.input.focus();
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close2();
      }
      if (event.key !== "Tab") return;
      event.preventDefault();
      (document.activeElement === cancel ? proceed : cancel).focus();
    };
    cancel.addEventListener("click", close2);
    proceed.addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      cancel.disabled = true;
      proceed.disabled = true;
      coreSetUIText(proceed, "textContent", corePhrase("处理中…"));
      const problem = await submit();
      busy = false;
      if (problem === null) {
        close2();
        return;
      }
      error.textContent = problem;
      cancel.disabled = false;
      proceed.disabled = false;
      proceed.textContent = action;
      proceed.focus();
    });
    shade.addEventListener("click", (event) => {
      if (event.target === shade) close2();
    });
    document.addEventListener("keydown", onKey);
    actions.append(cancel, proceed);
    dialog.append(
      line("span", "identity-confirm-kicker", "身份管理"),
      title2,
      subjectLine,
      description,
      error,
      actions
    );
    shade.append(dialog);
    root.append(shade);
    cancel.focus();
  };
  const facts = (one) => {
    const box = line("div", "who-facts");
    const put2 = (lead, value, thin) => {
      if (value === "") return;
      const bit = line("span", thin ? "thin" : "");
      bit.append(lead + " ");
      bit.append(line("b", "", value));
      box.append(bit);
    };
    if (one.full === true) {
      box.append(line("span", "thin", "已达记录上限，新增记录会替换最旧记录"));
    }
    if (one.favor != null) put2("关系评分", String(one.favor), true);
    return box;
  };
  const gist = (one) => {
    const said = String(one?.standing ?? "");
    if (said === "") {
      return line("p", "gist void", one === null ? NOBODY_YET : "");
    }
    return line("p", "gist", said);
  };
  const theSaid = (one, at_most) => {
    const rows = [...one.said ?? []].reverse();
    const newestOf = {};
    rows.forEach((said, index) => {
      if (newestOf[said.role] === void 0) newestOf[said.role] = index;
    });
    return rows.slice(0, at_most).map((said, index) => {
      const older = newestOf[said.role] !== index;
      const item = line("div", "said" + (said.role === "名字" ? " naming" : "") + (older ? " older" : ""));
      const date = /^(\d{4})(?:-?)(\d{2})(?:-?)(\d{2})(?:T|[- ]|$)/.exec(said.ts ?? "");
      const fullDate = date ? date.slice(1).join("-") : "";
      const duplicateDate = fullDate !== "" && said.text.startsWith(fullDate + " ");
      const when = line("span", "when", fullDate || said.when);
      if (fullDate) when.title = fullDate;
      item.append(when);
      const words = line("div", "words");
      const text2 = line("div", "said-text", duplicateDate ? said.text.slice(fullDate.length + 1) : said.text);
      if (duplicateDate) text2.title = said.text;
      words.append(text2);
      const meta = line("div", "said-meta");
      if (said.kind_label) meta.append(line("span", "from", said.kind_label));
      if (said.from !== "") {
        const source = line("span", "from", portraitSource(said.from));
        source.title = said.from;
        meta.append(source);
        if (said.source_cursor) meta.append(line("span", "from", "来源事件 #" + said.source_cursor));
      }
      if (older) meta.append(line("span", "from", "早先"));
      if (meta.childElementCount) words.append(meta);
      item.append(words);
      return item;
    });
  };
  const draw = (answer) => {
    roster.replaceChildren();
    face.replaceChildren();
    tail.replaceChildren(line("span", "page-footer-label", "人物与关系"));
    if (answer === null || answer.wired === false) {
      const said = line("p", "gist void", answer === null ? READ_FAILED3 : NOT_WIRED3);
      face.append(said);
      return;
    }
    const groups = portraitGroups(answer.everyone ?? []);
    const rows = groups.flatMap((group) => group.people);
    if (rows.length === 0) {
      roster.append(line("div", "empty", ROSTER_EMPTY));
      face.append(gist(null));
      tail.append(
        line("span", "", "共 0 个人")
      );
      return;
    }
    let at = Math.max(0, rows.findIndex((one) => one.who === selectedWho));
    const showFace = (index, expanded) => {
      const one = rows[index];
      expanded = expanded ?? expandedPeople.has(one.who);
      if (expanded) expandedPeople.add(one.who);
      else expandedPeople.delete(one.who);
      face.replaceChildren();
      const card = line("div", "portrait-head");
      const body = line("div", "who-body");
      const nameRow = line("div", "who-name");
      nameRow.append(line("h2", "", one.display_name || one.who));
      if (String(one.plat ?? "") !== "") {
        nameRow.append(line("span", "plat", portraitOrigin(one.display_account || String(one.plat))));
      }
      if (one.blocked) nameRow.append(line("span", "gate", " 已屏蔽"));
      body.append(nameRow);
      const rooms = portraitEncounters(one, rows);
      if (rooms.length) {
        const context = document.createElement("section");
        context.className = "portrait-encounters";
        const summary2 = line("h3", "");
        summary2.append(line("span", "", "相遇记录"), line("span", "encounter-count", rooms.length + " 个 QQ 群"));
        context.append(summary2);
        const list = line("div", "encounter-list");
        for (const visit of rooms) {
          const item = line("div", "encounter-row");
          item.append(line("span", "", "QQ 群 · " + visit.room));
          const date = (value) => value ? new Date(value).toLocaleDateString("zh-CN") : "";
          item.append(line("span", "encounter-date", visit.first ? "首次 " + date(visit.first) + (visit.last !== visit.first ? " · 最近 " + date(visit.last) : "") : "来自已保存的群聊记录"));
          list.append(item);
        }
        context.append(list);
        body.append(context);
      }
      if (one.standing?.trim()) {
        const overview = line("section", "portrait-overview");
        overview.append(line("h3", "", "人物概述"), gist(one));
        body.append(overview);
      }
      body.append(facts(one));
      card.append(body);
      face.append(card);
      const count4 = one.said?.length ?? 0;
      face.append(line("h3", "portrait-records-title", "画像记录 · " + count4));
      if (!count4) face.append(line("p", "portrait-empty", "暂无画像记录。"));
      for (const item of theSaid(one, expanded ? count4 : SAID_SHOWN)) face.append(item);
      const hidden = count4 - SAID_SHOWN;
      if (hidden > 0 && !expanded) {
        const zone = line("div", "more-zone");
        const first = line("div", "line1");
        first.append(line("span", "lead", expanded ? "已显示全部记录" : "还有 " + String(hidden) + " 条记录"));
        const op = line("button", "op", "显示全部记录");
        op.type = "button";
        op.addEventListener("click", () => {
          showFace(index, true);
        });
        first.append(op);
        zone.append(first);
        face.append(zone);
      }
      const join = theJoin();
      if (join !== null) face.append(join);
    };
    const theJoin = () => {
      if (!Array.isArray(answer.identities)) {
        return answer.identity_error ? line(
          "p",
          "identity-status",
          "身份绑定记录暂时读不到，已停止绑定操作；画像仍可查看。"
        ) : null;
      }
      const one = rows[at];
      const identity = one.identity;
      const zone = line("details", "join identity-link compact-disclosure");
      zone.append(line("summary", "line1", "账号关联"));
      zone.append(line(
        "p",
        "why",
        "把同一个人的不同账号关联起来，方便 Bot 识别。昵称相同不会自动关联。"
      ));
      const status = line("p", "identity-status");
      const update2 = async (body) => {
        try {
          const got = await pageFetch("/people/identity", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...body, confirm: true })
          });
          const result = await got.json();
          if (got.status === 409) return "账号关联已变化，请刷新后重新确认。";
          if (!got.ok || result.ok !== true) {
            return result.said || "操作没有完成。";
          }
          await refresh2(find2.input.value.trim());
          return null;
        } catch (why) {
          console.error("身份绑定操作失败：", why);
          return "操作失败，身份绑定未确认。";
        }
      };
      const association = one.association;
      if (!association?.revision) {
        zone.append(line("p", "identity-status", "账号信息不完整，请刷新后再操作。"));
        return zone;
      }
      const advanced = document.createElement("section");
      advanced.className = "identity-advanced";
      const summary2 = line("h3", "", "高级管理");
      advanced.append(summary2);
      if (association) {
        const linking = line("section", "identity-linking");
        linking.append(line("h4", "", "关联其他画像"));
        const target = document.createElement("select");
        target.className = "engine-input";
        coreSetUIAttribute(target, "aria-label", corePhrase("同一个人的另一份画像"));
        const current2 = document.createElement("option");
        current2.value = "";
        coreSetUIText(current2, "textContent", corePhrase("仅确认当前账号"));
        target.append(current2);
        for (const row2 of rows.filter((r) => r.who !== one.who)) {
          const option = document.createElement("option");
          option.value = row2.who;
          option.textContent = (row2.display_name || row2.who) + " · " + portraitOrigin(row2.display_account || row2.plat || row2.who);
          target.append(option);
        }
        const associate = line("button", "identity-button", "确认是同一个人");
        associate.addEventListener("click", () => {
          const chosen = target.value ? rows.find((row2) => row2.who === target.value) : void 0;
          const participants = chosen ? [one, chosen] : [one];
          if (participants.some((row2) => !row2.association || row2.association.revision !== association.revision)) {
            coreSetUIText(status, "textContent", corePhrase("关联信息已变化，请刷新后重试。"));
            return;
          }
          const profiles = [...new Set(participants.flatMap((r) => [...r.association.profile_keys]))].sort();
          const accounts = [...new Set(participants.flatMap((r) => [...r.association.accounts]))].sort();
          confirmIdentity(
            "确认是同一个人",
            profiles.join(" 与 "),
            "将关联画像：" + profiles.join("、") + "。账号：" + (accounts.join("、") || "暂无") + "。旧内容保留、关联读取；不开放其他群聊历史，不把私聊带进群里。本机控制台仅代表入口，请核对实际使用者。",
            "确认关联",
            () => update2({
              action: "associate",
              profile_keys: participants.map((r) => r.who).sort(),
              expected_profiles: profiles,
              expected_accounts: accounts,
              revision: association.revision
            })
          );
        });
        const controls = line("div", "identity-controls");
        controls.append(target, associate);
        linking.append(controls);
        zone.append(linking);
        if (identity?.profile_keys && identity.profile_keys.length > 1) {
          const separate2 = line("button", "identity-button subtle", "分开这份画像");
          separate2.addEventListener("click", () => {
            const accounts = [...association.separate_accounts ?? []];
            confirmIdentity(
              "分开这份画像",
              one.who,
              "保留这份画像的原内容，并移到独立身份。随它分开的账号：" + (accounts.join("、") || "无") + "。其余关联保留。",
              "确认分开",
              () => update2({
                action: "separate",
                profile_key: one.who,
                expected_accounts: accounts,
                revision: association.revision
              })
            );
          });
          separate2.classList.add("identity-separate");
          advanced.append(separate2);
        }
      } else {
      }
      if (!identity) {
        advanced.append(line("p", "why", "尚未设置账号关联。"));
        const create = line("button", "identity-button", "建立身份");
        create.addEventListener("click", () => {
          confirmIdentity(
            "建立独立身份",
            one.who,
            "只为这份画像建立身份记录。不会自动合并其他画像，也不会迁移旧消息。",
            "确认建立",
            () => update2({ action: "create", profile_key: one.who })
          );
        });
        advanced.append(create);
      } else {
        const renameBlock = line("section", "identity-editor");
        renameBlock.append(line("h4", "", "修改称呼"));
        const name = document.createElement("input");
        name.type = "text";
        name.maxLength = 80;
        name.value = (identity.profile_keys ?? []).includes(identity.label) ? "" : identity.label;
        name.placeholder = "人物称呼";
        name.setAttribute("aria-label", "人物称呼");
        const rename = line("button", "identity-button", "保存称呼");
        rename.addEventListener("click", () => {
          const label = name.value.trim();
          if (!label) {
            status.textContent = "请填写人物称呼。";
            return;
          }
          confirmIdentity(
            "确认人物称呼",
            label,
            "已绑定的私人入口会使用这个称呼。",
            "保存称呼",
            () => update2({
              action: "rename",
              person_id: identity.id,
              revision: association.revision,
              label
            })
          );
        });
        const naming = line("div", "identity-add");
        naming.append(name, rename);
        renameBlock.append(naming);
        advanced.append(renameBlock);
        const linked = line("section", "identity-linked");
        linked.append(line("h4", "", "已关联账号"));
        for (const account2 of identity.accounts) {
          const row2 = line("div", "identity-account");
          row2.append(line("span", "", portraitOrigin(account2)));
          const remove = line("button", "identity-button subtle", "解绑");
          remove.disabled = !association?.revision;
          remove.addEventListener("click", () => {
            if (!association?.revision) {
              coreSetUIText(status, "textContent", corePhrase("请刷新后再解绑。"));
              return;
            }
            const expected = {
              action: "unbind",
              account: account2,
              expected_person_id: identity.id,
              revision: association.revision
            };
            confirmIdentity(
              "解绑账号",
              account2,
              "解除这个账号与当前身份的关联。已有画像和历史消息不会被改写。",
              "确认解绑",
              () => update2(expected)
            );
          });
          row2.append(remove);
          linked.append(row2);
        }
        zone.insertBefore(linked, zone.querySelector(".identity-linking"));
        const accountBlock = line("section", "identity-editor");
        accountBlock.append(line("h4", "", "手动添加账号"));
        const add = line("div", "identity-add");
        const account = document.createElement("input");
        account.type = "text";
        account.autocomplete = "off";
        coreSetUIText(account, "placeholder", corePhrase("填写账号"));
        coreSetUIAttribute(account, "aria-label", corePhrase("平台账号"));
        const platform = document.createElement("select");
        platform.setAttribute("aria-label", "账号平台");
        platform.append(new Option("QQ", "qq"), new Option("控制台", "console"), new Option("其他平台", "custom"));
        platform.addEventListener("change", () => {
          account.hidden = platform.value === "console";
          account.placeholder = platform.value === "custom" ? "平台:账号" : "填写 QQ 号";
        });
        account.placeholder = "填写 QQ 号";
        const bind2 = line("button", "identity-button", "绑定账号");
        bind2.addEventListener("click", () => {
          const raw = account.value.trim();
          const key2 = platform.value === "console" ? "console:local-owner" : platform.value === "qq" ? raw ? "qq:" + raw : "" : raw;
          if (!key2) {
            coreSetUIText(status, "textContent", corePhrase("请填写平台账号。"));
            return;
          }
          status.textContent = "";
          confirmIdentity(
            "确认账号归属",
            key2,
            "请核对这个账号与「" + one.who + "」属于同一人。确认后，下一次私人请求才会使用这条关联。",
            "确认绑定",
            () => update2({ action: "bind", person_id: identity.id, account: key2 })
          );
        });
        add.append(platform, account, bind2);
        accountBlock.append(add);
        advanced.append(accountBlock);
      }
      const separate = advanced.querySelector(".identity-separate");
      if (separate) {
        const row2 = line("div", "identity-separate-row");
        row2.append(line("span", "", "解除当前画像的关联，保留原始记录。"), separate);
        advanced.append(row2);
      }
      const fields = line("div", "identity-advanced-body");
      while (advanced.children.length > 1) fields.append(advanced.children[1]);
      advanced.append(fields);
      zone.append(advanced);
      zone.append(status);
      zone.append(line(
        "p",
        "why",
        "关联后从下次对话生效。原始记录保留，不会跨群共享聊天内容。"
      ));
      return zone;
    };
    const pick2 = (index) => {
      at = index;
      selectedWho = rows[index].who;
      for (const [i, el6] of [...roster.querySelectorAll(".one")].entries()) {
        el6.classList.toggle("on", i === index);
        el6.setAttribute("aria-pressed", String(i === index));
      }
      showFace(index);
      face.scrollTop = 0;
    };
    let rowIndex = 0;
    for (const group of groups) {
      const section = line("section", "roster-group");
      const heading2 = line("h2", "roster-group-heading");
      heading2.append(line("span", "", group.label), line("span", "roster-group-count", String(group.people.length)));
      section.setAttribute("aria-label", group.label);
      const items = line("div", "roster-group-items");
      section.append(heading2, items);
      roster.append(section);
      group.people.forEach((one) => {
        const index = rowIndex++;
        const item = line("button", "one");
        item.type = "button";
        const label = line("span", "n", one.display_name || one.who);
        label.title = one.who;
        item.append(label);
        if (one.blocked) item.append(line("span", "gate", ""));
        if (String(one.plat ?? "") !== "") {
          item.append(line("span", "p", portraitOrigin(one.display_account || String(one.plat))));
        }
        item.addEventListener("click", () => {
          pick2(index);
        });
        if (portraitLinkedToConsole(one)) item.append(line("span", "roster-linked", "已关联控制台"));
        items.append(item);
      });
    }
    pick2(at);
    tail.append(
      line("span", "", "共 " + String(rows.length) + " 个人")
    );
  };
  let selectedWho = "";
  let requestId = 0;
  const readHint = pageLoad(root, "画像", () => {
    void refresh2(find2.input.value.trim());
  });
  const refresh2 = async (q) => {
    const mine = ++requestId;
    const ask2 = q === "" ? "/people" : "/people?q=" + encodeURIComponent(q);
    readHint.start();
    try {
      const answer = await getStatusJson(ask2);
      if (mine !== requestId || root.hidden) return;
      draw(answer);
      readHint.ready();
    } catch (why) {
      if (mine !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) {
        void refresh2(q);
        return;
      }
      console.error("画像读取失败：", why);
      readHint.fail();
    }
  };
  find2.onAsk((q) => {
    void refresh2(q);
  });
  return {
    root,
    show() {
      root.hidden = false;
      void refresh2(find2.input.value.trim());
    },
    stop() {
      requestId += 1;
      root.hidden = true;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/record-labels.ts
function experienceSourceName(path) {
  const name = path.split(/[\\/]/).filter(Boolean).at(-1)?.replace(/\.[^.]+$/, "") || "";
  if (/^对话(?:记录)?$/.test(name)) return "对话";
  if (/^自我记录归位-\d{8}$/.test(name)) return "自我记录";
  if (/^自述工具用法归位-\d{8}$/.test(name)) return "工具说明";
  const labels = { "整理那一趟": "记忆整理", "设定修订": "设定修改", "交接笔记": "会话摘要" };
  return labels[name] || name;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/experience-page.ts
var READ_FAILED4 = "经历加载失败，请重试。";
var NOT_WIRED4 = "当前服务暂不支持查看经历。";
var NOTHING_YET2 = "暂无经历。保存的对话和记录会显示在这里。";
var SHOWN = 4;
var theWho = (one, other) => {
  const mine = speaker();
  if (one.role === "user") return (other === "" ? "对方" : other) + "说";
  if (one.role === "经历") return mine + "手写";
  if (one.role === "assistant") return mine + "说";
  return "记下";
};
var theWords = (one, other, head = false) => theWho(one, other) + "：" + (head ? theFirstLine(one.text) : one.text);
var channels = [];
var channelEpoch = 0;
var learnChannels = async () => {
  const epoch = ++channelEpoch;
  channels = [];
  matchers = null;
  try {
    const got = await pageFetch("/plugins");
    if (!got.ok) return;
    const answer = await got.json();
    const all = [];
    for (const one of answer.plugins ?? []) {
      for (const ch of one.channels ?? []) {
        if (ch && Array.isArray(ch.markers)) all.push(ch);
      }
    }
    if (epoch !== channelEpoch) return;
    channels = all;
    matchers = null;
  } catch {
  }
};
var matchers = null;
var theMatchers = () => {
  if (matchers !== null) return matchers;
  const out = [];
  for (const channel of channels) {
    for (const marker of channel.markers ?? []) {
      const src = String(marker.pattern ?? "");
      if (src === "") continue;
      try {
        out.push({ channel, marker, re: new RegExp(src) });
      } catch {
        continue;
      }
    }
  }
  matchers = out;
  return out;
};
var theSpoken = (text2) => {
  const whole = String(text2 ?? "");
  for (const one of theMatchers()) {
    const got = one.re.exec(whole);
    if (got === null || got.index !== 0) continue;
    const groups = got.groups ?? {};
    const scope2 = String(one.marker.scope ?? "");
    const rest = whole.slice(got[0].length).replace(/^\s+/, "");
    return {
      label: String(one.channel.label ?? ""),
      scope: scope2,
      room: String(groups.room ?? ""),
      who: String(groups.who ?? ""),
      rest
    };
  }
  return null;
};
var thePrefixShape = (one, opened, make, flip) => {
  const hot = one.room !== "";
  const box = make("span", "ch", "");
  box.append(document.createTextNode("["));
  box.append(make("span", "ch-who", one.label));
  box.append(make("span", "ch-dot", "·"));
  const word = make(
    "span",
    opened ? "ch-scope is-open" : "ch-scope",
    opened ? one.scope + "·" + one.room : one.scope
  );
  if (hot) box.title = `${one.label} · ${one.scope} · ${one.room}`;
  box.append(word);
  box.append(document.createTextNode("]"));
  return box;
};
var theFrom = (one) => {
  const parts = String(one || "").split("/").filter((x) => x !== "");
  if (parts.length >= 3) return parts[parts.length - 2];
  return (parts[parts.length - 1] ?? "").replace(/\.[^.]+$/, "");
};
var theOwnName = (one) => String(one || "").split("/").filter((x) => x !== "").pop()?.replace(/\.[^.]+$/, "") ?? "";
var theCount = (one) => one.kind === "记录" ? String(one.count) + " 条" : "文档";
var theFirstLine = (one) => String(one ?? "").split("\n")[0];
var theFold = (one) => String(one ?? "").includes("\n");
var hit = (one, q) => {
  const needle = q.toLowerCase();
  const fields = [
    one.beat.text,
    one.beat.role,
    theWords(one.beat, one.other),
    one.beat.at,
    one.from,
    one.file.name,
    one.file.kind,
    theCount(one.file),
    one.day.day,
    one.day.label,
    one.day.sub
  ];
  return fields.some((x) => String(x).toLowerCase().includes(needle));
};
function experiencePage(shell2) {
  const root = document.createElement("section");
  root.className = "page-experience page-world world-studio";
  const line = (tag, cls, text2) => {
    const one = document.createElement(tag);
    if (cls !== "") one.className = cls;
    if (text2 !== void 0) one.textContent = text2;
    return one;
  };
  const head = document.createElement("div");
  head.className = "head";
  const title = document.createElement("h1");
  coreSetUIText(title, "textContent", corePhrase("经历 · EXPERIENCE"));
  const find2 = searchBox("关键词或日期…");
  head.append(title, find2.element);
  root.append(head);
  const axis = line("div", "axis");
  const scroll = line("div", "page-scroll experience-scroll");
  scroll.append(axis);
  root.append(scroll);
  const sources = line("div", "experience-source-list");
  const sourceDialog = document.createElement("dialog");
  sourceDialog.className = "avatar-crop-dialog experience-source-dialog";
  sourceDialog.setAttribute("aria-label", "记录来源");
  const closeSources = line("button", "", "关闭");
  closeSources.type = "button";
  closeSources.onclick = () => sourceDialog.close();
  const sourceActions = line("div", "avatar-crop-actions");
  sourceActions.append(closeSources);
  sourceDialog.append(line("h2", "", "记录来源"), sources, sourceActions);
  const sourceButton = line("button", "page-footer-link", "记录来源");
  sourceButton.type = "button";
  sourceButton.onclick = () => {
    sourceDialog.showModal();
    closeSources.focus();
  };
  sourceDialog.addEventListener("close", () => {
    if (!root.hidden) sourceButton.focus();
  });
  sourceDialog.addEventListener("click", (event) => event.stopPropagation());
  sourceDialog.addEventListener("keydown", (event) => event.stopPropagation());
  const tail = document.createElement("footer");
  tail.className = "tail page-footer";
  root.append(tail, sourceDialog);
  shell2.content.replaceChildren(root);
  const dayLimits = /* @__PURE__ */ new Map();
  const openedHeads = /* @__PURE__ */ new Set();
  let mine = null;
  let asked = "";
  const stamp = (key2, label, sub, rows) => {
    const box = line("div", "stamp");
    box.dataset.dayKey = key2;
    const day = line("div", "day", label === "" ? "日期未记录" : label);
    if (sub !== "") day.append(line("span", "", sub));
    box.append(day, line("div", "dot"));
    const limit = dayLimits.get(key2) ?? SHOWN;
    const body = line("div", "day-beats");
    body.id = "experience-day-" + encodeURIComponent(key2);
    body.setAttribute("role", "region");
    coreSetUIAttribute(body, "aria-label", [label || "日期未记录", corePhrase("的经历")]);
    const more = () => {
      const button4 = line("button", "fold-toggle", "显示更多");
      button4.setAttribute("type", "button");
      button4.addEventListener("click", () => {
        dayLimits.set(key2, limit + 20);
        draw(mine, asked);
      });
      return button4;
    };
    const meta = line("div", "stamp-meta");
    meta.append(line("span", "n", String(rows.length) + " 条"));
    box.append(meta, body);
    const showed = rows.slice(0, limit);
    for (const one of showed) {
      const whole = one.beat.text;
      const row2 = line("div", "beat");
      row2.append(line("span", "at", one.beat.at));
      const marked = one.beat.role === "user" ? theSpoken(whole) : null;
      let routed = null;
      for (const channel of channels) {
        for (const route2 of channel.routes ?? []) {
          try {
            const match = new RegExp(route2.pattern || "^$").exec(one.beat.scope || "");
            if (match && match.index === 0 && match.groups?.room) {
              routed = {
                label: channel.label || "",
                scope: route2.scope || "",
                room: match.groups.room,
                who: "",
                rest: whole
              };
            }
          } catch {
          }
        }
      }
      const spoken = marked ? { ...marked, room: marked.room || routed?.room || "" } : routed;
      const folded = theFold(whole);
      const txt = line("span", "txt", "");
      if (one.file.activity === "reflection") {
        txt.append(line("span", "ch", "[整理]"));
        txt.append(coreUITextNode([[speaker(), corePhrase("记下：")], folded ? theFirstLine(whole) : whole]));
      } else if (spoken !== null) {
        const headKey = String(one.id) + "|" + spoken.label + spoken.scope + spoken.room;
        const opened = openedHeads.has(headKey);
        txt.append(thePrefixShape(
          spoken,
          opened,
          line,
          () => {
            if (openedHeads.has(headKey)) openedHeads.delete(headKey);
            else openedHeads.add(headKey);
            draw(mine, asked);
          }
        ));
        const who = one.beat.role === "assistant" ? speaker() : one.beat.speaker_name || (spoken.who !== "" ? spoken.who : one.other || "用户");
        txt.append(coreUITextNode([[who, corePhrase("说：")], folded ? theFirstLine(spoken.rest) : spoken.rest]));
      } else {
        const isTerminal = ["console:local-owner", "private:console:local-owner"].includes(one.beat.scope || "");
        const isSpeech = ["user", "assistant"].includes(one.beat.role);
        if (isTerminal || isSpeech) {
          const origin = line("span", "ch", isTerminal ? "[终端]" : "[来源未记录]");
          if (!isTerminal) coreSetUIText(origin, "title", one.beat.scope ? corePhrase("记录保存了通道标识，但对应插件尚未提供显示规则。") : corePhrase("这条旧记录未保存通道信息，无法可靠确认来自终端还是外部平台。"));
          txt.append(origin);
        }
        txt.append(coreUITextNode(one.beat.role === "user" ? [[isTerminal ? one.other || "用户" : corePhrase("用户"), corePhrase("说：")], folded ? theFirstLine(whole) : whole] : theWords(one.beat, one.other, folded)));
      }
      txt.append(line("span", "src", one.from));
      row2.append(txt);
      if (theFold(whole)) {
        const view2 = line("button", "op", "查看全文");
        view2.setAttribute("type", "button");
        view2.addEventListener("click", () => showTextDetail("经历记录", whole));
        txt.append(view2);
      }
      body.append(row2);
    }
    const left = rows.length - showed.length;
    if (left > 0) {
      const status = line("div", "fold", "还有 " + left + " 条 · ");
      status.append(more());
      box.append(status);
    }
    return box;
  };
  const draw = (answer, q) => {
    axis.replaceChildren();
    sources.replaceChildren();
    tail.replaceChildren(line("span", "page-footer-label", "对话与经历"));
    sourceButton.disabled = true;
    mine = answer;
    asked = q;
    if (answer === null || answer.wired === false) {
      const prose = line("div", "prose");
      prose.append(line("p", "fade", answer === null ? READ_FAILED4 : NOT_WIRED4));
      axis.append(prose);
      return;
    }
    const rows = (answer.experience ?? []).filter((one) => !one.name.includes(".bak-"));
    const howMany = rows.filter((one) => one.kind === "记录").reduce((sum, one) => sum + one.count, 0);
    let flat = [];
    let id = 0;
    for (const one of rows) {
      for (const day of one.days ?? []) {
        for (const beat of day.beats) {
          flat.push({
            beat,
            from: theFrom(one.name),
            day,
            file: one,
            other: String(one.other ?? ""),
            id: JSON.stringify([one.name, beat.role, beat.sort_ts, beat.scope, beat.text])
          });
          id += 1;
        }
      }
    }
    if (q !== "") flat = flat.filter((one) => hit(one, q));
    const days = /* @__PURE__ */ new Map();
    for (const one of flat) {
      const key2 = one.day.day;
      const box = days.get(key2) ?? [];
      box.push(one);
      days.set(key2, box);
    }
    const keys = [...days.keys()].filter((key2) => key2 !== "").sort((a, b) => b.localeCompare(a));
    if (days.has("")) keys.push("");
    for (const entries of days.values()) entries.sort((a, b) => (b.beat.sort_ts ?? 0) - (a.beat.sort_ts ?? 0));
    if (flat.length === 0) {
      const prose = line("div", "prose");
      prose.append(line("p", "fade", q === "" ? NOTHING_YET2 : "「" + q + "」没有匹配记录。"));
      axis.append(prose);
    } else {
      for (const key2 of keys) {
        const box = days.get(key2);
        const label = key2 === "" ? "日期未记录" : box[0].day.label || key2;
        axis.append(stamp(key2 || "undated", label, key2 === "" ? "" : box[0].day.sub, box));
      }
    }
    if (rows.length > 0) {
      const kept = /* @__PURE__ */ new Set();
      for (const one of flat) kept.add(one.file.name);
      if (q !== "") {
        const needle = q.toLowerCase();
        for (const one of rows) {
          const mine2 = [one.name, one.kind, theCount(one), theOwnName(one.name)];
          if (mine2.some((x) => String(x).toLowerCase().includes(needle))) kept.add(one.name);
        }
      }
      const sourceList = sources;
      for (const one of rows) {
        if (q !== "" && !kept.has(one.name)) continue;
        const bit = line("span", "one", experienceSourceName(one.name));
        bit.title = one.name;
        bit.append(line("span", "thin", one.kind === "记录" ? theCount(one) : "文档"));
        sourceList.append(bit);
      }
    }
    sourceButton.disabled = sources.childElementCount === 0;
    sourceButton.textContent = "记录来源 · " + sources.childElementCount;
    tail.append(
      sourceButton,
      line("span", "page-footer-summary", "共 " + String(rows.length) + " 份 · " + String(howMany) + " 条")
    );
  };
  let requestId = 0;
  let previousSnapshot = "";
  const readHint = pageLoad(root, "经历", () => {
    void refresh2();
  });
  const refresh2 = async () => {
    const mine2 = ++requestId;
    readHint.start();
    try {
      const answer = await getStatusJson("/experience");
      if (mine2 !== requestId || root.hidden) return;
      const snapshot2 = JSON.stringify(answer);
      if (snapshot2 !== previousSnapshot) {
        previousSnapshot = snapshot2;
        draw(answer, find2.input.value.trim());
      }
      readHint.ready();
    } catch (why) {
      if (mine2 !== requestId || root.hidden) return;
      if (why instanceof StaleStatusError) {
        void refresh2();
        return;
      }
      console.error("经历读取失败：", why);
      readHint.fail();
    }
  };
  find2.onAsk((q) => {
    if (mine !== null) draw(mine, q);
  });
  const liveRecords = recordRefresh(root, refresh2);
  return {
    root,
    show() {
      root.hidden = false;
      liveRecords.start();
      void refresh2();
      const epoch = requestId;
      void Promise.all([learnChannels(), loadSpeaker()]).then(() => {
        if (epoch === requestId && !root.hidden && mine !== null) {
          draw(mine, find2.input.value.trim());
        }
      });
    },
    stop() {
      liveRecords.stop();
      if (sourceDialog.open) sourceDialog.close();
      requestId += 1;
      root.hidden = true;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/plugin-storage-settings.ts
var element2 = (tag, cls, label) => {
  const el6 = document.createElement(tag);
  el6.className = cls;
  if (label) uiLabel(el6, label);
  return el6;
};
function pluginStorageSettings() {
  const root = element2("section", "settings-card plugin-storage");
  root.append(element2("h3", "", "插件页面状态"), element2("p", "pcs-scope", "当前 Bot · 当前浏览器标签页 · 页签和分页等显示状态"));
  const panel2 = element2("div", "pcs-panel");
  const toolbar = element2("div", "pcs-toolbar");
  const search = document.createElement("input");
  search.type = "search";
  search.maxLength = 128;
  coreSetUIAttribute(search, "aria-label", corePhrase("搜索插件页面状态"));
  uiLabel(search, "名称、关键词或缩写", "placeholder");
  const filters = element2("div", "pcs-filters");
  let filter = "all";
  let plugins = [];
  let ready2 = false;
  let composing = false;
  let page2 = 0;
  const rows = element2("div", "pcs-rows");
  const footer = element2("div", "pcs-footer");
  const count4 = element2("span", "");
  const pager = element2("div", "pcs-pager");
  const prev = element2("button", "", "上一页");
  const next = element2("button", "", "下一页");
  const position = element2("span", "");
  pager.append(prev, position, next);
  footer.append(count4, pager);
  const message = element2("p", "pcs-message");
  message.setAttribute("role", "status");
  const draw = () => {
    rows.replaceChildren();
    let cached;
    try {
      cached = pluginDisplayEntries();
    } catch {
      ready2 = false;
      uiLabel(message, "无法访问浏览器缓存，未清理任何数据。");
      cached = [];
    }
    const all = new Map(plugins.map((p) => [p.name, p]));
    for (const entry of cached) if (!all.has(entry.name)) all.set(entry.name, { name: entry.name, unknown: true });
    const groupOf2 = (p) => p.unknown ? "来源待确认" : pluginOrigin(p, updateFor(p.name)?.repository ? "https://github.com/" + updateFor(p.name).repository : "");
    const selected = [...all.values()].filter((p) => {
      const has = cached.some((c) => c.name === p.name);
      return (filter === "all" || (filter === "cached" ? has : !has)) && fuzzyPluginMatch(p.displayName || p.name, p.name, search.value);
    }).sort((a, b) => pluginOriginOrder(groupOf2(a)) - pluginOriginOrder(groupOf2(b)) || (a.displayName || a.name).localeCompare(b.displayName || b.name));
    page2 = Math.min(page2, Math.max(0, Math.ceil(selected.length / 8) - 1));
    let lastGroup = "";
    for (const plugin of selected.slice(page2 * 8, (page2 + 1) * 8)) {
      const group = groupOf2(plugin);
      if (group !== lastGroup) {
        const heading2 = element2("div", "pcs-group");
        heading2.dataset.origin = group;
        const members = selected.filter((p) => groupOf2(p) === group);
        heading2.append(element2("strong", "", group), element2("span", "", `${members.length} 个插件`));
        rows.append(heading2);
        lastGroup = group;
      }
      const cache = cached.find((c) => c.name === plugin.name);
      const row2 = element2("div", "pcs-row");
      row2.dataset.plugin = plugin.name;
      const identity = element2("div", "pcs-identity");
      const icon2 = element2("span", "pcs-icon");
      icon2.textContent = (plugin.displayName || plugin.name).slice(0, 1).toUpperCase();
      const name = element2("span", "pcs-name");
      name.textContent = plugin.displayName || plugin.name;
      name.title = plugin.name;
      identity.append(icon2, name);
      const state3 = element2("span", "pcs-type", cache ? `${cache.records} 条状态` : "无记录");
      const size = element2("span", "pcs-size");
      size.textContent = cache ? `${cache.bytes} B` : "—";
      if (cache) size.title = `记录序列化大小（UTF-8）：键名与作用域 ${cache.bytes - cache.valueBytes} B，状态值 ${cache.valueBytes} B；不是磁盘或内存占用。`;
      const clean = element2("button", "pcs-clean", "清理");
      clean.type = "button";
      clean.disabled = !ready2 || !cache || pluginDisplayProtected(plugin.name);
      coreSetUIAttribute(clean, "aria-label", [corePhrase("清理"), " ", plugin.displayName || plugin.name]);
      if (pluginDisplayProtected(plugin.name)) uiLabel(clean, "有未保存内容，暂不清理", "title");
      clean.onclick = () => {
        uiLabel(message, clearPluginDisplay(plugin.name) ? "已清理显示缓存；配置和未保存内容保持不变。" : "未清理：缓存不可用或有内容需要保护。");
        draw();
      };
      row2.append(identity, state3, size, clean);
      rows.append(row2);
    }
    if (!selected.length) rows.append(element2("p", "pcs-empty", ready2 ? "没有匹配的插件页面状态。" : "正在读取…"));
    coreSetUIText(count4, "textContent", [selected.length, corePhrase(" 项"), " · ", selected.reduce((sum, p) => sum + (cached.find((c) => c.name === p.name)?.bytes ?? 0), 0), " B"]);
    position.textContent = `${page2 + 1} / ${Math.max(1, Math.ceil(selected.length / 8))}`;
    prev.disabled = page2 === 0;
    next.disabled = (page2 + 1) * 8 >= selected.length;
  };
  for (const [value, label] of [["all", "全部"], ["cached", "有状态"], ["empty", "无状态"]]) {
    const button4 = element2("button", "", label);
    button4.type = "button";
    button4.setAttribute("aria-pressed", String(value === filter));
    button4.onclick = () => {
      filter = value;
      page2 = 0;
      for (const child of filters.children) child.setAttribute("aria-pressed", String(child === button4));
      draw();
    };
    filters.append(button4);
  }
  search.oncompositionstart = () => {
    composing = true;
  };
  search.oncompositionend = () => {
    composing = false;
    page2 = 0;
    draw();
  };
  search.oninput = () => {
    if (!composing) {
      page2 = 0;
      draw();
    }
  };
  search.onkeydown = (e) => {
    if (e.key === "Escape" && !e.isComposing) {
      search.value = "";
      page2 = 0;
      draw();
    }
  };
  prev.onclick = () => {
    page2--;
    draw();
  };
  next.onclick = () => {
    page2++;
    draw();
  };
  toolbar.append(search, filters);
  panel2.append(toolbar, rows, footer);
  root.append(panel2);
  root.append(element2("p", "pcs-hint", "仅清理本标签页的显示状态，不影响配置、聊天、记忆或未保存内容。"));
  root.append(element2("p", "pcs-hint", "大小按记录的键名与值以 UTF-8 计算，不是插件的磁盘缓存或浏览器实际内存占用。"), message);
  void (async () => {
    ready2 = await readyPluginDisplay();
    try {
      const answer = await getStatusJson("/plugins", 5e3);
      plugins = (answer.plugins ?? []).filter((p) => typeof p.name === "string" && p.installed !== false);
      if (plugins.some((p) => p.repository === void 0)) await checkPluginUpdates();
    } catch {
      uiLabel(message, "插件列表读取失败；仍可查看本页已知的显示缓存。");
    }
    if (!ready2) uiLabel(message, "档案身份尚未确认，暂不允许清理。");
    draw();
  })();
  draw();
  return root;
}
function snowlumaSessionSettings() {
  const root = element2("section", "settings-card plugin-storage");
  root.append(
    element2("h3", "", "SnowLuma 管理登录会话"),
    element2("p", "pcs-scope", "当前 Bot · Core 后台")
  );
  const description = element2(
    "p",
    "pcs-hint",
    "清除后，Core 下次同步协议配置时会用已保存的管理密码重新登录。"
  );
  const status = element2("p", "pcs-message");
  status.setAttribute("role", "status");
  const clear = element2("button", "pcs-clean", "清除登录会话");
  clear.type = "button";
  clear.disabled = true;
  let active2 = "";
  root.append(description, clear, status);
  const refresh2 = async () => {
    clear.disabled = true;
    try {
      const response = await fetch("/infinity/qq/management-session", { cache: "no-store" });
      if (!response.ok) throw new Error();
      const state3 = await response.json();
      active2 = typeof state3.active === "string" ? state3.active : "";
      clear.disabled = !(active2 && state3.available === true && state3.hasSession === true);
      uiLabel(status, state3.available !== true ? "当前运行方式不支持会话清理。" : state3.hasSession === true ? "已保存登录会话" : "没有已保存的登录会话");
    } catch {
      uiLabel(status, "暂时无法读取 SnowLuma 登录会话状态。");
    }
  };
  clear.onclick = async () => {
    if (clear.disabled) return;
    clear.disabled = true;
    let message = "";
    try {
      if (!await confirmAction(
        "清除 SnowLuma 登录会话",
        `清除 ${active2} 的 Core 管理登录会话。已保存的密码、QQ 接入和会话路由会保留。`,
        "清除会话"
      ) || !root.isConnected) return;
      const response = await fetch("/infinity/qq/clear-management-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: active2 })
      });
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error(result.said || "清除会话失败。");
      message = result.cleared ? "已清除登录会话。" : "没有需要清除的登录会话。";
    } catch (error) {
      message = error instanceof Error && error.message ? error.message : "清除会话失败。";
    } finally {
      await refresh2();
      if (message) uiLabel(status, message);
    }
  };
  void refresh2();
  return root;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/model-proxy-settings.ts
var PROVIDERS = [["openai", "OpenAI / ChatGPT"], ["google", "Google Gemini"], ["anthropic", "Anthropic Claude"], ["xai", "xAI Grok"]];
var el5 = (tag, cls = "", text2 = "") => {
  const n = document.createElement(tag);
  n.className = cls;
  n.textContent = text2;
  return n;
};
var modes = [["direct", "直接连接"], ["fallback", "代理优先，失败后直连"], ["required", "仅使用代理"]];
var mode = (on, fallback) => !on ? "direct" : fallback ? "fallback" : "required";
var select = (label, options) => {
  const box = el5("label", "public-network-field", label), input2 = el5("select");
  input2.setAttribute("aria-label", label);
  for (const [value, text2] of options) input2.add(new Option(text2, value));
  box.append(input2);
  return { box, input: input2 };
};
function modelProxySettings() {
  const root = el5("section", "settings-card model-proxy-settings");
  configPresentation(root, "loading");
  const header = el5("div", "model-proxy-header");
  header.append(el5("h3", "", "网络与下载"));
  const note = el5("p", "set-note", "下载与模型 API 分别选择连接方式，共用一个代理地址。");
  const addressLabel = el5("label", "model-proxy-address", "代理地址");
  const addressRow = el5("div", "model-proxy-address-row");
  const address = el5("input", "engine-input engine-input-wide");
  address.type = "url";
  address.autocomplete = "off";
  address.spellcheck = false;
  address.placeholder = "http://127.0.0.1:7893";
  address.setAttribute("aria-label", "共用代理地址");
  const system = el5("button", "btn ghost", "填入系统代理");
  system.type = "button";
  addressRow.append(address, system);
  addressLabel.append(addressRow);
  const addressHint = el5("p", "set-note", "支持 HTTP(S) 代理；修改后保存生效。");
  const policies = el5("div", "public-network-policies");
  const download = el5("section", "public-network-policy");
  download.append(el5("h4", "", "下载与插件市场"));
  const source = select("来源顺序", [["mirror", "镜像优先"], ["official", "官方优先"]]);
  const connection = select("官方连接方式", modes);
  const downloadFlow = el5("p", "public-network-flow");
  download.append(source.box, connection.box, downloadFlow);
  const overrides = el5("section", "model-proxy-details");
  const overrideSummary = el5("h3", "", "插件市场单独设置");
  const separateLabel = el5("label", "model-proxy-provider");
  const separate = el5("input");
  separate.type = "checkbox";
  separate.setAttribute("aria-label", "插件市场使用独立设置");
  separateLabel.append(separate, "插件市场使用独立设置");
  const marketFields = el5("div", "model-network-fields");
  const marketSource = select("插件市场来源顺序", [["mirror", "镜像优先"], ["official", "官方优先"]]);
  const marketConnection = select("插件市场官方连接方式", modes);
  marketFields.append(marketSource.box, marketConnection.box);
  const marketFlow = el5("p", "public-network-flow");
  overrides.append(overrideSummary, separateLabel, marketFields, marketFlow);
  download.append(overrides);
  const downloadHint = el5("p", "public-network-caption", "用于 Core 更新、插件搜索与安装、模型和运行环境下载。镜像始终直连；校验摘要只向官方核验。");
  download.append(downloadHint);
  const model = el5("section", "public-network-policy");
  model.append(el5("h4", "", "模型 API"));
  const modelConnection = select("模型连接方式", modes);
  modelConnection.box.classList.add("model-network-wide");
  const modelFlow = el5("p", "public-network-flow");
  model.append(modelConnection.box, modelFlow);
  const providers = el5("section", "model-proxy-details");
  const providerSummary = el5("h3");
  const targets = el5("div", "model-proxy-targets");
  const boxes = PROVIDERS.map(([id, title]) => {
    const label = el5("label", "model-proxy-provider"), input2 = el5("input");
    input2.type = "checkbox";
    input2.setAttribute("aria-label", title);
    label.append(input2, title);
    targets.append(label);
    return { id, input: input2 };
  });
  providers.append(providerSummary, targets);
  model.append(providers, el5("p", "public-network-caption", "仅连接尚未建立、模型请求尚未发送时允许直连回退；已发送请求和生成中断不重发。自定义 API 地址保持直连。"));
  policies.append(download, model);
  const actions = el5("div", "model-proxy-actions");
  const save = el5("button", "btn", "保存"), reload = el5("button", "btn ghost", "重新读取");
  save.type = reload.type = "button";
  save.setAttribute("aria-label", "保存网络设置");
  actions.append(save, reload);
  const status = el5("p", "model-proxy-status");
  status.setAttribute("role", "status");
  root.append(header, note, addressLabel, addressHint, policies, actions, status);
  let revision = null, systemProxy = "", busy = false, saving = false, baseline = "", guarding = false;
  const fields = [address, source.input, connection.input, separate, marketSource.input, marketConnection.input, modelConnection.input, ...boxes.map((b) => b.input)];
  const draft = () => JSON.stringify(fields.map((f) => f instanceof HTMLInputElement && f.type === "checkbox" ? f.checked : f.value));
  const dirty = () => !!baseline && draft() !== baseline;
  const beforeUnload = (event) => {
    if (saving || dirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  const guard = () => {
    const needed = saving || dirty();
    if (needed === guarding) return;
    guarding = needed;
    if (needed) window.addEventListener("beforeunload", beforeUnload);
    else window.removeEventListener("beforeunload", beforeUnload);
  };
  const path = (src, transport) => {
    const official = transport === "direct" ? "官方直连" : transport === "fallback" ? "官方代理 → 官方直连" : "官方代理";
    return src === "mirror" ? "镜像直连 → " + official : official + " → 镜像直连";
  };
  const paint4 = () => {
    downloadFlow.textContent = path(source.input.value, connection.input.value);
    marketFields.hidden = marketFlow.hidden = !separate.checked;
    overrideSummary.textContent = separate.checked ? "插件市场 · 使用独立设置" : "插件市场 · 跟随下载设置";
    marketFlow.textContent = path(marketSource.input.value, marketConnection.input.value);
    modelFlow.textContent = modelConnection.input.value === "direct" ? "直接连接官方模型接口" : modelConnection.input.value === "fallback" ? "官方代理 → 官方直连（仅发送前连接失败）" : "仅通过代理连接；连接失败时报错";
    const chosen = boxes.filter((b) => b.input.checked);
    providerSummary.textContent = chosen.length === boxes.length ? "适用范围 · 全部官方供应商" : `适用范围 · 已选 ${chosen.length} 家官方供应商`;
    system.hidden = !systemProxy;
  };
  const lock = (value) => {
    busy = value;
    for (const field of fields) field.disabled = value || revision === null;
    save.disabled = value || revision === null;
    reload.disabled = value;
    system.disabled = value || revision === null || !systemProxy;
  };
  for (const field of fields) for (const event of ["input", "change"]) field.addEventListener(event, () => {
    paint4();
    guard();
  });
  separate.addEventListener("change", () => {
    if (separate.checked) {
      marketSource.input.value = source.input.value;
      marketConnection.input.value = connection.input.value;
    }
    paint4();
    guard();
  });
  const apply = (settings) => {
    revision = settings.revision;
    systemProxy = settings.system_proxy || "";
    address.value = settings.proxy_url;
    source.input.value = settings.downloads_source || "mirror";
    connection.input.value = mode(settings.downloads_enabled, settings.downloads_fallback);
    marketSource.input.value = settings.market_source || "mirror";
    marketConnection.input.value = mode(settings.market_enabled, settings.market_fallback);
    separate.checked = source.input.value !== marketSource.input.value || connection.input.value !== marketConnection.input.value;
    modelConnection.input.value = mode(settings.enabled, settings.model_fallback);
    for (const box of boxes) box.input.checked = settings.providers.includes(box.id);
    paint4();
    baseline = draft();
    guard();
    configPresentation(root, "ready");
  };
  const read4 = async () => {
    lock(true);
    status.textContent = "正在读取网络设置…";
    try {
      const r = await pageFetch("/settings/model-proxy");
      const result = await r.json();
      if (!r.ok || !result.ok || typeof result.settings?.revision !== "string") throw Error("无法读取网络设置，未用默认值覆盖。");
      apply(result.settings);
      status.textContent = "已读取当前设置";
    } catch (e) {
      revision = null;
      configPresentation(root, "error");
      status.textContent = e instanceof Error ? e.message : "读取失败，未覆盖。";
    } finally {
      lock(false);
    }
  };
  save.onclick = () => {
    void (async () => {
      if (busy || revision === null) return;
      const marketMode = separate.checked ? marketConnection.input.value : connection.input.value;
      if ([connection.input.value, marketMode, modelConnection.input.value].some((m) => m !== "direct") && !address.value.trim()) {
        status.textContent = "使用代理前，请填写代理地址。";
        return;
      }
      if (modelConnection.input.value !== "direct" && !boxes.some((b) => b.input.checked)) {
        status.textContent = "请选择至少一家适用的模型供应商。";
        return;
      }
      saving = true;
      guard();
      lock(true);
      status.textContent = "正在保存…";
      try {
        const r = await pageFetch("/settings/model-proxy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision, proxy_url: address.value.trim(), enabled: modelConnection.input.value !== "direct", model_fallback: modelConnection.input.value === "fallback", providers: boxes.filter((b) => b.input.checked).map((b) => b.id), downloads_source: source.input.value, downloads_enabled: connection.input.value !== "direct", downloads_fallback: connection.input.value === "fallback", market_source: separate.checked ? marketSource.input.value : source.input.value, market_enabled: marketMode !== "direct", market_fallback: marketMode === "fallback" }) });
        const result = await r.json();
        if (!r.ok || !result.ok || !result.settings) throw Error(result.error || "保存未确认，请重新读取核对；未自动重试。");
        apply(result.settings);
        status.textContent = "已保存，下次请求生效。";
      } catch (e) {
        status.textContent = e instanceof Error ? e.message : "保存未确认，请重新读取核对。";
      } finally {
        saving = false;
        guard();
        lock(false);
      }
    })();
  };
  reload.onclick = () => {
    void (async () => {
      if (busy) return;
      if (dirty() && !await confirmAction("重新读取网络设置", "尚未保存的修改将被替换。", "重新读取")) return;
      void read4();
    })();
  };
  system.onclick = () => {
    if (busy || !systemProxy) return;
    address.value = systemProxy;
    status.textContent = "已填入系统代理，尚未保存。";
    guard();
  };
  void read4();
  return root;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/settings-page.ts
var GROUPS = [
  ["背景层级", [
    ["--color-canvas", "页面背景"],
    ["--color-rail", "左栏背景"],
    ["--color-card", "主卡片背景"],
    ["--color-bubble", "聊天气泡"],
    ["--color-focus", "焦点背景"],
    ["--color-white", "内容背景"],
    ["--color-wash", "淡底"],
    ["--color-scrim", "遮罩"]
  ]],
  ["文字与边界", [
    ["--color-ink", "主要文字"],
    ["--color-dim", "次级文字"],
    ["--color-faint", "弱化文字"],
    ["--color-line", "细边界"]
  ]],
  ["交互与状态", [
    ["--color-mint", "主强调"],
    ["--color-mint-text", "强调色上文字"],
    ["--color-mint-wash", "强调淡底"],
    ["--color-mint-pick", "选中淡底"],
    ["--color-mint-select", "选中边"],
    ["--color-soft-red", "危险／失败"],
    ["--color-flag-neutral", "普通标签"],
    ["--color-flag-ink", "标签文字"],
    ["--color-flag-mint", "强调标签"]
  ]],
  ["圆角", [
    ["--radius-chip", "小标"],
    ["--radius-row", "一行"],
    ["--radius-card", "卡片"],
    ["--radius-pill", "胶囊"],
    ["--radius-dot", "小点"],
    ["--radius-bubble", "气泡"]
  ]],
  ["字体与尺寸", [
    ["--font-sans", "正文字体"],
    ["--font-mono", "等宽字体"],
    ["--sans", "正文（旧名）"],
    ["--mono", "等宽（旧名）"],
    ["--icon", "图标"],
    ["--avatar-msg", "聊天头像"],
    ["--avatar-rail", "左栏头像"],
    ["--nav-veil-blur", "毛玻璃"],
    ["--color-nav-veil", "导航遮罩"]
  ]]
];
function groupOf(name) {
  for (const [title, rows] of GROUPS) {
    if (rows.some((one) => one[0] === name)) return title;
  }
  return "其它";
}
function labelOf(name) {
  for (const [, rows] of GROUPS) {
    for (const [key2, label] of rows) if (key2 === name) return label;
  }
  return name;
}
var MODES = [
  ["auto", "跟随系统", "◐"],
  ["light", "浅色", "☀"],
  ["dark", "深色", "☾"]
];
function markTheMode() {
  const now = theMode();
  for (const one of document.querySelectorAll(".set-mode")) {
    one.classList.toggle("on", one.dataset.mode === now);
  }
}
function theModeRow() {
  const row2 = document.createElement("div");
  row2.className = "set-modes";
  for (const [key2, label, glyph] of MODES) {
    const bit = document.createElement("button");
    bit.className = "set-mode";
    bit.dataset.mode = key2;
    bit.textContent = glyph + " " + label;
    bit.addEventListener("click", () => {
      void setTheMode(key2).then(() => {
        markTheMode();
        markThePick();
      });
    });
    row2.append(bit);
  }
  return row2;
}
function markThePick() {
  const now = thePick();
  for (const one of document.querySelectorAll(".set-card")) {
    one.classList.toggle("picked", one.dataset.file === now);
  }
}
var TABS = [
  ["general", "通用"],
  ["look", "外观"],
  ["storage", "存储"]
];
function kindOf(value) {
  const v = value.trim();
  if (/^var\(/i.test(v)) return "token";
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return "color";
  if (/^rgb\(/i.test(v)) return "alpha";
  if (/^([0-9.]+px)([ \t]+[0-9.]+px)*$/.test(v)) return "length";
  return "font";
}
function rangeOf(name, seen) {
  if (name === "--radius-pill") return [0, 1e3];
  if (name.startsWith("--radius")) return [0, 32];
  if (name === "--icon") return [8, 40];
  if (name.startsWith("--avatar")) return [16, 96];
  if (name === "--nav-veil-blur") return [0, 40];
  return [0, Math.max(32, Math.ceil(seen * 2))];
}
function shortFamily(value) {
  const first = value.split(",")[0].trim();
  return first.replace(/^["']|["']$/g, "");
}
function rgbOf(value) {
  const shape = /^rgb\(\s*([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*\/\s*([0-9.]+)%\s*\)$/i;
  const m = shape.exec(value.trim());
  if (m === null) return { hex: "#000000", pct: 100 };
  const hex = "#" + [m[1], m[2], m[3]].map((bit) => Number(bit).toString(16).padStart(2, "0")).join("");
  return { hex, pct: Number(m[4]) };
}
function rgbText(hex, pct) {
  const at = (n) => parseInt(hex.slice(n, n + 2), 16);
  return "rgb(" + at(1) + " " + at(3) + " " + at(5) + " / " + pct + "%)";
}
function oneItem(name, put2, pool) {
  const item = document.createElement("div");
  item.className = "set-item";
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const words = document.createElement("span");
  words.className = "set-item-words";
  uiLabel(words, labelOf(name));
  words.title = labelOf(name);
  const cell = document.createElement("span");
  cell.className = "set-swatch";
  const slot = document.createElement("span");
  slot.className = "set-value";
  const kind = kindOf(value);
  if (kind === "font") {
    const pick2 = document.createElement("select");
    pick2.className = "set-select";
    pick2.setAttribute("aria-label", labelOf(name));
    for (const one of /* @__PURE__ */ new Set([value, ...pool.get(name) ?? []])) {
      const opt = document.createElement("option");
      opt.value = one;
      opt.textContent = shortFamily(one);
      opt.selected = one === value;
      pick2.append(opt);
    }
    pick2.addEventListener("change", () => {
      put2(name, pick2.value);
    });
    slot.append(pick2);
    item.append(cell, words, slot);
    return item;
  }
  if (kind === "token") {
    cell.classList.add("token");
    cell.title = value;
    const code2 = document.createElement("code");
    code2.className = "set-code";
    code2.textContent = value;
    code2.title = name;
    slot.append(code2);
    item.append(cell, words, slot);
    return item;
  }
  const code = document.createElement("code");
  code.className = "set-code";
  code.textContent = value;
  code.title = name;
  if (kind === "color") {
    const pick2 = document.createElement("input");
    pick2.type = "color";
    pick2.className = "set-pick";
    coreSetUIAttribute(pick2, "aria-label", [labelOf(name), corePhrase("颜色")]);
    pick2.value = value;
    pick2.addEventListener("input", () => {
      put2(name, pick2.value, code);
    });
    cell.append(pick2);
    slot.append(code);
  } else if (kind === "alpha") {
    const bits = rgbOf(value);
    const pick2 = document.createElement("input");
    pick2.type = "color";
    pick2.className = "set-pick";
    coreSetUIAttribute(pick2, "aria-label", [labelOf(name), corePhrase("颜色")]);
    pick2.value = bits.hex;
    const pct = document.createElement("input");
    pct.type = "range";
    pct.className = "set-range";
    coreSetUIAttribute(pct, "aria-label", [labelOf(name), corePhrase("透明度")]);
    pct.min = "0";
    pct.max = "100";
    pct.step = "1";
    pct.value = String(bits.pct);
    code.textContent = String(bits.pct) + "%";
    const both = () => {
      code.textContent = String(pct.value) + "%";
      put2(name, rgbText(pick2.value, Number(pct.value)));
    };
    pick2.addEventListener("input", both);
    pct.addEventListener("input", both);
    cell.append(pick2);
    slot.append(pct, code);
  } else {
    const seen = parseFloat(value) || 0;
    const bounds = rangeOf(name, seen);
    const num = document.createElement("input");
    num.type = "range";
    num.className = "set-range";
    coreSetUIAttribute(num, "aria-label", [labelOf(name), corePhrase("尺寸")]);
    num.min = String(bounds[0]);
    num.max = String(bounds[1]);
    num.step = "1";
    num.value = String(seen);
    num.addEventListener("input", () => {
      const unit = value.replace(/^[0-9.\s-]+/, "") || "px";
      put2(name, num.value + unit, code);
    });
    cell.classList.add("plain");
    slot.append(num, code);
  }
  item.append(cell, words, slot);
  return item;
}
function specimen() {
  const box = document.createElement("section");
  box.className = "set-spec settings-card";
  const head = document.createElement("h3");
  head.className = "set-spec-head";
  const title = document.createElement("span");
  coreSetUIText(title, "textContent", corePhrase("聊天预览"));
  const note = document.createElement("span");
  note.className = "set-spec-note";
  coreSetUIText(note, "textContent", corePhrase("配色实时同步"));
  head.append(title, note);
  box.append(head);
  const demo = document.createElement("div");
  demo.className = "set-spec-demo";
  appendFrame(demo, { kind: "msg", who: "user", at: "14:52", text: "帮我查一下之前的相关记录。" });
  appendFrame(demo, {
    kind: "tool",
    who: null,
    at: "14:53",
    tool: "memory_search",
    stage: "call",
    display_id: "preview-memory:function_call",
    text: '{"query":"相关记录"}'
  });
  appendFrame(demo, {
    kind: "tool",
    who: null,
    at: "14:53",
    tool: "memory_search",
    stage: "result",
    display_id: "preview-memory:function_result",
    text: "已找到 3 条相关记录。"
  });
  appendFrame(demo, {
    kind: "msg",
    who: "bot",
    at: "14:53",
    text: "找到了 3 条相关记录，我来帮你整理。"
  }, "BOT");
  box.append(demo);
  return box;
}
function settingsPage(shell2) {
  const root = document.createElement("section");
  root.className = "page-settings page-world settings-unified";
  const bar2 = document.createElement("div");
  bar2.className = "set-bar";
  const title = document.createElement("h1");
  coreSetUIText(title, "textContent", corePhrase("设置 · SETTINGS"));
  bar2.append(title);
  root.append(bar2);
  const book = document.createElement("div");
  book.className = "set-book";
  const tabs = document.createElement("nav");
  tabs.className = "set-tabs";
  const body = document.createElement("div");
  body.className = "set-body";
  book.append(tabs, body);
  root.append(book);
  onUILanguage((value) => {
    for (const button4 of root.querySelectorAll(".settings-language-button")) {
      button4.setAttribute("aria-pressed", String(button4.dataset.language === value));
    }
  });
  let viewEpoch = 0;
  let refreshPalette;
  let unsubscribeLook;
  let editingPalette = false;
  const refreshLook = () => {
    if (root.hidden || editingPalette) return;
    markTheMode();
    markThePick();
    refreshPalette?.();
  };
  let proxyPanel;
  const readHint = pageLoad(root, "外观预设", () => {
    void drawLook();
  });
  const drawGeneral = () => {
    viewEpoch++;
    refreshPalette = void 0;
    readHint.ready();
    body.replaceChildren();
    const language2 = document.createElement("section");
    language2.className = "settings-card settings-language";
    const languageTitle = document.createElement("h3");
    coreSetUIText(languageTitle, "textContent", corePhrase("界面语言"));
    const languageOptions = document.createElement("div");
    languageOptions.className = "settings-language-options";
    languageOptions.setAttribute("role", "group");
    coreSetUIAttribute(languageOptions, "aria-label", corePhrase("界面语言"));
    for (const [value, label] of [["zh-Hans", "简体中文"], ["zh-Hant", "繁體中文"]]) {
      const button4 = document.createElement("button");
      button4.type = "button";
      button4.className = "settings-language-button";
      button4.dataset.language = value;
      button4.textContent = label;
      button4.setAttribute("aria-pressed", String(uiLanguage() === value));
      button4.addEventListener("click", () => setUILanguage(value));
      languageOptions.append(button4);
    }
    language2.append(languageTitle, languageOptions);
    body.append(language2);
    body.append(packageUpdateCard());
    body.append(proxyPanel ??= modelProxySettings());
  };
  const drawLook = async () => {
    const epoch = ++viewEpoch;
    refreshPalette = void 0;
    readHint.start();
    body.replaceChildren();
    const two = document.createElement("div");
    two.className = "set-two";
    const left = document.createElement("div");
    left.className = "set-left settings-card";
    const right = document.createElement("div");
    right.className = "set-right";
    const controls = document.createElement("div");
    controls.className = "set-controls";
    controls.append(left);
    two.append(controls, right);
    const preview = specimen();
    right.append(preview);
    body.append(two);
    const appearanceTitle = document.createElement("h3");
    coreSetUIText(appearanceTitle, "textContent", corePhrase("主题与配色"));
    left.append(appearanceTitle, theModeRow());
    const modeHint = document.createElement("p");
    modeHint.className = "set-note";
    coreSetUIText(modeHint, "textContent", corePhrase("跟随系统自动切换明暗配色；选择预设或手动调色后，使用固定配色。"));
    left.append(modeHint);
    markTheMode();
    const scheme = document.createElement("div");
    scheme.className = "set-scheme";
    const title2 = document.createElement("div");
    title2.className = "set-scheme-title";
    coreSetUIText(title2, "textContent", corePhrase("颜色方案"));
    scheme.append(title2);
    const cards = document.createElement("div");
    cards.className = "set-cards";
    scheme.append(cards);
    left.append(scheme);
    const presets = await thePresets();
    let incomplete = presets.length === 0;
    if (epoch !== viewEpoch || root.hidden) return;
    const presetReads = new Map(presets.map((one) => [
      one.file,
      readLookAsset(one.file).then((r) => r.ok ? r.json() : null).catch(() => null)
    ]));
    for (const one of presets) {
      const card = document.createElement("button");
      card.className = "set-card";
      card.dataset.file = one.file;
      const name = document.createElement("span");
      name.className = "set-card-name";
      name.textContent = one.name;
      const seen = document.createElement("span");
      seen.className = "set-card-swatch";
      try {
        const box = await presetReads.get(one.file);
        if (epoch !== viewEpoch || root.hidden) return;
        if (!box) incomplete = true;
        if (box) {
          for (const key2 of [
            "--color-canvas",
            "--color-rail",
            "--color-card",
            "--color-mint",
            "--color-ink"
          ]) {
            const bit = document.createElement("i");
            if (typeof box[key2] === "string") bit.style.background = box[key2];
            seen.append(bit);
          }
        }
      } catch (why) {
        console.error("那一套读不到：", why);
      }
      const mark = document.createElement("span");
      mark.className = "set-card-mark";
      coreSetUIText(mark, "textContent", corePhrase("内置"));
      card.append(name, mark, seen);
      card.addEventListener("click", () => {
        void putOn(one.file).then(() => {
          markTheMode();
          markThePick();
        });
      });
      cards.append(card);
    }
    const palette = document.createElement("div");
    palette.className = "set-palette";
    const ptitle = document.createElement("div");
    ptitle.className = "set-scheme-title";
    coreSetUIText(ptitle, "textContent", corePhrase("细节调整"));
    palette.append(ptitle);
    let names = THE_LOOK_ROWS.map((one) => one[0]);
    try {
      const got = await readLookAsset("default.json");
      if (got.ok) {
        const box = await got.json();
        const plenty = Object.keys(box).filter((k) => !k.startsWith("__"));
        if (plenty.length > 0) names = plenty;
      }
    } catch (why) {
      console.error("调色盘的名单读不到，退回那六行：", why);
    }
    if (epoch !== viewEpoch || root.hidden) return;
    const put2 = (name, value, code) => {
      theLook[name] = value;
      editingPalette = true;
      try {
        writeTheLook(false, name);
      } finally {
        editingPalette = false;
      }
      markTheMode();
      void keepTheLook();
      if (code !== void 0) code.textContent = value;
    };
    const pool = /* @__PURE__ */ new Map();
    for (const one of presets) {
      try {
        const box = await presetReads.get(one.file);
        if (epoch !== viewEpoch || root.hidden) return;
        if (!box) continue;
        for (const [name, cell] of Object.entries(box)) {
          if (name.startsWith("__") || typeof cell !== "string") continue;
          const seen = pool.get(name) ?? [];
          if (!seen.includes(cell)) pool.set(name, [...seen, cell]);
        }
      } catch (why) {
        console.error("收可选值时那一份读不到：", one.file, why);
      }
    }
    names = names.filter((one) => one !== "--font-sans" && one !== "--font-mono" && one !== "--sans" && one !== "--mono");
    const byGroup = /* @__PURE__ */ new Map();
    for (const name of names) {
      const key2 = groupOf(name);
      byGroup.set(key2, [...byGroup.get(key2) ?? [], name]);
    }
    const order = [...GROUPS.map((one) => one[0]), "其它"];
    refreshPalette = () => {
      palette.replaceChildren(ptitle);
      for (const key2 of order) {
        const mine = byGroup.get(key2);
        if (mine === void 0 || mine.length === 0) continue;
        const box = document.createElement("section");
        box.className = "set-group";
        const h = document.createElement("h3");
        h.className = "set-group-title";
        uiLabel(h, key2);
        box.append(h);
        const grid = document.createElement("div");
        grid.className = "set-grid";
        for (const name of mine) grid.append(oneItem(name, put2, pool));
        box.append(grid);
        palette.append(box);
      }
    };
    refreshPalette();
    const advanced = document.createElement("section");
    advanced.className = "settings-card set-advanced";
    const advancedTitle = document.createElement("h3");
    coreSetUIText(advancedTitle, "textContent", corePhrase("高级外观"));
    advanced.append(advancedTitle, palette);
    two.classList.add("is-editing");
    two.append(advanced);
    if (epoch !== viewEpoch || root.hidden) return;
    markThePick();
    if (incomplete) readHint.fail();
    else readHint.ready();
  };
  const draws = {
    general: drawGeneral,
    storage: () => {
      viewEpoch++;
      refreshPalette = void 0;
      readHint.ready();
      body.replaceChildren(pluginStorageSettings(), snowlumaSessionSettings());
    },
    look: () => {
      void drawLook();
    }
  };
  for (const [key2, label] of TABS) {
    const bit = document.createElement("button");
    bit.className = "set-tab";
    uiLabel(bit, label);
    bit.addEventListener("click", () => {
      draws[key2]();
      body.scrollTop = 0;
      markTheTab(key2);
    });
    bit.dataset.tab = key2;
    tabs.append(bit);
  }
  const markTheTab = (key2) => {
    for (const one of tabs.querySelectorAll(".set-tab")) {
      one.classList.toggle("on", one.dataset.tab === key2);
    }
  };
  shell2.content.replaceChildren(root);
  return {
    root,
    show() {
      root.hidden = false;
      unsubscribeLook ??= onLookChanged(refreshLook);
      drawGeneral();
      markTheTab("general");
    },
    stop() {
      viewEpoch++;
      root.hidden = true;
      refreshPalette = void 0;
      unsubscribeLook?.();
      unsubscribeLook = void 0;
    }
  };
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/pages.ts
function page(row2, build) {
  return { ...row2, build };
}
var PAGES = [
  page({
    group: "CORE",
    id: "live",
    label: "控制台",
    icon: "terminal",
    file: "live.ts"
  }, chatPage),
  // **刀 4：那一项落 `CORE` 并改名「模型」**（设计稿 裁定 5 ／ 10 ／ 11）：
  //    · `group`：`WORLD` → `CORE` ⇒ ⚠️ **`WORLD` 那一组随之消失**（`groupsOf()` 按首次出现
  //      收组名 ⇒ 没有一项落在它里面，组标自己就不出现，`shell.ts` **一行不改**）；
  //    · `label`：`引擎` → `模型`（它现在担两件事：看 ＋ 选）；
  // · **`id` 保留 `engine`**：`pageAt()` 对认不出的路由**掉回第一页**
  //      ⇒ 改 id 会让 `#/engine` 老书签**静默跳到别处**；
  //    · 绿点（`dot: true`）留着 —— 它是"当前那一条"的指示灯，与新语义正好配。
  // **图标：`layers` → `plug`**（2026-10-03 作者点头；设计稿 §十二第 4 条）——
  //      `layers` 在参考那边是「World 总览」的图标，而这一项改叫「模型」之后，
  //      `ICONS` 里那个**空着没人用**的 `plug`（插头）才贴它；改完 `layers` 空出来，
  // 留给将来那类"总览"的页。 判据钉着"这个名字在 `ICONS` 里"（写错只会是一条空路径）。
  page({
    group: "CORE",
    id: "engine",
    label: "模型",
    icon: "chip",
    dot: true,
    file: "engine-page.ts"
  }, enginePage),
  // 2026-10-10（外观那一页 重做）：**设置**（ 左下角那一颗 ⚙ 跳到它 ）。
  page({
    group: "CORE",
    id: "settings",
    label: "设置",
    icon: "plug",
    offRail: true,
    file: "settings-page.ts"
  }, settingsPage),
  page({
    group: "PERSONA & MEMORY",
    id: "persona",
    label: "人设",
    icon: "person",
    file: "persona-page.ts"
  }, personaPage),
  // **刀 1：`INFINITY` 那一组终于有真的一页了**（作者 2026-10-05："先做 1"）
  //    —— 就是左栏那个「插件」入口（从前它在 `SOON` 里、点不动）。
  //    ⚠️ **它必须写在最后**：`groupsOf()` 按**首次出现**排 ⇒ 一个"既在 `PAGES`
  //      又在 `SOON`"的组会**跳到 `PAGES` 那一边的位置**上去；写在末尾 ⇒
  // `INFINITY` 照旧是**最后一组** （这是 `groupsOf()` 那一段自己点名的隐式依赖 ）。
  // 而 `SOON` 里那一项**同时删掉**：同一个入口不许有两个登记处
  // （"加一页 = 一个文件 ＋ 一行" ／ 而"还没做"那条清单是另一回事 ）。
  // **「记忆」那一页**（2026-10-05 作者："给记忆页开个头"）—— 它原先在 `SOON` 里
  //    （点不动），而**服务那一侧早就有了**（`web.py` 的 `/memory` 路由＋入口递的
  // `memory.everyone(home)` ）⇒ 这一刀把它接上：**一个文件 ＋ 一行** 。
  page({
    group: "PERSONA & MEMORY",
    id: "memory",
    label: "记忆",
    icon: "memory",
    file: "memory-page.ts"
  }, memoryPage),
  // **「」那一组**（2026-10-08，作者拍的「①THE WORLD」）——
  // · 组名 `THE WORLD`（ 与 `CORE` ／ `PERSONA & MEMORY` 同一套大写 ）；
  // · **三个子项**： 知识库 ／ 画像 ／ 经历 —— 就是记忆页那条尾那三样 ；
  // · ⚠️ **它写在 `INFINITY` 之前**：`groupsOf()` 按**首次出现**排 ⇒
  // 那一组照旧落在**最后一组** （ 那条位次判据钉着它 —— 我先写在最后，
  // 判据当场红了 ⇒ **是它把我改对的** ）。
  // ⚠️ 那三条口**后端早就在**（`/knowledge` ／ `/people` ／ `/experience` ）⇒
  // 这里只补前端 （ 而三页都**只读** —— 与记忆那一页同一条规矩 ）。
  // ⚠️ 图标名**只许用 `shell.ts::ICONS` 里有的那几个**
  //      （可用的就这些：`terminal` ／ `pulse` ／ `chart` ／ `plug` ／ `puzzle` ／
  // `person` ／ `memory` ／ `note` ／ `layers` —— `book` ／ `messages` 都不在里面 ，
  // 而那条判据**两次**都把我拦下了 ）。
  page({
    group: "THE WORLD",
    id: "knowledge",
    label: "知识库",
    icon: "book",
    file: "knowledge-page.ts"
  }, knowledgePage),
  page({
    group: "THE WORLD",
    id: "people",
    label: "画像",
    icon: "contacts",
    file: "people-page.ts"
  }, peoplePage),
  page({
    group: "THE WORLD",
    id: "experience",
    label: "经历",
    icon: "history",
    file: "experience-page.ts"
  }, experiencePage),
  // **刀 1：`INFINITY` 那一组终于有真的一页了**（作者 2026-10-05："先做 1"）
  //    —— 就是左栏那个「插件」入口（从前它在 `SOON` 里、点不动）。
  //    ⚠️ **它必须写在最后**：`groupsOf()` 按**首次出现**排 ⇒ 一个"既在 `PAGES`
  //      又在 `SOON`"的组会**跳到 `PAGES` 那一边的位置**上去；写在末尾 ⇒
  // `INFINITY` 照旧是**最后一组** （这是 `groupsOf()` 那一段自己点名的隐式依赖 ）。
  // 而 `SOON` 里那一项**同时删掉**：同一个入口不许有两个登记处
  // （"加一页 = 一个文件 ＋ 一行" ／ 而"还没做"那条清单是另一回事 ）。
  page({
    group: "INFINITY",
    id: "plugin",
    label: "插件市场",
    icon: "grid",
    file: "plugin-market-page.ts"
  }, pluginMarketPage),
  page({
    group: "INFINITY",
    id: "infinity",
    label: "插件总览",
    icon: "puzzle",
    file: "plugin-workspace.ts"
  }, pluginWorkspace),
  page({
    group: "INFINITY",
    id: "infinity-entry",
    label: "插件配置",
    icon: "puzzle",
    offRail: true,
    file: "infinity-plugin-page.ts"
  }, infinityPluginPage)
];
var SOON = [
  // INFINITY 的插件页已在 PAGES 中注册；SOON 仅用于不可点击的预留入口。人格、记忆和知识库属于核心功能。
];
function groupsOf(pages = PAGES, soon = SOON) {
  const seen = [];
  for (const one of [...pages, ...soon]) if (!seen.includes(one.group)) seen.push(one.group);
  return seen;
}
function navItems(group, pages = PAGES) {
  return pages.filter((one) => one.group === group).filter((one) => one.offRail !== true).map(({ id, label, icon: icon2, dot }) => ({ id, label, icon: icon2, dot }));
}
function soonItems(group, soon = SOON) {
  return soon.filter((one) => one.group === group);
}
function pageAt(route2, pages = PAGES) {
  const id = route2.replace(/^#\/?/, "");
  if (id.startsWith("infinity/") && id.length > "infinity/".length) {
    return pages.find((one) => one.id === "infinity-entry") ?? pages[0];
  }
  return pages.find((one) => one.id === id) ?? pages[0];
}
function currentPage(pages = PAGES) {
  return pageAt(location.hash, pages);
}
var live = null;
function route(shell2, pages = PAGES) {
  const one = currentPage(pages);
  const selected = one.id === "infinity-entry" ? location.hash.replace(/^#\/?/, "") : one.id;
  for (const el6 of shell2.railNav.querySelectorAll(".rail-item")) {
    el6.classList.toggle("active", el6.dataset.page === selected);
  }
  if (live !== null) live.stop();
  live = one.build(shell2);
  live.show();
  return live;
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/state.ts
var COUNTER_KEYS = ["tokens", "messages", "batches", "paused"];
var ENGINE_KEYS = ["endpoint", "model", "profile", "model_available"];
function count2(value) {
  return typeof value === "number" ? String(value) : "—";
}
function machineStatus(m) {
  if (m === null) return "—";
  if (m.starting) return "正在唤醒";
  if (m.activity?.state === "stopping") return "正在休止";
  if (m.paused) return "已休止";
  return m.stopped_by_hand ? "静候中" : "已唤醒";
}
function counterChips(m) {
  if (m === null) {
    return [["tokens", "—"], ["messages", "—"], ["batches", "—"], ["paused", "—"]];
  }
  return [
    ["tokens", m.context_estimate_source === "standby_archive_preview" && typeof m.tokens === "number" ? `~${m.tokens}` : count2(m.tokens)],
    ["messages", count2(m.messages)],
    ["batches", count2(m.batches)],
    ["paused", machineStatus(m)]
  ];
}

// ../../../../../../tmp/telyin-qq-environment-release-20261126/core/src/web/client/main.ts
var READ_NAMES = [
  ...COUNTER_KEYS,
  ...ENGINE_KEYS,
  "connected"
];
function machineOf(st) {
  void st.connected;
  if (st.machine === null) return;
  void st.machine.tokens;
  void st.machine.messages;
  void st.machine.batches;
  void st.machine.paused;
  void st.machine.endpoint;
  void st.machine.model;
  void st.machine.profile;
  void st.machine.model_available;
}
function isChat(view2) {
  return typeof view2 === "object" && view2 !== null && typeof view2.append === "function";
}
function need(id) {
  const el6 = document.getElementById(id);
  if (el6 === null) throw new Error(`那一页的壳里少了 #${id}`);
  return el6;
}
function anchors() {
  for (const id of ["rail", "topbar", "content", "bottom"]) need(id);
}
void initializeDisplayScope();
anchors();
var mounted = createShell(document.body);
if (typeof window.setInterval === "function") {
  startPackageUpdates();
  startPluginUpdates();
  startPluginRestart();
}
var shell = mounted.shell;
var bar = mounted.topbar;
var conn = bar.provider;
for (const [index, group] of groupsOf().entries()) {
  shell.place(index, group, navItems(group), soonItems(group, SOON));
}
var displayScopeReady = false;
var pluginNavPending = false;
var pluginNavKey = "";
var pluginNavGeneration = 0;
var latestPluginEntries = null;
var navigationOrder = pluginNavigationOrder(shell.railNav, () => {
  pluginNavKey = "";
  if (latestPluginEntries !== null) placePluginNav(groupsOf().indexOf("INFINITY"), latestPluginEntries);
  else restorePluginNav();
});
function restorePluginNav() {
  if (navigationOrder.editing) return;
  try {
    const cached = JSON.parse(readDisplaySession("plugin-nav") || "[]");
    if (Array.isArray(cached) && cached.every((one) => typeof one.id === "string" && one.id.startsWith("infinity/") && typeof one.label === "string")) {
      const index = groupsOf().indexOf("INFINITY");
      if (index >= 0 && cached.length) {
        shell.place(index, "INFINITY", [
          ...navItems("INFINITY"),
          ...cached.map((one) => ({ ...one, signals: void 0, dot: false }))
        ]);
        navigationOrder.mount(cached);
      }
    }
  } catch {
  }
}
restorePluginNav();
onDisplayScopeReady(() => {
  displayScopeReady = true;
  if (latestPluginEntries !== null) {
    pluginNavKey = "";
    placePluginNav(groupsOf().indexOf("INFINITY"), latestPluginEntries);
  } else restorePluginNav();
});
function pluginEntries(plugins) {
  return plugins.filter((one) => one.installed !== false && one.name).map((one) => {
    const signals = pluginSignals(one);
    return {
      id: `infinity/${encodeURIComponent(one.name)}`,
      label: one.displayName || one.name,
      icon: pluginIcon(one),
      child: true,
      signals,
      navigationParent: navigationParent(one)
    };
  });
}
function placePluginNav(index, entries) {
  latestPluginEntries = entries;
  const key2 = JSON.stringify(entries);
  if (key2 === pluginNavKey || navigationOrder.editing) return;
  pluginNavKey = key2;
  try {
    writeDisplaySession("plugin-nav", JSON.stringify(entries));
  } catch {
  }
  shell.place(index, "INFINITY", [...navItems("INFINITY"), ...entries]);
  navigationOrder.mount(entries, displayScopeReady);
  const active2 = location.hash.replace(/^#\/?/, "") || "live";
  for (const link of shell.railNav.querySelectorAll(".rail-item")) {
    link.classList.toggle("active", link.dataset.page === active2);
  }
}
async function refreshPluginNav() {
  if (pluginNavPending) return;
  const index = groupsOf().indexOf("INFINITY");
  if (index < 0) return;
  pluginNavPending = true;
  const generation = ++pluginNavGeneration;
  try {
    const answer = await getStatusJson("/plugins", 5e3);
    if (!answer.wired) return;
    const plugins = answer.plugins ?? [];
    placePluginNav(index, pluginEntries(plugins));
    void refreshPluginHealth(plugins).then(() => {
      if (generation !== pluginNavGeneration) return;
      placePluginNav(index, pluginEntries(plugins));
    });
  } catch {
  } finally {
    pluginNavPending = false;
  }
}
void refreshPluginNav();
window.addEventListener("plugins-changed", () => {
  void refreshPluginNav();
});
if (typeof window.setInterval === "function") window.setInterval(() => {
  if (document.visibilityState !== "hidden") {
    void refreshPluginNav();
  }
}, 3e3);
var panel = null;
var view = null;
var chat = null;
function applyNames() {
  shell.setSpeaker(speaker());
  if (chat !== null) chat.setSpeaker(speaker());
}
void Promise.all([loadSpeaker(), loadUserLabel()]).then(applyNames);
window.addEventListener("bot-display-changed", () => {
  void Promise.all([loadSpeaker(true), loadUserLabel(true)]).then(applyNames);
});
function switchPage() {
  panel = route(shell);
  view = panel.view;
  chat = isChat(view) ? view : null;
  applyNames();
  void refresh();
}
var stateRead = 0;
async function refresh() {
  const read4 = ++stateRead;
  const usageRead = pageFetch("/usage").then((r) => r.ok ? r.json() : null).catch(() => null);
  let where = "读 /state";
  try {
    const got = await pageFetch("/state");
    if (!got.ok) throw new Error(`/state 回了 ${got.status}`);
    const st = await got.json();
    const total = null;
    void usageRead.then((box) => {
      if (read4 !== stateRead || typeof box?.total !== "number") return;
      shell.counters(counterChips({ ...st.machine, tokens: box.total, context_estimate_source: box.estimate_source }));
    });
    if (read4 !== stateRead) return;
    where = "写机器那几个数";
    machineOf(st);
    syncGateState(shell.root, st.machine);
    where = "写顶栏那几格";
    shell.counters(counterChips(total === null ? st.machine : { ...st.machine, tokens: total }));
    window.dispatchEvent(new CustomEvent("core-console-state", { detail: st.machine }));
    const localHealth = st.machine?.model_available;
    coreSetUIText(conn, "textContent", localHealth === false ? corePhrase("模型离线") : !st.connected ? corePhrase("页面断开") : corePhrase("已连接"));
    conn.dataset.on = String(st.connected && localHealth !== false);
    conn.dataset.model = localHealth === false ? "offline" : localHealth === true ? "online" : "unknown";
    coreSetUIText(conn, "title", localHealth === false ? corePhrase("本地模型未响应；QQ 消息仍可入站，但 Agent 暂时无法生成回复。") : localHealth === true ? corePhrase("本地模型探活成功。") : corePhrase("仅表示页面连接状态；当前模型未做探活。"));
    bar.model.dataset.empty = st.machine === null || st.machine.model === null ? "true" : "false";
    bar.model.textContent = st.machine === null || st.machine.model === null ? "—" : modelDisplayName(st.machine.model);
    bar.model.title = (st.machine?.model ? st.machine.model + " · " : "") + "打开模型配置";
    shell.counters(counterChips(
      total === null ? st.machine : { ...st.machine, tokens: total }
    ));
    where = "画那一页";
  } catch (exc) {
    if (read4 !== stateRead) return;
    console.error("refresh 抛在：", where, exc);
    conn.dataset.on = "false";
    conn.dataset.model = "unknown";
    coreSetUIText(conn, "title", corePhrase("无法刷新连接状态，请检查服务后重试。"));
    coreSetUIText(conn, "textContent", corePhrase("状态读取失败"));
    bar.model.dataset.empty = "true";
    bar.model.textContent = "—";
    bar.model.title = "";
    shell.counters(COUNTER_KEYS.map((key2) => [key2, "—"]));
  }
}
var events = new EventSource("/events");
events.onmessage = (e) => {
  try {
    const frame = JSON.parse(e.data);
    if (frame.kind === "control_state") {
      window.dispatchEvent(new CustomEvent("core-console-state", { detail: frame }));
      void refresh();
      return;
    }
  } catch {
  }
  if (chat !== null) chat.append(e.data);
  window.dispatchEvent(new Event("core-records-changed"));
  void loadUserLabel().then(applyNames);
  void refresh();
};
events.onopen = () => {
  chat?.refreshHistory?.();
  window.dispatchEvent(new Event("core-records-changed"));
  void loadAvatar();
  void Promise.all([loadSpeaker(true), loadUserLabel(true)]).then(applyNames);
  coreSetUIText(conn, "textContent", corePhrase("已连接"));
  void refresh();
};
events.onerror = () => {
  conn.dataset.on = "false";
  coreSetUIText(conn, "textContent", corePhrase("断开"));
  void refresh();
};
onEngineChanged(() => {
  void refresh();
});
window.addEventListener("core-control-refresh", () => {
  void refresh();
});
window.addEventListener("hashchange", switchPage);
switchPage();
if (typeof window.setInterval === "function") {
  window.setInterval(() => {
    void refresh();
  }, 15e3);
}
export {
  PAGES,
  mountChat
};
