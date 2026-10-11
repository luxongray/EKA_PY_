// EKA 数据 provider：替换原 StreamProvider（LangGraph SDK），对接自研后端。
// 对外接口刻意贴近 thread/index 的用法：messages/isLoading/error/submit/stop，
// 另有 regenerate（重发最后一条 human）、resend（编辑重发），
// getMessagesMetadata 恒返回 undefined（无分支语义，BranchSwitcher 对空返回 null），
// setBranch 空函数，values.ui 空数组，中断恒无（agent-inbox 永不触发）。
import type { Message, Thread } from "@langchain/langgraph-sdk";
import type { UIMessage } from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { v4 as uuidv4 } from "uuid";
import {
  FILE_KIND,
  generateTitle,
  getSessionContent,
  listFiles,
  streamChat,
  uploadFiles,
  type ChatFileVO,
} from "@/lib/eka-api";
import type { CompactionState } from "@/lib/eka-types";
import { parseArgs, toMessages } from "@/lib/eka-history";
import { useThreads } from "@/providers/Thread";
import { getContentString } from "@/components/thread/utils";

/**
 * 空闲超时毫秒数：自上一次收到事件起算，超时仍无任何事件才主动中止并提示。
 * 不能按"整个请求"计时——上下文压缩可能持续数分钟且期间没有文本输出
 */
const REQUEST_TIMEOUT_MS = 120_000;

/** 扩展名到文件格式编码的映射：上传占位卡片按扩展名给出格式，与后端 FileFormat 编码一致 */
const FORMAT_BY_EXT: Record<string, number> = {
  docx: 1,
  xlsx: 2,
  pdf: 3,
  html: 4,
};

interface EkaStreamValue {
  messages: Message[];
  /** 当前会话的文件列表（上传 + 生成），消息卡片按 anchorMessageId 归组渲染 */
  files: ChatFileVO[];
  /**
   * 本轮随消息上传的文件：文件锚点要等后端轮次收尾才回填，在此之前卡片先挂在
   * 乐观的 human 消息（anchorId 为本地 uuid）下，保证发送后立刻可见
   */
  roundFiles: { anchorId: string; files: ChatFileVO[] } | null;
  isLoading: boolean;
  error: Error | null;
  /** 本轮上下文压缩状态（null 表示未压缩），供消息区展示压缩进度提示 */
  compaction: CompactionState | null;
  /** 发送消息：attachFiles 为本轮随消息上传的附件（先上传换 ID，再随消息发给后端） */
  submit: (text: string, attachFiles?: File[]) => Promise<void>;
  regenerate: () => Promise<void>;
  resend: (text: string, dropAfterId: string) => Promise<void>;
  stop: () => void;
  getMessagesMetadata: (message?: unknown) => undefined;
  setBranch: (branch?: string) => void;
  values: { ui: UIMessage[] };
}

const EkaContext = createContext<EkaStreamValue | undefined>(undefined);

/** 会话伪装 Thread（标题取首条消息内容）：ThreadHistory 只读这两个字段。id 可为真实 memoryId 或临时占位 */
function toThread(memoryId: number | string, title: string): Thread {
  return {
    thread_id: String(memoryId),
    values: { messages: [{ content: title }] },
  } as unknown as Thread;
}

