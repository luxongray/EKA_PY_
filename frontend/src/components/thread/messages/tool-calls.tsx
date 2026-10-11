import { AIMessage, ToolMessage } from "@langchain/langgraph-sdk";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp, LoaderCircle } from "lucide-react";
import { useStreamContext } from "@/providers/Eka";
import { cn } from "@/lib/utils";

function isComplexValue(value: any): boolean {
  return Array.isArray(value) || (typeof value === "object" && value !== null);
}

/** 把 tool 消息的 content 归一成字符串（可能有 string / 多段数组 / JSON 对象） */
function contentToText(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((c) => (isComplexValue(c) && c && (c as any).text ? (c as any).text : JSON.stringify(c)))
      .join("");
  }
  return JSON.stringify(content);
}

/** 结果文本里出现这些关键字视为调用失败，整条 tool call 标红 */
const ERROR_RE = /Exception|Error|失败|拒绝|denied|refused|unavailable|Unresolved/i;

/** 单个工具调用块：默认折叠只露工具名，点开显示入参与返回结果，失败标红 */
function ToolCallBlock({ call }: { call: NonNullable<AIMessage["tool_calls"]>[number] }) {
  const [open, setOpen] = useState(false);
  const { messages, isLoading } = useStreamContext();
  const resultMsg = messages.find(
    (m) => m.type === "tool" && m.tool_call_id === call.id,
  ) as ToolMessage | undefined;
  const resultText = resultMsg ? contentToText(resultMsg.content) : "";
  // 还在跑：整体仍在流式、且这次调用尚未拿到结果；停止后无结果的不再转圈
  const running = !resultMsg && isLoading;
  const isError = !!resultText && ERROR_RE.test(resultText);
  const args = call.args as Record<string, any>;
  const entries = Object.entries(args);

  // 折叠态头部摘要：参数以 key=value 内联，太长单行省略
  const summary = entries
    .map(([key, value]) => `${key}=${isComplexValue(value) ? JSON.stringify(value) : String(value)}`)
    .join("  ");

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border",
        isError ? "border-red-300" : "border-gray-200",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center justify-between gap-2 px-4 py-2 text-left",
          isError ? "bg-red-50" : "bg-gray-50",
          !open && "border-b border-gray-200",
        )}
      >
        <span className="flex min-w-0 items-center gap-2">
          {/* 运行状态位：执行中转圈，已结束留同尺寸占位，保证各工具块左侧对齐 */}
          {running ? (
            <LoaderCircle className="size-4 shrink-0 animate-spin text-blue-500" />
          ) : (
            <span className="inline-block size-4 shrink-0" />
          )}
          <span
            className={cn(
              "shrink-0 font-mono font-medium",
              isError ? "text-red-600" : "text-gray-900",
            )}
          >
            {call.name}
          </span>
          {summary && (
            <span className="truncate font-mono text-sm text-gray-500">{summary}</span>
          )}
          {isError && <span className="shrink-0 text-xs text-red-500">调用失败</span>}
        </span>
        <span className="shrink-0 text-gray-400">
          {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            {entries.length > 0 ? (
              <div className="max-h-64 space-y-1.5 overflow-y-auto px-4 py-3 font-mono text-sm">
                {entries.map(([key, value], argIdx) => (
                  <div
                    key={argIdx}
                    className="flex gap-2"
                  >
                    {/* 参数尚在流式生成时 JSON 不完整，parseArgs 会回落成 _raw，此处只展示原文不露占位键名 */}
                    {key !== "_raw" && (
                      <span className="shrink-0 text-gray-500">{key}</span>
                    )}
                    <span className="min-w-0 break-all text-gray-800">
                      {isComplexValue(value) ? JSON.stringify(value) : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            ) : running ? (
              // 运行中但参数还没到（或为空）：给个占位，避免展开是空白像点不开
              <div className="px-4 py-3 font-mono text-sm text-gray-400">参数生成中…</div>
            ) : null}
            {resultText && (
              <div
                className={cn(
                  "border-t px-4 py-3 font-mono text-sm",
                  isError
                    ? "border-red-200 text-red-700"
                    : "border-gray-200 text-gray-700",
                )}
              >
                <div className="mb-1 text-xs text-gray-400">
                  {isError ? "调用失败" : "返回结果"}
                </div>
                {/* 结果过长时内部滚动查看，不再截断或二次展开 */}
                <div className="max-h-64 overflow-y-auto whitespace-pre-wrap break-all">
                  {resultText}
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ToolCalls({
  toolCalls,
}: {
  toolCalls: AIMessage["tool_calls"];
}) {
  if (!toolCalls || toolCalls.length === 0) return null;

  return (
    <div className="mx-auto grid w-full max-w-3xl grid-cols-[1fr] gap-2">
      {toolCalls.map((tc, idx) => (
        <ToolCallBlock
          key={`${tc.id ?? "tc"}-${idx}`}
          call={tc}
        />
      ))}
    </div>
  );
}
