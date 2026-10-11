// 历史映射：后端 /content 的「消息 ID + LangChain 官方序列化 JSON」列表转前端 Message。
// 存储结构为 message_to_dict 的单条结果：{ type: "human"|"ai"|"system"|"tool", data: {...} }，
// 正文在 data.content，AI 的工具调用在 data.tool_calls（args 已是对象），
// 思考内容在 data.additional_kwargs.reasoning_content，
// 工具结果在 data.tool_call_id / data.name / data.content，1:1 映射，不合并。
// Message.id 直接用后端消息 ID（字符串化），供文件卡片锚点等场景前后端对齐。
import type { Message } from "@langchain/langgraph-sdk";
import type { ChatMessageVO } from "./eka-api";

/** LangChain 序列化消息：外层 type + data 负载 */
interface SerializedMessage {
  type?: string;
  data?: {
    content?: unknown;
    additional_kwargs?: { reasoning_content?: string };
    tool_calls?: { id?: string; name?: string; args?: unknown }[];
    tool_call_id?: string;
    name?: string;
  };
}

/** args 字符串转对象：解析失败回落空对象，不阻断渲染（流式工具请求的 arguments 为字符串） */
export function parseArgs(args: string | undefined): Record<string, unknown> {
  if (!args) return {};
  try {
    return JSON.parse(args);
  } catch {
    return { _raw: args };
  }
}

/** 把 LangChain 消息内容统一成纯文本：字符串直接返回，内容块数组取其中的 text */
function contentToText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object" && "text" in block) {
          return String((block as { text?: unknown }).text ?? "");
        }
        return "";
      })
      .join("");
  }
  return "";
}

/** 后端消息列表转 Message 数组：system 忽略，Message.id 取后端消息 ID */
export function toMessages(rows: ChatMessageVO[]): Message[] {
  if (!Array.isArray(rows)) return [];
  const messages: Message[] = [];
  for (const row of rows) {
    let raw: SerializedMessage;
    try {
      raw = JSON.parse(row.message) as SerializedMessage;
    } catch {
      continue;
    }
    // 消息稳定标识：用后端自增 ID 字符串化，保证与文件卡片 anchorMessageId 可对齐
    const id = String(row.id);
    const data = raw.data ?? {};
    if (raw.type === "human") {
      // 用户提问：正文可能为字符串或内容块数组
      messages.push({ id, type: "human", content: contentToText(data.content) } as Message);
    } else if (raw.type === "ai") {
      // AI 回复：正文 + 工具调用（含纯工具调用轮，正文可能为空；args 已是对象，无需再解析），
      // 思考内容从 additional_kwargs.reasoning_content 读回，刷新历史后仍能内联展示
      messages.push({
        id,
        type: "ai",
        content: contentToText(data.content),
        tool_calls: (data.tool_calls ?? []).map((call) => ({
          id: call.id ?? "",
          name: call.name ?? "",
          args: (call.args ?? {}) as Record<string, unknown>,
        })),
        additional_kwargs: { reasoning_content: data.additional_kwargs?.reasoning_content ?? "" },
      } as unknown as Message);
    } else if (raw.type === "tool") {
      // 工具结果：按调用 ID 配对展示
      messages.push({
        id,
        type: "tool",
        tool_call_id: data.tool_call_id ?? "",
        name: data.name ?? "",
        content: contentToText(data.content),
      } as unknown as Message);
    }
  }
  return messages;
}
