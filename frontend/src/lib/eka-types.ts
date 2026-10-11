// EKA SSE 事件载荷：与后端 AgentEvent 各事件的 data JSON 对齐
export interface ToolRequestData {
  callId: string;
  toolName: string;
  args: string;
}

export interface ToolResultData {
  callId: string;
  toolName: string;
  args: string;
  result: string;
}

/** 上下文压缩完成事件载荷 */
export interface CompactionDoneData {
  /** 本次被压成摘要的早期消息条数 */
  compactedMessages: number;
  /** 新摘要占用的 token 数 */
  summaryTokens: number;
}

/**
 * 上下文压缩的前端展示状态：null 表示本轮没有发生压缩。
 * 压缩发生在每轮回答之前，阶段依次为 compacting -> done / error
 */
export type CompactionState =
  | { phase: "compacting" }
  | ({ phase: "done" } & CompactionDoneData)
  | { phase: "error"; message: string };
