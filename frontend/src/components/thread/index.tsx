import { Fragment, ReactNode, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useStreamContext } from "@/providers/Eka";
import { useState, FormEvent } from "react";
import { Button } from "../ui/button";
import { AssistantMessage, AssistantMessageLoading } from "./messages/ai";
import { HumanMessage } from "./messages/human";
import { CompactionIndicator } from "./compaction-indicator";
import { FileCards, fileIconByName } from "./file-cards";
import { FilePreviewSheet } from "./file-preview-sheet";
import { SessionFilesSheet } from "./session-files-sheet";
import { getContentString } from "./utils";
import { DO_NOT_RENDER_ID_PREFIX } from "@/lib/ensure-tool-responses";
import type { ChatFileVO } from "@/lib/eka-api";

import {
  ArrowDown,
  FolderOpen,
  LoaderCircle,
  PanelRightOpen,
  PanelRightClose,
  Paperclip,
  XIcon,
} from "lucide-react";
import { useQueryState, parseAsBoolean } from "nuqs";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";
import ThreadHistory from "./history";
import { toast } from "sonner";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
  useArtifactOpen,
  ArtifactContent,
  ArtifactTitle,
} from "./artifact";

/** 附件约束：单次最多上传文件数（前端先拦，后端再校验一次） */
const MAX_UPLOAD_FILES = 5;

/** 允许上传的源文件扩展名 */
const ALLOWED_UPLOAD_EXT = ["docx", "xlsx", "pdf"];

/** 单文件大小上限：与后端 multipart 的 max-file-size 保持一致 */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