export function EkaProvider({ children }: { children: ReactNode }) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { setThreads } = useThreads();
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<ChatFileVO[]>([]);
  // 本轮上传文件的临时锚定：重拉拿到后端真实锚点（或切换会话）后清空
  const [roundFiles, setRoundFiles] = useState<{
    anchorId: string;
    files: ChatFileVO[];
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  // 本轮压缩状态：后端只在真正触发压缩时才推事件，未触发则始终保持 null
  const [compaction, setCompaction] = useState<CompactionState | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 主动发送时刚设置 threadId，跳过其触发的历史拉取，避免覆盖乐观显示的 human/AI
  const skipLoadRef = useRef(false);
  // 请求超时兜底：超时主动中止流并置标志，让 catch 把超时升为报错（区别于用户点取消的静默中止）
  const requestTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timedOutRef = useRef(false);

  // 会话切换：有 ID 拉后端历史与文件列表，无 ID 清空
  useEffect(() => {
    if (!threadId) {
      setMessages([]);
      setFiles([]);
      setError(null);
      setCompaction(null);
      setRoundFiles(null);
      return;
    }
    let cancelled = false;
    if (skipLoadRef.current) {
      skipLoadRef.current = false;
      return;
    }
    // 真正切换会话（非本轮主动建会话）时，丢弃上一轮残留的临时文件锚定
    setRoundFiles(null);
    Promise.all([getSessionContent(threadId), listFiles(threadId)])
      .then(([rows, fileList]) => {
        if (!cancelled) {
          setMessages(toMessages(rows));
          setFiles(fileList);
          // 历史消息里不含压缩标记（压缩是过程状态），切会话时一并清掉
          setCompaction(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e : new Error("加载历史失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  // 组件卸载时中止进行中的流，避免请求悬挂泄漏（切换会话/刷新会先走这里）
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (requestTimeoutRef.current) clearTimeout(requestTimeoutRef.current);
    };
  }, []);

  /** 核心发送：建会话（如无）→ 上传附件 → 乐观 human → SSE 累 ai → 按需重拉历史与文件 */
  const sendCore = useCallback(
    async (text: string, attachFiles?: File[], dropAfterId?: string) => {
      const content = text.trim();
      if (!content || isLoading) return;
      setIsLoading(true);
      setError(null);
      setCompaction(null);
      abortRef.current = new AbortController();
      // 空闲超时：每次收到事件（文本/工具/压缩）都重置计时，长时间完全无事件才判超时。
      // 压缩阶段单独暂停计时——该阶段本就长时间没有文本输出，计入会被误杀
      const armRequestTimeout = () => {
        if (requestTimeoutRef.current) clearTimeout(requestTimeoutRef.current);
        requestTimeoutRef.current = setTimeout(() => {
          timedOutRef.current = true;
          abortRef.current?.abort();
        }, REQUEST_TIMEOUT_MS);
      };
      const pauseRequestTimeout = () => {
        if (requestTimeoutRef.current) clearTimeout(requestTimeoutRef.current);
        requestTimeoutRef.current = null;
      };
      timedOutRef.current = false;
      armRequestTimeout();
      // 步骤 1：先乐观入列 human，UI 立即显示用户消息，不等建会话/取名
      const human: Message = { id: uuidv4(), type: "human", content } as Message;
      setMessages((prev) => {
        // 编辑场景：被改那条及之后全丢，换上新文本（后端 history 追加一轮，语义差异可接受）
        const idx = dropAfterId ? prev.findIndex((m) => m.id === dropAfterId) : -1;
        const kept = idx >= 0 ? prev.slice(0, idx) : prev;
        return [...kept, human];
      });
      // 步骤 2：先摆上占位文件卡片（同步、在任何 await 之前），否则输入框清空后
      // 到上传请求返回之间会出现"卡片突然消失"的一帧；id 为负仅作列表 key，
      // 源文件本就不可预览/下载，不会误发请求
      if (attachFiles && attachFiles.length > 0) {
        setRoundFiles({
          anchorId: String(human.id),
          files: attachFiles.map((f, i) => ({
            id: -i - 1,
            fileName: f.name,
            kind: FILE_KIND.SOURCE,
            format: FORMAT_BY_EXT[f.name.split(".").pop()?.toLowerCase() ?? ""] ?? 0,
            fileSize: f.size,
          })),
        });
      }
      // 步骤 3：按 SSE 事件到达顺序切段：一条 ai 段 = 模型一个回合（该轮文字 + 该轮工具调用），
      // 让“文字→工具→文字→工具…”按时间线交替呈现，与历史还原 toMessages 的结构一致。
      // 工具结果以独立 tool 消息追加，收到结果即结算当前回合，下一条文字/工具另起新段
      let curAiId: string | null = null;
      // 结果帧按 callId 幂等：同一结果重复推送时只追加一条 tool 消息，避免配对出两条重复的返回结果
      // （刷新走 toMessages 读后端真相则正常）
      const seenToolResults = new Set<string>();
      try {
        // 步骤 4：无会话先建行拿 ID（标题 AI 生成）。建行期间左侧列表先显示"新会话"，取名成功再替换真实标题
        let id = threadId;
        if (!id) {
          const tempThreadId = `temp-${uuidv4()}`;
          setThreads((prev) => [toThread(tempThreadId, "新会话"), ...prev]);
          const created = await generateTitle(content);
          id = String(created.memoryId);
          // 标记：跳过刚设置 threadId 触发的历史拉取，避免覆盖乐观显示的 human/AI
          skipLoadRef.current = true;
          setThreadId(id);
          setThreads((prev) =>
            prev.map((t) =>
              t.thread_id === tempThreadId ? toThread(created.memoryId, created.title) : t,
            ),
          );
        }
        const finalId = id;
        // 步骤 5：上传本轮附件换文件 ID，随消息一起发给后端（后端据此把卡片锚到本轮用户消息）
        let fileIds: number[] = [];
        if (attachFiles && attachFiles.length > 0) {
          const uploaded = await uploadFiles(finalId, attachFiles);
          fileIds = uploaded.map((f) => f.id);
          // 上传成功后把占位卡片换成带真实 ID 的卡片：文件锚点要等后端轮次收尾才回填，
          // 不这样做的话整轮回答期间卡片都不可见
          setRoundFiles({ anchorId: String(human.id), files: uploaded });
        }
        // 步骤 5：无活动回合时先开一条 ai 段并设为当前回合，返回其 id（content/tool_calls/思考初值空）
        const openAiSegment = () => {
          const segmentId = uuidv4();
          setMessages((prev) => [
            ...prev,
            {
              id: segmentId,
              type: "ai",
              content: "",
              tool_calls: [],
              additional_kwargs: { reasoning_content: "" },
            } as unknown as Message,
          ]);
          curAiId = segmentId;
          return segmentId;
        };
        // 步骤 6：文本增量累加进当前回合段（同一回合的连续 token 持续追加到该段 content）
        const appendText = (t: string) => {
          const target = curAiId ?? openAiSegment();
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== target) return m;
              // 不可变更新：返回新对象，避免 React StrictMode 双调用 updater 时对共享引用做变更
              return { ...(m as object), content: m.content + t } as Message;
            }),
          );
        };
        // 步骤 6.1：思考增量累加进当前回合段的 additional_kwargs.reasoning_content（内联淡色展示）
        const appendReasoning = (t: string) => {
          const target = curAiId ?? openAiSegment();
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== target) return m;
              const typed = m as Message & { additional_kwargs?: Record<string, unknown> };
              const kwargs = typed.additional_kwargs ?? {};
              const prevText =
                typeof kwargs.reasoning_content === "string" ? kwargs.reasoning_content : "";
              return {
                ...(m as object),
                additional_kwargs: { ...kwargs, reasoning_content: prevText + t },
              } as unknown as Message;
            }),
          );
        };
        // 步骤 7：工具调用归属当前回合段（模型先吐文字再请求工具，故并入同一条 ai 段；纯工具轮自动开段）。
        // 同一 callId 会先后收到两条请求帧——参数生成刚开始时一条（让卡片立即出现并转圈）、
        // 工具执行前再一条（带完整参数）——这里按 callId 就地更新参数，而不是重复建卡
        const appendTool = (d: { callId: string; toolName: string; args: string }) => {
          const target = curAiId ?? openAiSegment();
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== target) return m;
              const typed = m as Message & { tool_calls?: { id: string; name: string; args: unknown }[] };
              // 不可变更新：tool_calls 用新数组，绝不 push 共享引用（否则 StrictMode 双调用会把同一工具翻倍）
              const calls = typed.tool_calls ?? [];
              const idx = d.callId ? calls.findIndex((c) => c.id === d.callId) : -1;
              const nextCalls =
                idx >= 0
                  ? calls.map((c, i) =>
                      i === idx
                        ? { ...c, name: d.toolName || c.name, args: parseArgs(d.args) }
                        : c,
                    )
                  : [...calls, { id: d.callId, name: d.toolName, args: parseArgs(d.args) }];
              return { ...(m as object), tool_calls: nextCalls } as Message;
            }),
          );
        };
        // 本轮是否改动了文件：仅涉及文件的轮次才需重拉（普通对话保持乐观消息，避免整列重渲染）
        let usedFileTool = false;
        await streamChat(
          finalId,
          content,
          {
            onText: (t) => {
              armRequestTimeout();
              appendText(t);
            },
            onThinking: (t) => {
              // 思考增量用独立回调处理：兜底模型不产出思考时该回调不会触发
              armRequestTimeout();
              appendReasoning(t);
            },
            onToolRequest: (d) => {
              armRequestTimeout();
              // lc4j 以方法名注册工具，实际送达的是驼峰名（writeFile/editFile），
              // 同时兼容下划线写法，避免工具命名风格调整后这里静默失效、文件卡片挂不上
              if (/^(writeFile|editFile|write_file|edit_file)$/.test(d.toolName)) usedFileTool = true;
              appendTool(d);
            },
            onToolResult: (d) => {
              armRequestTimeout();
              // 同一 callId 的结果帧只认一次，避免追加两条重复 tool 消息
              if (d.callId) {
                if (seenToolResults.has(d.callId)) return;
                seenToolResults.add(d.callId);
              }
              // 工具结果以独立 tool 消息追加（供工具卡按 callId 配对显示），并结算当前回合
              curAiId = null;
              setMessages((prev) => [
                ...prev,
                {
                  id: uuidv4(),
                  type: "tool",
                  tool_call_id: d.callId,
                  name: d.toolName,
                  content: d.result,
                } as unknown as Message,
              ]);
            },
            onCompactionStart: () => {
              // 压缩期间没有文本输出，暂停空闲超时，待压缩出结果后再恢复计时
              pauseRequestTimeout();
              setCompaction({ phase: "compacting" });
            },
            onCompactionDone: (d) => {
              armRequestTimeout();
              setCompaction({
                phase: "done",
                compactedMessages: d.compactedMessages,
                summaryTokens: d.summaryTokens,
              });
            },
            onCompactionError: (msg) => {
              armRequestTimeout();
              setCompaction({ phase: "error", message: msg });
            },
            onDone: () => {},
            onError: (msg) => {
              throw new Error(msg);
            },
          },
          abortRef.current.signal,
          fileIds,
        );
        // 步骤 8：本轮涉及文件时重拉历史与文件列表——消息 ID 换成后端真实 ID，
        // 文件卡片才能按 anchorMessageId 归组；普通对话不重拉，避免消息整列重渲染
        if (fileIds.length > 0 || usedFileTool) {
          try {
            const [rows, fileList] = await Promise.all([
              getSessionContent(finalId),
              listFiles(finalId),
            ]);
            setMessages(toMessages(rows));
            setFiles(fileList);
            // 后端真实锚点已随文件列表拿到，乐观锚定完成使命
            setRoundFiles(null);
          } catch {
            // 重拉失败不影响本轮展示，保留乐观消息
          }
        }
      } catch (e) {
        // 超时主动中止升为报错；用户点取消的 AbortError 静默忽略；其余正常抛错
        if (timedOutRef.current) {
          setError(new Error("请求超时，请稍后重试"));
        } else if ((e as Error).name !== "AbortError") {
          setError(e instanceof Error ? e : new Error("请求失败"));
        }
      } finally {
        if (requestTimeoutRef.current) clearTimeout(requestTimeoutRef.current);
        setIsLoading(false);
        abortRef.current = null;
      }
    },
    [isLoading, threadId, setThreadId, setThreads],
  );

  const submit = useCallback(
    (text: string, attachFiles?: File[]) => sendCore(text, attachFiles),
    [sendCore],
  );

  /** 重生成：重发最后一条 human（EKA 无 checkpoint 语义） */
  const regenerate = useCallback(async () => {
    const lastHuman = [...messages].reverse().find((m) => m.type === "human");
    if (!lastHuman) return;
    await sendCore(getContentString(lastHuman.content));
  }, [messages, sendCore]);

  /** 编辑重发：截断到被改那条（含）之后全丢，以新文本重发 */
  const resend = useCallback(
    (text: string, dropAfterId: string) => sendCore(text, undefined, dropAfterId),
    [sendCore],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const value: EkaStreamValue = {
    messages,
    files,
    roundFiles,
    isLoading,
    error,
    compaction,
    submit,
    regenerate,
    resend,
    stop,
    getMessagesMetadata: () => undefined,
    setBranch: () => {},
    values: { ui: [] },
  };

  return <EkaContext.Provider value={value}>{children}</EkaContext.Provider>;
}

export function useStreamContext(): EkaStreamValue {
  const context = useContext(EkaContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a EkaProvider");
  }
  return context;
}
