// EKA 自研后端 API：会话记忆 6 接口 + Agent SSE 流式问答。
// 后端直连（NEXT_PUBLIC_API_URL），Result 包裹 code 写死 200/500。
import type {
  CompactionDoneData,
  ToolRequestData,
  ToolResultData,
} from "./eka-types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

/** 解 Result 包裹：非 200 抛业务错 */
async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`请求失败：${resp.status}`);
  const body = await resp.json();
  if (body?.code !== 200) throw new Error(body?.message || "请求失败");
  return body.data as T;
}

/** 新建会话：传首问，后端自增 ID + AI 生成标题 */
export function generateTitle(firstMessage: string): Promise<{
  memoryId: number;
  title: string;
}> {
  return fetch(
    `${BASE}/chat-memory/title/generate?firstMessage=${encodeURIComponent(firstMessage)}`,
    { method: "POST" },
  ).then((r) => unwrap(r));
}

/** 分页查会话：按修改时间降序，不带消息内容 */
export function pageSessions(
  pageNum: number,
  pageSize: number,
): Promise<{
  records: { id: number; title: string; updatedAt?: string }[];
  total: number;
}> {
  return fetch(
    `${BASE}/chat-memory/page?pageNum=${pageNum}&pageSize=${pageSize}`,
  ).then((r) => unwrap(r));
}

/** 改名：只更新标题列 */
export function renameSession(
  memoryId: string,
  title: string,
): Promise<boolean> {
  return fetch(
    `${BASE}/chat-memory/title?memoryId=${memoryId}&title=${encodeURIComponent(title)}`,
    { method: "PUT" },
  ).then((r) => unwrap(r));
}

/** 批量删会话：逻辑删，body 传 ID 数组 */
export function batchDeleteSessions(memoryIds: number[]): Promise<boolean> {
  return fetch(`${BASE}/chat-memory/batch`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(memoryIds),
  }).then((r) => unwrap(r));
}

/** 会话消息视图：消息 ID + 单条 LangChain 官方序列化消息 JSON */
export interface ChatMessageVO {
  id: number;
  message: string;
}

/** 查单会话内容：消息 ID 与正文一起返回，前端用真实 ID 作消息稳定标识 */
export function getSessionContent(memoryId: string): Promise<ChatMessageVO[]> {
  return fetch(`${BASE}/chat-memory/content?memoryId=${memoryId}`).then((r) =>
    unwrap<ChatMessageVO[]>(r),
  );
}

/** 文件来源编码：与后端 FileKind 枚举一一对应 */
export const FILE_KIND = { SOURCE: 1, GENERATED: 2 } as const;

/** 文件格式编码：与后端 FileFormat 枚举一一对应 */
export const FILE_FORMAT = { DOCX: 1, XLSX: 2, PDF: 3, HTML: 4 } as const;

/** 会话文件元数据：与后端 ChatFileVO 一一对应 */
export interface ChatFileVO {
  id: number;
  fileName: string;
  /** 来源：1 用户上传 / 2 AI 生成（对应 FILE_KIND） */
  kind: number;
  /** 格式：1 docx / 2 xlsx / 3 pdf / 4 html（对应 FILE_FORMAT） */
  format: number;
  description?: string;
  fileSize?: number;
  /** 锚定的消息 ID，前端据此把卡片挂到对应消息下 */
  anchorMessageId?: number | null;
  /** 最后修改时间（卡片展示此时间） */
  updatedAt?: string;
}

/**
 * 该文件对用户是否呈现为 PDF：AI 生成的产物内部实为 HTML 源（edit_file 依赖它做修改），
 * 但用户拿到的、看到的都应当是 PDF 文档，磁盘格式不对用户暴露
 */
export function presentedAsPdf(file: ChatFileVO): boolean {
  return file.kind === FILE_KIND.GENERATED;
}

/** 用户可见的文件名：生成产物把 .html 后缀换成 .pdf，上传的源文件原样返回 */
export function displayFileName(file: ChatFileVO): string {
  if (!presentedAsPdf(file)) return file.fileName;
  return file.fileName.replace(/\.html?$/i, "") + ".pdf";
}

/** 上传会话源文件：FormData 传 memoryId + files（单次最多 5 个） */
export function uploadFiles(
  memoryId: string,
  files: File[],
): Promise<ChatFileVO[]> {
  const form = new FormData();
  form.append("memoryId", memoryId);
  for (const f of files) form.append("files", f);
  return fetch(`${BASE}/file/upload`, { method: "POST", body: form }).then((r) =>
    unwrap<ChatFileVO[]>(r),
  );
}

/** 查会话文件列表：含上传与生成，前端按 anchorMessageId 归组渲染 */
export function listFiles(memoryId: string): Promise<ChatFileVO[]> {
  return fetch(`${BASE}/file/list?memoryId=${memoryId}`).then((r) =>
    unwrap<ChatFileVO[]>(r),
  );
}

