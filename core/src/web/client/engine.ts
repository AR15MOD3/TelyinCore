import { pageFetch as fetch } from './status-json';
import { notifyEngineChanged } from './engine-events';
// **「模型」那一页的数据层**（刀 4 重做那一刀）—— 那四条端点都在这儿。
//
// ⚠️ **这一份只管"怎么跟服务说话"**：建元素 ／ 画两栏 ／ 收字全在 `engine-page.ts`
//    ⇒ 于是那一页里**没有一处**裸 `fetch`，而"打哪个地址、发什么形状"只在这儿出现一次。
// 请求通过 status-json 的 pageFetch；变更通知通过 engine-events 同步其他消费者。

/** **载荷的顶层名字** —— ⚠️ **与 `web.py::ENGINE_PAGE_KEYS` 必须逐字相同**（两处不许各答一个数）。 */
export const ENGINE_PAGE_KEYS = ['profiles', 'current', 'keys', 'availability', 'secret',
  'can_probe', 'boundary', 'presets'] as const;

/** 一条档案（ 与 `engine.Profile` 那十格同名 —— 服务交回来的就是它们）。 */
export interface Profile {
  readonly model_name?: string;
  readonly multimodal?: boolean;
  readonly auth?: 'api_key' | 'chatgpt';
  readonly name: string;
  readonly display_name?: string;
  readonly kind: string;
  readonly base_url: string;
  readonly model: string;
  readonly max_tokens: number | null;
  readonly thinking: boolean;
  readonly context_window: number | null;
  readonly temperature: number | null;
  readonly reasoning_effort: string | null;
  readonly reasoning_budget_tokens?: number | null;
  readonly secret: string;
}

/** 一个键的答案：**值** ＋ **它从哪来**（`default` ／ `file` ／ `env`）。 */
export interface Sourced {
  readonly value: unknown;
  readonly source: string;
}

/** **一条预设**（`engine.PRESETS` 那一份 —— 地址**每一条都量过**）。 */
export interface Preset {
  readonly auth?: 'api_key' | 'chatgpt';
  readonly name: string;
  readonly kind: string;
  readonly base_url: string;
  readonly model: string;
  readonly secret: string;
  /** **这一家声明的那几档推理强度**（照参考 `ProviderModule.reasoningTiers`）；
   *  ⚠️ **空 = 开放**（它那一句原话："开放模块接受任意非空字符串"）—— 没量过的家留空。 */
  readonly efforts?: string[];
  /** 开放模块的**备选值**（照参考 `effortSuggestions`）。 */
  readonly effort_suggestions?: string[];
}

/** `GET /engine` 交回来的那一份。 */
export interface EngineReport {
  readonly profiles: Profile[];
  readonly current: string | null;
  readonly keys: Record<string, Sourced>;
  readonly defaults?: Record<string, unknown>;
  readonly capacity_status?: string;
  readonly capacities?: Record<string, {model: string; value: number | null; source: string; status: string; http_status?: number | null}>;
  readonly availability: Record<string, boolean>;
  /** **变量名 → 来源**（`env` ／ `file` ／ `missing`）—— **一个值都没有**（只写不回显）。 */
  readonly secret: Record<string, string>;
  readonly can_probe: boolean;
  /** 那一句边界（**一处**：由服务交出来 —— 页自己一个字都不写）。 */
  readonly boundary: string;
  /** **预设那七条**（DeepSeek ／ 智谱 GLM ／ Kimi ／ OpenAI ／ OpenRouter ／ 本地 ／ 自定义）——
   * 页拿它做"**填了名字就带出地址与协议**"； 于是地址在页里**只此一份**（不写死）。 */
  readonly presets: Preset[];
}

/** **上游自己报的那几格** —— 字段名照参考项目那份 `ListedModel` 取
 *  （`src/providers/base.ts`）：`display_name` ／ `context_window` ／ `max_output_tokens` ／
 *  `input_images`（那一格的原话："picking the model sets the endpoint's `multimodal` to this"）。
 *  ⚠️ **上游没说的都是 `null`**（不编）；页上落什么由 `applySpecOf()` **一处**决定。 */
