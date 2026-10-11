import { Thread } from "@langchain/langgraph-sdk";
import { useQueryState } from "nuqs";
import {
  createContext,
  useContext,
  ReactNode,
  useCallback,
  useRef,
  useState,
  Dispatch,
  SetStateAction,
} from "react";
import { pageSessions } from "@/lib/eka-api";

/** 每页会话条数：与后端 /page 默认值保持一致 */
const PAGE_SIZE = 20;

interface ThreadContextType {
  getThreads: () => Promise<Thread[]>;
  loadMore: () => Promise<void>;
  hasMore: boolean;
  loadingMore: boolean;
  threads: Thread[];
  setThreads: Dispatch<SetStateAction<Thread[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
}

const ThreadContext = createContext<ThreadContextType | undefined>(undefined);

/** 会话伪装 Thread：历史列表只读 thread_id 与首条消息内容（当标题） */
function toThread(id: number, title: string): Thread {
  return {
    thread_id: String(id),
    values: { messages: [{ content: title }] },
  } as unknown as Thread;
}

export function ThreadProvider({ children }: { children: ReactNode }) {
  const [apiUrl] = useQueryState("apiUrl");
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  // 已加载到第几页；是否还有下一页；是否正在追加下一页
  const pageRef = useRef(1);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // 防重入标记：滚动事件一帧内可能触发多次，而 setState 是异步的，拦不住并发请求
  const loadingMoreRef = useRef(false);

  // 会话列表：拉第一页并重置分页游标（初次进入、刷新列表用）
  const getThreads = useCallback(async (): Promise<Thread[]> => {
    void apiUrl;
    const page = await pageSessions(1, PAGE_SIZE);
    pageRef.current = 1;
    // 返回条数不足一页即说明已到底，省掉一次注定为空的请求
    setHasMore(page.records.length === PAGE_SIZE);
    return page.records.map((s) => toThread(s.id, s.title));
  }, []);

  // 追加下一页：滚动到底部时调用，结果并到列表尾部，不影响已加载的头部
  const loadMore = useCallback(async (): Promise<void> => {
    // 正在加载或已到底则忽略，避免重复请求同一页
    if (loadingMoreRef.current || !hasMore) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const next = pageRef.current + 1;
      const page = await pageSessions(next, PAGE_SIZE);
      const more = page.records.map((s) => toThread(s.id, s.title));
      setThreads((prev) => [...prev, ...more]);
      pageRef.current = next;
      setHasMore(page.records.length === PAGE_SIZE);
    } catch (e) {
      // 加载更多失败不打断已有列表，仅记录日志，用户滚动可再次触发
      console.error(e);
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }, [hasMore]);

  const value = {
    getThreads,
    loadMore,
    hasMore,
    loadingMore,
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
  };

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}

export function useThreads() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreads must be used within a ThreadProvider");
  }
  return context;
}
