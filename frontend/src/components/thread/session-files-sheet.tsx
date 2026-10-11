// EKA 会话级文件面板：右侧抽屉按来源分为「生成文件 / 上传文件」两类分别列出，
// 作为消息流卡片的兜底入口——文件多、或卡片被上下文挤走时用这里找
"use client";

import { useEffect, useState } from "react";

import type { ChatFileVO } from "@/lib/eka-api";
import { FILE_KIND } from "@/lib/eka-api";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { FileCard } from "./file-cards";

/** 文件来源页签：生成产物与用户上传分列两页 */
type FileTab = "generated" | "upload";

/**
 * 会话文件抽屉：按来源分「生成文件 / 上传文件」两个页签，各自按最后修改时间倒序展示
 */
export function SessionFilesSheet({
  open,
  onOpenChange,
  files,
  onPreview,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  files: ChatFileVO[];
  onPreview: (file: ChatFileVO) => void;
}) {
  // 步骤 1：按来源把文件分成两组，供两个页签分别渲染
  const generated = files.filter((file) => file.kind === FILE_KIND.GENERATED);
  const uploaded = files.filter((file) => file.kind === FILE_KIND.SOURCE);

  // 步骤 2：当前页签；每次打开抽屉时停在有内容的一侧（优先生成文件，其次上传文件）
  const [tab, setTab] = useState<FileTab>("generated");
  useEffect(() => {
    if (open) setTab(generated.length > 0 ? "generated" : "upload");
  }, [open, generated.length]);

  const list = tab === "generated" ? generated : uploaded;

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
    >
      <SheetContent
        side="right"
        className="w-full gap-0 p-0 sm:max-w-md"
      >
        <SheetHeader className="border-b p-4">
          <SheetTitle className="text-base">会话文件（{files.length}）</SheetTitle>
        </SheetHeader>
        {/* 页签栏：生成文件 / 上传文件，各带数量 */}
        <div className="flex gap-1 border-b px-4">
          {(
            [
              { key: "generated", label: "生成文件", count: generated.length },
              { key: "upload", label: "上传文件", count: uploaded.length },
            ] as const
          ).map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setTab(item.key)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2.5 text-sm transition-colors",
                tab === item.key
                  ? "border-foreground text-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground border-transparent",
              )}
            >
              {item.label}（{item.count}）
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {list.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              {tab === "generated"
                ? "本会话还没有智能体生成的文件。让它生成报告后，会出现在这里。"
                : "本会话还没有上传的文件。上传 docx/xlsx/pdf 后，会出现在这里。"}
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {list.map((file) => (
                <FileCard
                  key={file.id}
                  file={file}
                  onPreview={onPreview}
                  className="w-full"
                />
              ))}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
