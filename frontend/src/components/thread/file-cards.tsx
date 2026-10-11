// EKA 会话文件卡片：挂在锚定消息下方，展示文件名/大小/最后修改时间；
// AI 生成的产物对用户统一呈现为 PDF（内部实为 HTML 源），可点开预览、下载 PDF；
// 上传的源文件按真实格式展示，仅展示不可预览
"use client";

import type { ChatFileVO } from "@/lib/eka-api";
import {
  displayFileName,
  exportHtmlAsPdf,
  presentedAsPdf,
} from "@/lib/eka-api";
import { cn } from "@/lib/utils";
import {
  Download,
  Eye,
  FileSpreadsheet,
  FileText,
} from "lucide-react";
import { toast } from "sonner";

/**
 * 按文件名后缀取图标：本地待上传文件、以及上传后的源文件都按扩展名区分——
 * 表格绿、PDF 红、其余文档蓝；尺寸可由调用方传入以适配不同容器
 */
export function fileIconByName(
  fileName: string,
  className = "size-4 shrink-0",
) {
  const ext = fileName.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "xlsx":
      return <FileSpreadsheet className={cn(className, "text-green-600")} />;
    case "pdf":
      return <FileText className={cn(className, "text-red-600")} />;
    default:
      return <FileText className={cn(className, "text-blue-600")} />;
  }
}

/** 按文件取图标：生成产物对用户就是 PDF，一律红图标；源文件按扩展名区分 */
function iconOf(file: ChatFileVO) {
  if (presentedAsPdf(file)) {
    return <FileText className={cn("size-4 shrink-0", "text-red-600")} />;
  }
  return fileIconByName(file.fileName);
}

/** 字节数转可读大小（B / KB / MB），未知时返回空串 */
function formatSize(bytes?: number): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 时间戳转 yyyy-MM-dd HH:mm：后端为 ISO 字符串，直接截取避免时区解析歧义 */
function formatTime(value?: string): string {
  if (!value) return "";
  return value.replace("T", " ").slice(0, 16);
}

/** 单张文件卡片：文件名 + 元信息 + 预览/导出操作；className 可覆盖默认宽度 */
export function FileCard({
  file,
  onPreview,
  className,
}: {
  file: ChatFileVO;
  onPreview: (file: ChatFileVO) => void;
  className?: string;
}) {
  const asPdf = presentedAsPdf(file);
  const previewable = asPdf;
  const exportable = asPdf;

  return (
    <div
      className={cn(
        "bg-muted/60 hover:bg-muted flex w-64 items-center gap-3 rounded-xl border border-solid px-3 py-2.5 transition-colors",
        className,
      )}
    >
      {iconOf(file)}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{displayFileName(file)}</div>
        <div className="text-muted-foreground truncate text-xs">
          {[formatSize(file.fileSize), formatTime(file.updatedAt)]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {previewable && (
          <button
            type="button"
            title="预览"
            onClick={() => onPreview(file)}
            className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
          >
            <Eye className="size-4" />
          </button>
        )}
        {exportable && (
          <button
            type="button"
            title="下载 PDF"
            onClick={() => {
              exportHtmlAsPdf(file.id, displayFileName(file)).catch((e) =>
                toast.error(e.message),
              );
            }}
            className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
          >
            <Download className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * 一组文件卡片：渲染在某条锚定消息下方，human 消息靠右、AI 消息靠左
 */
export function FileCards({
  files,
  align = "left",
  onPreview,
}: {
  files: ChatFileVO[];
  align?: "left" | "right";
  onPreview: (file: ChatFileVO) => void;
}) {
  if (files.length === 0) return null;
  return (
    <div
      className={cn(
        "flex flex-col gap-1.5",
        align === "right" ? "items-end" : "items-start",
      )}
    >
      {files.map((file) => (
        <FileCard
          key={file.id}
          file={file}
          onPreview={onPreview}
        />
      ))}
    </div>
  );
}
