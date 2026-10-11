import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useThreads } from "@/providers/Thread";
import { batchDeleteSessions, renameSession } from "@/lib/eka-api";
import { Thread } from "@langchain/langgraph-sdk";
import { useEffect, useRef, useState } from "react";

import { getContentString } from "../utils";
import { useQueryState, parseAsBoolean } from "nuqs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  ChevronDown,
  PanelRightClose,
  Pencil,
  Search,
  SquarePen,
  Trash2,
} from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";

function ThreadList({
  threads,
  onThreadClick,
  selectMode,
  checked,
  onCheck,
  onLoadMore,
  hasMore,
  loadingMore,
}: {
  threads: Thread[];
  onThreadClick?: (threadId: string) => void;
  selectMode: boolean;
  checked: Set<string>;
  onCheck: (threadId: string) => void;
  onLoadMore: () => Promise<void>;
  hasMore: boolean;
  loadingMore: boolean;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { setThreads } = useThreads();
  // “聊天”分组折叠：默认展开
  const [groupOpen, setGroupOpen] = useState(true);
  // 改名弹窗：被改会话 + 输入框草稿
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // 列表滚动容器：用它判断是否已滚到底部
  const listRef = useRef<HTMLDivElement>(null);

  /** 滚动到底部附近时追加下一页：留 40px 提前量，滚动手感更顺 */
  const handleScroll = (): void => {
    if (!groupOpen || !hasMore || loadingMore) return;
    const el = listRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
      void onLoadMore();
    }
  };

  // 内容不足一屏时滚动条不存在，永远触发不了滚动事件：主动补一页，直到撑满或到底
  useEffect(() => {
    if (!groupOpen || !hasMore || loadingMore) return;
    const el = listRef.current;
    if (el && el.scrollHeight <= el.clientHeight) {
      void onLoadMore();
    }
  }, [threads, groupOpen, hasMore, loadingMore, onLoadMore]);

  /** 确认改名：调后端改标题，成功后同步本地列表 */
  const confirmRename = async (): Promise<void> => {
    if (!renaming || !draft.trim()) return;
    setSaving(true);
    try {
      await renameSession(renaming.id, draft.trim());
      const title = draft.trim();
      const id = renaming.id;
      setThreads((prev) =>
        prev.map((t) =>
          t.thread_id === id
            ? ({ ...t, values: { messages: [{ content: title }] } } as unknown as Thread)
            : t,
        ),
      );
      setRenaming(null);
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      ref={listRef}
      onScroll={handleScroll}
      className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent"
    >
      <button
        className="flex items-center gap-1 px-3 text-sm text-gray-500 hover:text-gray-800"
        onClick={() => setGroupOpen((p) => !p)}
      >
        聊天
        <ChevronDown
          className={`size-4 transition-transform ${groupOpen ? "" : "-rotate-90"}`}
        />
      </button>
      {groupOpen &&
        threads.map((t) => {
          let itemText = t.thread_id;
          if (
            typeof t.values === "object" &&
            t.values &&
            "messages" in t.values &&
            Array.isArray(t.values.messages) &&
            t.values.messages?.length > 0
          ) {
            const firstMessage = t.values.messages[0];
            itemText = getContentString(firstMessage.content);
          }
          // 当前选中项：加常驻灰底，和「仅悬停出现」的灰色区分开
          const isActive = t.thread_id === threadId;
          return (
            <div
              key={t.thread_id}
              className="group relative w-[228px]"
            >
              <Button
                variant="ghost"
                className={`w-[228px] items-center justify-start gap-2 text-left font-normal group-hover:bg-gray-100 focus:bg-transparent ${
                  isActive && !selectMode ? "bg-gray-100 hover:bg-gray-200" : ""
                }`}
                onClick={(e) => {
                  e.preventDefault();
                  // 点击后失焦：灰底只在鼠标悬停时出现，移开即消失
                  e.currentTarget.blur();
                  // 多选模式点行即勾选，不跳转
                  if (selectMode) {
                    onCheck(t.thread_id);
                    return;
                  }
                  onThreadClick?.(t.thread_id);
                  if (t.thread_id === threadId) return;
                  setThreadId(t.thread_id);
                }}
              >
                {selectMode && (
                  <input
                    type="checkbox"
                    className="size-4 shrink-0 accent-black"
                    checked={checked.has(t.thread_id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => onCheck(t.thread_id)}
                  />
                )}
                <p className="flex-1 truncate text-ellipsis pr-6">{itemText}</p>
              </Button>
              {!selectMode && (
                <button
                  className="absolute hidden bg-transparent p-1 text-gray-400 hover:bg-transparent hover:text-gray-800 group-hover:block"
                  style={{ top: "50%", right: "8px", transform: "translateY(-50%)" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setRenaming({ id: t.thread_id, title: itemText });
                    setDraft(itemText);
                  }}
                >
                  <Pencil className="size-4" />
                </button>
              )}
            </div>
          );
        })}
      {/* 分页尾部提示：正在追加下一页 */}
      {groupOpen && loadingMore && (
        <div className="px-3 py-1 text-xs text-gray-400">加载中…</div>
      )}
      {/* 改名弹窗：点遮罩取消，保存调后端并同步本地标题 */}
      {renaming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setRenaming(null)}
        >
          <div
            className="w-[320px] rounded-lg bg-white p-4 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 text-sm font-medium">修改会话名</div>
            <Input
              value={draft}
              maxLength={50}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void confirmRename();
              }}
            />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setRenaming(null)}>
                取消
              </Button>
              <Button size="sm" disabled={!draft.trim() || saving} onClick={() => void confirmRename()}>
                保存
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ThreadHistoryLoading() {
  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {Array.from({ length: 30 }).map((_, i) => (
        <Skeleton
          key={`skeleton-${i}`}
          className="h-10 w-[228px]"
        />
      ))}
    </div>
  );
}

