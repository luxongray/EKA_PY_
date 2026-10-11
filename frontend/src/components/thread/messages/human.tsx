import { useStreamContext } from "@/providers/Eka";
import { Message } from "@langchain/langgraph-sdk";
import { useState } from "react";
import { getContentString } from "../utils";
import { cn } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";
import { BranchSwitcher, CommandBar } from "./shared";
import { MultimodalPreview } from "@/components/thread/MultimodalPreview";
import { isBase64ContentBlock } from "@/lib/multimodal-utils";

/**
 * 剥掉后端为了「让模型知道本轮带了哪些文件、文件 ID 是多少」而拼在消息末尾的
 * 内部标注（【本轮上传文件】[id=…] 文件名）。它只是给模型看的，
 * 展示给用户、复制、编辑时都应还原成用户原本输入的内容
 */
function stripFileNotice(text: string): string {
  return text.replace(/\n*【本轮上传文件】[\s\S]*$/, "").trimEnd();
}

function EditableContent({
  value,
  setValue,
  onSubmit,
}: {
  value: string;
  setValue: React.Dispatch<React.SetStateAction<string>>;
  onSubmit: () => void;
}) {
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      onSubmit();
    }
  };

  return (
    <Textarea
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={handleKeyDown}
      className="focus-visible:ring-0"
    />
  );
}

export function HumanMessage({
  message,
  isLoading,
  filesSlot,
}: {
  message: Message;
  isLoading: boolean;
  /** 本轮上传文件的卡片插槽：插在气泡正下方、操作按钮上方，避免被按钮占位推远 */
  filesSlot?: React.ReactNode;
}) {
  const thread = useStreamContext();
  const contentString = stripFileNotice(getContentString(message.content));

  const [isEditing, setIsEditing] = useState(false);
  const [value, setValue] = useState("");

  // EKA：编辑=截断到被改这条之后丢弃，以新文本重发（后端 history 追加一轮）
  const handleSubmitEdit = () => {
    setIsEditing(false);
    if (!value.trim() || !message.id) return;
    void thread.resend(value, message.id);
  };

  return (
    <div
      className={cn(
        "group ml-auto flex items-center gap-2",
        isEditing && "w-full max-w-xl",
      )}
    >
      <div className={cn("flex flex-col gap-2", isEditing && "w-full")}>
        {isEditing ? (
          <EditableContent
            value={value}
            setValue={setValue}
            onSubmit={handleSubmitEdit}
          />
        ) : (
          <div className="flex flex-col gap-2">
            {/* Render images and files if no text */}
            {Array.isArray(message.content) && message.content.length > 0 && (
              <div className="flex flex-wrap items-end justify-end gap-2">
                {message.content.reduce<React.ReactNode[]>(
                  (acc, block, idx) => {
                    if (isBase64ContentBlock(block)) {
                      acc.push(
                        <MultimodalPreview
                          key={idx}
                          block={block}
                          size="md"
                        />,
                      );
                    }
                    return acc;
                  },
                  [],
                )}
              </div>
            )}
            {/* Render text if present, otherwise fallback to file/image name */}
            {contentString ? (
              <p className="bg-muted ml-auto w-fit rounded-3xl px-4 py-2 text-right whitespace-pre-wrap">
                {contentString}
              </p>
            ) : null}
          </div>
        )}

        {filesSlot}

        <div
          className={cn(
            "ml-auto flex items-center gap-2 transition-opacity",
            "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100",
            isEditing && "opacity-100",
          )}
        >
          <BranchSwitcher
            branch={undefined}
            branchOptions={undefined}
            onSelect={(branch) => thread.setBranch(branch)}
            isLoading={isLoading}
          />
          <CommandBar
            isLoading={isLoading}
            content={contentString}
            isEditing={isEditing}
            setIsEditing={(c) => {
              if (c) {
                setValue(contentString);
              }
              setIsEditing(c);
            }}
            handleSubmitEdit={handleSubmitEdit}
            isHumanMessage={true}
          />
        </div>
      </div>
    </div>
  );
}