/** 预览地址：iframe 直接加载（生成文件为 HTML） */
export function previewUrl(fileId: number): string {
  return `${BASE}/file/preview?fileId=${fileId}`;
}

/**
 * 导出 PDF：由后端把生成的 HTML 渲染成 PDF 后以字节流返回，前端拿到即触发浏览器下载。
 * 渲染在后端内存中完成、不落盘；传入 fileName 作为下载文件名
 * （后端 Content-Disposition 也带了同样的业务名）
 */
export async function exportHtmlAsPdf(
  fileId: number,
  fileName?: string,
): Promise<void> {
  const resp = await fetch(`${BASE}/file/export?fileId=${fileId}`);
  if (!resp.ok) {
    // 失败时后端返回的是统一 Result 包裹的 JSON，优先取里面的中文提示，取不到再退回状态码
    let message = `导出失败：${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.message) message = body.message;
    } catch {
      // 响应不是 JSON，保留状态码提示
    }
    throw new Error(message);
  }

  // 以 Blob 下载：创建临时对象地址挂到隐藏链接上点一下，随即回收，不产生中间页面
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName ?? "导出文档.pdf";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * 流式回调：思考增量 / 文本增量 / 工具请求 / 工具结果 / 上下文压缩 / 结束 / 异常。
 * 思考回调可选：兜底模型不产出思考内容时不会触发。
 * 压缩三个回调只在后端真正触发压缩时才会被调用——用量未超触发线时后端不发任何压缩事件
 */
export interface StreamHandlers {
  onText: (text: string) => void;
  /** 模型思考增量（DeepSeek reasoning_content）：可选，未实现或模型不产出时忽略 */
  onThinking?: (text: string) => void;
  onToolRequest: (data: ToolRequestData) => void;
  onToolResult: (data: ToolResultData) => void;
  /** 压缩回调设为可选：调用方或热更新残留的旧模块未实现时，不能让整个流式解析崩掉 */
  onCompactionStart?: () => void;
  onCompactionDone?: (data: CompactionDoneData) => void;
  onCompactionError?: (message: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

/**
 * 一轮流式问答：POST 建流，事件格式为 event:NAME + data:JSON（data 单行 JSON）。
 * 浏览器 EventSource 不支持 POST body，故用 fetch 流手动切块。
 */
export async function streamChat(
  memoryId: string,
  message: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  fileIds?: number[],
): Promise<void> {
  // 步骤 1：POST 建流，建流失败（400 等）直接抛错，不进事件
  const resp = await fetch(`${BASE}/agent/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ message, memoryId: Number(memoryId), fileIds }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`建流失败：${resp.status}`);
  }

  // 步骤 2：逐块读流，攒 buffer 按空行切事件块
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf("\n\n");
    while (idx >= 0) {
      await dispatch(buffer.slice(0, idx), handlers);
      buffer = buffer.slice(idx + 2);
      idx = buffer.indexOf("\n\n");
    }
  }
  // 步骤 3：收尾残块（服务端正常关闭一般无残留）
  if (buffer.trim()) await dispatch(buffer, handlers);
}

/** 解析单个事件块并分发：只认 event: / data: 两行，data 为单行 JSON */
async function dispatch(chunk: string, handlers: StreamHandlers): Promise<void> {
  let name = "";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!name || !data) return;
  let payload: Record<string, string> = {};
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  switch (name) {
    case "THINKING":
      handlers.onThinking?.(payload.text ?? "");
      break;
    case "STREAM_TEXT":
      handlers.onText(payload.text ?? "");
      break;
    case "TOOL_CALL_REQUEST": {
      handlers.onToolRequest({
        callId: payload.callId ?? "",
        toolName: payload.toolName ?? "",
        args: payload.arguments ?? "",
      });
      // 让出一次宏任务：工具执行很快时，请求与结果两个事件常在同一批 chunk 里，
      // 同步连续处理会被 React 合并成一次渲染，「运行中」的转圈就根本没机会显示
      await new Promise((resolve) => setTimeout(resolve, 0));
      break;
    }
    case "TOOL_CALL_RESULT":
      handlers.onToolResult({
        callId: payload.callId ?? "",
        toolName: payload.toolName ?? "",
        args: payload.arguments ?? "",
        result: payload.resultSummary ?? "",
      });
      break;
    case "COMPACTION_START":
      handlers.onCompactionStart?.();
      break;
    case "COMPACTION_DONE":
      handlers.onCompactionDone?.({
        compactedMessages: Number(payload.compactedMessages ?? 0),
        summaryTokens: Number(payload.summaryTokens ?? 0),
      });
      break;
    case "COMPACTION_ERROR":
      handlers.onCompactionError?.(payload.message ?? "上下文压缩失败");
      break;
    case "DONE":
      handlers.onDone();
      break;
    case "ERROR":
      handlers.onError(payload.message ?? "未知异常");
      break;
    default:
      break;
  }
}