export interface ModelSpec {
  readonly id: string;
  readonly display_name: string;
  readonly context_window: number | null;
  readonly max_output_tokens: number | null;
  readonly input_images: boolean | null;
  readonly temperature?: number | null;
  readonly effort_default: string | null;
  readonly effort_levels: string[];
}

/** 探活那一份（ `error` 那四句话是**服务给**的，页只照原样显示）。 */
export interface ProbeResult {
  readonly ok: boolean;
  readonly status: number | null;
  readonly models: string[] | null;
  /** **上游报的窗口 ／ 额度 ／ 推理强度**（`mode: 'models'` 那一趟带回来）——
   *  ⚠️ 本仓实测：DeepSeek 的 `/v1/models` 真的报这三样（1048576 ／ 393216 ／ `high`）⇒
   *  「上下文上限」这类默认值**问上游要**，而不是我们猜一个。 */
  readonly specs?: ModelSpec[] | null;
  /** 四种下场之一：`模型名不能为空` ／ `API地址不能为空` ／ `API地址有误，请检查` ／
   * `模型名或协议有误，请检查`（ 都由服务那一侧判，页不自己编）。 */
  readonly error: string | null;
  /** **上游那一次真回话**（作者要"请求一次 API 的返回"）—— 成没成都有。 */
  readonly reply?: string | null;
  readonly detail?: string | null;
  readonly url: string;
}

/** 一次请求的三种下场（ 判据要分得开它们：成了 ／ 服务说不行 ／ 连不上）。 */
export interface Reply<T> {
  readonly ok: boolean;
  /** HTTP 那个数（400 ／ 403 ／ 409 ／ 503 …）—— 页上照实显示它。 */
  readonly status: number;
  /** 成了带那一份；没成带 `null`（而 `note` 里是服务说的那句话）。 */
  readonly data: T | null;
  readonly note: string;
}

async function ask<T>(url: string, init?: RequestInit): Promise<Reply<T>> {
  try {
    const resp = await fetch(url, init);
    const text = await resp.text();
    let data: unknown = null;
    try {
      data = JSON.parse(text);
    } catch {
      data = null; // 那几条错回的是**一行字**（不是 JSON）
    }
    return {
      ok: resp.ok,
      status: resp.status,
      data: resp.ok ? (data as T) : null,
      note: resp.ok ? '' : (text.trim() || `HTTP ${resp.status}`),
    };
  } catch (exc) {
    // ⚠️ **连不上**也是一条要说出来的话（不是"什么都没发生"）。
    return { ok: false, status: 0, data: null, note: `连不上：${String(exc)}` };
  }
}

const JSON_POST = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** `GET /engine` —— 档案清单 ＋ 当前 ＋ 每个键的生效值与来源 ＋ 可用性。 */
export function engineReport(): Promise<Reply<EngineReport>> {
  return ask<EngineReport>('/engine');
}

/** Save receipts are authoritative; reports are display-only. */
export interface SecretBinding {
  readonly profile_name: string;
  readonly secret: string;
  readonly revision: string;
}
export interface EngineCommandResult {
  readonly code: string;
  readonly error: string;
}
export interface EngineSaveReceipt extends EngineCommandResult {
  readonly profile: Profile;
  readonly secret_binding: SecretBinding;
}
export function engineCommand(asked: Record<string, unknown> & {
  action: 'save'}): Promise<Reply<EngineSaveReceipt>>;
export function engineCommand(asked: Record<string, unknown>): Promise<Reply<EngineCommandResult>>;
export async function engineCommand(asked: Record<string,
  unknown>): Promise<Reply<EngineCommandResult>> {
  const reply = await ask<EngineCommandResult>('/engine', JSON_POST(asked));
  if (reply.ok && reply.data?.code === 'ok') notifyEngineChanged();
  if (reply.ok && reply.data?.code !== 'ok') return {...reply, ok: false,
    note: reply.data?.error || '保存回执无效，结果未确认'};
  return reply;
}

