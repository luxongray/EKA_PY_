// EKA 生成文件预览面板：右侧滑出，iframe 直接加载后端 /file/preview 返回的 HTML 原文；
// 对用户呈现为 PDF（文件名、按钮均按 PDF 展示）
"use client";

import type { ChatFileVO } from "@/lib/eka-api";
import { displayFileName, exportHtmlAsPdf, previewUrl } from "@/lib/eka-api";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Download } from "lucide-react";
import { toast } from "sonner";

/**
 * 文件预览抽屉：file 为空表示关闭；仅 AI 生成的 HTML 产物会走本组件
 */
export function FilePreviewSheet({
  file,
  onClose,
}: {
  file: ChatFileVO | null;
  onClose: () => void;
}) {
  return (
    <Sheet
      open={!!file}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent
        side="right"
        className="w-full gap-0 p-0 sm:max-w-3xl"
      >
        <SheetHeader className="border-b p-4">
          <SheetTitle className="truncate pr-8 text-base">
            {file && displayFileName(file)}
          </SheetTitle>
        </SheetHeader>
        {file && (
          <>
            <div className="flex items-center justify-end border-b px-4 py-2">
              <button
                type="button"
                onClick={() => {
                  exportHtmlAsPdf(file.id, displayFileName(file)).catch((e) =>
                    toast.error(e.message),
                  );
                }}
                className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-sm transition-colors"
              >
                <Download className="size-4" />
                下载 PDF
              </button>
            </div>
            <iframe
              key={file.id}
              src={previewUrl(file.id)}
              title={displayFileName(file)}
              className="w-full flex-1 border-0 bg-white"
            />
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