export default function ThreadHistory() {
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );

  const {
    getThreads,
    loadMore,
    hasMore,
    loadingMore,
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
  } = useThreads();
  const [threadId, setThreadId] = useQueryState("threadId");
  const [searchOpen, setSearchOpen] = useState(false);
  const [keyword, setKeyword] = useState("");
  // 批量删除：多选模式 + 已勾选集合
  const [selectMode, setSelectMode] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  // 删除确认弹窗开关
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setThreadsLoading(true);
    getThreads()
      .then(setThreads)
      .catch(console.error)
      .finally(() => setThreadsLoading(false));
  }, []);

  // 搜索过滤：按首条消息内容（即会话标题）本地匹配
  const filteredThreads = keyword.trim()
    ? threads.filter((t) => {
        if (
          typeof t.values === "object" &&
          t.values &&
          "messages" in t.values &&
          Array.isArray(t.values.messages) &&
          t.values.messages?.length > 0
        ) {
          return getContentString(t.values.messages[0].content).includes(
            keyword.trim(),
          );
        }
        return t.thread_id.includes(keyword.trim());
      })
    : threads;

  return (
    <>
      <div className="shadow-inner-right hidden h-screen w-[260px] shrink-0 flex-col items-start justify-start gap-6 border-r-[1px] border-slate-300 lg:flex">
        <div className="flex w-full items-center justify-between px-4 pt-1.5">
          <span className="text-xl font-semibold tracking-tight">EKA</span>
          <div className="flex items-center">
            <Button
              className="hover:bg-gray-100"
              variant="ghost"
              onClick={() => setSearchOpen((p) => !p)}
            >
              <Search className="size-5" />
            </Button>
            <Button
              className="hover:bg-gray-100"
              variant="ghost"
              onClick={() => {
                // 进多选模式：勾选会话后批量删除
                setChecked(new Set());
                setSelectMode(true);
              }}
            >
              <Trash2 className="size-5" />
            </Button>
            <Button
              className="hover:bg-gray-100"
              variant="ghost"
              onClick={() => setChatHistoryOpen((p) => !p)}
            >
              <PanelRightClose className="size-5" />
            </Button>
          </div>
        </div>
        {searchOpen && (
          <div className="w-full px-4">
            <Input
              placeholder="搜索会话"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </div>
        )}
        <div className="w-full px-1">
          <Button
            variant="ghost"
            className="w-full items-center justify-start gap-2 rounded-xl bg-transparent py-5 text-left font-normal hover:bg-gray-100 focus:bg-transparent"
            onClick={(e) => {
              // 点击后失焦：焦点态与默认同为浅灰，深灰只在鼠标悬停时出现
              e.currentTarget.blur();
              setThreadId(null);
            }}
          >
            <SquarePen className="size-5" />
            新建会话
          </Button>
        </div>
        {threadsLoading ? (
          <ThreadHistoryLoading />
        ) : (
          <ThreadList
            threads={filteredThreads}
            selectMode={selectMode}
            checked={checked}
            onCheck={(id) => {
              setChecked((prev) => {
                const next = new Set(prev);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              });
            }}
            onLoadMore={loadMore}
            hasMore={hasMore}
            loadingMore={loadingMore}
          />
        )}
        {selectMode && (
          <div className="flex w-full items-center justify-between border-t-[1px] border-slate-200 px-4 py-3">
            <span className="text-sm text-gray-500">已选 {checked.size} 项</span>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSelectMode(false);
                  setChecked(new Set());
                }}
              >
                取消
              </Button>
              <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={checked.size === 0 || deleting}
                  onClick={() => setConfirmOpen(true)}
                >
                  删除
                </Button>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>确认删除</AlertDialogTitle>
                    <AlertDialogDescription>
                      将删除选中的 {checked.size}{" "}
                      个会话，消息不可恢复，确定继续吗？
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction
                      className="bg-destructive text-white shadow-xs hover:bg-destructive/90"
                      onClick={() => {
                        // 批量删除：调后端逻辑删，本地过滤列表，删到当前会话则新建
                        const ids = [...checked];
                        setDeleting(true);
                        batchDeleteSessions(ids.map(Number))
                          .then(() => {
                            setThreads((prev) =>
                              prev.filter((t) => !checked.has(t.thread_id)),
                            );
                            if (threadId && checked.has(threadId)) {
                              setThreadId(null);
                            }
                            setChecked(new Set());
                            setSelectMode(false);
                          })
                          .catch(console.error)
                          .finally(() => setDeleting(false));
                      }}
                    >
                      确认删除
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
        )}
      </div>
      <div className="lg:hidden">
        <Sheet
          open={!!chatHistoryOpen && !isLargeScreen}
          onOpenChange={(open) => {
            if (isLargeScreen) return;
            setChatHistoryOpen(open);
          }}
        >
          <SheetContent
            side="left"
            className="flex lg:hidden"
          >
            <SheetHeader>
              <SheetTitle>EKA</SheetTitle>
            </SheetHeader>
            <ThreadList
              threads={threads}
              onThreadClick={() => setChatHistoryOpen((o) => !o)}
              selectMode={false}
              checked={checked}
              onCheck={() => {}}
              onLoadMore={loadMore}
              hasMore={hasMore}
              loadingMore={loadingMore}
            />
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}