/** No unconditional slot write from the profile-save flow. */
export async function engineSecret(name: string, value: string,
  expected: SecretBinding): Promise<Reply<EngineCommandResult>> {
  const reply = await ask<EngineCommandResult>('/engine/secret', JSON_POST({name, value,
    expected}));
  if (reply.ok && reply.data?.code !== 'ok') return {...reply, ok: false,
    note: reply.data?.error || '密钥写入结果未确认'};
  return reply;
}

/** `POST /engine/probe` —— **测试连通**（它是"另一件事"，不参与可用性判断）。

 *  ⚠️ 递进去的是**这一套配置的四样**（`mode` ／ `name` ／ `kind` ／ `model` ／ `base_url`）——
 * 当前草稿可在保存前探测；同一服务可复用本实例的密钥，换服务须明确提供新密钥。私有密钥仅经请求正文
 * 交给同代捕获入口，不出现在报告或日志；捕获失败不回退当前档案或环境凭据。
 *  · `mode: 'minimal'`（缺省）—— **真发一次最小请求**，把上游的回话拿回来；
 *  · `mode: 'models'`  —— 「取模型列表」用（打 `/models`）。
 */
export function probeEngine(asked: Record<string, unknown>): Promise<Reply<ProbeResult>> {
  return ask<ProbeResult>('/engine/probe', JSON_POST(asked));
}

export interface ChatGPTStatus {
  login_completed?: boolean;
  login_in_progress?: boolean;
  connected: boolean;
  pending: boolean;
  account: string;
  message: string;
  storage: string;
  authorization_url?: string;
}

export async function chatgptCommand(name: string,
  action: 'status' | 'start' | 'cancel' | 'logout'):
  Promise<Reply<ChatGPTStatus>> {
  const got = await ask<ChatGPTStatus>('/engine/chatgpt', JSON_POST({ name, action }));
  if (!got.ok && (got.status === 404 || got.status === 501)) {
    return { ...got, note: '后端尚未加载订阅登录功能，请重启 Core 后刷新此页。' };
  }
  if (!got.ok && /<!doctype|<html|<head|<body/i.test(got.note)) {
    return { ...got, note: `登录服务返回异常（HTTP ${got.status}），请重启 Core 后重试。` };
  }
  return got;
}

/** Freeze both phases before awaiting. Never select a slot from a later report. */
export async function saveEngineDraft(asked: Record<string, unknown>, typed: string): Promise<{
  profile: Reply<EngineSaveReceipt>; secret?: Reply<EngineCommandResult>; error?: string;
}> {
  const target = {...(asked.profile as Record<string, unknown>)};
  const request = {...asked, action: 'save' as const, profile: target};
  const wantsSecret = typed !== '' && target.auth !== 'chatgpt'
    && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(
      String(target.base_url).trim());
  const profile = await engineCommand(request);
  if (!profile.ok) return {profile};
  const saved = profile.data?.profile, binding = profile.data?.secret_binding;
  if (!saved || saved.name !== target.name || saved.kind !== target.kind
      || (saved.auth || 'api_key') !== (target.auth || 'api_key')
      || saved.base_url !== String(target.base_url).trim()
      || !binding || binding.profile_name !== saved.name || binding.secret !== saved.secret
      || typeof binding.revision !== 'string' || binding.revision.trim() === ''
      || (wantsSecret && (typeof saved.secret !== 'string' ||
        !/^[A-Za-z_][A-Za-z0-9_]*$/.test(saved.secret)))) {
    return {profile, error: '档案保存已返回，但权威绑定回执未确认；未写入密钥，请刷新核对。'};
  }
  if (!wantsSecret) return {profile};
  const expected = {profile_name: binding.profile_name, secret: binding.secret,
    revision: binding.revision};
  const secret = await engineSecret(expected.secret, typed, expected);
  return {profile, secret};
}