function StickyToBottomContent(props: {
  content: ReactNode;
  footer?: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  const context = useStickToBottomContext();
  return (
    <div
      ref={context.scrollRef}
      style={{ width: "100%", height: "100%" }}
      className={props.className}
    >
      <div
        ref={context.contentRef}
        className={props.contentClassName}
      >
        {props.content}
      </div>

      {props.footer}
    </div>
  );
}

function ScrollToBottom(props: { className?: string }) {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();

  if (isAtBottom) return null;
  return (
    <Button
      variant="outline"
      className={props.className}
      onClick={() => scrollToBottom()}
    >
      <ArrowDown className="h-4 w-4" />
      <span>回到底部</span>
    </Button>
  );
}

export function Thread() {
  const [artifactOpen, closeArtifact] = useArtifactOpen();

  const [threadId, _setThreadId] = useQueryState("threadId");
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );
  const [input, setInput] = useState("");
  const [firstTokenReceived, setFirstTokenReceived] = useState(false);
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");

  // 待发送附件：发送前只在前端暂存，随消息一起上传换文件 ID
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 预览中的生成文件（null 表示抽屉关闭）
  const [previewFile, setPreviewFile] = useState<ChatFileVO | null>(null);
  // 会话级文件面板开关
  const [filesOpen, setFilesOpen] = useState(false);

  const stream = useStreamContext();
  const messages = stream.messages;
  const files = stream.files;
  const roundFiles = stream.roundFiles;
  const isLoading = stream.isLoading;

  // 按锚点消息 ID 归组文件：同一消息下可能有多个文件卡片
  const filesByAnchor = new Map<string, ChatFileVO[]>();
  for (const f of files) {
    if (f.anchorMessageId == null) continue;
    const key = String(f.anchorMessageId);
    const list = filesByAnchor.get(key);
    if (list) list.push(f);
    else filesByAnchor.set(key, [f]);
  }

  const lastError = useRef<string | undefined>(undefined);

  const setThreadId = (id: string | null) => {
    _setThreadId(id);

    // close artifact
    closeArtifact();
  };

  // 切换会话时清空上一个会话的暂存附件与文件面板状态：
  // 否则 A 会话选好的文件会跟着带到 B 会话，且旧会话文件的预览/抽屉残留
  useEffect(() => {
    setPendingFiles([]);
    setPreviewFile(null);
    setFilesOpen(false);
    setDragActive(false);
  }, [threadId]);

  useEffect(() => {
    if (!stream.error) {
      lastError.current = undefined;
      return;
    }
    try {
      const message = (stream.error as any).message;
      if (!message || lastError.current === message) {
        // Message has already been logged. do not modify ref, return early.
        return;
      }

      // Message is defined, and it has not been logged yet. Save it, and send the error
      lastError.current = message;
      toast.error("出错了，请稍后重试", {
        description: message,
        richColors: true,
        closeButton: true,
      });
    } catch {
      // no-op
    }
  }, [stream.error]);

  // 首个真实 AI 文本流出时才置 firstTokenReceived：
  // 空 content 的占位 AI 不算，转圈持续显示直到第一个 token 产出
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (last?.type === "ai" && getContentString(last.content).length > 0) {
      setFirstTokenReceived(true);
    }
  }, [messages]);

  /** 追加待发送附件：过滤非法类型、按名去重并限制数量，超限给出提示 */
  const addFiles = (incoming: FileList | null) => {
    if (!incoming || incoming.length === 0) return;
    // 以当前暂存列表为基准过滤，同时记录真正新增的数量用于成功回执
    const merged = [...pendingFiles];
    let added = 0;
    for (const f of Array.from(incoming)) {
      const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
      if (!ALLOWED_UPLOAD_EXT.includes(ext)) {
        toast.error(`不支持的文件类型：${f.name}`);
        continue;
      }
      if (f.size > MAX_UPLOAD_BYTES) {
        toast.error(`文件过大（超过 20MB）：${f.name}`);
        continue;
      }
      if (merged.some((m) => m.name === f.name)) continue;
      if (merged.length >= MAX_UPLOAD_FILES) {
        toast.error(`单次最多上传 ${MAX_UPLOAD_FILES} 个文件`);
        break;
      }
      merged.push(f);
      added += 1;
    }
    // 一份都没新增就不动列表、不打扰用户；有新增则落盘暂存并回执
    if (added === 0) return;
    setPendingFiles(merged);
    toast.success(`已添加 ${added} 个文件`);
  };

  /** 移除某个待发送附件 */
  const removeFile = (index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  };

  // EKA：文本 + 附件提交，后端自己管 memory，无 artifact 上下文
  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (input.trim().length === 0 || isLoading) return;
    setFirstTokenReceived(false);

    void stream.submit(input, pendingFiles);
    setInput("");
    setPendingFiles([]);
  };

  // EKA：无 checkpoint 语义，重生成=重发最后一条 human（provider 内实现）
  const handleRegenerate = () => {
    // 重新生成时先回到等待态，首个文本流出后再脱离转圈
    setFirstTokenReceived(false);
    void stream.regenerate();
  };

  const chatStarted = !!threadId || !!messages.length;

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <div className="relative hidden lg:flex">
        <motion.div
          className="absolute z-20 h-full overflow-hidden border-r bg-white"
          style={{ width: 260 }}
          animate={
            isLargeScreen
              ? { x: chatHistoryOpen ? 0 : -260 }
              : { x: chatHistoryOpen ? 0 : -260 }
          }
          initial={{ x: -260 }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          <div
            className="relative h-full"
            style={{ width: 260 }}
          >
            <ThreadHistory />
          </div>
        </motion.div>
      </div>

      <div
        className={cn(
          "grid w-full grid-cols-[1fr_0fr] transition-all duration-500",
          artifactOpen && "grid-cols-[3fr_2fr]",
        )}
      >
        <motion.div
          className={cn(
            "relative flex min-w-0 flex-1 flex-col overflow-hidden",
            !chatStarted && "grid-rows-[1fr]",
          )}
          layout={isLargeScreen}
          animate={{
            marginLeft: chatHistoryOpen ? (isLargeScreen ? 260 : 0) : 0,
            width: chatHistoryOpen
              ? isLargeScreen
                ? "calc(100% - 260px)"
                : "100%"
              : "100%",
          }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          {!chatStarted && (
            <div className="absolute top-0 left-0 z-10 flex w-full items-center justify-between gap-3 px-4 pt-1.5">
              <div>
                {(!chatHistoryOpen || !isLargeScreen) && (
                  <Button
                    className="hover:bg-gray-100"
                    variant="ghost"
                    onClick={() => setChatHistoryOpen((p) => !p)}
                  >
                    {chatHistoryOpen ? (
                      <PanelRightOpen className="size-5" />
                    ) : (
                      <PanelRightClose className="size-5" />
                    )}
                  </Button>
                )}
              </div>
            </div>
          )}
          {chatStarted && (
            <div
              className="relative z-10 flex items-center"
              style={{ padding: "6px 16px 8px" }}
            >
              {(!chatHistoryOpen || !isLargeScreen) && (
                <Button
                  className="hover:bg-gray-100"
                  variant="ghost"
                  onClick={() => setChatHistoryOpen((p) => !p)}
                >
                  {chatHistoryOpen ? (
                    <PanelRightOpen className="size-5" />
                  ) : (
                    <PanelRightClose className="size-5" />
                  )}
                </Button>
              )}
              <Button
                className="ml-auto hover:bg-gray-100"
                variant="ghost"
                title="会话文件"
                onClick={() => setFilesOpen(true)}
              >
                <FolderOpen className="size-5" />
                <span>文件{files.length > 0 ? `（${files.length}）` : ""}</span>
              </Button>
              <div
                className="from-background to-background/0 pointer-events-none absolute inset-x-0 bg-gradient-to-b"
                style={{ top: "100%", height: 20 }}
              />
            </div>
          )}

          <StickToBottom className="relative flex-1 overflow-hidden">
            <StickyToBottomContent
              className={cn(
                "absolute inset-0 overflow-y-scroll px-4 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent",
                !chatStarted && "mt-[25vh] flex flex-col items-stretch",
                chatStarted && "grid grid-rows-[1fr_auto]",
              )}
              contentClassName="pt-8 pb-16 max-w-3xl mx-auto flex flex-col gap-4 w-full"
              content={
                <>
                  {messages
                    .filter((m) => !m.id?.startsWith(DO_NOT_RENDER_ID_PREFIX))
                    .map((message, index, visible) => {
                      // 压缩提示紧跟最后一个 human（即本轮回答开始之前）。
                      // 不能渲染在列表末尾——本轮 AI 与工具卡片会陆续追加，提示会被越推越靠下
                      const isLastHuman =
                        message.type === "human" &&
                        !visible
                          .slice(index + 1)
                          .some((m) => m.type === "human");
                      // 文件卡片优先按后端真实锚点归组；本轮刚上传的文件在后端回填锚点之前，
                      // 先挂到乐观 human 消息下，否则整轮回答期间都看不到刚上传的文件
                      const anchored = filesByAnchor.get(String(message.id));
                      const optimistic =
                        message.type === "human" &&
                        roundFiles !== null &&
                        roundFiles.anchorId === message.id
                          ? roundFiles.files
                          : null;
                      const cardFiles = anchored ?? optimistic ?? [];
                      return (
                        <Fragment key={message.id || `${message.type}-${index}`}>
                          {/* human 消息的文件卡片插进气泡内部（紧贴气泡、位于操作按钮上方），
                              AI 消息的卡片则跟在其后；两处都紧贴所属消息，不被列表 gap 推远 */}
                          {message.type === "human" ? (
                            <HumanMessage
                              message={message}
                              isLoading={isLoading}
                              filesSlot={
                                <FileCards
                                  files={cardFiles}
                                  align="right"
                                  onPreview={setPreviewFile}
                                />
                              }
                            />
                          ) : (
                            <div className="flex flex-col gap-2">
                              <AssistantMessage
                                message={message}
                                isLoading={isLoading}
                                handleRegenerate={handleRegenerate}
                              />
                              <FileCards
                                files={cardFiles}
                                align="left"
                                onPreview={setPreviewFile}
                              />
                            </div>
                          )}
                          {isLastHuman && stream.compaction && (
                            <CompactionIndicator state={stream.compaction} />
                          )}
                        </Fragment>
                      );
                    })}
                  {isLoading &&
                    !firstTokenReceived &&
                    stream.compaction?.phase !== "compacting" && (
                      <AssistantMessageLoading />
                    )}
                </>
              }
              footer={
                <div className="sticky bottom-0 flex flex-col items-center gap-8 bg-white">
                  {!chatStarted && (
                    <div className="flex items-center gap-3">
                      <h1 className="text-2xl font-semibold tracking-tight">
                        政务档案利用与分析智能体
                      </h1>
                    </div>
                  )}

                  <ScrollToBottom className="animate-in fade-in-0 zoom-in-95 absolute bottom-full left-1/2 mb-4 -translate-x-1/2" />

                  <div className="bg-muted relative z-10 mx-auto mb-8 w-full max-w-3xl rounded-2xl border border-solid shadow-xs transition-all">
                    <form
                      onSubmit={handleSubmit}
                      onDragOver={(e) => {
                        e.preventDefault();
                        setDragActive(true);
                      }}
                      onDragLeave={() => setDragActive(false)}
                      onDrop={(e) => {
                        e.preventDefault();
                        setDragActive(false);
                        addFiles(e.dataTransfer.files);
                      }}
                      className={cn(
                        "mx-auto flex max-w-3xl flex-col gap-2 rounded-xl transition-all",
                        dragActive && "ring-2 ring-blue-300",
                      )}
                    >
                      {pendingFiles.length > 0 && (
                        <div className="flex flex-wrap gap-2 px-3.5 pt-3">
                          {pendingFiles.map((f, i) => (
                            <span
                              key={`${f.name}-${i}`}
                              className="bg-background flex items-center gap-1.5 rounded-lg border border-solid px-2 py-1 text-xs"
                            >
                              {fileIconByName(f.name, "size-3.5 shrink-0")}
                              <span className="max-w-[160px] truncate">{f.name}</span>
                              <button
                                type="button"
                                onClick={() => removeFile(i)}
                                className="text-muted-foreground hover:text-foreground"
                              >
                                <XIcon className="size-3" />
                              </button>
                            </span>
                          ))}
                        </div>
                      )}
                      <textarea
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (
                            e.key === "Enter" &&
                            !e.shiftKey &&
                            !e.metaKey &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault();
                            const el = e.target as HTMLElement | undefined;
                            const form = el?.closest("form");
                            form?.requestSubmit();
                          }
                        }}
                        placeholder="输入消息..."
                        className="field-sizing-content resize-none border-none bg-transparent p-3.5 pb-0 shadow-none ring-0 outline-none focus:ring-0 focus:outline-none"
                      />

                      <div className="flex items-center gap-2 p-2 pt-4">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          title="上传文件（docx/xlsx/pdf，最多 5 个）"
                          disabled={isLoading}
                          onClick={() => fileInputRef.current?.click()}
                          className={cn(
                            "rounded-full transition-all hover:bg-muted-foreground/20 active:scale-95",
                            dragActive && "bg-blue-100 text-blue-700",
                          )}
                        >
                          <Paperclip className="size-4" />
                        </Button>
                        <input
                          ref={fileInputRef}
                          type="file"
                          multiple
                          accept=".docx,.xlsx,.pdf"
                          className="hidden"
                          onChange={(e) => {
                            addFiles(e.target.files);
                            e.target.value = "";
                          }}
                        />
                        {stream.isLoading ? (
                          <Button
                            key="stop"
                            onClick={() => stream.stop()}
                            className="ml-auto"
                          >
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                            取消
                          </Button>
                        ) : (
                          <Button
                            type="submit"
                            className="ml-auto shadow-md transition-all"
                            disabled={isLoading || !input.trim()}
                          >
                            发送
                          </Button>
                        )}
                      </div>
                    </form>
                  </div>
                </div>
              }
            />
          </StickToBottom>
        </motion.div>
        <div className="relative flex flex-col border-l">
          <div className="absolute inset-0 flex min-w-[30vw] flex-col">
            <div className="grid grid-cols-[1fr_auto] border-b p-4">
              <ArtifactTitle className="truncate overflow-hidden" />
              <button
                onClick={closeArtifact}
                className="cursor-pointer"
              >
                <XIcon className="size-5" />
              </button>
            </div>
            <ArtifactContent className="relative flex-grow" />
          </div>
        </div>
      </div>

      <FilePreviewSheet
        file={previewFile}
        onClose={() => setPreviewFile(null)}
      />

      <SessionFilesSheet
        open={filesOpen}
        onOpenChange={setFilesOpen}
        files={files}
        onPreview={(f) => {
          // 先关文件面板再开预览，避免两个右侧抽屉叠在一起
          setFilesOpen(false);
          setPreviewFile(f);
        }}
      />
    </div>
  );
}
